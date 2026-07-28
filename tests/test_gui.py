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
        t_search_route_matches_server_verbatim(synthetic_db)
        t_search_route_rejects_malformed_json(synthetic_db)
        t_search_route_rejects_bad_argument_name(synthetic_db)

    with tempfile.TemporaryDirectory(prefix="gui-path-cp-") as tmp:
        path_db = _build_synthetic_m3_for_path(tmp)
        path_m2_db = _build_synthetic_m2_for_path(tmp)
        mesh_db = _build_synthetic_mesh(tmp, {"m3": path_db, "m2": path_m2_db})
        t_path_reports_unreachable_rather_than_omitting(path_db, mesh_db)
        t_path_finds_a_real_bridge(path_db, path_m2_db, mesh_db)
        t_path_cap_is_reported(path_db, mesh_db)
        t_neighbors_route_returns_edges(path_db, mesh_db)
        t_neighbors_route_without_mesh_answers_rather_than_resets(path_db)

    t_isolated_matches_md_gaps(real_m3())
    t_graph_route_matches_real_corpus(real_m3())
    t_path_route_answers_over_the_real_corpus(real_m3(), real_mesh())

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
