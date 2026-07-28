#!/usr/bin/env python3
"""CP-GUI -- the GUI's payload builders and HTTP routes.

Every check here is about what Python decides, because the page decides
nothing. The structural checks run against a synthetic M3 store built in a
tempdir, the way every other checkpoint builds its own corpus, so this file
is not the one checkpoint in `tests/` that silently does nothing on a machine
without `~/.homegraph/real-*.db`. The one check that still needs the real
corpus is `t_isolated_matches_md_gaps`: it ties this surface to an
already-measured fact, that the set `/graph` calls isolated is the set
`isolated_notes` reports, so the GUI and `md gaps` cannot drift apart without
anyone saying so -- and it prints a named SKIPPED line rather than vanishing
when the real store is absent.

Run:
    python3 tests/test_gui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter                                       # noqa: E402

from homegraph import gui                                          # noqa: E402
from homegraph.models.m3_build import build, isolated_notes        # noqa: E402
from homegraph.store import Store                                  # noqa: E402

results, check = reporter(50)


def _running(model_paths, mesh_db=None, limit_per_model=None):
    """Context manager yielding a live port on a real socket. No mocking.

    `limit_per_model` defaults to `graph_payload`'s own default (uncapped);
    passed through only by the check that needs a forced cap to put
    something non-empty in `truncated`.
    """
    import contextlib
    import threading
    from http.server import HTTPServer

    from homegraph.mcp_server import Server

    @contextlib.contextmanager
    def cm():
        kwargs = {} if limit_per_model is None else {"limit_per_model": limit_per_model}
        payload = gui.graph_payload(model_paths, mesh_db=mesh_db, **kwargs)
        httpd = HTTPServer(("127.0.0.1", 0),
                           gui.build_handler(Server(model_paths,
                                                    mesh_db=mesh_db), payload))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()

    return cm()


def _post(port, path, body):
    """POST JSON to a live handler and return the decoded JSON response."""
    import json
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _build_synthetic_m3(root):
    """A tiny M3 store: a linked pair and a file nothing points at or from.

    Not `tests/fixtures/synthetic.py`: that fixture plants a declared answer
    key for six checkpoints of classification and extraction (CP-0..CP-6).
    `graph_payload` never classifies or extracts anything -- it reads nodes
    and edges `m3_build.build` already wrote -- so the only property this
    needs from a corpus is one file that links to another, and one that
    links to nothing, which three files on disk are enough to state.
    """
    def write(name, text):
        path = os.path.join(root, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    paths = [
        write("linked-a.md",
              "---\ntags: [demo]\n---\n# A\n\n## Sub\n\n[[linked-b]]\n"),
        write("linked-b.md", "# B\n\nSome text.\n"),
        write("isolated.md", "# Isolated\n\nNothing links here.\n"),
    ]
    db = os.path.join(root, "synthetic-m3.db")
    with Store(db, model="m3") as store:
        build(store, paths, date(2026, 7, 22))
        # `build` writes nodes and edges; the FTS5 shadow table is separate
        # postprocessing (`Store.rebuild_fts`, the only thing that writes to
        # it) that the real ingest pipeline runs and this synthetic corpus
        # would otherwise skip, leaving `mesh_search` matching nothing.
        store.rebuild_fts()
    return db


def t_file_kinds_are_the_measured_four():
    """document, image, file, code -- and nothing else.

    Named explicitly rather than derived from a suffix or a name: M1's
    `reference` (180 of them) and M4's `archive_entry` (69) are about files
    without being files, and any rule that guessed would take them.
    """
    check("FILE_KINDS is exactly the four file-bearing kinds",
          gui.FILE_KINDS == frozenset({"file", "document", "image", "code"}),
          repr(sorted(gui.FILE_KINDS)))


def t_full_read_has_every_planted_file(m3_db):
    """Uncapped, nothing is lost to the per-model cap.

    Real-corpus measurement of this same property: `collect` caps per model
    BEFORE any filter runs, so at limit 2000, M3's 602 files lose 325 of
    themselves to its 6035 sections. That exact figure needs the real store
    to reproduce; the property -- an uncapped read returns every planted file
    and reports no truncation -- does not, so it runs here on a corpus of
    three.
    """
    payload = gui.graph_payload({"m3": m3_db})
    check("every planted file node reaches the payload",
          len(payload["nodes"]) == 3, "%d node(s)" % len(payload["nodes"]))
    check("an uncapped read truncates nothing",
          payload["truncated"] == [], repr(payload["truncated"]))


def t_capped_read_names_the_model_it_capped(m3_db):
    """The guard. A ceiling that does not announce itself is the defect.

    The synthetic corpus holds 3 files + 3 sections + 1 tag = 7 raw nodes;
    a limit below that forces the same cap `collect` applies on the real
    store, just at a size this file does not need a fixture library to reach.
    """
    payload = gui.graph_payload({"m3": m3_db}, limit_per_model=5)
    check("a capped read reports which model was cut",
          payload["truncated"] == ["m3"], repr(payload["truncated"]))


def t_payload_drops_non_file_kinds(m3_db):
    """The kind filter, exercised: the synthetic corpus has sections and a
    tag, so a payload containing only `file` proves something was removed,
    not merely that nothing else was ever there.
    """
    raw_nodes, _edges, _missing = gui.collect({"m3": m3_db}, gui.NO_LIMIT)
    raw_kinds = {n["kind"] for n in raw_nodes}
    payload = gui.graph_payload({"m3": m3_db})
    kinds = {n["kind"] for n in payload["nodes"]}
    check("the raw read actually contains non-file kinds to filter",
          bool(raw_kinds - gui.FILE_KINDS), "raw kinds=%s" % sorted(raw_kinds))
    check("no section, tag or wikilink node reaches the payload",
          kinds <= gui.FILE_KINDS, "kinds=%s" % sorted(kinds))


def t_isolated_computation(m3_db):
    """The linked pair is connected; the third file stands alone.

    `linked-a.md` WIKILINKS_TO `linked-b.md`, so both are linked; `isolated.md`
    has no edge to or from another file node (its only edge is CONTAINS into
    its own section, a non-file kind already gone by the time `isolated` is
    computed).
    """
    payload = gui.graph_payload({"m3": m3_db})
    isolated_paths = {n["path"] for n in payload["nodes"]
                       if n["key"] in set(payload["isolated"])}
    names = {os.path.basename(p) for p in isolated_paths}
    check("only the file with no file-to-file edge is isolated",
          names == {"isolated.md"}, "isolated=%s" % sorted(names))


def t_search_route_returns_hits(m3_db):
    """POST /search over a live socket, routed to `Server.mesh_search`.

    `mesh_search` needs no mesh store to answer (measured 2026-07-28), so
    this runs against the synthetic corpus with no `mesh_db` at all, always
    -- no SKIPPED degradation. "isolated" is the search term because it is
    text that is actually in the synthetic corpus ("wikilink" names a
    relation kind, not body text, and matches nothing); a check against a
    query with zero possible hits could never fail.
    """
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/search", {"query": "isolated", "limit": 5})
    check("POST /search returns ranked hits",
          out.get("status") in ("complete", "partial") and out.get("hits"),
          "status=%s hits=%d" % (out.get("status"), len(out.get("hits", []))))


def t_missing_model_is_reported_as_partial(m3_db):
    """The gate that must be able to say no.

    A model that is configured but absent must surface as `partial` with the
    model named. Swallowing it would make a half-answer indistinguishable from
    a whole one -- the cbm failure this package was built against.
    """
    with _running({"m3": m3_db, "m9": "/nonexistent/m9.db"}) as port:
        out = _post(port, "/search", {"query": "isolated", "limit": 5})
    check("a missing model makes the answer partial and names itself",
          out.get("status") == "partial"
          and "m9" in out.get("models_missing", []),
          "status=%s missing=%s" % (out.get("status"),
                                    out.get("models_missing")))


def t_query_route_refuses_unknown_model(m3_db):
    """POST /query for a model the server was never given.

    Measured against the real `Server.query` (2026-07-28): a model absent
    from `model_paths` is caught before any query runs, returning
    `status="error"` with the models the server does have named in the
    message -- not `"refused"`, which is what `query()` returns for a
    *configured* model whose language a request asks more of than it
    supports.
    """
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/query", {"model": "m9", "query": "NODES"})
    check("POST /query names the models it does have when refusing",
          out.get("status") == "error" and "m3" in out.get("error", ""),
          repr(out.get("error"))[:60])


def t_binds_loopback_only():
    """The address is not configurable, and that is the point."""
    import inspect
    src = inspect.getsource(gui.serve)
    params = list(inspect.signature(gui.serve).parameters)
    check("serve() hardcodes 127.0.0.1 and takes no host argument",
          "127.0.0.1" in src and "host" not in params, "params=%s" % params)


def t_graph_route_serves_the_payload(m3_db):
    """GET /graph over a real socket -- a live port, a real HTTP request, no
    mocking of `HTTPServer` or the handler. Compares the served body against
    `graph_payload`'s own return value field-for-field -- through the same
    JSON round-trip the wire imposes, so the tuple edges `graph_payload`
    returns compare equal to the lists JSON turns them into -- not just node
    and isolated counts, which would stay green even if `missing` or
    `truncated` silently dropped out before `json.dumps`. Runs against the
    synthetic store so it always executes; `t_graph_route_matches_real_corpus`
    below ties the same route to the real corpus's 602/315, and
    `t_graph_route_preserves_missing_and_truncated` is the one that forces
    those two honesty fields non-empty over the wire.
    """
    import json
    import urllib.request
    expected = json.loads(json.dumps(gui.graph_payload({"m3": m3_db})))
    with _running({"m3": m3_db}) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/graph" % port, timeout=10) as resp:
            body = json.loads(resp.read())
    check("GET /graph returns the payload over HTTP",
          body == expected,
          "%d node(s), %d isolated" % (len(body["nodes"]), len(body["isolated"])))


def t_graph_route_matches_real_corpus(real_db):
    """602 nodes, 315 isolated, served over the same socket path as above,
    with the same full-body equality as `t_graph_route_serves_the_payload`.

    Same SKIPPED pattern as `t_isolated_matches_md_gaps`: the figures are
    measured on the real store, so this degrades to a named SKIPPED line
    rather than vanishing when `~/.homegraph/real-m3.db` is absent.
    """
    if real_db is None:
        check("GET /graph serves the real corpus (602 nodes, 315 isolated)",
              True, "SKIPPED -- ~/.homegraph/real-m3.db not present on this machine")
        return
    import json
    import urllib.request
    expected = json.loads(json.dumps(gui.graph_payload({"m3": real_db})))
    with _running({"m3": real_db}) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/graph" % port, timeout=30) as resp:
            body = json.loads(resp.read())
    check("GET /graph serves the real corpus (602 nodes, 315 isolated)",
          body == expected and len(body["nodes"]) == 602 and len(body["isolated"]) == 315,
          "%d node(s), %d isolated" % (len(body["nodes"]), len(body["isolated"])))


def t_graph_route_preserves_missing_and_truncated(m3_db):
    """`missing` and `truncated` are the fields the design names as the ones
    the whole GUI's honesty rests on, and both checks above have them empty
    on the synthetic corpus -- an equality between two empty lists proves
    nothing. Forces both non-empty over the same real socket: a model with
    no store on disk (`missing`) and a cap sized below the synthetic corpus's
    7 raw nodes, same as `t_capped_read_names_the_model_it_capped` (`truncated`).
    """
    import json
    import urllib.request
    model_paths = {"m3": m3_db, "ghost": "/no/such/path.db"}
    expected = json.loads(json.dumps(
        gui.graph_payload(model_paths, limit_per_model=5)))
    with _running(model_paths, limit_per_model=5) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/graph" % port, timeout=10) as resp:
            body = json.loads(resp.read())
    check("GET /graph carries missing and truncated over the wire, not just nodes",
          bool(expected["missing"]) and bool(expected["truncated"]) and body == expected,
          "missing=%r truncated=%r" % (body.get("missing"), body.get("truncated")))


def t_isolated_matches_md_gaps(m3_db):
    """The cross-check against the real corpus. 315 of 602, if it is here.

    Kept as an additional check on the real store rather than folded into the
    structural check above: the synthetic corpus proves the isolated
    computation is correct in shape, but only the real corpus proves it
    agrees with `isolated_notes()` at the scale the defect was measured at.
    """
    if m3_db is None:
        check("isolated set equals isolated_notes()", True,
              "SKIPPED -- ~/.homegraph/real-m3.db not present on this machine")
        return
    with Store(m3_db) as store:
        gold_paths, gold_total = isolated_notes(store)
    payload = gui.graph_payload({"m3": m3_db})
    got = {n["path"] for n in payload["nodes"]
           if n["key"] in set(payload["isolated"])}
    check("isolated set equals isolated_notes()",
          got == set(gold_paths),
          "gui=%d gold=%d of %d" % (len(got), len(gold_paths), gold_total))


def real_m3():
    """Path to the real M3 store, or None.

    Deliberately decoupled from the mesh: no check in this file calls
    `mesh_path` or `mesh_neighbors`, so a missing `real-mesh.db` must not
    disable the checks that only ever touch M3. Tasks 3 and 4 add their own
    mesh requirement where they add the checks that actually need one.
    """
    m3 = os.path.expanduser("~/.homegraph/real-m3.db")
    return m3 if os.path.exists(m3) else None


def main():
    t_file_kinds_are_the_measured_four()
    t_binds_loopback_only()

    with tempfile.TemporaryDirectory(prefix="gui-cp-") as tmp:
        synthetic_db = _build_synthetic_m3(tmp)
        t_full_read_has_every_planted_file(synthetic_db)
        t_capped_read_names_the_model_it_capped(synthetic_db)
        t_payload_drops_non_file_kinds(synthetic_db)
        t_isolated_computation(synthetic_db)
        t_graph_route_serves_the_payload(synthetic_db)
        t_graph_route_preserves_missing_and_truncated(synthetic_db)
        t_search_route_returns_hits(synthetic_db)
        t_missing_model_is_reported_as_partial(synthetic_db)
        t_query_route_refuses_unknown_model(synthetic_db)

    t_isolated_matches_md_gaps(real_m3())
    t_graph_route_matches_real_corpus(real_m3())

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
