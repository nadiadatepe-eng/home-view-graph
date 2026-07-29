#!/usr/bin/env python3
"""CP-GUI -- the GUI's payload builders and HTTP routes.

Every check here is about what Python decides, because the page decides
nothing. The structural checks run against a synthetic M3 store built in a
tempdir, the way every other checkpoint builds its own corpus, so this file
is not the one checkpoint in `tests/` that silently does nothing on a machine
without `~/.homegraph/real-*.db`.

FOUR checks still need the real corpus, not one: `t_isolated_matches_md_gaps`,
`t_graph_route_matches_real_corpus`, `t_band_matches_the_real_corpus` and
`t_path_route_answers_over_the_real_corpus`. Each ties this surface to an
already-measured
fact -- that the set `/graph` calls isolated is the set `isolated_notes`
reports, so the GUI and `md gaps` cannot drift apart without anyone saying so
-- and each prints a named SKIPPED line rather than vanishing when the store
is absent. Eleven more need `node`, in `t_page_behaviour`. A green run on a
machine with neither is a green run in which fifteen checks asserted nothing,
which is why they are named here rather than counted.

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
from homegraph.mesh import Mesh                                    # noqa: E402
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


def _post(port, path, body=None, raw=None):
    """POST to a live handler; return (status_code, decoded JSON body).

    `urlopen` raises `HTTPError` on a 4xx/5xx response, which would make the
    body of an error response unreachable from a check -- caught here so a
    400 is a return value, not an exception to route around. `raw`, given as
    bytes, is sent as-is instead of JSON-encoding `body`: the only way to
    reach `do_POST`'s malformed-JSON branch, since no `body` dict this
    helper could build would ever fail `json.dumps`.
    """
    import json
    import urllib.error
    import urllib.request

    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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


def _build_synthetic_m3_for_path(root):
    """A bigger M3 store, for the `/path` checks: a linked pair, one file
    truly isolated (no edge in either direction), a note that names an image
    file (for `_build_synthetic_m2_for_path`'s FIGURE_FOR edge below), and
    enough filler that a `/path` call actually has more than `DEFAULT_CAP`
    destinations to slice.

    Not a second version of `_build_synthetic_m3`'s tiny corpus: that one's
    three files are enough for every check above, but `t_path_cap_is_reported`
    needs `DEFAULT_CAP + 5` node keys to exist before it can even ask whether
    the 21st onward got dropped -- with three files, a capped response and an
    uncapped one would look identical, and the check could never go red.
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
        # WIKILINKS_TO (linked-a/linked-b above) lives inside m3's own store;
        # `Mesh.build_edges` mirrors NODES from each model but computes only
        # NEW cross-model edges (FIGURE_FOR, MENTIONS_FILE, CITES_CODE,
        # TEMPORAL_COHORT) -- "a cohort inside one model is not a mesh fact",
        # in that method's own docstring. So a mesh built over m3 alone has
        # zero edges, and `/neighbors` needs a note that names an image file,
        # matched against the m2 store `_build_synthetic_m2_for_path` builds.
        write("figures.md", "# Figures\n\nSee synthetic-figure.png for detail.\n"),
        # An apostrophe in a filename, planted deliberately. The page
        # interpolates node keys into single-quoted attributes, and until this
        # fixture existed nothing in the suite could produce the character that
        # closes one early -- so `data-key='m3::/tmp/…/it'` truncated at the
        # apostrophe, `select()` posted a key no node has, and `/path`
        # answered "no bridges" about a node that was never asked about.
        write("it's-a-note.md", "# Apostrophe\n\nA filename with a quote.\n"),
    ]
    paths += [write("filler-%03d.md" % i, "# Filler %d\n\nBody text %d.\n" % (i, i))
              for i in range(gui.DEFAULT_CAP + 10)]
    db = os.path.join(root, "synthetic-m3-path.db")
    with Store(db, model="m3") as store:
        build(store, paths, date(2026, 7, 22))
        store.rebuild_fts()
    return db


def _build_synthetic_m2_for_path(root):
    """One image, matched by `figures.md`'s body above -- the FIGURE_FOR
    edge is the cross-model fact `/neighbors` and `/path` need to find
    anything at all (see the note in `_build_synthetic_m3_for_path`).
    """
    from homegraph.models import m2_build

    imgdir = os.path.join(root, "img")
    os.makedirs(imgdir, exist_ok=True)
    imgpath = os.path.join(imgdir, "synthetic-figure.png")
    with open(imgpath, "wb") as fh:
        fh.write(b"\x89PNG" + b"\0" * 32)
    db = os.path.join(root, "synthetic-m2-path.db")
    with Store(db, model="m2") as store:
        m2_build.build(store, [imgpath], date(2026, 7, 22))
        store.rebuild_fts()
    return db


def _build_synthetic_mesh(root, model_paths):
    """A mesh over `model_paths`, built the way `tests/test_cp6.py` builds
    one: mirror every model node into `mesh.db`, then compute cross-model
    edges over the mirror.

    Needed because `Mesh._read_mesh` refuses outright without a mesh --
    deliberately, so a read against an absent mesh cannot leave an empty
    database behind and answer `count: 0` -- and `mesh_path` / `mesh_neighbors`
    both go through it. Built fresh in a tempdir rather than reused from a
    machine-local corpus, so the `/path` and `/neighbors` checks always run.
    """
    meshdb = os.path.join(root, "synthetic-mesh.db")
    with Mesh(model_paths, mesh_db=meshdb) as mesh:
        mesh.build_edges(date(2026, 7, 22).isoformat())
    return meshdb


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
    # `counts` is the whole of h1's summary -- `renderSummary` prints one
    # `<dd>N filer</dd>` per model straight out of it -- and until this check
    # nothing asserted it at all. Both conjuncts: the literal ties it to the
    # three files the fixture plants, and the sum ties it to the node list it
    # is supposed to be counting, so a per-model count that drifts from the
    # nodes actually shipped cannot pass by matching a stale literal.
    check("counts is the per-model node count h1's summary prints",
          payload["counts"] == {"m3": 3}
          and sum(payload["counts"].values()) == len(payload["nodes"]),
          repr(payload["counts"]))


def t_kind_counts_are_the_other_half_of_the_summary(m3_db):
    """The spec's h1 summary is "per file type and partition"; `counts` was
    only the partition half.

    Counted in Python for the same reason `counts` is: a per-kind total the
    browser derived would be a second measurement of the same nodes. The sum
    conjunct is what keeps it tied to the node list -- a per-kind count that
    drifted from the nodes actually shipped cannot pass by matching a stale
    literal.
    """
    payload = gui.graph_payload({"m3": m3_db})
    check("kind_counts is the per-kind node count, and kinds names them",
          payload["kind_counts"] == {"file": 3}
          and payload["kinds"] == ["file"]
          and sum(payload["kind_counts"].values()) == len(payload["nodes"]),
          "%r %r" % (payload["kinds"], payload["kind_counts"]))


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
        _, out = _post(port, "/search", {"query": "isolated", "limit": 5})
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
        _, out = _post(port, "/search", {"query": "isolated", "limit": 5})
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
        _, out = _post(port, "/query", {"model": "m9", "query": "NODES"})
    check("POST /query names the models it does have when refusing",
          out.get("status") == "error" and "m3" in out.get("error", ""),
          repr(out.get("error"))[:60])


def t_search_route_matches_server_verbatim(m3_db):
    """The verbatim claim, checked on all five keys, not the three the
    checks above happen to read.

    `mesh_search` returns `status`, `models_queried`, `models_missing`,
    `warnings` and `hits`. The checks above only ever look at three of them
    -- a transport that quietly dropped `warnings` and `models_queried`
    before sending the response would still pass every one. Compares the
    POST body against `Server.mesh_search` called directly, through the same
    JSON round-trip the wire imposes (`t_graph_route_serves_the_payload`'s
    pattern). Runs the missing-model case so `warnings` and `models_missing`
    are non-empty at the moment of comparison -- two empty lists would
    compare equal without proving the field survived the trip.
    """
    import json

    from homegraph.mcp_server import Server

    model_paths = {"m3": m3_db, "m9": "/nonexistent/m9.db"}
    expected = json.loads(json.dumps(
        Server(model_paths).mesh_search(query="isolated", limit=5)))
    with _running(model_paths) as port:
        _, out = _post(port, "/search", {"query": "isolated", "limit": 5})
    check("POST /search returns mesh_search's response verbatim, all five keys",
          out == expected and bool(expected["warnings"])
          and bool(expected["models_missing"]),
          "warnings=%r missing=%r" % (out.get("warnings"),
                                      out.get("models_missing")))


def t_search_route_rejects_malformed_json(m3_db):
    """Bytes that are not JSON at all -- the 400 body, not just the code.

    `_post`'s `raw` bytes bypass `json.dumps` entirely; no `body` dict this
    file could construct would ever reach `do_POST`'s `except ValueError`
    branch on its own.
    """
    with _running({"m3": m3_db}) as port:
        status, out = _post(port, "/search", raw=b"{not json")
    check("malformed JSON is a 400 with an error, not a tool result",
          status == 400 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


def t_search_route_rejects_bad_argument_name(m3_db):
    """A call `mesh_search`'s own signature refuses -- the case Important 1
    exists to keep separate from a real failure inside the tool. Binding is
    tried before the tool runs, so this is a 400 naming the bad call, not a
    500 or a dropped connection.
    """
    with _running({"m3": m3_db}) as port:
        status, out = _post(port, "/search", {"nonexistent_kwarg": 1})
    check("an unknown argument name is a 400 with an error, not a tool result",
          status == 400 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


def t_path_reports_unreachable_rather_than_omitting(m3_db, mesh_db):
    """Absence of a path is an answer, not a shorter list.

    A hit with no bridge must come back named in `unreachable`. Dropping it
    would let the schematic draw four of five hits and look complete.
    """
    payload = gui.graph_payload({"m3": m3_db})
    isolated = set(payload["isolated"])
    linked = [n["key"] for n in payload["nodes"] if n["key"] not in isolated][:2]
    lonely = payload["isolated"][:1]
    if len(linked) < 2 or not lonely:
        check("corpus has both a linked pair and an isolated note", False,
              "linked=%d isolated=%d" % (len(linked), len(isolated)))
        return
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        _, out = _post(port, "/path",
                       {"src": linked[0], "dsts": [linked[1]] + lonely})
    total = len(out.get("bridges", [])) + len(out.get("unreachable", []))
    check("an unreachable hit is named, not dropped",
          lonely[0] in out.get("unreachable", []) and total == 2,
          "unreachable=%s of %d dst(s)" % (len(out.get("unreachable", [])),
                                           total))


def t_path_finds_a_real_bridge(m3_db, m2_db, mesh_db):
    """The found case, not only the absent one.

    `t_path_reports_unreachable_rather_than_omitting` above passes even if
    `bridges()` finds nothing at all: its `linked-a.md`/`linked-b.md` pair is
    an in-model WIKILINKS_TO, invisible to a mesh that only computes
    cross-model edges (see `_build_synthetic_m3_for_path`), so both of its
    destinations land in `unreachable` and the check cannot distinguish that
    from a `bridges()` that always answers "no path". This one uses the one
    edge in this fixture the mesh actually has -- `figures.md`'s FIGURE_FOR
    to the image it names -- and requires it to come back as a bridge with
    an actual path, not merely absent from `unreachable`.
    """
    m3_payload = gui.graph_payload({"m3": m3_db})
    figure_note = next((n["key"] for n in m3_payload["nodes"]
                        if n["path"].endswith("figures.md")), None)
    m2_payload = gui.graph_payload({"m2": m2_db})
    image_key = m2_payload["nodes"][0]["key"] if m2_payload["nodes"] else None
    if figure_note is None or image_key is None:
        check("corpus has a note+image pair joined by a real mesh edge",
              False, "figure_note=%r image_key=%r" % (figure_note, image_key))
        return
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        _, out = _post(port, "/path", {"src": figure_note, "dsts": [image_key]})
    bridge = out.get("bridges", [{}])[0] if out.get("bridges") else {}
    check("a real cross-model edge comes back as a bridge with a path",
          bridge.get("dst") == image_key and bool(bridge.get("path"))
          and not out.get("unreachable"),
          "bridges=%r unreachable=%r" % (out.get("bridges"),
                                         out.get("unreachable")))


def t_path_cap_is_reported(m3_db, mesh_db):
    """A truncated view must say it was truncated."""
    payload = gui.graph_payload({"m3": m3_db})
    keys = [n["key"] for n in payload["nodes"]][:gui.DEFAULT_CAP + 5]
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        _, out = _post(port, "/path", {"src": keys[0], "dsts": keys[1:]})
    total = len(out.get("bridges", [])) + len(out.get("unreachable", []))
    check("over the cap, the response says truncated and how many it took",
          out.get("truncated") is True and total == gui.DEFAULT_CAP,
          "cap=%s took=%d truncated=%s" % (out.get("cap"), total,
                                           out.get("truncated")))


def t_path_route_rejects_missing_src(m3_db):
    """A missing `src` is the caller's mistake, not the answer layer's.

    Before this fix round, `/path` returned before the same
    `inspect.signature(...).bind` step every other route goes through, so
    `bridges` read `args["src"]` directly and a missing key surfaced as
    `KeyError` -- reported as a 500, the exact blame inversion `a9b8022`
    removed for `/search` and `/query` one route earlier. No `mesh_db`
    needed: binding fails before `bridges`'s body, let alone `mesh_path`,
    ever runs.
    """
    with _running({"m3": m3_db}) as port:
        status, out = _post(port, "/path", {"dsts": ["m3::/x.md"]})
    check("a /path call missing src is a 400, not a 500",
          status == 400 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


def t_path_route_rejects_malformed_max_depth(m3_db):
    """A `max_depth` that is not an integer is also the caller's mistake,
    not the answer layer's.

    `int("deep")` raises `ValueError` inside `bridges`, past the binding
    step (`max_depth` is a real, bindable keyword) -- caught there and
    re-raised as `gui.BadArgument`, the exception `do_POST`'s `_run` reads
    as a 400. Not `TypeError`: round 2 found that catching `TypeError` here
    made a *real* `TypeError` from inside a route body -- a genuine bug --
    also read as "bad arguments", reopening `a9b8022` for all four routes
    (see `t_internal_type_error_is_500_not_400` below, which proves the
    distinction the other way). No `mesh_db` needed: the conversion runs
    before the `dsts` loop ever reaches `mesh_path`.
    """
    with _running({"m3": m3_db}) as port:
        status, out = _post(port, "/path",
                            {"src": "m3::/x.md", "max_depth": "deep"})
    check("a /path call with a non-integer max_depth is a 400, not a 500",
          status == 400 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


def t_path_route_rejects_non_list_dsts(m3_db):
    """A `dsts` that is not a list is the caller's mistake too, checked
    explicitly rather than left to whatever `list(...)` happens to do with
    it.

    `dsts: 5` used to be a 400 only by accident, through the same
    overloaded `except TypeError` `t_path_route_rejects_malformed_max_depth`
    documents being removed -- without an explicit check it would have
    become a 500 instead. `dsts: "a string"` was never an error at all:
    `list("abc")` silently turns one string into three one-character
    destinations. Both are refused now, by an `isinstance` check ahead of
    the `mesh_path` loop.
    """
    with _running({"m3": m3_db}) as port:
        status, out = _post(port, "/path", {"src": "m3::/x.md", "dsts": 5})
    check("a /path call with a non-list dsts is a 400, not a 500",
          status == 400 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


def t_internal_type_error_is_500_not_400(m3_db):
    """The round 2 finding, proved directly: a `TypeError` raised from
    *inside* a route body -- past `bind()`, so arguments were already valid
    and this is a bug in the answer layer, not a malformed request -- must
    be a 500 with a traceback printed to stderr, not a 400.

    Without this check, the suite could not fail on `_run` catching
    `TypeError` instead of `gui.BadArgument`: every check above sends a
    request that is either entirely valid or invalid in a way `bridges`
    itself turns into `BadArgument`, so 26/26 was not evidence against the
    overload round 2 found. Monkeypatches `Server.mesh_search` on the class
    (restored in `finally`, so no other check in this file is affected) to
    raise `TypeError` unconditionally, then sends a request that binds fine
    (`query` is present) so the only way to reach 400 would be the bug this
    check exists to catch. `contextlib.redirect_stderr` captures the
    handler thread's `traceback.print_exc()` output -- `sys.stderr` is a
    single process-global object, so this works across threads, and
    `_post`'s blocking `urlopen` guarantees the print already happened by
    the time the response returns.
    """
    import contextlib
    import io

    from homegraph.mcp_server import Server

    def _boom(self, query, limit=20, as_of=None, include_all=False):
        raise TypeError("simulated internal bug, not a bad request")

    original = Server.mesh_search
    Server.mesh_search = _boom
    captured = io.StringIO()
    try:
        with _running({"m3": m3_db}) as port:
            with contextlib.redirect_stderr(captured):
                status, out = _post(port, "/search", {"query": "isolated"})
    finally:
        Server.mesh_search = original
    check("a TypeError from inside a route body is a 500 with a traceback, "
          "not a 400",
          status == 500 and "TypeError" in out.get("error", "")
          and "Traceback" in captured.getvalue(),
          "status_code=%d body=%r traceback_printed=%s"
          % (status, out, "Traceback" in captured.getvalue()))


def t_neighbors_route_returns_edges(m3_db, mesh_db):
    """POST /neighbors, routed since Task 3 but never exercised until now.

    Needs a mesh for the same reason `/path` does: `mesh_neighbors` ->
    `Mesh.neighbours` -> `Mesh._read_mesh`, which refuses without one. And it
    needs a genuine CROSS-model edge, not `linked-a.md`'s in-model
    WIKILINKS_TO to `linked-b.md`: `mesh.db` mirrors nodes but computes only
    new cross-model edges, so a mesh built over m3 alone never sees that
    wikilink at all (see `_build_synthetic_m3_for_path`). `figures.md` names
    an image `_build_synthetic_m2_for_path` actually built, which
    `Mesh.build_edges`'s FIGURE_FOR pass turns into a real mesh edge -- a
    check against a node guaranteed to have none could never fail.
    """
    payload = gui.graph_payload({"m3": m3_db})
    figure_note = next((n["key"] for n in payload["nodes"]
                        if n["path"].endswith("figures.md")), None)
    if figure_note is None:
        check("corpus has a note that mentions an image, to probe /neighbors with",
              False, "no figures.md in corpus")
        return
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        status, out = _post(port, "/neighbors", {"node": figure_note})
    check("POST /neighbors returns edges for a node that has them",
          status == 200 and out.get("count", 0) > 0,
          "status_code=%d count=%s" % (status, out.get("count")))


def t_neighbourhood_splits_the_edges(m3_db, m2_db, mesh_db):
    """The spec's v2 fallback, in Python: incoming and outgoing, over the real
    mesh.

    Two separate checks over one call, because they are two separate
    properties and a compound name is a gate that cannot say which of them
    broke -- the defect this branch measured in its own harness on 07-29.
    Ordering is NOT checked here; see
    `t_neighbourhood_sorts_an_input_that_arrives_unsorted` for why this
    corpus cannot check it.

    The corpus is the note+image pair `t_path_finds_a_real_bridge` uses: the
    note has an outgoing FIGURE_FOR-family edge to the image, and the image
    has the same edge incoming, so both sides are non-empty across the two
    calls and neither check is comparing empty lists.
    """
    payload = gui.graph_payload({"m3": m3_db, "m2": m2_db}, mesh_db=mesh_db)
    note = next((n["key"] for n in payload["nodes"]
                 if n["path"].endswith("figures.md")), None)
    image = next((n["key"] for n in payload["nodes"]
                  if n["key"].startswith("m2::")), None)
    if note is None or image is None:
        check("corpus has the note+image pair to probe the neighbourhood with",
              False, "note=%r image=%r" % (note, image))
        return
    with _running({"m3": m3_db, "m2": m2_db}, mesh_db=mesh_db) as port:
        _s, from_note = _post(port, "/neighbors", {"node": note})
        _s, from_image = _post(port, "/neighbors", {"node": image})

    # The verbatim half. `neighbourhood` adds two keys; it must not lose or
    # rewrite `mesh_neighbors`' own five, which are the answer's account of
    # what it found and how sure it is.
    check("POST /neighbors keeps mesh_neighbors' own keys and adds two",
          all(k in from_note for k in ("node", "depth", "count", "status",
                                       "warnings", "edges", "incoming",
                                       "outgoing"))
          and from_note["count"] == len(from_note["edges"]),
          "keys=%s count=%s" % (sorted(from_note), from_note.get("count")))

    # Direction, from both ends of the same edge: the note sees it outgoing,
    # the image sees it incoming, and the OTHER side of each is empty.
    #
    # The empty side is asserted by length, not by "the far key is absent".
    # The first version of this check asked only that `image` not appear in
    # the note's `incoming`, and a mutation that dropped the direction test
    # entirely SURVIVED it: with `if e[near] == node` gone, `incoming` is
    # built with `far="src"`, so it filled with the note's OWN key -- the
    # key the check was looking for was never going to be there either way.
    check("the neighbourhood splits edges by direction, from both ends",
          [e["key"] for e in from_note["outgoing"]] == [image]
          and from_note["incoming"] == []
          and [e["key"] for e in from_image["incoming"]] == [note]
          and from_image["outgoing"] == [],
          "note out=%d in=%d, image out=%d in=%d"
          % (len(from_note["outgoing"]), len(from_note["incoming"]),
             len(from_image["outgoing"]), len(from_image["incoming"])))


def t_neighbourhood_sorts_an_input_that_arrives_unsorted():
    """Ordering, driven by a fake server rather than by the real mesh.

    The real-mesh check above cannot test this and must not pretend to. The
    synthetic corpus yields ONE edge per side, and a one-element list is
    sorted in every order: the first version of this check asked
    `sorted(side) == side` over those lists, and the mutation that replaced
    the sort key with a constant SURVIVED it -- an `all()` over a list that
    cannot disagree with itself, the same family this package has now found
    in its own gates seven times.

    Growing the fixture would not fix it either, because the order the mesh
    happens to return two edges in may already be the sorted one, and a gate
    that passes or fails on that accident is worse than none.

    So the input is constructed to arrive REVERSED, which is the only way the
    property can be observed at all: three edges per side, deliberately out of
    order, and the answer must come back sorted by key then relation. The
    fake is the whole server surface `neighbourhood` uses -- one method -- so
    nothing about the real one is being mocked away except the corpus.
    """
    class _Reversed:
        def mesh_neighbors(self, node, depth=1):
            def e(src, dst, rel):
                return {"src": src, "rel": rel, "dst": dst,
                        "method": "exact", "confidence": 1.0}
            return {"node": node, "depth": depth, "count": 6,
                    "status": "complete", "warnings": [],
                    # Reverse-sorted on both sides, and the two `/c.md` edges
                    # differ only by relation so the tie-breaker is exercised
                    # too -- `WIKILINKS_TO` must follow `CITES_CODE`.
                    "edges": [e("m3::/c.md", node, "WIKILINKS_TO"),
                              e("m3::/c.md", node, "CITES_CODE"),
                              e("m3::/a.md", node, "MENTIONS"),
                              e(node, "m3::/z.md", "WIKILINKS_TO"),
                              e(node, "m3::/b.md", "MENTIONS")]}

    out = gui.neighbourhood(_Reversed(), "m3::/mid.md")
    check("the neighbourhood sorts an input that arrived unsorted",
          [(e["key"], e["rel"]) for e in out["incoming"]]
          == [("m3::/a.md", "MENTIONS"), ("m3::/c.md", "CITES_CODE"),
              ("m3::/c.md", "WIKILINKS_TO")]
          and [e["key"] for e in out["outgoing"]] == ["m3::/b.md", "m3::/z.md"],
          "in=%s out=%s" % ([e["key"] for e in out["incoming"]],
                            [e["key"] for e in out["outgoing"]]))


def t_neighbourhood_marks_derived_by_confidence(m3_db, m2_db, mesh_db):
    """`derived` is `confidence < 1.0`, the same rule `provenance_note` uses.

    The spec says `method != "exact"`. The code's own honesty rule is the
    confidence one, and `provenance_note`'s docstring names a second copy of
    that question as how one copy ends up always answering no. They agree on
    every method in `EDGE_METHODS` today (`exact` 1.0, `path_prefix` 0.7,
    `basename` 0.6, `mention` 0.5) and would stop agreeing the moment a
    stated-but-uncertain method is added.

    Checks the flag against the confidence of the SAME edge rather than
    against a literal, so it holds whichever methods the fixture's mesh
    produces -- and asserts the fixture actually contains a derived edge, so
    the equality is not being satisfied by an empty list.
    """
    payload = gui.graph_payload({"m3": m3_db, "m2": m2_db}, mesh_db=mesh_db)
    note = next((n["key"] for n in payload["nodes"]
                 if n["path"].endswith("figures.md")), None)
    with _running({"m3": m3_db, "m2": m2_db}, mesh_db=mesh_db) as port:
        _s, out = _post(port, "/neighbors", {"node": note})
    sides = out["incoming"] + out["outgoing"]
    check("a neighbour edge is derived exactly when its confidence is below 1",
          bool(sides)
          and all(e["derived"] == (e["confidence"] is not None
                                   and e["confidence"] < 1.0) for e in sides)
          and any(e["derived"] for e in sides),
          "%d edge(s), confidences=%s"
          % (len(sides), sorted({e["confidence"] for e in sides})))


def t_neighbors_route_rejects_malformed_depth(m3_db, mesh_db):
    """A `depth` that binds but will not parse is a 400, not a 500.

    Same shape as `/path`'s `max_depth`, and for the same reason: past the
    pre-call binding check a `TypeError` means a bug in the answer layer, so
    a caller's malformed value has to raise `BadArgument` instead of being
    left to blow up inside `mesh_neighbors`.
    """
    payload = gui.graph_payload({"m3": m3_db})
    node = payload["nodes"][0]["key"]
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        status, out = _post(port, "/neighbors",
                            {"node": node, "depth": "dypt"})
    check("a /neighbors call with a non-integer depth is a 400, not a 500",
          status == 400 and "depth" in out.get("error", ""),
          "status_code=%d body=%r" % (status, out))


def t_neighbors_route_without_mesh_answers_rather_than_resets(m3_db):
    """The Task 3 review's warning, acted on: `mesh_neighbors` raises
    `ModelUnavailable` -- not a `TypeError` -- when the server has no mesh
    configured, and that exception used to have nothing around it in
    `do_POST`. It escaped the handler entirely, past
    `BaseHTTPRequestHandler`'s own handling, so the client saw the
    connection reset with no body at all. Runs with no `mesh_db` on
    purpose -- the one `/neighbors` check in this file that deliberately
    withholds the mesh -- to prove the exception now comes back as a 500
    with a body instead of a dropped socket, which `urlopen` would raise
    through rather than let this check observe.
    """
    payload = gui.graph_payload({"m3": m3_db})
    node = payload["nodes"][0]["key"]
    with _running({"m3": m3_db}) as port:                 # no mesh_db
        status, out = _post(port, "/neighbors", {"node": node})
    check("a route that raises without a mesh answers instead of resetting",
          status == 500 and "error" in out and "status" not in out,
          "status_code=%d body=%r" % (status, out))


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


def t_graph_route_answers_when_the_payload_will_not_serialise(m3_db):
    """A GET that fails is a status and a body, not a reset connection.

    Until this check GET had no error path at all: `do_GET` called `_send`
    with nothing around it, so anything raising while answering escaped past
    `BaseHTTPRequestHandler` and the client saw `RemoteDisconnected` -- no
    status, no body -- while the page's `boot()` guard claimed to handle
    exactly that case. `_run` had removed this for POST months earlier.

    The failure is induced the way it would really arrive: a payload holding
    a value `json.dumps` refuses. That is not hypothetical for a prebuilt
    payload -- `graph_payload` builds sets internally (`linked`, the isolated
    computation) and one of them reaching the returned dict is a one-line
    mistake. `_send` serialises before it writes a byte, so the 500 is
    reachable rather than a half-written response.
    """
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import HTTPServer

    from homegraph.mcp_server import Server

    bad = {"nodes": {"a set json cannot encode"}}
    httpd = HTTPServer(("127.0.0.1", 0),
                       gui.build_handler(Server({"m3": m3_db}), bad))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    status, body = None, None
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/graph" % httpd.server_address[1],
                timeout=10) as resp:
            status, body = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read())
    except Exception as exc:                                    # noqa: BLE001
        # The pre-fix behaviour, kept reachable rather than raised: the
        # connection resets and there is nothing to read. Reported as the
        # failure it is, not as an error in this file.
        body = {"error": "no response at all: %s" % type(exc).__name__}
    finally:
        httpd.shutdown()
        httpd.server_close()
    check("GET /graph that cannot be serialised is a 500 with a body",
          status == 500 and isinstance(body, dict) and "TypeError" in body.get("error", ""),
          "status=%r body=%r" % (status, body))


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


def t_positions_are_deterministic(m3_db):
    """The same corpus draws the same picture twice.

    The reason the layout is computed in Python at all: `visualize._layout` is
    seeded (20260722) and reads no clock, so this week's screenshot can be laid
    beside last week's. A layout re-run in the browser would lose that, and so
    would one that reached for `random.random()` here -- which is exactly what
    would turn this check red. Compared as an ordered list of pairs rather than
    as a set, so a reordering of the nodes fails too: the page indexes edges by
    position in `nodes`, and a stable set of coordinates attached to shuffled
    nodes draws different lines.
    """
    first = [(n["key"], n["x"], n["y"]) for n in gui.graph_payload({"m3": m3_db})["nodes"]]
    second = [(n["key"], n["x"], n["y"]) for n in gui.graph_payload({"m3": m3_db})["nodes"]]
    spread = len({(x, y) for _, x, y in first})
    check("two builds of the same corpus place every node identically",
          first == second and spread == len(first),
          "%d node(s), %d distinct position(s)" % (len(first), spread))


# Measured on this fixture 2026-07-28 with `_layout(seed=20260722,
# iterations=180, width=1600.0)` and `BAND_GAP = 140.0`. Stable across
# tempdirs: the node order is `ORDER BY node_key`, and all three files share
# one prefix, so the machine's temporary directory does not reach the layout.
_PINNED = [("isolated.md", -564.5, 620.9),      # banded, x0 = the cloud's own
           ("linked-a.md", -564.5, -455.6),
           ("linked-b.md", 63.5, 480.9)]
_PINNED_DIVIDER = 550.9


def t_positions_match_their_measured_values(m3_db):
    """The literal, pinned -- the half of determinism the comparison above
    cannot see.

    `t_positions_are_deterministic` compares two builds of the SAME version, so
    it is silent about a behavioural change to the layout: retune
    `visualize._layout`'s `seed`, `iterations` or `width`, or `gui.BAND_GAP`,
    and every node moves while both builds still agree and all other checks
    stay green. `gui.py` calls `_layout` across a module boundary and `_layout`
    is private, so nothing else in the tree would report it either. A rename or
    an arity change is already loud; this is the quiet one.

    Re-blessing these numbers on a deliberate retune is the point of them, not
    a nuisance: the diff then says the picture changed, which is exactly what
    happened.
    """
    payload = gui.graph_payload({"m3": m3_db})
    got = [(os.path.basename(n["path"]), n["x"], n["y"]) for n in payload["nodes"]]
    check("the layout still places the fixture where it was measured",
          got == _PINNED and payload["band_divider"] == _PINNED_DIVIDER,
          "got=%s divider=%s" % (got, payload["band_divider"]))


def t_isolated_nodes_are_banded_not_simulated(m3_db):
    """An isolated node has no information in its position, so it does not get
    one from the simulation.

    The force layout runs over the connected subgraph only; the rest land in a
    sorted band beneath it. The check is positional and can go red both ways:
    an implementation that fed every node to `_layout` would scatter
    `isolated.md` among the connected pair rather than below it, and one that
    banded the connected nodes too would collapse the pair onto the same row.
    The synthetic corpus is built for this -- `linked-a.md` WIKILINKS_TO
    `linked-b.md`, and `isolated.md` has no file-to-file edge at all.
    """
    payload = gui.graph_payload({"m3": m3_db})
    isolated = set(payload["isolated"])
    band_y = [n["y"] for n in payload["nodes"] if n["key"] in isolated]
    linked_y = [n["y"] for n in payload["nodes"] if n["key"] not in isolated]
    if not band_y or not linked_y:
        check("the band sits below every connected node", False,
              "band=%d linked=%d -- fixture has no contrast to measure"
              % (len(band_y), len(linked_y)))
        return
    check("the band sits below every connected node",
          min(band_y) > max(linked_y),
          "band from %.1f, cloud ends at %.1f" % (min(band_y), max(linked_y)))


def t_band_states_its_count_and_share(m3_db):
    """The caption is a measurement, and it is measured in Python.

    `isolated_count` and `isolated_share` travel in the payload so the browser
    never divides two numbers and calls the result a fact. One of three files
    is isolated here, so the share is 33.3 -- neither 0 nor 100, which a
    fixture with nothing isolated (or nothing linked) could not distinguish
    from a field that was never filled in.
    """
    payload = gui.graph_payload({"m3": m3_db})
    expected = round(100.0 * len(payload["isolated"]) / len(payload["nodes"]), 1)
    check("the payload carries the band's own count and share",
          payload["isolated_count"] == len(payload["isolated"])
          and payload["isolated_share"] == expected
          and 0.0 < payload["isolated_share"] < 100.0,
          "%d of %d, %.1f %%" % (payload["isolated_count"],
                                 len(payload["nodes"]),
                                 payload["isolated_share"]))


def t_band_matches_the_real_corpus(real_db):
    """315 of 602, 52.3 % -- the figure the design quotes, from the payload.

    Same SKIPPED pattern as the other real-corpus checks: measured on the real
    store, so it names itself rather than vanishing when the store is absent.
    """
    if real_db is None:
        check("the real corpus's band is 315 of 602 (52.3 %)", True,
              "SKIPPED -- ~/.homegraph/real-m3.db not present on this machine")
        return
    payload = gui.graph_payload({"m3": real_db})
    check("the real corpus's band is 315 of 602 (52.3 %)",
          payload["isolated_count"] == 315 and len(payload["nodes"]) == 602
          and payload["isolated_share"] == 52.3,
          "%d of %d, %s %%" % (payload["isolated_count"],
                               len(payload["nodes"]),
                               payload["isolated_share"]))


def t_page_is_shipped_and_self_contained(m3_db, mesh_db):
    """No CDN, no external fetch. The page must work with the network down."""
    import re
    import urllib.request
    page = os.path.join(os.path.dirname(gui.__file__), "assets", "gui.html")
    check("assets/gui.html ships with the package", os.path.exists(page),
          os.path.basename(page))
    if not os.path.exists(page):
        return
    text = open(page, encoding="utf-8").read()
    # Not a `src=`/`href=` pattern: that shape passes a `fetch("https://…")`,
    # an `@import url(…)`, a `new Image().src = …` and a WebSocket, none of
    # which are attributes. Any absolute URL at all is the thing being banned,
    # so the scheme is the needle. Strictly stronger, and green today.
    external = [m for m in re.findall(r"https?://[^\s\"'<>)]+", text)]
    check("the page references no external host",
          not external, "%d external URL(s): %s" % (len(external), external[:2]))
    # A source check, and the only one available: nothing in this suite renders
    # CSS, so reinstating this rule is otherwise undetectable. `#schematic` is
    # an ID selector (1,0,0) and an SVG geometry presentation attribute enters
    # the cascade at 0, so a sizing rule here beats the width/height the page
    # computes from the row count. With a viewBox it does not clip -- it scales
    # the whole schematic down to fit, so the `overflow:auto` wrapper can never
    # overflow and never scrolls. At DEFAULT_CAP = 20 rows 34 units apart, ~700
    # units are squeezed into a pane near 280 px: the labels land near 3.6 px.
    sized = re.search(r"#schematic\s*\{[^}]*(width|height)\s*:", text)
    check("no CSS rule overrides the schematic's own size",
          sized is None, "found: %s" % (sized.group(0) if sized else "none"))
    with _running({"m3": m3_db}, mesh_db=mesh_db) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/" % port, timeout=10) as resp:
            served = resp.read().decode("utf-8")
    check("GET / serves that same page", served == text,
          "%d byte(s) served, %d on disk" % (len(served), len(text)))


# The page's own script, run under `node` against a fake DOM and the REAL
# payload `graph_payload` produced for the synthetic corpus. Nothing is
# installed and nothing is imported into the page; the checks below degrade to
# named SKIPPED lines on a machine without `node`, the same way the real-corpus
# checks do. `boot()` is stripped from the source so the harness drives the
# functions itself.
#
# The first version exported only `{boot, renderSchematic, state}`, which left
# `renderHits`, `renderRows`, `runSearch`, `select` and `nodeAt` never
# executed -- four concrete defects stayed green under it, all four now
# covered: a dropped `models_missing`, a dropped `warnings` loop, a broken
# `model + "::" + node_key` join, and a `dsts` list that includes the node the
# path starts from.
#
# `fetch` is route-aware and recording, so `/path`'s request body can be read
# back, and `/search` can be made to answer 500 -- the case where the page used
# to print `undefined: 0 treff` and call a server exception an empty result.
_PAGE_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");   // argv[1] is this file
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const mk = () => ({innerHTML: "", textContent: "", className: "", value: "",
  hidden: false, style: {}, dataset: {}, closest: () => null,
  addEventListener() {}, getContext: () => new Proxy({}, {get: () => () => {}}),
  getBoundingClientRect: () => ({width: 600, height: 300, left: 0, top: 0})});
const els = {};
global.document = {getElementById: (id) => els[id] || (els[id] = mk()),
  createElement: () => ({textContent: "", get innerHTML() {
    return String(this.textContent).replace(/&/g, "&amp;")
      .replace(/</g, "&lt;").replace(/>/g, "&gt;"); }})};
global.window = {devicePixelRatio: 1, addEventListener() {}};

// Two real keys out of the payload: the one with an apostrophe in its path,
// and any other. Taken from the fixture rather than written here, so the
// escaping is exercised against a filename that exists on disk.
const apo = payload.nodes.find((n) => n.key.indexOf("'") !== -1);
const other = payload.nodes.find((n) => n !== apo);
const asHit = (n) => ({model: n.model, node_key: n.key.slice(n.model.length + 2),
                       title: n.title, path: n.path});
const searchOK = {status: "complete", hits: [asHit(apo), asHit(other)],
                  warnings: ["EN ADVARSEL"], models_missing: ["m9"]};

let graphStatus = 200, graphBody = payload;
let searchStatus = 200, searchBody = searchOK;
let queryStatus = 200, queryBody = null;
// `/path` answering with a bridge is the normal case; emptied later to drive
// the neighbourhood fallback, which only fires when NO bridge was found.
let pathBody = {src: apo.key, max_depth: 4, cap: 20, truncated: true,
                bridges: [{dst: other.key, path: [apo.key, other.key]}],
                unreachable: ["m3::/gone.md"]};
// One stated edge and one derived one, so "drawn differently" has both sides
// to be different about. Shaped like `neighbourhood()`'s real answer.
let neighboursStatus = 200;
let neighboursBody = {node: apo.key, depth: 1, count: 2, status: "partial",
  warnings: ["derived, not stated: 1 by basename (0.6)"],
  edges: [],
  incoming: [{key: "m3::/inn.md", rel: "MENTIONS", method: "exact",
              confidence: 1.0, derived: false}],
  outgoing: [{key: "m3::/ut.md", rel: "LIKELY_COPY", method: "basename",
              confidence: 0.6, derived: true}]};
const calls = [];
global.fetch = async (url, opts) => {
  calls.push({url: url, body: opts && opts.body ? JSON.parse(opts.body) : null});
  if (url === "/graph")  return {status: graphStatus, json: async () => graphBody};
  if (url === "/search") return {status: searchStatus, json: async () => searchBody};
  if (url === "/query")  return {status: queryStatus, json: async () => queryBody};
  if (url === "/path")   return {status: 200, json: async () => pathBody};
  if (url === "/neighbors")
    return {status: neighboursStatus, json: async () => neighboursBody};
  return {status: 404, json: async () => ({error: "no route"})};
};

const api = new Function(src.replace(/\nboot\(\);\s*$/, "\n") +
  "\nreturn {boot, renderSchematic, renderHits, renderRows, runSearch, select," +
  " nodeAt, sx, sy, state, toggleFilter, visible, hiddenCount};")();
const v2 = () => document.getElementById("v2").innerHTML;
const status = () => document.getElementById("status").textContent;
// The last request to a NAMED route. `calls[calls.length - 1]` was read
// instead, and one click can now make two calls: the moment the neighbourhood
// fallback fired, "the last call" was /neighbors and `out.path_request.dsts`
// raised KeyError halfway through the checks -- which the mutation harness
// reported as a different gate's kill rather than as the crash it was.
const lastCall = (route) => {
  for (let i = calls.length - 1; i >= 0; i--)
    if (calls[i].url === route) return calls[i].body;
  return null;
};
const out = {};

// A 500 from /graph FIRST, on a page that has booted nothing yet: `payload`
// and `view` are still null, so `renderError` cannot be reused here and the
// next line used to read `.nodes` off `undefined` -- a blank page saying
// nothing. Re-booted normally straight after, which every check below needs.
graphStatus = 500; graphBody = {error: "simulated internal bug"};
api.boot().then(async () => {
  out.graph_error_status = status();
  out.graph_error_v1 = document.getElementById("v1").innerHTML;
  graphStatus = 200; graphBody = payload;
  await api.boot();
  out.boot_status = status();

  // -- the schematic's two conditions ------------------------------------
  api.renderSchematic();                              out.neither = v2();
  api.state.search = searchOK;
  api.renderSchematic();                              out.search_only = v2();
  api.state.search = null; api.state.selection = apo.key;
  api.renderSchematic();                              out.selection_only = v2();
  api.state.search = null; api.state.selection = null;

  // -- a real search, through runSearch ----------------------------------
  document.getElementById("mode").value = "search";
  document.getElementById("q").value = "noe";
  await api.runSearch();
  out.search_request = lastCall("/search");
  out.search_status = status();
  out.hits_html = document.getElementById("hits").innerHTML;
  const m = out.hits_html.match(/data-key='([^']*)'/);
  out.first_key = m ? m[1].replace(/&#39;/g, "'").replace(/&quot;/g, '"')
                          .replace(/&amp;/g, "&") : null;
  out.apo_key = apo.key;

  // -- a click: the dsts must not contain the node the path starts from --
  const beforeBridged = calls.length;
  await api.select(apo.key);
  out.routes_when_bridged = calls.slice(beforeBridged).map((c) => c.url);
  out.path_request = lastCall("/path");
  out.both = v2();

  // -- a 500 from /search is not an empty result set ---------------------
  searchStatus = 500; searchBody = {error: "simulated internal bug"};
  await api.runSearch();
  out.error_status = status();
  out.error_hits = document.getElementById("hits").innerHTML;

  // -- the closed language: a table, and a refusal -----------------------
  searchStatus = 200; searchBody = searchOK;
  document.getElementById("mode").value = "query";
  queryBody = {status: "complete", columns: ["a.path"], rows: [["/x.md"]],
               warnings: [], candidates: []};
  await api.runSearch();
  out.rows_status = status();
  out.rows_html = document.getElementById("hits").innerHTML;
  queryStatus = 500; queryBody = {error: "simulated internal bug"};
  await api.runSearch();
  out.query_error_status = status();

  // -- hit testing -------------------------------------------------------
  const n0 = payload.nodes[0];
  const found = api.nodeAt({clientX: api.sx(n0.x), clientY: api.sy(n0.y)});
  out.hit_test = found ? found.key : null;
  out.hit_test_miss = api.nodeAt({clientX: -9999, clientY: -9999});

  // -- the neighbourhood fallback ----------------------------------------
  // /path with not one bridge. Everything else is left as it was, so the
  // only difference from the click above is the empty `bridges`.
  document.getElementById("mode").value = "search";
  searchStatus = 200; searchBody = searchOK;
  await api.runSearch();
  pathBody = {src: apo.key, max_depth: 4, cap: 20, truncated: false,
              bridges: [], unreachable: [other.key, "m3::/gone.md"]};
  const before = calls.length;
  await api.select(apo.key);
  out.fallback_routes = calls.slice(before).map((c) => c.url);
  out.fallback_request = lastCall("/neighbors");
  out.fallback_v2 = v2();

  // A 500 from /neighbors is not an empty neighbourhood, same rule as every
  // other route.
  neighboursStatus = 500; neighboursBody = {error: "simulated internal bug"};
  await api.select(apo.key);
  out.fallback_error_status = status();
  neighboursStatus = 200;

  // -- the filters --------------------------------------------------------
  // Driven through `toggleFilter` with the event the checkbox would send,
  // because `addEventListener` is a no-op in this fake DOM.
  const twoModels = payload.models.length > 1;
  const victim = payload.nodes.find((n) => n.model === payload.models[0]);
  const spared = payload.nodes.find((n) => n.model !== payload.models[0]);
  // A sentinel on the canvas, because comparing the SCALE cannot see the
  // defect: `fit()` recomputes the same transform from the same payload and
  // the same rect, so a filter that called it left `sx(100)` untouched and
  // the mutation adding `fit()` survived. `fit()` does write the canvas
  // size, so a value it would overwrite is what makes the call observable.
  document.getElementById("graph").width = -1;
  const scaleBefore = api.sx(100);
  const callsBefore = calls.length;
  api.toggleFilter({target: {checked: false, dataset:
    {axis: "models", value: payload.models[0]}}});
  out.filter_status = status();
  out.filter_hidden = api.hiddenCount();
  out.filter_total = payload.nodes.length;
  out.filter_victim_visible = api.visible(victim);
  out.filter_spared_visible = spared ? api.visible(spared) : null;
  out.filter_two_models = twoModels;
  out.filter_no_fetch = calls.length === callsBefore;
  out.filter_no_relayout = api.sx(100) === scaleBefore &&
                           document.getElementById("graph").width === -1;
  // A hidden node has no dot, so the hit test must not find one at its own
  // coordinates -- the picture and the click have to agree.
  const hitHidden = api.nodeAt({clientX: api.sx(victim.x),
                                clientY: api.sy(victim.y)});
  out.filter_hit_hidden = hitHidden ? hitHidden.key : null;
  out.filter_victim_key = victim.key;
  api.toggleFilter({target: {checked: true, dataset:
    {axis: "models", value: payload.models[0]}}});
  out.filter_restored = api.hiddenCount();

  console.log(JSON.stringify(out));
}).catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
"""


_PAGE_LIMIT = 7          # see `_run_page`: a number `DEFAULT_CAP` is not


def _run_page(m3_db, m2_db):
    """Run the page's script under `node` against a real payload.

    Returns the harness's dict, or a string naming why it could not run.
    """
    import json
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    page = os.path.join(os.path.dirname(gui.__file__), "assets", "gui.html")
    if node is None or not os.path.exists(page):
        return "SKIPPED -- no `node` on this machine to run the page under"
    script = re.search(r"<script>(.*)</script>",
                       open(page, encoding="utf-8").read(), re.S).group(1)
    # A model with no store on disk, so `payload["missing"]` is non-empty and
    # the page's `delvis:` banner has something to say. Two empty honesty
    # fields would let a page that dropped the banner entirely stay green.
    #
    # TWO real models, not one: with a single model every filter hides the
    # whole corpus, and "hides everything" would satisfy a filter check that
    # a correct implementation and a `return false` both pass. With m2 beside
    # m3 the check can require that the filtered model went and the other
    # stayed.
    payload = gui.graph_payload({"m3": m3_db, "m2": m2_db,
                                 "ghost": "/no/such/store.db"})
    # Overwritten to something `DEFAULT_CAP` is not, on purpose: the page's
    # `/search` limit used to be a literal `20` in JavaScript, and a check that
    # compared the request against `gui.DEFAULT_CAP` -- also 20 -- would have
    # passed on the literal. `_PAGE_LIMIT` is a value only the payload can
    # supply, so reading it back proves the page took it from there.
    payload["search_limit"] = _PAGE_LIMIT
    if not any("'" in n["key"] for n in payload["nodes"]):
        # The harness picks the apostrophe node out of the payload; without one
        # the escaping check would compare a key that never needed escaping to
        # itself and could not go red.
        return "the fixture planted no filename with an apostrophe"
    with tempfile.TemporaryDirectory(prefix="gui-page-") as tmp:
        paths = {}
        for name, text in (("page.js", script),
                           ("harness.js", _PAGE_HARNESS),
                           ("payload.json", json.dumps(payload))):
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "w", encoding="utf-8") as fh:
                fh.write(text)
        try:
            proc = subprocess.run(
                [node, paths["harness.js"], paths["page.js"],
                 paths["payload.json"]], capture_output=True, text=True,
                timeout=60)
        except subprocess.TimeoutExpired:
            return "the page's script did not finish in 60 s"
    if proc.returncode != 0:
        return ("the page's script threw: %s"
                % (proc.stderr.strip().splitlines() or [""])[0][:90])
    return json.loads(proc.stdout)


def t_page_behaviour(m3_db, m2_db):
    """Eleven properties of the page, driven through its own script.

    Grouped into one `node` run because the run is the expensive part; each
    check below reads a different key out of it, and each can go red on its
    own. Every one maps to a defect that was live in this file:

    1. The schematic's two conditions -- three distinct texts for the three
       incomplete states, so a page that fell back to one generic message
       fails.
    2. Every unreachable destination is drawn (`ingen sti`) and a capped
       answer says so (`avkortet`): four bridges out of five must not look
       complete.
    3. Every schematic circle carries its key as a CHILD `<title>`. The
       visible label is sliced to 22 characters, so a sibling title -- which
       SVG renders as no tooltip at all -- makes the full key unrecoverable
       from the picture.
    4. The lead names the node the bridges start from. At four models the band
       packs nodes 3,65 px apart against a 10 px hit radius, so a click
       selects a neighbour often enough that an unnamed answer is a wrong
       answer nobody can see is wrong.
    5. An apostrophe in a filename survives into `data-key`. Before `esc`
       escaped quotes, `it's-a-note.md` produced a key truncated at the
       apostrophe, and `/path` answered "no bridges" about a node nobody asked
       about -- a wrong answer wearing the shape of a finding.
    6. A hit's key is joined `model::node_key`. `mesh_search` answers with the
       two halves separate and the graph is keyed with them joined; dropping
       the prefix greys every hit and selects nothing.
    7. A non-200 is not a result set. `undefined: 0 treff` was a 500 from
       `/search` rendered as an empty search.
    8. `warnings` and `models_missing` reach the status line, and the `/search`
       limit comes from the payload rather than a second copy of `DEFAULT_CAP`.
    9. The closed language's table states its row count AND keeps the corpus
       banner: `renderRows` was the third writer to #status and the one that
       forgot `missing`/`truncated`, so one query erased `delvis:` for the
       rest of the session.
    10. `/path`'s `dsts` exclude the node the path starts from, and `nodeAt`
        finds a node at its own coordinates and nothing at the far corner.
    11. A `/graph` that fails says so instead of drawing nothing.

    Eleven names, not five. Four mutations used to name check 1 as their gate
    while mutating properties 2 and 3, and one named check 5 while mutating
    property 6 -- 8 of 36 mutations resting on a gate name that did not name
    what they broke. A compound name is also a single point of edit: one
    rewrite of that check silently drops the coverage of every mutation
    pointing at it.
    """
    names = ["the schematic names which of its two conditions is missing",
             "the schematic draws every unreachable hit and says when capped",
             "every schematic circle carries its key in a child <title>",
             "the schematic's lead names the node the bridges start from",
             "an apostrophe in a filename survives into data-key",
             "a hit's key is joined as model::node_key",
             "a non-200 answer is an error, not an empty result set",
             "warnings, models_missing and the payload's own limit are used",
             "the closed language's table counts its rows and keeps the banner",
             "a click asks for bridges to the OTHER hits, and only those",
             "a /graph that fails says so instead of drawing nothing",
             "no bridge found falls back to /neighbors, and only then",
             "the fallback still names the hits with no path",
             "a derived neighbour edge is drawn differently from a stated one",
             "a 500 from /neighbors is an error, not an empty neighbourhood",
             "the filter hides the model it is given and keeps the others",
             "the filter says on the status line how much it hides",
             "a filtered-out node cannot be clicked",
             "the filter triggers no fetch and no re-layout"]
    out = _run_page(m3_db, m2_db)
    if isinstance(out, str):
        for name in names:
            check(name, out.startswith("SKIPPED"), out)
        return

    import re
    states = [out["neither"], out["search_only"], out["selection_only"]]
    check(names[0],
          len(set(states)) == 3
          and "mangler: en valgt node" in out["search_only"]
          and "mangler: et søk" in out["selection_only"],
          "; ".join(re.sub(r"<[^>]+>", "", s)[:30] for s in states))

    check(names[1],
          "ingen sti" in out["both"] and "avkortet" in out["both"],
          re.sub(r"<[^>]+>", " ", out["both"])[:70])

    # `<title>` a CHILD of the circle, not a sibling: SVG renders no tooltip
    # for a sibling, and the visible label is sliced to 22 characters, so the
    # full key would be unrecoverable. EVERY circle, not merely one of them:
    # the first version of this clause asked only that `</title></circle>`
    # appear somewhere, and a mutation that self-closed the bridge circles
    # stayed green because the unreachable circle still carried its title.
    check(names[2],
          out["both"].count("<circle") == out["both"].count("</title></circle>")
          and "/><title>" not in out["both"],
          "%d circle(s), %d titled"
          % (out["both"].count("<circle"),
             out["both"].count("</title></circle>")))

    # The lead is read back and unescaped, so the check compares the key the
    # harness asked about with the key the pane says it answered about -- not
    # merely that the lead is non-empty.
    lead = re.search(r"<p class='lead[^']*'>(.*?)</p>", out["both"])
    lead_text = (lead.group(1) if lead else "").replace("&#39;", "'")
    check(names[3], out["apo_key"] in lead_text,
          "lead=%r src=%r" % (lead_text[:52], out["apo_key"][-24:]))

    check(names[4],
          "'" in out["apo_key"] and "&#39;" in out["hits_html"],
          "key=%r escaped in hits=%s" % (out["apo_key"][-24:],
                                         "&#39;" in out["hits_html"]))

    check(names[5], out["first_key"] == out["apo_key"],
          "key=%r read back as %r" % (out["apo_key"][-24:],
                                      (out["first_key"] or "")[-24:]))

    check(names[6],
          out["error_status"].startswith("HTTP 500")
          and "undefined" not in out["error_status"]
          and out["error_hits"] == ""
          and out["query_error_status"].startswith("HTTP 500"),
          "search=%r query=%r" % (out["error_status"][:40],
                                  out["query_error_status"][:40]))

    check(names[7],
          "m9" in out["search_status"] and "EN ADVARSEL" in out["search_status"]
          and "delvis" in out["boot_status"] and "ghost" in out["boot_status"]
          and out["search_request"]["limit"] == _PAGE_LIMIT,
          "boot=%r status=%r limit=%r" % (out["boot_status"][:34],
                                          out["search_status"][:40],
                                          out["search_request"].get("limit")))

    # `ghost` is the model with no store on disk (see `_run_page`), so the
    # corpus banner has something to say through every writer to #status. It
    # is asserted HERE because `renderRows` is the writer that forgot it.
    check(names[8],
          "1 rad(er)" in out["rows_status"] and "ghost" in out["rows_status"],
          "rows=%r" % out["rows_status"][:60])

    dsts = out["path_request"]["dsts"]
    check(names[9],
          out["path_request"]["src"] == out["apo_key"]
          and out["apo_key"] not in dsts and len(dsts) == 1
          and out["hit_test"] is not None and out["hit_test_miss"] is None,
          "src excluded from %d dst(s), hit_test=%s"
          % (len(dsts), (out["hit_test"] or "")[-18:]))

    # `boot()` was the one fetch with no status check left. A 500 from /graph
    # is reachable -- the payload is prebuilt, but it is served through the
    # same handler as every other route -- and it used to read `.nodes` off
    # `undefined`, leaving a page that was blank and silent about why.
    check(names[10],
          out["graph_error_status"].startswith("HTTP 500")
          and "undefined" not in out["graph_error_status"]
          and "ingen graf" in out["graph_error_v1"],
          "status=%r v1=%r" % (out["graph_error_status"][:44],
                               re.sub(r"<[^>]+>", "", out["graph_error_v1"])[:24]))

    # -- the neighbourhood fallback (step 2b) ------------------------------
    #
    # BOTH halves, in one check because they are one property: the click that
    # found a bridge must NOT have called /neighbors, and the click that
    # found none must. A check that only asserted the second would pass a
    # page that fetched the neighbourhood on every click.
    check(names[11],
          out["fallback_routes"] == ["/path", "/neighbors"]
          and out["routes_when_bridged"] == ["/path"]
          and out["fallback_request"] == {"node": out["apo_key"]},
          "routes=%r request=%r" % (out["fallback_routes"],
                                    out["fallback_request"]))

    fb = re.sub(r"<[^>]+>", " ", out["fallback_v2"])
    check(names[12],
          "ingen sti til 2 treff innen dybde 4" in fb,
          "lead=%r" % fb[:76])

    # Stated and derived must not render the same. Asserted as a difference
    # between the two edges the harness supplies -- one `derived: true`, one
    # `derived: false` -- rather than as a literal colour, so restyling the
    # pane does not silently turn the distinction off.
    # EXACTLY two dashed marks -- the derived edge's line and its circle --
    # not "at least two": a pane that dashed every edge would satisfy a
    # minimum while drawing the distinction away, which is the same shape of
    # vacuous clause this branch has now caught six times.
    check(names[13],
          out["fallback_v2"].count("stroke-dasharray") == 2
          and "/ut.md" in out["fallback_v2"] and "/inn.md" in out["fallback_v2"]
          and "basename" in out["fallback_v2"],
          "%d dashed mark(s) over 3 circles"
          % out["fallback_v2"].count("stroke-dasharray"))

    check(names[14],
          out["fallback_error_status"].startswith("HTTP 500")
          and "undefined" not in out["fallback_error_status"],
          "status=%r" % out["fallback_error_status"][:44])

    # -- the filters (step 2a) ---------------------------------------------
    check(names[15],
          out["filter_two_models"]
          and out["filter_victim_visible"] is False
          and out["filter_spared_visible"] is True
          and 0 < out["filter_hidden"] < out["filter_total"]
          and out["filter_restored"] == 0,
          "hid %d of %d, spared visible=%r, restored to %d"
          % (out["filter_hidden"], out["filter_total"],
             out["filter_spared_visible"], out["filter_restored"]))

    check(names[16],
          ("filteret skjuler %d av %d noder"
           % (out["filter_hidden"], out["filter_total"])) in out["filter_status"],
          "status=%r" % out["filter_status"][:76])

    check(names[17], out["filter_hit_hidden"] is None,
          "click at the hidden node's own coordinates found %r"
          % (out["filter_hit_hidden"] or "nothing"))

    check(names[18],
          out["filter_no_fetch"] and out["filter_no_relayout"],
          "no fetch=%r, scale unchanged=%r"
          % (out["filter_no_fetch"], out["filter_no_relayout"]))


def t_gui_subcommand_exists_without_a_host_flag(m3_db):
    """`homegraph gui` is reachable from the CLI, and there is no flag that
    could publish it.

    `t_binds_loopback_only` proves `serve()` takes no host argument; this
    proves the CLI grew no `--host` that would only fail deeper down. Driven
    through `cli.main`, the real parser -- it is built inline, so there is no
    parser object to borrow.

    `gui.serve` is replaced by a recorder for the duration (restored in
    `finally`, so nothing else in this file is affected) for a reason found
    the hard way: an earlier version of this check ran the `--host` call
    against the real `serve`, and under the mutation it was written to catch
    -- a `--host` added to the subparser -- argparse accepted the flag,
    `cmd_gui` ran, and the check hung in `serve_forever` instead of going red.
    A check that hangs on the defect it targets has not failed, it has
    stopped. With the recorder in place both calls return: the first records
    the arguments `cmd_gui` forwarded, the second exits 2 at the parser.
    """
    import contextlib
    import io

    from homegraph import cli, gui as gui_mod

    seen = {}

    def _record(model_paths, mesh_db=None, port=0, open_browser=True):
        seen.update(model_paths=model_paths, mesh_db=mesh_db, port=port,
                    open_browser=open_browser)

    original, gui_mod.serve = gui_mod.serve, _record
    try:
        rc = cli.main(["gui", "--model", "m3=%s" % m3_db, "--no-browser"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                cli.main(["gui", "--model", "m3=%s" % m3_db,
                          "--host", "127.0.0.1"])
                rejected = False
            except SystemExit as exc:
                rejected = exc.code == 2
    finally:
        gui_mod.serve = original
    check("the gui subcommand forwards its arguments and refuses --host",
          rc == 0 and rejected and seen.get("model_paths") == {"m3": m3_db}
          and seen.get("mesh_db") is None and seen.get("port") == 0
          and seen.get("open_browser") is False,
          "rc=%r forwarded=%r --host rejected=%s" % (rc, seen, rejected))


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


def real_mesh():
    """Path to the real mesh store, or None. Same decoupling as `real_m3()`:
    the structural `/path` and `/neighbors` checks build their own synthetic
    mesh in a tempdir and must run whether or not this file exists on the
    machine.
    """
    mesh = os.path.expanduser("~/.homegraph/real-mesh.db")
    return mesh if os.path.exists(mesh) else None


def t_path_route_answers_over_the_real_corpus(real_m3_db, real_mesh_db):
    """`/path` against the real corpus and its real mesh, at the depth
    `DEFAULT_MAX_DEPTH` was measured at (2026-07-28: 6 calls, 38/46/54 ms at
    depth 2/3/4). Not a timing assertion -- machine load varies too much for
    a hard budget in a checkpoint -- just proof the two constants were
    picked against a real answer and not only the three-file synthetic
    corpus above. Same SKIPPED pattern as `t_graph_route_matches_real_corpus`
    when the real stores are not on this machine.
    """
    if real_m3_db is None or real_mesh_db is None:
        check("POST /path answers over the real corpus", True,
              "SKIPPED -- ~/.homegraph/real-m3.db or real-mesh.db not present "
              "on this machine")
        return
    payload = gui.graph_payload({"m3": real_m3_db})
    keys = [n["key"] for n in payload["nodes"]][:6]
    if len(keys) < 2:
        check("POST /path answers over the real corpus", False,
              "fewer than 2 nodes in the real corpus")
        return
    with _running({"m3": real_m3_db}, mesh_db=real_mesh_db) as port:
        status, out = _post(port, "/path", {"src": keys[0], "dsts": keys[1:]})
    total = len(out.get("bridges", [])) + len(out.get("unreachable", []))
    check("POST /path answers over the real corpus",
          status == 200 and total == len(keys) - 1,
          "status_code=%d took=%d of %d" % (status, total, len(keys) - 1))


def main():
    t_file_kinds_are_the_measured_four()
    t_neighbourhood_sorts_an_input_that_arrives_unsorted()
    t_binds_loopback_only()

    with tempfile.TemporaryDirectory(prefix="gui-cp-") as tmp:
        synthetic_db = _build_synthetic_m3(tmp)
        t_full_read_has_every_planted_file(synthetic_db)
        t_kind_counts_are_the_other_half_of_the_summary(synthetic_db)
        t_capped_read_names_the_model_it_capped(synthetic_db)
        t_payload_drops_non_file_kinds(synthetic_db)
        t_isolated_computation(synthetic_db)
        t_positions_are_deterministic(synthetic_db)
        t_positions_match_their_measured_values(synthetic_db)
        t_isolated_nodes_are_banded_not_simulated(synthetic_db)
        t_band_states_its_count_and_share(synthetic_db)
        t_gui_subcommand_exists_without_a_host_flag(synthetic_db)
        t_graph_route_serves_the_payload(synthetic_db)
        t_graph_route_preserves_missing_and_truncated(synthetic_db)
        t_graph_route_answers_when_the_payload_will_not_serialise(synthetic_db)
        t_search_route_returns_hits(synthetic_db)
        t_missing_model_is_reported_as_partial(synthetic_db)
        t_query_route_refuses_unknown_model(synthetic_db)
        t_search_route_matches_server_verbatim(synthetic_db)
        t_search_route_rejects_malformed_json(synthetic_db)
        t_search_route_rejects_bad_argument_name(synthetic_db)
        t_path_route_rejects_missing_src(synthetic_db)
        t_path_route_rejects_malformed_max_depth(synthetic_db)
        t_path_route_rejects_non_list_dsts(synthetic_db)
        t_internal_type_error_is_500_not_400(synthetic_db)

    with tempfile.TemporaryDirectory(prefix="gui-path-cp-") as tmp:
        path_db = _build_synthetic_m3_for_path(tmp)
        path_m2_db = _build_synthetic_m2_for_path(tmp)
        mesh_db = _build_synthetic_mesh(tmp, {"m3": path_db, "m2": path_m2_db})
        t_path_reports_unreachable_rather_than_omitting(path_db, mesh_db)
        t_path_finds_a_real_bridge(path_db, path_m2_db, mesh_db)
        t_path_cap_is_reported(path_db, mesh_db)
        t_neighbors_route_returns_edges(path_db, mesh_db)
        t_neighbourhood_splits_the_edges(path_db, path_m2_db, mesh_db)
        t_neighbourhood_marks_derived_by_confidence(path_db, path_m2_db, mesh_db)
        t_neighbors_route_rejects_malformed_depth(path_db, mesh_db)
        t_neighbors_route_without_mesh_answers_rather_than_resets(path_db)
        t_page_is_shipped_and_self_contained(path_db, mesh_db)
        # Needs the corpus that plants `it's-a-note.md`.
        t_page_behaviour(path_db, path_m2_db)

    t_isolated_matches_md_gaps(real_m3())
    t_graph_route_matches_real_corpus(real_m3())
    t_band_matches_the_real_corpus(real_m3())
    t_path_route_answers_over_the_real_corpus(real_m3(), real_mesh())

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
