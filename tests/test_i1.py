#!/usr/bin/env python3
"""CP-I1 -- Ollama as an opt-in embeddings provider.

H3 built the whole vector path against a data file. This checkpoint adds the
second provider -- a local inference server the user names -- and asserts that
adding it changed nothing about the contract: the same `namespace`, the same
`embed(text) -> unit vector`, the same None-vs-[] distinction, the same refusal
to do anything at all until someone turns it on.

**No test here talks to a real Ollama, and that is load-bearing.** A live
model's vectors move with its version, its quantisation and the machine's BLAS,
so a known-answer test against one would assert numbers no other machine
reproduces -- green only where it was written, which is the H3 lesson repeated.
Instead a local `http.server` returns FIXED vectors. That tests exactly what
this provider is responsible for: the request shape, the response parsing, the
normalisation, the dim contract, and every error path. Whether all-minilm is a
good embedding model is Ollama's problem, not homegraph's.

**The fixed vectors are deliberately NOT unit length.** `search._cosine` is a
plain dot product because its operands are documented as normalised, and a
provider that forgot to normalise would not crash -- it would silently rank
long vectors above close ones. So the fixture is built so that the raw dot and
the cosine DISAGREE: the decoy has six times the magnitude and only 0.6 of the
direction. Only a provider that actually normalises can produce the
target-first order, which is what makes this gate test normalisation rather
than merely observing that floats came back. (The H3 lesson: a gate naming a
component must go red when that component is neutralised.)

The load-bearing checks are the ones that can say NO:

  * an unreachable endpoint refuses with exit 2 and writes NOTHING, rather than
    leaving a half-filled namespace that later searches would trust;
  * `dim` is measured from the server, never taken on the config's word, and a
    declared dim that disagrees is refused;
  * a model swapped behind its name mid-run is refused rather than mixed into
    one namespace;
  * importing the provider opens no socket;
  * an unknown locator scheme is refused, not silently read as a filename.

Run:
    python3 tests/test_i1.py
"""
from __future__ import annotations

import array
import http.server
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import providers                                     # noqa: E402
from homegraph.models.m3_build import build as m3_build             # noqa: E402
from homegraph.providers import ollama                              # noqa: E402
from homegraph.search import hybrid_search, vector_search           # noqa: E402
from homegraph.store import Store                                   # noqa: E402

results, check = reporter(64)


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="i1-", dir=os.path.expanduser("~/.homegraph"))


# -- the fixed vectors, declared --------------------------------------------
#
# Two dimensions, and the server picks a row by looking for one marker token in
# the text it was sent. Every number below is checkable by hand:
#
#   query  "query index retrieval"  -> [0.6, 0]    |v| = 0.6  (SHORT)
#   target contains "retrieval"     -> [0.6, 0]    |v| = 0.6  cos(q) = 1.00
#   decoy  contains "onion"         -> [6, 8]      |v| = 10   cos(q) = 0.60
#
#   raw dot:    target 0.36  <  decoy 3.6    -> DECOY first   (wrong)
#   normalised: target 1.00  >  decoy 0.60   -> TARGET first  (right)
#
# **Two mutations, two different checks, and the audit proved one is not enough.**
# The ordering above catches "no normalisation at all". It does NOT catch
# normalising with the wrong divisor: under L-infinity the decoy becomes
# [0.75, 1.0] and the target [1, 0], so the target still ranks first and a gate
# resting on order alone stays green over vectors of length 1.25. And under a
# "skip vectors that are already short enough" mutation the target stays
# [0.6, 0] -- which is why the target is SHORT here rather than unit length as
# it was in the first cut. So the stored vectors are also read back and their
# norms asserted. Ranking catches the missing operation; the norm catches the
# wrong one. (sim-auditor CP-I1 finding 3 -- and DECISIONS §30 argued against
# exactly this assertion, which was the error.)
_DIM = 2
_MODEL = "fake-embed"
_V_TARGET = [0.6, 0.0]
_V_DECOY = [6.0, 8.0]
_V_ZERO = [0.0, 0.0]
# A third direction, distinct from both, so an ORDER check has three different
# answers to tell apart. With only target/decoy two of the batch's texts map to
# the same vector and a reversed batch is invisible -- measured 2026-07-31, the
# reversal mutation SURVIVED the first version of that gate.
_V_THIRD = [0.0, 0.5]


def _vector_for(text: str) -> list[float]:
    if "pepper" in text:
        return list(_V_THIRD)
    if "onion" in text:
        return list(_V_DECOY)
    if "retrieval" in text:
        return list(_V_TARGET)
    return list(_V_ZERO)


# -- the fake server --------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass                                    # keep the gate's output clean

    def _send(self, code: int, payload: object) -> None:
        body = (payload if isinstance(payload, bytes)
                else json.dumps(payload).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:                                      # noqa: N802
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            payload = json.loads(raw)
        except Exception:                                           # noqa: BLE001
            payload = {}
        srv.log.append({"path": self.path,
                        "content_type": self.headers.get("Content-Type"),
                        "payload": payload})

        mode = srv.behaviour
        if mode == "unreachable":                 # answered, but as a hard error
            self._send(500, {"error": "boom"})
        elif mode == "model404":
            self._send(404, {"error": "model 'fake-embed' not found"})
        elif mode == "error200":
            self._send(200, {"error": "does not support embeddings"})
        elif mode == "notjson":
            self._send(200, b"<html>nope</html>")
        elif mode == "twovectors":
            self._send(200, {"embeddings": [[1.0, 0.0], [0.0, 1.0]]})
        elif mode == "widerdim":
            self._send(200, {"embeddings": [[1.0, 0.0, 0.0]]})
        elif mode == "truncated":
            # A Content-Length that promises more than is sent, then the
            # connection closes: `resp.read()` raises `http.client.
            # IncompleteRead`. Deterministic, unlike a raw socket returning
            # garbage -- that version raced, and when the close won the race
            # urllib raised `RemoteDisconnected`, which IS an OSError and so
            # was caught by the clause the gate was trying to prove necessary.
            # The gate passed for the wrong reason, and the full mutation sweep
            # caught it as a survivor where the standalone run had not.
            body = b'{"embeddings": [[1.0, 0.0]]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + 200))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
        elif mode == "proxy500":
            # What a reverse proxy or a crashed runner looks like. The word
            # "model" is in the text, and the fix is NOT `ollama pull`.
            self._send(500, {"error": "model runner crashed"})
        elif mode == "zeros":
            # A degenerate model: it answers, with the right shape and the
            # right dim, and says nothing. The dangerous case, because every
            # check that only looks for "did floats come back" passes.
            # One zero vector PER INPUT. Answering a batch with a single vector
            # would trip the count check instead, and the degenerate-model gate
            # would pass on the wrong refusal -- measured 2026-07-31, the
            # mutation that stores zeros SURVIVED while this returned one.
            got = payload.get("input")
            n = len(got) if isinstance(got, list) else 1
            self._send(200, {"embeddings": [[0.0, 0.0]] * n})
        elif mode == "shortbatch":
            # One vector fewer than asked for. With a single input this is the
            # `twovectors` case in reverse; with a batch it is the shape that
            # would silently shift every vector after the gap onto the wrong
            # node if the count were not checked.
            got = payload.get("input")
            if not isinstance(got, list):
                # `connect` probes with a single input. Answering that one short
                # would kill the connection before the batch path is reached, and
                # the gate would pass on the wrong refusal.
                self._send(200, {"embeddings": [_vector_for(str(got))]})
            else:
                self._send(200,
                           {"embeddings": [_vector_for("retrieval")] * (len(got) - 1)})
        elif mode == "widerdim_second":
            # The FIRST vector is the right width and the second is not. A dim
            # check that looks at vectors[0] and stops passes this.
            got = payload.get("input")
            if not isinstance(got, list):
                self._send(200, {"embeddings": [[1.0, 0.0]]})
            else:
                self._send(200, {"embeddings": [[1.0, 0.0], [1.0, 0.0, 0.0]]})
        else:
            got = payload.get("input", "")
            if isinstance(got, list):
                self._send(200, {"embeddings": [_vector_for(str(t)) for t in got]})
            else:
                self._send(200, {"embeddings": [_vector_for(str(got))]})


class _FakeOllama:
    """A real HTTP server on a real socket -- just not Ollama.

    A real socket rather than a monkeypatched `urlopen` on purpose: the thing
    under test is a urllib client, and patching the transport would leave the
    request shape, the timeout and the error mapping untested while looking
    like coverage.
    """

    def __init__(self, behaviour: str = "ok"):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.behaviour = behaviour                            # type: ignore[attr-defined]
        self.httpd.log = []                                         # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    @property
    def log(self) -> list:
        return self.httpd.log                                       # type: ignore[attr-defined]

    def behave(self, mode: str) -> None:
        self.httpd.behaviour = mode                                 # type: ignore[attr-defined]

    def __enter__(self) -> "_FakeOllama":
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# -- the mini-corpus, declared ----------------------------------------------
#
# Same shape as H3's: heading-less files so each is exactly one `file` node, and
# a query whose OR-shortlist contains both target and decoy so the ranking is
# decided by the vectors and not by which documents were candidates.
_CORPUS = {
    "target.md": "retrieval search ranking\n",     # shares {retrieval}
    "decoy.md": "query index onion\n",             # shares {query, index}
    "zero.md": "nothing here at all\n",            # shares nothing
}
_QUERY = "query index retrieval"


def _build(d: str) -> str:
    paths = []
    for name, text in _CORPUS.items():
        p = os.path.join(d, name)
        open(p, "w").write(text)
        paths.append(p)
    db = os.path.join(d, "m3.db")
    with Store(db, model="m3") as s:
        m3_build(s, sorted(paths), "2026-07-22")
        s.rebuild_fts()
    return db


def _embed_all(db: str, emb: object) -> int:
    """Mirror of `cli._embed_store`, including the zero-vector skip.

    The skip is not incidental here. When this helper stored zero vectors and
    production did not, the two disagreed about what a namespace contains, and
    the unit-length assertion below failed on a vector production would never
    have written -- a test failing for a reason the product does not have.
    """
    prov, mdl, dim = emb.namespace                                  # type: ignore[attr-defined]
    n = 0
    with Store(db, model="m3") as s:
        s.begin_immediate()
        for r in s.db.execute("SELECT id, title, body FROM nodes").fetchall():
            text = " ".join(p for p in (r["title"], r["body"]) if p).strip()
            if not text:
                continue
            vec = emb.embed(text)                                   # type: ignore[attr-defined]
            if not any(vec):
                continue
            s.upsert_embedding(r["id"], prov, mdl, dim, vec)
            n += 1
    return n


def _open(db: str, model: str = _MODEL) -> Store:
    return Store(db, embeddings={"provider": "ollama", "model": model})


# -- gates ------------------------------------------------------------------


def t_request_shape():
    """The client POSTs JSON to /api/embed with the documented field names.

    Asserted because the endpoint is a contract with software this project does
    not own: a silent rename to `prompt` would still 'work' against a forgiving
    server and fail against a strict one, and nothing else here would notice.
    """
    with _FakeOllama() as srv:
        emb = ollama.connect(srv.endpoint, _MODEL)
        emb.embed("retrieval")
        first, last = srv.log[0], srv.log[-1]
        check("POSTs to /api/embed", last["path"] == ollama.EMBED_PATH,
              last["path"])
        check("sends Content-Type: application/json",
              last["content_type"] == "application/json",
              str(last["content_type"]))
        check("payload names model and input",
              last["payload"].get("model") == _MODEL
              and last["payload"].get("input") == "retrieval",
              str(last["payload"]))
        check("connect() probes exactly once before any embed",
              len(srv.log) == 2 and first["payload"].get("input")
              == ollama.PROBE_TEXT,
              "%d request(s)" % len(srv.log))


def t_dim_is_measured_not_declared():
    """dim comes from the server's answer; a config that disagrees is refused.

    The failure this prevents is quiet: vectors written under dim A and searched
    under dim B match nothing, and 'no semantic hits' is indistinguishable from
    'the corpus has no match'.
    """
    with _FakeOllama() as srv:
        emb = ollama.connect(srv.endpoint, _MODEL)
        check("dim is measured from the response", emb.dim == _DIM,
              "dim=%d" % emb.dim)
        check("namespace is (ollama, model, dim)",
              emb.namespace == ("ollama", _MODEL, _DIM), str(emb.namespace))
        try:
            ollama.connect(srv.endpoint, _MODEL, declared_dim=99)
            ok, detail = False, "accepted a declared dim of 99"
        except ollama.OllamaError as exc:
            ok, detail = "99" in str(exc), str(exc)[:60]
        check("a declared dim that disagrees is refused", ok, detail)

        # And through the config door, which is the one `embed` uses.
        try:
            providers.from_config({"provider": "ollama", "model": _MODEL,
                                   "endpoint": srv.endpoint, "dim": 99})
            ok2 = False
        except ollama.OllamaError:
            ok2 = True
        check("from_config refuses the same mismatch", ok2)


def t_normalisation_sets_the_order():
    """Normalisation, not magnitude, decides the ranking.

    The server returns the decoy at six times the target's magnitude and 0.6 of
    its direction. A raw dot ranks the decoy first; cosine ranks the target
    first. Both documents are in the shortlist, so the only thing that can flip
    the order is whether the provider normalised. Neutralise `l2_normalise` and
    this goes red -- which is the point.
    """
    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama() as srv:
            emb = ollama.connect(srv.endpoint, _MODEL)
            _embed_all(db, emb)
            with _open(db) as s:
                hits = vector_search(s, _QUERY, limit=10, embedder=emb)
            keys = [os.path.basename(h["node_key"]) for h in (hits or [])]
            both = {"target.md", "decoy.md"} <= set(keys)
            check("both target and decoy are in the shortlist", both,
                  ",".join(keys))
            check("cosine order wins: target before decoy",
                  bool(keys) and keys[0] == "target.md",
                  "order=%s" % ",".join(keys))

            # Read the vectors back out of the store, not out of the embedder:
            # what matters is the length of what was WRITTEN, since that is what
            # `search._cosine` dots without re-normalising. A wrong divisor
            # leaves the order intact and only shows up here.
            with _open(db) as s:
                rows = s.db.execute(
                    "SELECT vec, dim FROM embeddings").fetchall()
            norms = []
            for r in rows:
                v = array.array("f")
                v.frombytes(r["vec"])
                norms.append(math.sqrt(sum(x * x for x in v)))
            worst = max((abs(n - 1.0) for n in norms), default=1.0)
            check("every stored vector is unit length",
                  bool(norms) and worst < 1e-5,
                  "%d vector(s), worst |norm-1| = %.4g" % (len(norms), worst))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_unreachable_refuses_and_writes_nothing():
    """A dead endpoint is exit 2 with nothing written, not a partial namespace."""
    from homegraph import cli

    d = _tmp()
    try:
        db = _build(d)
        # A port nothing listens on: bind one, learn its number, close it.
        import socket as _socket
        s = _socket.socket()
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
        s.close()

        try:
            ollama.connect("http://127.0.0.1:%d" % dead, _MODEL)
            ok, detail = False, "connected to a dead port"
        except ollama.OllamaUnreachable as exc:
            ok, detail = "Nothing was written" in str(exc), str(exc)[:60]
        check("a dead endpoint raises OllamaUnreachable", ok, detail)

        cfg = os.path.join(d, "config.toml")
        open(cfg, "w").write(
            'root = "%s"\n[roles]\nimage = []\n[embeddings]\n'
            'provider = "ollama"\nmodel = "%s"\n'
            'endpoint = "http://127.0.0.1:%d"\n' % (d, _MODEL, dead))

        class Args:
            def __init__(self):
                self.config, self.model = cfg, ["m3=%s" % db]

        rc = cli.cmd_embed(Args())
        with Store(db) as st:
            n = st.embedding_count("ollama", _MODEL, _DIM)
        check("embed against a dead endpoint exits 2", rc == 2, "rc=%d" % rc)
        check("and writes no vectors at all", n == 0, "embedded=%d" % n)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_failure_midrun_leaves_the_store_untouched():
    """The server dying between nodes rolls the whole store back.

    Half a namespace is worse than none: `vector_search` would run, return the
    handful that made it, and report `hybrid` -- a confident ranking over a
    fraction of the corpus, with nothing saying so.
    """
    from homegraph import cli

    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama() as srv:
            cfg = os.path.join(d, "config.toml")
            open(cfg, "w").write(
                'root = "%s"\n[roles]\nimage = []\n[embeddings]\n'
                'provider = "ollama"\nmodel = "%s"\nendpoint = "%s"\n'
                % (d, _MODEL, srv.endpoint))

            class Args:
                def __init__(self):
                    self.config, self.model = cfg, ["m3=%s" % db]

            # Healthy for the connect probe, then broken for the writes.
            original_post = ollama._post
            state = {"calls": 0}

            def flaky(endpoint, path, payload, timeout):
                state["calls"] += 1
                if state["calls"] > 2:          # probe + one node, then die
                    raise ollama.OllamaUnreachable("link went down")
                return original_post(endpoint, path, payload, timeout)

            # One text per request for this gate: the failure it names happens
            # BETWEEN writes, and at the shipped batch size the whole corpus is
            # a single round trip with no middle to die in. Shrinking the batch
            # keeps the meaning rather than weakening it to "the first request
            # failed" -- which would pass over a provider that wrote half a
            # namespace and then stopped.
            original_batch = cli.EMBED_BATCH
            cli.EMBED_BATCH = 1
            ollama._post = flaky
            try:
                rc = cli.cmd_embed(Args())
            except Exception as exc:                            # noqa: BLE001
                # An escaping provider error IS the failure this gate names, so
                # it has to be reported as a red check rather than crash the
                # run: a traceback here would say "something broke" where the
                # point is "the command stopped refusing".
                rc = "escaped %s" % type(exc).__name__
            finally:
                ollama._post = original_post
                cli.EMBED_BATCH = original_batch

            with Store(db) as st:
                n = st.embedding_count("ollama", _MODEL, _DIM)
            check("a mid-run failure exits 2", rc == 2, "rc=%s" % rc)
            check("and leaves zero vectors, not a partial namespace",
                  n == 0, "embedded=%d" % n)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_model_missing_names_the_fix():
    """A server without the model says so, and says what to do about it."""
    # Both arms catch the BASE error and then assert the specific type. Catching
    # only the specific one would turn "raised the wrong type" into an uncaught
    # exception -- a crash where a red check belongs, which tells the reader
    # nothing about which promise broke.
    with _FakeOllama("model404") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok, detail = False, "accepted a 404"
        except ollama.OllamaError as exc:
            ok = isinstance(exc, ollama.OllamaModelMissing) and "pull" in str(exc)
            detail = "%s: %s" % (type(exc).__name__,
                                 str(exc).replace("\n", " ")[:50])
        check("a 404 becomes OllamaModelMissing naming `ollama pull`", ok,
              detail)
    # ...and the other direction: a failure that merely CONTAINS the word
    # "model" must not be dressed as a missing model. The first cut classified
    # on any status whose text matched "model" or "embed", so a proxy's 500
    # "model runner crashed" told the reader to run `ollama pull` -- an
    # instruction for a problem they do not have, while the real one (the server
    # is broken) went unsaid. Both audits found this independently.
    with _FakeOllama("proxy500") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok3, d3 = False, "accepted a 500"
        except ollama.OllamaError as exc:
            ok3 = (not isinstance(exc, ollama.OllamaModelMissing)
                   and "pull" not in str(exc))
            d3 = "%s: %s" % (type(exc).__name__, str(exc)[:44])
        check("a 500 mentioning 'model' does not say `ollama pull`", ok3, d3)

    with _FakeOllama("error200") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok2, d2 = False, "accepted a 200 carrying an error"
        except ollama.OllamaError as exc:
            ok2 = (isinstance(exc, ollama.OllamaModelMissing)
                   and "support" in str(exc))
            d2 = "%s: %s" % (type(exc).__name__, str(exc)[:44])
        check("a 200 with an `error` field is a failure, not a vector", ok2, d2)


def t_malformed_responses_are_refused():
    """Anything that is not exactly one numeric vector is refused, not salvaged."""
    with _FakeOllama("notjson") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok = False
        except ollama.OllamaError:
            ok = True
        check("a non-JSON body is refused", ok)
    with _FakeOllama("twovectors") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok2 = False
        except ollama.OllamaError as exc:
            ok2 = "exactly 1" in str(exc)
        check("two vectors for one input is refused, not [0]", ok2)


def t_dim_change_midrun_is_refused():
    """A model swapped behind its name mid-run cannot poison the namespace."""
    with _FakeOllama() as srv:
        emb = ollama.connect(srv.endpoint, _MODEL)
        srv.behave("widerdim")
        try:
            emb.embed("retrieval")
            ok, detail = False, "accepted a 3-dim vector under a 2-dim namespace"
        except ollama.OllamaError as exc:
            ok, detail = "changed" in str(exc), str(exc)[:60]
        check("a vector of the wrong length mid-run is refused", ok, detail)


def t_namespace_invalidation():
    """Vectors written under one model are never served under another.

    H3's contract, re-asserted through this provider: the model name is part of
    the namespace, so a switch reports 'nothing embedded here' (None) rather
    than ranking one model's query against another model's vectors.
    """
    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama() as srv:
            emb = ollama.connect(srv.endpoint, _MODEL)
            _embed_all(db, emb)
            with _open(db) as s:
                same = vector_search(s, _QUERY, limit=10, embedder=emb)
            other = ollama.OllamaEmbedder(model="other-model", dim=_DIM,
                                          endpoint=srv.endpoint)
            with _open(db, "other-model") as s:
                switched = vector_search(s, _QUERY, limit=10, embedder=other)
            check("the writing namespace finds vectors", bool(same),
                  "%s hit(s)" % (len(same) if same is not None else "None"))
            check("a different model returns None, not stale vectors",
                  switched is None, repr(switched)[:40])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_none_vs_empty():
    """None (did not run) and [] (ran, found nothing) stay distinct."""
    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama() as srv:
            emb = ollama.connect(srv.endpoint, _MODEL)
            with _open(db) as s:
                before = vector_search(s, _QUERY, limit=10, embedder=emb)
            check("nothing embedded yet -> None", before is None, repr(before))
            _embed_all(db, emb)
            with _open(db) as s:
                nomatch = vector_search(s, "zzzznotaword", limit=10,
                                        embedder=emb)
            check("embedded but no lexical shortlist -> [] not None",
                  nomatch == [], repr(nomatch))
            with _open(db) as s:
                res = hybrid_search(s, _QUERY, limit=10, embedder=emb)
            check("hybrid reports the vector path ran",
                  res._out_mode in ("hybrid", "vector"), res._out_mode)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_no_network_at_import():
    """Importing the provider opens no socket.

    Checked in a subprocess with an audit hook, because the failure it guards
    against -- a probe that ran at import time 'to be helpful' -- is invisible in
    a process that has already imported the module.

    An audit hook rather than a monkeypatched `socket.socket`: replacing the
    class breaks the import machinery itself (the stdlib subclasses it), so that
    version of this test failed for a reason that had nothing to do with
    homegraph. `sys.addaudithook` observes the three events that mean 'reaching
    for the network' without changing any object.

    The hook RECORDS rather than raises, and that difference is load-bearing:
    the realistic bad version of this is a probe wrapped in
    `try: ... except Exception: pass` -- helpful-looking, and it would swallow a
    raising hook whole, leaving the import 'clean'. A recorded event cannot be
    caught by the code that caused it. (mutate_i1 found exactly this.)
    """
    prog = (
        "import sys\n"
        "seen = []\n"
        "WATCHED = ('socket.connect', 'socket.getaddrinfo', 'urllib.Request')\n"
        "def hook(event, args):\n"
        "    if event in WATCHED:\n"
        "        seen.append(event)\n"
        "sys.addaudithook(hook)\n"
        "import homegraph.providers as p\n"
        "import homegraph.providers.ollama as o\n"
        "assert o.EMBED_PATH == '/api/embed'\n"
        "print('NETWORK:' + ','.join(seen) if seen else 'clean')\n")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", prog], cwd=root,
                         capture_output=True, text=True, timeout=60)
    check("importing the ollama provider opens no socket",
          out.returncode == 0 and out.stdout.strip() == "clean",
          (out.stdout.strip() or
           (out.stderr.strip().splitlines() or [""])[-1])[:60])


def t_locator_forms():
    """The locator parser refuses what it cannot read, rather than guessing."""
    with _FakeOllama() as srv:
        port = srv.httpd.server_address[1]
        emb = providers.from_locator("ollama://%s@127.0.0.1:%d" % (_MODEL, port))
        check("ollama://model@host:port builds an ollama embedder",
              emb.namespace == ("ollama", _MODEL, _DIM), str(emb.namespace))

    # Each case asserts WHY it was refused, not merely that something was
    # raised. Without that, `s3://...` falling through to the static loader
    # still 'passes' -- it raises EmbedderDataMissing about a file that never
    # existed, which is a refusal for the wrong reason and sends the reader
    # looking for a path they never wrote. (mutate_i1 found exactly this.)
    # Every entry below was reproduced by the audit as MISPARSED rather than
    # refused, and each reached a different wrong place: `a@b@c` built
    # `http://b@c:11434`, where RFC 3986 reads `b` as userinfo and the request
    # goes to host `c`; the `#` form put the real host in a URL fragment; the
    # full-width digits passed `str.isdigit()` and then raised UnicodeEncodeError
    # out of urllib as a traceback on plain CLI input. (sim-auditor finding 7.)
    bad = {
        "ollama://all-minilm": ("no @host", "malformed"),
        "ollama://@localhost": ("no model", "malformed"),
        "ollama://m@h:notaport": ("non-numeric port", "port"),
        "s3://bucket/matrix.json": ("unknown scheme", "scheme"),
        "": ("empty", "needs a value"),
        "ollama://a@b@c": ("two @, ambiguous host", "more than one '@'"),
        "ollama://m@evil.example#@localhost": ("host hidden by a fragment",
                                               "more than one '@'"),
        "ollama://m@h:1:2": ("two colons in the host", "more than one ':'"),
        "ollama://m@localhost:99999": ("port out of range", "1-65535"),
        "ollama://m@localhost:0": ("port zero", "1-65535"),
        "ollama://m@localhost:１１４３４":
            ("full-width digits", "1-65535"),
        "ollama://m@ localhost ": ("whitespace in the host", "hostname"),
        "ollama://m@localhost/path": ("a path after the host", "hostname"),
        "ollama:// @localhost": ("whitespace-only model", "whitespace"),
    }
    for spec, (why, must_say) in bad.items():
        try:
            providers.from_locator(spec)
            check("locator refused: %s" % why, False, "accepted %r" % spec)
        except providers.PROVIDER_ERRORS as exc:
            check("locator refused: %s" % why, must_say in str(exc),
                  str(exc)[:60])
    # A path stays a path -- the static door is untouched by any of this.
    try:
        providers.from_locator("/no/such/matrix.json")
        ok = False
    except providers.PROVIDER_ERRORS as exc:
        ok = "matrix" in str(exc)
    check("a plain path is still read as a static matrix", ok)

    # Bracketed IPv6 is accepted, because refusing it would be a different kind
    # of wrong: `[::1]` is unambiguous and the old parser mangled it into
    # "'1]' is not a port number".
    ep, _ = providers.parse_ollama_locator("ollama://m@[::1]:11435")
    check("a bracketed IPv6 host parses", ep == "http://[::1]:11435", ep)

    # URI schemes are case-insensitive per RFC 3986; the first cut refused this
    # as an "unknown scheme".
    ep2, _ = providers.parse_ollama_locator("OLLAMA://m@localhost")
    check("the scheme is case-insensitive", ep2 == "http://localhost:11434", ep2)


def t_default_port_only_when_host_is_named():
    """The port may be defaulted; the host may not.

    Naming a host is the act of choosing where to go. 11434 is a convention
    about where a chosen host listens -- so defaulting it decides nothing the
    user did not already decide, while defaulting the host would.

    Asserted against the PURE parser, not by calling `from_locator` and reading
    the failure message. The first cut did the latter and had three defects the
    audit named: it opened a real socket to whatever was listening on 11434
    (against this file's own "no test here talks to a real Ollama"); its
    assertion looked for `DEFAULT_PORT` inside a URL built from `DEFAULT_PORT`,
    so it could only catch "no port was added at all"; and on any machine where
    `all-minilm` IS pulled the connect SUCCEEDED and the gate went red -- red on
    exactly the machines the feature works on. The literal 11434 below is
    deliberate: it is the number README and the docstrings promise, so changing
    the constant should redden this.
    """
    endpoint, model = providers.parse_ollama_locator(
        "ollama://all-minilm@127.0.0.1")
    check("an omitted port resolves to 11434",
          endpoint == "http://127.0.0.1:11434" and model == "all-minilm",
          "%s (%s)" % (endpoint, model))
    endpoint2, _ = providers.parse_ollama_locator(
        "ollama://all-minilm@127.0.0.1:11435")
    check("a named port is used verbatim",
          endpoint2 == "http://127.0.0.1:11435", endpoint2)

    try:
        providers.from_config({"provider": "ollama", "model": "all-minilm"})
        ok2, d2 = False, "accepted a config with no endpoint"
    except ollama.OllamaError as exc:
        ok2, d2 = "never" in str(exc) or "endpoint" in str(exc), str(exc)[:50]
    check("a config with no endpoint is refused, never defaulted", ok2, d2)


def t_visualize_refuses_a_locator():
    """`visualize --embeddings` is static-only, and says why.

    Not a gap in the wiring: the page inlines a word matrix so the browser can
    embed offline. Refused by name so the user does not get 'no such file'
    about a string that was never a path.

    The gate asserts the REASON, not just exit 2, and that is the whole
    difference. Now that `cmd_visualize` also catches a bad matrix path,
    neutralising the locator check no longer crashes -- it falls through to the
    static loader, which fails with "no embedding matrix at
    ollama://all-minilm@localhost" and ALSO returns 2. Asserting the exit code
    alone would stay green over precisely the confusing message this check
    exists to prevent. (sim-auditor CP-I1 finding 6: the first cut crashed
    instead of reddening, and could not discriminate either.)
    """
    import contextlib
    import io

    from homegraph import cli

    d = _tmp()
    try:
        class Args:
            def __init__(self):
                self.model = ["m3=%s" % os.path.join(d, "m3.db")]
                self.mesh_db = None
                self.out = os.path.join(d, "g.html")
                self.limit = 10
                self.min_degree = 0
                self.title = "t"
                self.embeddings = "ollama://all-minilm@localhost"

        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                rc = cli.cmd_visualize(Args())
        except Exception as exc:                                # noqa: BLE001
            rc = "escaped %s" % type(exc).__name__
        said = err.getvalue()
        check("visualize refuses an ollama locator with exit 2", rc == 2,
              "rc=%s" % rc)
        check("and the refusal names the locator, not a missing file",
              "matrix data file, not" in said and "no embedding matrix" not in said,
              said.strip().splitlines()[0][:60] if said.strip() else "(stille)")
        check("and writes no page", not os.path.exists(Args().out))

        # The sibling hole CP-I1 left open: a typo'd matrix path had no handler
        # at all in this command and was a traceback, while every other command
        # exits 2. Asserted here so it cannot reopen.
        class BadPath(Args):
            def __init__(self):
                super().__init__()
                self.embeddings = os.path.join(d, "no-such-matrix.json")

        err2 = io.StringIO()
        try:
            with contextlib.redirect_stderr(err2):
                rc2 = cli.cmd_visualize(BadPath())
        except Exception as exc:                                # noqa: BLE001
            rc2 = "escaped %s" % type(exc).__name__
        check("visualize exits 2 on a missing matrix instead of a traceback",
              rc2 == 2, "rc=%s" % rc2)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_zero_vector_model_is_refused():
    """A model that answers only zero vectors is a refusal, not a green run.

    The worst failure this checkpoint had, and no gate saw it: the server
    answers with the right shape and the right dim and says nothing. Vectors
    were stored, `embedding_count` went positive, `vector_search` therefore RAN,
    every cosine was 0.0, and the stable sort left the BM25 shortlist in its
    original order -- so `--mode vector` printed "pure cosine ranking" over
    lexical order with 0.00000 beside every line, and exited 0.
    (sim-auditor CP-I1 finding 1.)
    """
    from homegraph import cli

    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama("zeros") as srv:
            cfg = os.path.join(d, "config.toml")
            open(cfg, "w").write(
                'root = "%s"\n[roles]\nimage = []\n[embeddings]\n'
                'provider = "ollama"\nmodel = "%s"\nendpoint = "%s"\n'
                % (d, _MODEL, srv.endpoint))

            class Args:
                def __init__(self):
                    self.config, self.model = cfg, ["m3=%s" % db]

            rc = cli.cmd_embed(Args())
            with Store(db) as st:
                n = st.embedding_count("ollama", _MODEL, _DIM)
            check("a model answering only zero vectors exits 2", rc == 2,
                  "rc=%s" % rc)
            check("and no zero vector reaches the store", n == 0,
                  "embedded=%d" % n)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_non_http_server_is_refused():
    """Something answering on the port that is not HTTP is a refusal.

    `http.client.HTTPException` (BadStatusLine, IncompleteRead) derives from
    Exception, not OSError, so the URLError/OSError clause never saw it and it
    escaped as a traceback. Reachable by pointing the locator at a TLS port --
    the handshake bytes get read as a status line. (sim-auditor finding 8,
    codex angle 1.)

    A truncated body rather than a raw garbage socket, and the difference is
    the whole point: the first version raced the connection close, and when the
    close won, urllib raised `RemoteDisconnected` -- an `OSError` -- so the gate
    went green through the very clause it exists to show is insufficient. It
    passed standalone and survived its mutation in the full sweep, which is
    exactly how a flaky gate reads: green, until the one run that matters.
    """
    with _FakeOllama("truncated") as srv:
        try:
            ollama.connect(srv.endpoint, _MODEL)
            ok, detail = False, "accepted a truncated response"
        except ollama.OllamaError as exc:
            ok = isinstance(exc, ollama.OllamaUnreachable)
            detail = "%s: %s" % (type(exc).__name__,
                                 str(exc).replace("\n", " ")[:44])
        except Exception as exc:                                # noqa: BLE001
            ok, detail = False, "escaped %s" % type(exc).__name__
    check("a truncated HTTP body is refused, not a traceback", ok, detail)


def t_search_refuses_when_the_provider_dies_mid_query():
    """`search` reaches the provider twice; the handler has to cover both.

    `from_locator` probes, and then `hybrid_search` embeds the QUERY -- a second
    network call, which had no handler. A server that died between the two gave
    a traceback where every other failure in this command gives exit 2.
    (sim-auditor CP-I1 finding 2.)
    """
    from homegraph import cli

    d = _tmp()
    try:
        db = _build(d)
        with _FakeOllama() as srv:
            emb = ollama.connect(srv.endpoint, _MODEL)
            _embed_all(db, emb)
            port = srv.httpd.server_address[1]

            class Args:
                def __init__(self):
                    self.db = db
                    self.query = [_QUERY]
                    self.limit = 5
                    self.mode = "auto"
                    self.embeddings = ("ollama://%s@127.0.0.1:%d"
                                       % (_MODEL, port))

            original = ollama._post
            state = {"calls": 0}

            def flaky(endpoint, path, payload, timeout):
                state["calls"] += 1
                if state["calls"] > 1:      # the probe lands; the query does not
                    raise ollama.OllamaUnreachable("link went down")
                return original(endpoint, path, payload, timeout)

            ollama._post = flaky
            try:
                rc = cli.cmd_search(Args())
            except Exception as exc:                            # noqa: BLE001
                rc = "escaped %s" % type(exc).__name__
            finally:
                ollama._post = original
            check("search exits 2 when the provider dies mid-query", rc == 2,
                  "rc=%s" % rc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_batching_is_a_round_trip_saving_and_nothing_else():
    """CP-BATCH -- one request per batch, same vectors, same order.

    Measured against a real bge-m3 before this was written: 255.6 ms per text
    one at a time, 8.1 ms at batch 64, and the vectors bit-identical (largest
    element difference 0.00e+00). So the whole risk of this change is ALIGNMENT,
    not arithmetic. A single call cannot confuse two texts; a batch can, and a
    misaligned batch writes correct vectors onto the wrong nodes -- a store that
    is confidently, systematically wrong with nothing raising.
    """
    with _FakeOllama() as srv:
        emb = ollama.connect(srv.endpoint, _MODEL, declared_dim=_DIM)

        texts = ["about retrieval", "an onion", "", "a pepper", "  "]
        many = emb.embed_many(texts)
        one = [emb.embed(t) for t in texts]
        check("embed_many agrees with embed, text for text", many == one,
              "%r != %r" % (many, one))

        # Order, asserted against DIFFERENT expected vectors per position, so a
        # reversed or rotated batch cannot pass by symmetry.
        check("the batch keeps the caller's order",
              many[0] == emb.embed("about retrieval")
              and many[1] == emb.embed("an onion")
              and many[3] == emb.embed("a pepper")
              and len({tuple(many[0]), tuple(many[1]), tuple(many[3])}) == 3,
              "%r" % ([many[0], many[1], many[3]],))

        check("an empty text is a zero vector and shifts nothing",
              many[2] == [0.0] * _DIM and many[4] == [0.0] * _DIM
              and many[3] == emb.embed("a pepper"),
              "%r" % (many,))

        # Only the non-empty texts are sent: the empties are known answers, and
        # a round trip for them would cost latency to learn nothing.
        srv.log.clear()
        emb.embed_many(["about retrieval", "", "an onion"])
        sent = srv.log[-1]["payload"]["input"]
        check("empty texts are not sent to the server",
              sent == ["about retrieval", "an onion"], "sent %r" % (sent,))

    with _FakeOllama("shortbatch") as srv:
        emb = ollama.connect(srv.endpoint, _MODEL, declared_dim=_DIM)
        try:
            emb.embed_many(["about retrieval", "an onion", "a pepper"])
            ok, detail = False, "a short batch was accepted"
        except ollama.OllamaError as exc:
            ok, detail = "expected exactly" in str(exc), str(exc)[:70]
        check("a batch answered short is refused, not truncated", ok, detail)

    with _FakeOllama("widerdim_second") as srv:
        emb = ollama.connect(srv.endpoint, _MODEL, declared_dim=_DIM)
        try:
            emb.embed_many(["about retrieval", "an onion"])
            ok, detail = False, "a wrong dim in position 2 was accepted"
        except ollama.OllamaError as exc:
            ok, detail = "dimensional" in str(exc), str(exc)[:70]
        check("the dim is checked on every vector, not just the first",
              ok, detail)


def t_the_write_loop_does_not_trust_a_providers_count():
    """`_embed_store` takes ANY provider with `embed_many`, so it checks.

    The ollama provider refuses a miscounted response itself, which made this
    look covered. It is not: the loop is generic, `zip` stops at the shorter
    side, and a provider returning three vectors for four texts would leave the
    fourth node silently unembedded while the run reported success. Raised by
    codex, 2026-07-31.
    """
    from homegraph import cli

    class Stingy:
        namespace = ("stingy", "m", 2)

        def embed(self, text):                                  # noqa: ARG002
            return [1.0, 0.0]

        def embed_many(self, texts):
            return [[1.0, 0.0]] * (len(texts) - 1)              # one short

    tmp = tempfile.mkdtemp(prefix="i1-count-", dir=os.path.expanduser("~/.homegraph"))
    try:
        db = os.path.join(tmp, "m3.db")
        with Store(db, model="m3") as st:
            for i in range(3):
                st.upsert_node("n%d" % i, kind="file", path="/x/%d.md" % i,
                               title="t%d" % i, body="b%d" % i, as_of="2026-07-31")
        try:
            cli._embed_store(db, Stingy())
            ok, detail = False, "a short batch was written anyway"
        except ValueError as exc:
            ok, detail = "partly-aligned" in str(exc), str(exc)[:60]
        check("the write loop refuses a provider that returns too few vectors",
              ok, detail)
        with Store(db) as st:
            n = st.embedding_count("stingy", "m", 2)
        check("and writes no vector at all when it does", n == 0, "stored=%d" % n)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_a_matrix_cannot_claim_another_providers_namespace():
    """A static matrix file declaring `provider = "ollama"` is refused.

    `provider` is the first component of the namespace. For static it is read
    out of the data file; for ollama it is a hardcoded literal. So a hand-written
    matrix could have static's arithmetic write vectors under the network
    provider's namespace, and a later `search --embeddings ollama://...` would
    serve them against real Ollama query vectors -- confidently, and wrongly.
    Harmless while there was one provider; reachable the moment there were two.
    (sim-auditor CP-I1 finding 10.)
    """
    d = _tmp()
    try:
        path = os.path.join(d, "liar.json")
        json.dump({"provider": "ollama", "model": _MODEL, "dim": _DIM,
                   "tokens": ["retrieval"], "matrix": [[1.0, 0.0]]},
                  open(path, "w"))
        try:
            providers.from_config({"provider": "static", "path": path,
                                   "model": _MODEL, "dim": _DIM})
            ok, detail = False, "accepted a matrix claiming provider 'ollama'"
        except providers.PROVIDER_ERRORS as exc:
            ok, detail = "namespace" in str(exc), str(exc)[:60]
        check("a static matrix cannot declare another provider", ok, detail)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    for fn in (t_request_shape, t_dim_is_measured_not_declared,
               t_normalisation_sets_the_order,
               t_unreachable_refuses_and_writes_nothing,
               t_failure_midrun_leaves_the_store_untouched,
               t_model_missing_names_the_fix, t_malformed_responses_are_refused,
               t_dim_change_midrun_is_refused, t_namespace_invalidation,
               t_none_vs_empty, t_no_network_at_import, t_locator_forms,
               t_default_port_only_when_host_is_named,
               t_visualize_refuses_a_locator,
               t_a_zero_vector_model_is_refused,
               t_a_non_http_server_is_refused,
               t_search_refuses_when_the_provider_dies_mid_query,
               t_batching_is_a_round_trip_saving_and_nothing_else,
               t_the_write_loop_does_not_trust_a_providers_count,
               t_a_matrix_cannot_claim_another_providers_namespace):
        fn()
    bad = [r for r in results if not r[1]]
    print("\nCP-I1: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_checkpoint_i1():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
