#!/usr/bin/env python3
"""MCP server over stdio: mesh_search, mesh_neighbors, mesh_path, mesh_explain.

Hand-rolled JSON-RPC rather than an SDK, for the same reason the rest of this
package is stdlib-only: the protocol surface actually needed here is three
methods, and a dependency to get them would be the first one in the project.

Read-only by construction. Every tool opens the stores through `Mesh`, which
only ever issues SELECTs, and no tool takes a path to write to. An MCP server is
something an agent drives unattended; that is the wrong place to discover that a
tool could modify a graph.

Partial results stay partial. `mesh_search` returns the same `status` field the
CLI prints, and if a model did not answer the response says so in the text an
agent reads. A federated search that quietly drops a model looks exactly like a
smaller corpus -- more so to an agent than to a person, since the agent never
sees the model list.

Usage:
    python3 -m homegraph.mcp_server --model m3=/path/m3.db [--mesh-db /path]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .mesh import Mesh
from .store import provenance_note

PROTOCOL_VERSION = "2024-11-05"
# Read the version rather than repeat it: this string goes out to every client
# that connects, and a third copy is a third thing to forget to bump.
SERVER_INFO = {"name": "homegraph", "version": __version__}

TOOLS = [
    {
        "name": "mesh_search",
        "description": (
            "Federated search across the homegraph models. Returns hits with "
            "the model each came from, and a status of 'complete' or "
            "'partial'. A partial result means a model did not answer; counts "
            "and ranking are incomplete and must not be read as a full answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search terms"},
                "limit": {"type": "integer", "default": 20},
                "as_of": {"type": "string",
                          "description": "ISO date; search the graph as it "
                                         "stood on that day"},
                "include_all": {"type": "boolean", "default": False,
                                "description": "include hidden subtypes such "
                                               "as transcripts"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mesh_neighbors",
        "description": "Cross-model edges touching a node, in either direction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string",
                         "description": "mesh node key, e.g. m3::/path/to.md"},
                "depth": {"type": "integer", "default": 1},
            },
            "required": ["node"],
        },
    },
    {
        "name": "mesh_path",
        "description": (
            "Shortest path between two mesh nodes. Returns null when there is "
            "none within max_depth -- absence of a path is an answer, not an "
            "error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string"},
                "dst": {"type": "string"},
                "max_depth": {"type": "integer", "default": 4},
            },
            "required": ["src", "dst"],
        },
    },
    {
        "name": "query",
        "description": (
            "One structural query over a single model's graph, in homegraph's "
            "closed query language. Grammar: "
            "MATCH (a:label)-[e:REL]->(b:label) [WHERE cond AND cond] "
            "[AS OF 'YYYY-MM-DD'] RETURN a.prop, b.prop. "
            "Conditions use = != < <= > >= PREFIX CONTAINS, or "
            "`a NAMED 'basename'` to look a node up by filename. "
            "There is no OR, no DISTINCT, no aggregation and no "
            "variable-length path; anything outside the grammar is refused "
            "with a message naming what is missing, never partially "
            "interpreted. A status of 'ambiguous' means a NAMED lookup "
            "matched several files and none was chosen -- ask again with one "
            "of the candidates. A status of 'partial' means some returned "
            "edge was inferred rather than stated; the warning names how."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string",
                          "description": "which model to query, e.g. m3"},
                "query": {"type": "string"},
            },
            "required": ["model", "query"],
        },
    },
    {
        "name": "mesh_explain",
        "description": (
            "Which models answered a query, at what rank, and how the results "
            "were fused. Use this when a result looks surprising: it shows "
            "whether a model was missing rather than merely unhelpful."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
]


def _text(payload):
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2,
                                            ensure_ascii=False)}]}


class Server:
    def __init__(self, model_paths, mesh_db=None):
        self.model_paths = model_paths
        self.mesh_db = mesh_db

    def _mesh(self):
        return Mesh(self.model_paths, mesh_db=self.mesh_db)

    # -- tools ------------------------------------------------------------

    def mesh_search(self, query, limit=20, as_of=None, include_all=False):
        with self._mesh() as mesh:
            res = mesh.search(query, limit=limit, as_of=as_of,
                              include_all=include_all)
            return {
                "status": res.status,
                "models_queried": res.models_queried,
                "models_missing": res.models_missing,
                "warnings": res.warnings,
                "hits": [{"rank": h["rank"], "model": h["model"],
                          "title": h.get("title"),
                          "title_confidence": h.get("title_confidence"),
                          "node_key": h["node_key"],
                          "path": h.get("path"), "sources": h["sources"]}
                         for h in res.hits],
            }

    def mesh_neighbors(self, node, depth=1):
        with self._mesh() as mesh:
            edges = mesh.neighbours(node, depth=depth)
        # An agent never sees the terminal, so this is the one caller for
        # which a derived edge that looks stated is entirely undetectable.
        # `status` is `partial` here for the same reason it is when a model
        # is missing: the answer is not the whole truth about its own
        # certainty.
        note = provenance_note(edges)
        return {"node": node, "depth": depth, "count": len(edges),
                "status": "partial" if note else "complete",
                "warnings": [note] if note else [],
                "edges": [{"src": e.src, "rel": e.rel, "dst": e.dst,
                           "method": e.method, "confidence": e.confidence}
                          for e in edges]}

    def query(self, model, query):
        from .query import QueryError, run
        path = self.model_paths.get(model)
        if not path:
            return {"status": "error", "error": "no model %r; this server "
                    "has %s" % (model, ", ".join(sorted(self.model_paths)))}
        with Mesh({model: path}) as mesh:
            try:
                store = mesh.store(model)
            except Exception as exc:                            # noqa: BLE001
                return {"status": "error", "error": repr(exc)}
            try:
                res = run(store, query)
            except QueryError as exc:
                # Refused, with the capability named. An agent that gets
                # "syntax error" retries the same shape; one that is told
                # "this language has no OR" rewrites the query.
                return {"status": "refused", "error": str(exc),
                        "unsupported": exc.missing}
        return {"status": res.status, "columns": res.columns,
                "rows": [list(r) for r in res.rows],
                "warnings": res.warnings, "candidates": res.candidates}

    def mesh_path(self, src, dst, max_depth=4):
        with self._mesh() as mesh:
            trail = mesh.path(src, dst, max_depth=max_depth)
        return {"src": src, "dst": dst, "max_depth": max_depth,
                "found": trail is not None, "path": trail,
                "length": len(trail) if trail else 0}

    def mesh_explain(self, query, limit=20):
        with self._mesh() as mesh:
            return mesh.explain(query, limit=limit)

    # -- protocol ---------------------------------------------------------

    def handle(self, request):
        method = request.get("method")
        rid = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            result = {"protocolVersion": PROTOCOL_VERSION,
                      "capabilities": {"tools": {}},
                      "serverInfo": SERVER_INFO}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            fn = {"mesh_search": self.mesh_search,
                  "query": self.query,
                  "mesh_neighbors": self.mesh_neighbors,
                  "mesh_path": self.mesh_path,
                  "mesh_explain": self.mesh_explain}.get(name)
            if fn is None:
                return self._error(rid, -32601, "unknown tool %r" % name)
            try:
                result = _text(fn(**args))
            except TypeError as exc:
                return self._error(rid, -32602, "bad arguments: %s" % exc)
            except Exception as exc:                            # noqa: BLE001
                # Reported as a tool error, not a protocol error: the client
                # should see what failed rather than lose the connection.
                result = {"content": [{"type": "text",
                                       "text": "tool failed: %r" % exc}],
                          "isError": True}
        elif method in ("notifications/initialized", "initialized"):
            return None                       # notification: no reply at all
        elif method == "ping":
            result = {}
        else:
            return self._error(rid, -32601, "unknown method %r" % method)

        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _error(rid, code, message):
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": code, "message": message}}

    def serve(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "parse error")
            else:
                response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="homegraph.mcp_server")
    ap.add_argument("--model", action="append", required=True,
                    metavar="NAME=PATH")
    ap.add_argument("--mesh-db", dest="mesh_db", default=None)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    models = {}
    for spec in args.model:
        name, _, path = spec.partition("=")
        models[name] = path
    Server(models, args.mesh_db).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
