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

import json
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


def build_handler(server, payload):
    """A request handler bound to one Server and one prebuilt graph payload.

    A factory rather than class attributes: two GUIs in one process would
    otherwise share whichever stores were configured last, and the tests run
    several handlers in the same interpreter.

    `server` is unused until Task 3 wires `/search` and friends to it -- kept
    as a parameter now so the route methods added then do not change this
    signature.
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
