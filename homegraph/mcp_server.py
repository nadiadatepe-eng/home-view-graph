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
    homegraph mcp --model m3=/path/m3.db [--mesh-db /path]

One door, on purpose. This module used to carry its own `main()` and argparse
beside `homegraph mcp`, and the two validated `--model` separately: `cmd_mcp`
kept its own copy of `spec.partition("=")`, so the fix that taught this module
to refuse `--model m3` without a path left the CLI still registering the model
against "". Two doors and one of them unlocked is worse than either alone.
`cli.cmd_mcp` is the door now; `parse_model_specs` and `Server` are what it
opens, and there is one copy of each.
"""
from __future__ import annotations

import inspect
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
                          "path": h.get("path"), "sources": h["sources"],
                          # CP-H7: per hit, not per corpus. The reader is about
                          # to use these, not the other 8 500 rows.
                          "staleness": h.get("staleness"),
                          "embedding_status": h.get("embedding_status")}
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
        # Gyldig JSON er ikke nødvendigvis et objekt. `serve()` kaller dette i `else`-grenen
        # til en try som bare dekker `json.loads`, så `[]`, `"x"`, `3` eller `null` ga
        # `AttributeError` ut av løkka og drepte serveren -- fire tegn fra en klient.
        if not isinstance(request, dict):
            return self._error(None, -32600, "invalid request: expected an object")
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
            # A caller can send anything as `name`, including nothing. Non-str
            # falls through to the same -32601 as an unknown name, which is what
            # the protocol asks for either way.
            fn = {"mesh_search": self.mesh_search,
                  "query": self.query,
                  "mesh_neighbors": self.mesh_neighbors,
                  "mesh_path": self.mesh_path,
                  "mesh_explain": self.mesh_explain}.get(name) if isinstance(name, str) else None
            if fn is None:
                return self._error(rid, -32601, "unknown tool %r" % name)
            # Bindingen prøves for seg. Da `except TypeError` lå rundt selve kallet, dekket
            # den hele verktøykroppen: en TypeError dypt inne i et verktøy ble rapportert som
            # -32602 «bad arguments», altså en protokollfeil som klandrer klienten for en
            # serverfeil. Nå kan bare en ekte signaturmismatch gi -32602.
            try:
                inspect.signature(fn).bind(**args)
            except TypeError as exc:
                return self._error(rid, -32602, "bad arguments: %s" % exc)
            try:
                result = _text(fn(**args))
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


def parse_model_specs(specs):
    """`["m3=/path/m3.db"]` -> `{"m3": "/path/m3.db"}`.

    Refuses a spec without `=`. `"m3".partition("=")` yields an empty path, so `--model m3`
    used to register the model against `""` and the failure surfaced far away as an unrelated
    store error. A bad flag should fail at the flag.
    """
    models = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise ValueError("--model needs NAME=PATH, got %r" % spec)
        models[name.strip()] = path.strip()
    return models


if __name__ == "__main__":
    # `python3 -m homegraph.mcp_server` was the second entry point until 2026-07-26.
    # Removing `main()` without this leaves the module importable and silent: it exits 0,
    # starts nothing, and a documented command becomes a no-op that reports success.
    # A withdrawn interface has to say so.
    raise SystemExit(
        "`python3 -m homegraph.mcp_server` is gone. Use:\n"
        "    homegraph mcp --model NAME=PATH [--mesh-db PATH]\n"
        "One entry point, because two meant `--model m3` without a path was refused "
        "by one of them and accepted by the other.")
