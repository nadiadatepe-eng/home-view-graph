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
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .visualize import collect

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

    counts = {}
    for n in out_nodes:
        # The key is `model::node_key`, and for a file node the node_key IS
        # the path. Split here rather than re-querying: a second read could
        # disagree with the one the edges were built from.
        n["path"] = n["key"].split("::", 1)[1] if "::" in n["key"] else n["key"]
        counts[n["model"]] = counts.get(n["model"], 0) + 1

    isolated = [n["key"] for i, n in enumerate(out_nodes) if i not in linked]

    return {"nodes": out_nodes, "edges": out_edges, "isolated": isolated,
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

        def do_GET(self):
            if self.path == "/graph":
                self._send(payload)
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
                      "/neighbors": server.mesh_neighbors,
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
            """
            try:
                result = call()
            except BadArgument as exc:
                self._send({"error": "bad arguments: %s" % exc}, 400)
                return
            except Exception as exc:                            # noqa: BLE001
                traceback.print_exc()
                self._send({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)
                return
            # Returned verbatim. `status`, `warnings` and `models_missing`
            # are the answer's own account of how complete it is, and a
            # transport that summarised them would be deciding something.
            self._send(result)

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
