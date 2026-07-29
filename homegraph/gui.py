#!/usr/bin/env python3
"""The GUI's payloads and its HTTP transport.

There is no answer logic here. `mcp_server.Server` already produces every
answer this interface shows, and it produces them as plain dicts -- `_text()`
wraps them in MCP form only inside `handle()`. So this module is a second
transport over the same object, not a second opinion about the same stores.
Nothing outside this file is modified to make that work.

Everything the page draws is decided here, in Python, for the same reason
`visualize.collect` decides the `link` flag there rather than in the browser:
what Python decides is under test.
"""
from __future__ import annotations

import functools
import inspect
import json
import math
import os
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .visualize import _layout, collect

# The kinds that stand for something on disk. Named, not derived: M1's
# `reference` (180 of them) and M4's `archive_entry` (69) are about files
# without being files, and a rule that guessed from the name would take them.
FILE_KINDS = frozenset({"file", "document", "image", "code"})

# `collect` caps per model BEFORE any filtering, so a cap sized to what we
# expect to keep silently drops files: measured 2026-07-28, limit 2000 returns
# 2 147 file nodes where the corpus holds 2 472 -- M3's 602 files losing to its
# 6 035 sections. Reading everything costs the same 0,06 s and 32 MB RSS, so
# there is nothing to buy by capping.
NO_LIMIT = 10 ** 9

# Both set by the Task 0 measurement, not by taste. `DEFAULT_CAP` bounds how
# many bridges one click pays for; `truncated` in the response is what stops a
# capped view from reading as a complete one. Measured 2026-07-28 on the real
# corpus after a lookup defect was fixed: 6 path calls cost 38/46/54 ms at
# depth 2/3/4, finding 3/3/5 paths; scaled to the 19 calls a full 20-hit set
# implies, ~171 ms at depth 4 -- under the 300 ms boundary set in advance.
DEFAULT_MAX_DEPTH = 4
DEFAULT_CAP = 20

# Band geometry, expressed in the coordinate space `_layout` returns rather
# than in pixels: the page scales that space onto whatever canvas it has, and
# a constant in pixels here would be this module deciding how big a window is.
BAND_GAP = 140.0
BAND_WIDTH = 1600.0                       # `_layout`'s own default `width`


def _positions(out_nodes, out_edges, linked):
    """`([(x, y), ...], band_divider)`: a force layout for the connected nodes,
    a band for the rest, and the y the two are separated at.

    Computed in Python, and shipped in the payload, for two reasons. The first
    is this module's whole premise -- the page draws, it does not decide. The
    second is `_layout`'s own: it is seeded (20260722) and reads no clock, so
    the same corpus produces the same picture twice, and this week's screenshot
    can be laid beside last week's. A simulation re-run in JavaScript on every
    load would give that up, and the GUI already chose to highlight in place
    rather than re-lay-out precisely to keep it.

    The simulation runs over the CONNECTED subgraph only. An isolated node has
    no information in its position: dropped into the same simulation it would
    be shoved somewhere by pure repulsion and land next to files it has no
    relation to, which draws noise that looks like data. Isolated nodes get a
    sorted band along the bottom instead, where the position says only "this
    one has no edge" -- which is the entire fact about it.

    `_layout` is private to `visualize`, and is called rather than copied: a
    second force implementation here would be a second opinion about what the
    same graph looks like, and the two would drift.

    `band_divider` -- the y halfway up the gap, or `None` when there is no band
    or no cloud to separate it from -- is returned rather than left for the
    page to derive. It first shipped as a literal `bandTop + 70` in JavaScript,
    which is `BAND_GAP / 2` hand-copied across a language boundary with no gate
    between the two: retuning `BAND_GAP` to 40 here would have drawn the
    divider *inside* the band, striking through the isolated nodes, with every
    check still green.
    """
    order = sorted(linked)
    slot = {old: new for new, old in enumerate(order)}
    laid = _layout([out_nodes[i] for i in order],
                   [(slot[a], slot[b]) for a, b, *_ in out_edges])

    pos = [(0.0, 0.0)] * len(out_nodes)
    for new, old in enumerate(order):
        pos[old] = laid[new]

    # Sorted by path, with the key breaking ties: two models can hold the same
    # path, and an ordering that depended on which one `collect` read first
    # would move nodes between runs -- the exact property this function exists
    # to preserve.
    band = sorted((i for i in range(len(out_nodes)) if i not in linked),
                  key=lambda i: (out_nodes[i]["path"], out_nodes[i]["key"]))
    if laid:
        x0 = min(x for x, _ in laid)
        span = (max(x for x, _ in laid) - x0) or BAND_WIDTH
        floor = max(y for _, y in laid)
        top = floor + BAND_GAP
        divider = round(floor + BAND_GAP / 2, 1) if band else None
    else:
        # Every node isolated -- the band is the whole picture, and there is no
        # cloud above it to sit under, so there is nothing to divide.
        x0, span, top, divider = -BAND_WIDTH / 2, BAND_WIDTH, 0.0, None
    cols = max(1, math.ceil(math.sqrt(len(band)) * 2))
    step = span / cols
    row_height = max(step, BAND_GAP / 4)
    for k, i in enumerate(band):
        pos[i] = (round(x0 + (k % cols) * step, 1),
                  round(top + (k // cols) * row_height, 1))
    return pos, divider


class BadArgument(Exception):
    """A route body's own complaint about its arguments -- distinct from
    `TypeError`, which `a9b8022` established means "a genuine bug in the
    answer layer" once binding has already passed (see `_run`, below).
    """


def graph_payload(model_paths, mesh_db=None, limit_per_model=NO_LIMIT):
    """File-level nodes, the edges among them, and which stand alone.

    `isolated` holds node keys with no edge at either end, computed after the
    non-file kinds are gone -- a file whose only edge is CONTAINS into its own
    sections is isolated, which is exactly what `md gaps` says about it.

    `truncated` names any model that came back at exactly the cap. It is
    normally empty; a caller that passes a real limit gets told which model it
    cut rather than a picture that looks like a smaller corpus.

    Every node also carries `x`/`y`. See `_positions` for why the layout is
    computed here and not in the browser, and why the isolated nodes are not
    part of the simulation that placed the rest.
    """
    nodes, edges, missing = collect(model_paths, limit_per_model,
                                    mesh_db=mesh_db)

    raw_per_model = {}
    for n in nodes:
        raw_per_model[n["model"]] = raw_per_model.get(n["model"], 0) + 1
    truncated = sorted(m for m, c in raw_per_model.items()
                       if c >= limit_per_model)

    keep = [i for i, n in enumerate(nodes) if n["kind"] in FILE_KINDS]
    remap = {old: new for new, old in enumerate(keep)}
    out_nodes = [dict(nodes[i]) for i in keep]
    out_edges = [(remap[a], remap[b], rel, method, conf)
                 for a, b, rel, method, conf in edges
                 if a in remap and b in remap]

    linked = set()
    for a, b, *_ in out_edges:
        linked.add(a)
        linked.add(b)

    counts, kind_counts = {}, {}
    for n in out_nodes:
        # The key is `model::node_key`, and for a file node the node_key IS
        # the path. Split here rather than re-querying: a second read could
        # disagree with the one the edges were built from.
        n["path"] = n["key"].split("::", 1)[1] if "::" in n["key"] else n["key"]
        counts[n["model"]] = counts.get(n["model"], 0) + 1
        # The other half of the spec's "summary per file type and partition".
        # Counted here for the same reason `counts` is: a per-kind total the
        # browser derived would be a second measurement of the same nodes,
        # and the two would eventually disagree. Measured 2026-07-29 on four
        # models: file 1655, code 561, image 183, document 73 -- four values,
        # which is what makes `kind` the filter axis and `subtype` (over
        # twenty values) not one, though every node still carries `subtype`
        # for whoever wants it later.
        kind_counts[n["kind"]] = kind_counts.get(n["kind"], 0) + 1

    isolated = [n["key"] for i, n in enumerate(out_nodes) if i not in linked]

    pos, band_divider = _positions(out_nodes, out_edges, linked)
    for n, (x, y) in zip(out_nodes, pos):
        n["x"] = x
        n["y"] = y

    # The band's own caption, as numbers rather than as a sentence the page
    # assembles from `nodes.length`: "315 of 602, 52.3%" is a measurement, and
    # a measurement recomputed in the browser is a second one.
    share = round(100.0 * len(isolated) / len(out_nodes), 1) if out_nodes else 0.0

    return {"nodes": out_nodes, "edges": out_edges, "isolated": isolated,
            "isolated_count": len(isolated), "isolated_share": share,
            "band_divider": band_divider, "models": sorted(counts),
            # The filter axes, named by Python. The page turns them into
            # checkboxes and decides nothing about what they mean.
            "kinds": sorted(kind_counts), "kind_counts": kind_counts,
            # The page's `/search` limit, sent rather than written twice. It is
            # `DEFAULT_CAP` on purpose: a search's hits become `/path`'s
            # `dsts`, so a limit above the cap would make every click report
            # `truncated` and a limit below it would leave the cap unreachable.
            "search_limit": DEFAULT_CAP,
            "missing": missing, "counts": counts, "truncated": truncated}


def bridges(server, src, dsts=None, max_depth=None, cap=None):
    """Every path from one node to the other hits, capped and honest about it.

    The loop lives here rather than in the page for the same reason every
    other decision does: what Python decides is under test. `mesh_path`
    returns null outside `max_depth`, and that null becomes a named entry in
    `unreachable` -- a hit with no bridge is a finding, and dropping it would
    let the schematic draw four of five and look complete.

    A real signature (`src` required, the rest keyword-optional) rather than
    a single `args` dict: `do_POST` binds every route's arguments against its
    function's own signature *before* calling it, the same way
    `mcp_server.py`'s `tools/call` does, and a missing `src` used to reach
    this function at all and raise `KeyError` -- a client mistake answered
    as a 500, the exact blame inversion `a9b8022` fixed for the other routes.

    `max_depth`/`cap`/`dsts` that bind fine but are the wrong shape (a
    string that will not parse as an int, a `dsts` that is not a list) raise
    `BadArgument`, not `TypeError`: a `TypeError` past this point is a bug in
    `bridges` or `mesh_path`, and `_run` needs to tell that apart from a
    caller's malformed request rather than reporting both as 400.
    """
    if dsts is None:
        dsts = []
    elif not isinstance(dsts, list):
        # `list("abc")` silently becomes three one-character destinations
        # instead of refusing a caller who meant one; `list(5)` raises
        # `TypeError`, which past this point reads as an internal bug, not a
        # caller mistake. Both are wrong shapes, checked explicitly instead
        # of relying on either accident.
        raise BadArgument("dsts must be a list of node keys, got %s"
                          % type(dsts).__name__)
    try:
        depth = int(max_depth) if max_depth else DEFAULT_MAX_DEPTH
        cap = int(cap) if cap else DEFAULT_CAP
    except (TypeError, ValueError) as exc:
        raise BadArgument("max_depth and cap must be integers: %s" % exc) from exc
    truncated = len(dsts) > cap
    dsts = dsts[:cap]

    found, unreachable = [], []
    for dst in dsts:
        out = server.mesh_path(src=src, dst=dst, max_depth=depth)
        if out.get("path"):
            found.append({"dst": dst, "path": out["path"]})
        else:
            unreachable.append(dst)
    return {"src": src, "bridges": found, "unreachable": unreachable,
            "max_depth": depth, "cap": cap, "truncated": truncated}


def neighbourhood(server, node, depth=None):
    """`mesh_neighbors`' answer verbatim, plus the two sides of it, sorted.

    The spec's fallback for v2: when no path exists between the clicked file
    and the other hits, draw its neighbourhood instead -- the file in the
    middle, incoming left, outgoing right, deterministically sorted.

    The verbatim answer is passed through untouched (`count`, `status`,
    `warnings` and `edges` are `mesh_neighbors`' own account of what it found
    and how sure it is, and a transport that rewrote them would be deciding
    something). `incoming` and `outgoing` are added beside it, because the
    split and the order ARE decisions and this package puts those in Python.
    Sorted by the far key then the relation, so the same neighbourhood draws
    the same picture twice -- the same property `_positions` exists to keep.

    `derived` is `confidence < 1.0`, NOT `method != "exact"`. The spec says
    the latter; `store.provenance_note` -- "the whole of the honesty rule,
    and it lives here once" -- says the former, and its docstring names a
    second copy of that question as the way one copy ends up always
    answering no. `exact` is 1.0, `path_prefix` 0.7, `basename` 0.6,
    `mention` 0.5, so the two agree today on every method in
    `EDGE_METHODS`; they would stop agreeing the moment a stated-but-uncertain
    method is added, and then the confidence reading is the correct one.

    A self-edge lands in both lists, which is the honest answer: it is both
    an incoming and an outgoing relation of the node.
    """
    try:
        depth = int(depth) if depth else 1
    except (TypeError, ValueError) as exc:
        raise BadArgument("depth must be an integer: %s" % exc) from exc
    out = server.mesh_neighbors(node=node, depth=depth)

    def side(edges, near, far):
        return sorted(({"key": e[far], "rel": e["rel"],
                        "method": e["method"], "confidence": e["confidence"],
                        "derived": e["confidence"] is not None
                        and e["confidence"] < 1.0}
                       for e in edges if e[near] == node),
                      key=lambda e: (e["key"], e["rel"]))

    edges = out.get("edges", [])
    out["incoming"] = side(edges, "dst", "src")
    out["outgoing"] = side(edges, "src", "dst")
    return out


def build_handler(server, payload):
    """A request handler bound to one Server and one prebuilt graph payload.

    A factory rather than class attributes: two GUIs in one process would
    otherwise share whichever stores were configured last, and the tests run
    several handlers in the same interpreter.

    `server` backs `do_POST`'s `/search`, `/query`, `/neighbors` and `/path`
    routes.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass                       # one line per request is noise, not a log

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_page(self):
            """Send the page itself, and return nothing -- see `_run`.

            Read per request rather than cached at import: the file is the
            interface, and editing it while the server runs should show up on
            reload the way editing any other page does.
            """
            page = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "assets", "gui.html")
            with open(page, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            """Both real routes go through `_run`, for the reason POST does.

            GET had no error path at all. Measured 2026-07-28: make `_send`
            raise on `/graph` and the client gets `RemoteDisconnected` -- no
            status, no body -- which is exactly the failure `_run` was added
            to remove for POST, and exactly the failure the page's `boot()`
            claims to handle (`gui.html`, the 200 guard). The claim was true
            of nothing the server could produce.

            The 404 branch is left as a direct `_send`: its body is a
            constant dict that `json.dumps` cannot refuse, so there is no
            failure for a guard to catch.
            """
            if self.path == "/graph":
                self._run(lambda: payload)
            elif self.path in ("/", "/index.html"):
                self._run(self._serve_page)
            else:
                self._send({"error": "no route %r" % self.path}, 404)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self):
            routes = {"/search": server.mesh_search,
                      "/query": server.query,
                      "/neighbors": functools.partial(neighbourhood, server),
                      "/path": functools.partial(bridges, server)}
            fn = routes.get(self.path)
            if fn is None:
                self._send({"error": "no route %r" % self.path}, 404)
                return
            try:
                args = self._read_json()
            except ValueError as exc:
                self._send({"error": "bad JSON: %s" % exc}, 400)
                return
            # Bindingen prøves for seg (samme løsning som mcp_server.py sitt
            # tools/call): da `except TypeError` lå rundt selve kallet,
            # dekket den hele verktøykroppen, og en TypeError dypt inne i
            # f.eks. mesh_search ble rapportert som klientens skyld. Nå kan
            # bare en ekte signaturmismatch gi "bad arguments".
            try:
                inspect.signature(fn).bind(**args)
            except TypeError as exc:
                self._send({"error": "bad arguments: %s" % exc}, 400)
                return
            self._run(lambda: fn(**args))

        def _run(self, call):
            """Run a route body and answer even when it raises.

            `/neighbors` (Task 3) and `/path` both reach `Mesh`, which raises
            `ModelUnavailable` -- not a `TypeError` -- when a server has no
            mesh configured (`Mesh._read_mesh` refuses rather than silently
            answering `count: 0`). Nothing used to sit around that call: the
            exception escaped `do_POST` entirely, past
            `BaseHTTPRequestHandler`'s own handling, so the client saw the
            connection reset with no body at all -- worse than any error
            reply. Mirrors the tool-body `try/except Exception` already in
            `mcp_server.py`'s `tools/call` (not touched here): the client
            should see what failed rather than lose the connection.

            `BadArgument` is caught separately, as a 400 -- a route body's
            own complaint about a value that bound fine but was the wrong
            shape (`bridges`'s `max_depth`/`cap`/`dsts`). A `TypeError`
            raised from *inside* a route body, past the pre-call binding
            check above, is `a9b8022`'s original case: a genuine bug in
            `mesh_search`, `query`, `mesh_neighbors` or `bridges` itself, not
            a malformed request, and reporting it as "bad arguments" is the
            exact blame inversion that commit exists to prevent. It falls
            through to `except Exception` below, same as any other internal
            failure. `log_message` above is a no-op, so without
            `traceback.print_exc()` here a 500 would leave nothing at all in
            the process's own output -- not even the traceback a plain
            escaping exception used to get for free from
            `BaseHTTPRequestHandler`.

            `call` returning `None` means it has already sent its own
            response -- `_serve_page` does, because the page is bytes of HTML
            and not a dict. Every route body that produces an answer returns
            it; none of them returns `None`.

            The send is INSIDE the guard, not after it. `_send` serialises the
            whole body before it writes a single byte, so a payload that will
            not encode becomes a 500 with a body instead of a reset
            connection. Once bytes are on the wire a second response cannot
            help, and does not: the write raises again and escapes, which is
            what happened before this method existed.
            """
            try:
                result = call()
                # Returned verbatim. `status`, `warnings` and `models_missing`
                # are the answer's own account of how complete it is, and a
                # transport that summarised them would be deciding something.
                if result is not None:
                    self._send(result)
            except BadArgument as exc:
                self._send({"error": "bad arguments: %s" % exc}, 400)
            except Exception as exc:                            # noqa: BLE001
                traceback.print_exc()
                self._send({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    return _Handler


def serve(model_paths, mesh_db=None, port=0, open_browser=True):
    """Run in the foreground until Ctrl-C. No daemon, nothing outlives the shell.

    Loopback only, and there is deliberately no host argument: this serves a
    whole home directory's corpus, and a flag that could publish it is a flag
    somebody will pass by accident. Reach it from elsewhere with `ssh -L`.
    """
    from .mcp_server import Server

    payload = graph_payload(model_paths, mesh_db=mesh_db)
    httpd = HTTPServer(("127.0.0.1", port),
                       build_handler(Server(model_paths, mesh_db=mesh_db),
                                     payload))
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    print("serving on %s  (Ctrl-C to stop)" % url)
    if payload["missing"]:
        print("partial: no store for %s" % ", ".join(payload["missing"]))
    if payload["truncated"]:
        print("partial: capped read on %s" % ", ".join(payload["truncated"]))
    if open_browser and not webbrowser.open(url):
        print("could not open a browser; the URL above is the whole interface")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
