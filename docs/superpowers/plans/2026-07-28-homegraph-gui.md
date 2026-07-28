# homegraph GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `homegraph gui` — a foreground HTTP server that serves a four-pane browser interface for exploring the corpus without knowing what you are looking for.

**Architecture:** A second transport over the answer layer that already exists. `mcp_server.Server`'s five methods return plain JSON-ready dicts; `gui.py` maps HTTP routes onto them and serves one page. All decisions are computed in Python; the page only draws what it is handed.

**Tech Stack:** Python 3.12, stdlib only (`http.server`, `json`, `webbrowser`, `sqlite3`). No frontend framework, no CDN, no build step.

**Spec:** `docs/superpowers/specs/2026-07-28-homegraph-gui-design.md` (approved 2026-07-28)

## Global Constraints

- **`dependencies = []` must survive.** No runtime dependency may be added, for the page or the server. `pyproject.toml` line 24 is the gate.
- **No existing module is modified except `cli.py`.** `visualize.py` and `mcp_server.py` are read and called, never edited. If a task appears to need an edit to either, stop and escalate — the design's whole claim is that this surface adds a transport, not a second opinion.
- **The page holds no decisions.** Filtering to file level, which nodes are isolated, which path won, what got truncated — all computed in Python and sent finished. The browser turns values into pixels and nothing else.
- **Bind 127.0.0.1 only.** No `--host` flag exists. The server exposes an entire home-directory corpus.
- **Every response carries `status` and `warnings` through unchanged.** Never strip, never summarise, never swallow. A confidence field nothing forces you to read is decoration.
- **Test convention:** checkpoint files use `from report import reporter` → `results, check = reporter(WIDTH)`, `t_*` helper functions driven by `main()` returning an exit code, plus a one-line `test_checkpoint_*` pytest adapter. Run standalone with `python3 tests/test_gui.py`. `pytest` is not in `.venv`; use `uvx` for `ruff`/`mypy`.
- **Fixtures must be copied into a worktree** before tests run there (`CONTRIBUTING.md`).

---

## The measurement this plan is built on

`visualize.collect` applies `LIMIT ?` **per model, before anything else**. M3 holds 6 928 nodes — 602 files among 6 035 sections, 65 tags and 226 wikilink stubs — so a capped read loses files, and which ones it loses depends on how `path` and `path#heading` keys interleave alphabetically.

Measured 2026-07-28 against the real stores:

| `limit_per_model` | Time | Nodes | Edges | **File nodes** |
|---|---|---|---|---|
| 2 000 | 0,06 s | 4 978 | 5 754 | **2 147** |
| uncapped | 0,06 s | 9 906 | 13 336 | **2 472** |

Peak RSS for the whole process: 32 MB.

**The cap buys nothing and costs 325 file nodes.** So `graph_payload` reads uncapped and filters in Python — no change to `collect`, and the filter never needed to be in SQL. What needed fixing was the ceiling, not the ordering.

Because that is a ceiling rather than a proof, Task 1 also carries a guard: any model that comes back at exactly the limit is reported as truncated rather than drawn as if complete.

---

## File Structure

| File | Responsibility |
|---|---|
| `homegraph/gui.py` (create) | Payload building + HTTP transport. No answer logic. |
| `homegraph/assets/gui.html` (create) | The page. Draws only. |
| `homegraph/cli.py` (modify) | `cmd_gui` + the `gui` subparser. |
| `pyproject.toml` (modify) | `package-data` gains `assets/*.html`. |
| `tests/test_gui.py` (create) | Checkpoint suite, standalone-runnable. |
| `tests/mutate_gui.py` (create) | Mutation harness in the existing shape. |

`homegraph/visualize.py` and `homegraph/mcp_server.py` appear in no row. That is the point.

---

## Task 0: Measure the `/path` cost before writing any of it

The spec names one unknown and says it must be measured, not assumed: 20 hits means 19 path searches at up to 4 hops per click. **This task writes no production code.** If the number comes back too slow, the design changes before it is built.

**Files:**
- Create: `scratchpad/measure_path_cost.py` (throwaway, not committed)
- Modify: `TODO.md` (record the result)

**Interfaces:**
- Consumes: `homegraph.mcp_server.Server`
- Produces: a measured milliseconds-per-click figure that Task 4 uses to set `DEFAULT_MAX_DEPTH` and `DEFAULT_CAP`

- [ ] **Step 1: Write the probe**

```python
#!/usr/bin/env python3
"""How long does one schematic click cost? Measures, changes nothing."""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/homegraph"))
from homegraph.mcp_server import Server

HOME = os.path.expanduser("~/.homegraph")
MODELS = {m: os.path.join(HOME, "real-%s.db" % m) for m in ("m1", "m2", "m3", "m4")}
MESH = os.path.join(HOME, "real-mesh.db")

server = Server(MODELS, mesh_db=MESH)
res = server.mesh_search("wikilink graph", limit=20)
hits = [h["node_key"] for h in res["hits"]]
print("%d hits, status=%s" % (len(hits), res["status"]))
if len(hits) < 2:
    print("corpus gave too few hits to measure; pick another query")
    sys.exit(1)

for depth in (2, 3, 4):
    src = hits[0]
    t0 = time.perf_counter()
    found = 0
    for dst in hits[1:]:
        out = server.mesh_path(src=src, dst=dst, max_depth=depth)
        if out.get("path"):
            found += 1
    ms = (time.perf_counter() - t0) * 1000
    print("max_depth=%d: %d calls, %.0f ms total, %.1f ms/call, %d path(s) found"
          % (depth, len(hits) - 1, ms, ms / (len(hits) - 1), found))
```

- [ ] **Step 2: Run it**

Run: `python3 scratchpad/measure_path_cost.py`
Expected: three lines, one per `max_depth`. No assertion — this is a measurement.

- [ ] **Step 3: Decide the default from the number**

Rule, fixed here so it is not argued after the fact:

- under 300 ms total at `max_depth=4` → `DEFAULT_MAX_DEPTH = 4`, `DEFAULT_CAP = 20`
- 300 ms to 2 s → `DEFAULT_MAX_DEPTH = 3`, `DEFAULT_CAP = 20`
- over 2 s → `DEFAULT_MAX_DEPTH = 2`, `DEFAULT_CAP = 10`

Whichever branch applies, the cap is reported in the `/path` response (Task 4) so a truncated view says it was truncated.

- [ ] **Step 4: Record it in TODO.md with the measured number**

Add a new section:

```markdown
## CP-GUI — GUI over MCP-svarlaget (2026-07-28)

Spec: `docs/superpowers/specs/2026-07-28-homegraph-gui-design.md`.
Plan: `docs/superpowers/plans/2026-07-28-homegraph-gui.md`.

- [x] **Task 0 — stikostnaden målt, ikke antatt.** <N> treff, max_depth 2/3/4 =
      <A>/<B>/<C> ms totalt per klikk. Valgt: max_depth <D>, tak <E>.
- [x] **Lesetaket målt.** `collect` med limit 2000 gir 2 147 filnoder, uten tak
      2 472 — 325 tapt, og begge lesningene tar 0,06 s. Derfor leser
      `graph_payload` uten tak; `visualize.py` er urørt.
```

- [ ] **Step 5: Commit the TODO.md change only**

```bash
git add TODO.md
git commit -m "Measure what a schematic click costs before building it

20 hits means 19 path searches per click and the cost was unknown. Now it
is <C> ms at max_depth 4, so the default is <D>."
```

---

## Task 1: `graph_payload` — file-level nodes, the isolated set, and a truncation guard

**Files:**
- Create: `homegraph/gui.py`
- Create: `tests/test_gui.py`

**Interfaces:**
- Consumes: `homegraph.visualize.collect` (called, never modified)
- Produces: `gui.FILE_KINDS: frozenset[str]`, `gui.NO_LIMIT: int`, `gui.graph_payload(model_paths, mesh_db=None, limit_per_model=NO_LIMIT) -> dict` with keys `nodes`, `edges`, `isolated`, `missing`, `counts`, `truncated`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gui.py`:

```python
#!/usr/bin/env python3
"""CP-GUI -- the GUI's payload builders and HTTP routes.

Every check here is about what Python decides, because the page decides
nothing. The one that ties this surface to an already-measured fact is
`t_isolated_matches_md_gaps`: the set `/graph` calls isolated must be the set
`isolated_notes` reports, or the GUI and `md gaps` can drift apart without
anyone saying so.

Run:
    python3 tests/test_gui.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter                                       # noqa: E402

from homegraph import gui                                          # noqa: E402
from homegraph.models.m3_build import isolated_notes               # noqa: E402
from homegraph.store import Store                                  # noqa: E402

results, check = reporter(50)


def t_file_kinds_are_the_measured_four():
    """document, image, file, code -- and nothing else.

    Named explicitly rather than derived from a suffix or a name: M1's
    `reference` (180 of them) and M4's `archive_entry` (69) are about files
    without being files, and any rule that guessed would take them.
    """
    check("FILE_KINDS is exactly the four file-bearing kinds",
          gui.FILE_KINDS == frozenset({"file", "document", "image", "code"}),
          repr(sorted(gui.FILE_KINDS)))


def t_all_file_nodes_survive_the_read(m3_db):
    """The measured defect, as a check.

    `collect` caps per model before anything else. At limit 2000, M3's 602
    files compete with its 6035 sections and 325 file nodes never arrive.
    """
    payload = gui.graph_payload({"m3": m3_db})
    check("every M3 file node reaches the payload",
          len(payload["nodes"]) == 602, "%d node(s)" % len(payload["nodes"]))


def t_capped_read_names_the_model_it_capped(m3_db):
    """The guard. A ceiling that does not announce itself is the defect."""
    payload = gui.graph_payload({"m3": m3_db}, limit_per_model=2000)
    check("a capped read reports which model was cut",
          payload["truncated"] == ["m3"], repr(payload["truncated"]))


def t_payload_drops_non_file_kinds(m3_db):
    payload = gui.graph_payload({"m3": m3_db})
    kinds = {n["kind"] for n in payload["nodes"]}
    check("no section, tag or wikilink node reaches the payload",
          kinds <= gui.FILE_KINDS, "kinds=%s" % sorted(kinds))


def t_isolated_matches_md_gaps(m3_db):
    """The cross-check. 315 of 602 on the real corpus."""
    with Store(m3_db) as store:
        gold_paths, gold_total = isolated_notes(store)
    payload = gui.graph_payload({"m3": m3_db})
    got = {n["path"] for n in payload["nodes"]
           if n["key"] in set(payload["isolated"])}
    check("isolated set equals isolated_notes()",
          got == set(gold_paths),
          "gui=%d gold=%d of %d" % (len(got), len(gold_paths), gold_total))
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `python3 tests/test_gui.py`
Expected: `ModuleNotFoundError: No module named 'homegraph.gui'`

- [ ] **Step 3: Write the implementation**

Create `homegraph/gui.py`:

```python
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
```

- [ ] **Step 4: Add the driver and adapter to `tests/test_gui.py`**

```python
def corpus():
    """The real M3 store when it exists, else say so and skip.

    The isolated cross-check needs a corpus with links in it; the synthetic
    fixture has fifteen declared relations and would pass trivially.
    """
    real = os.path.expanduser("~/.homegraph/real-m3.db")
    return real if os.path.exists(real) else None


def main():
    t_file_kinds_are_the_measured_four()
    m3_db = corpus()
    if m3_db:
        t_all_file_nodes_survive_the_read(m3_db)
        t_capped_read_names_the_model_it_capped(m3_db)
        t_payload_drops_non_file_kinds(m3_db)
        t_isolated_matches_md_gaps(m3_db)
    else:
        print("no real-m3.db -- payload checks skipped, and this line is the "
              "report that they were")

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `python3 tests/test_gui.py`
Expected: PASS, five checks, with `isolated set equals isolated_notes()` reporting `gui=315 gold=315 of 602`.

- [ ] **Step 6: Commit**

```bash
git add homegraph/gui.py tests/test_gui.py
git commit -m "Build the GUI's graph payload without capping the read

collect() caps per model before any filter runs, so at limit 2000 M3's 602
files lose 325 of themselves to its 6035 sections. Reading uncapped costs the
same 0.06s and 32MB, so the payload reads everything and filters in Python --
visualize.py is untouched. Any model that does come back at the cap is named
in `truncated` rather than drawn as if it were the whole corpus."
```

---

## Task 2: The HTTP transport and `GET /graph`

**Files:**
- Modify: `homegraph/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `gui.graph_payload` from Task 1
- Produces: `gui.build_handler(server, payload) -> type[BaseHTTPRequestHandler]`, `gui.serve(model_paths, mesh_db=None, port=0, open_browser=True) -> None`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui.py`, above `corpus()`:

```python
def _post(port, route, body):
    import json
    import urllib.request
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, route),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _running(model_paths, mesh_db=None):
    """Context manager yielding a live port on a real socket. No mocking."""
    import contextlib
    import threading
    from http.server import HTTPServer

    from homegraph.mcp_server import Server

    @contextlib.contextmanager
    def cm():
        payload = gui.graph_payload(model_paths, mesh_db=mesh_db)
        httpd = HTTPServer(("127.0.0.1", 0),
                           gui.build_handler(Server(model_paths,
                                                    mesh_db=mesh_db), payload))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()

    return cm()


def t_graph_route_serves_the_payload(m3_db):
    import json
    import urllib.request
    with _running({"m3": m3_db}) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/graph" % port, timeout=10) as resp:
            body = json.loads(resp.read())
    check("GET /graph returns the payload over HTTP",
          len(body["nodes"]) == 602 and len(body["isolated"]) == 315,
          "%d node(s), %d isolated" % (len(body["nodes"]),
                                       len(body["isolated"])))


def t_binds_loopback_only():
    """The address is not configurable, and that is the point."""
    import inspect
    src = inspect.getsource(gui.serve)
    params = list(inspect.signature(gui.serve).parameters)
    check("serve() hardcodes 127.0.0.1 and takes no host argument",
          "127.0.0.1" in src and "host" not in params, "params=%s" % params)
```

Register `t_binds_loopback_only()` next to the FILE_KINDS check and `t_graph_route_serves_the_payload(m3_db)` inside the `if m3_db:` branch.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_gui.py`
Expected: FAIL — `AttributeError: module 'homegraph.gui' has no attribute 'build_handler'`

- [ ] **Step 3: Implement the transport**

In `homegraph/gui.py`, extend the import block:

```python
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from .visualize import collect
```

Append at the end of the module:

```python
def build_handler(server, payload):
    """A request handler bound to one Server and one prebuilt graph payload.

    A factory rather than class attributes: two GUIs in one process would
    otherwise share whichever stores were configured last, and the tests run
    several handlers in the same interpreter.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_gui.py`
Expected: PASS, seven checks.

- [ ] **Step 5: Commit**

```bash
git add homegraph/gui.py tests/test_gui.py
git commit -m "Serve the graph payload over a foreground loopback socket

http.server from the stdlib, bound to 127.0.0.1 with no host flag, dying on
Ctrl-C. Same shape as watch.py, where codegraph's daemon was deliberately
not borrowed."
```

---

## Task 3: `POST /search` and `POST /query`, with `partial` carried through

**Files:**
- Modify: `homegraph/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `gui.build_handler` from Task 2
- Produces: `do_POST` routing for `/search`, `/query`, `/neighbors`; response bodies are the `Server` method's return value verbatim

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui.py`:

```python
def t_search_route_returns_hits(m3_db):
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/search", {"query": "wikilink", "limit": 5})
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
        out = _post(port, "/search", {"query": "wikilink", "limit": 5})
    check("a missing model makes the answer partial and names itself",
          out.get("status") == "partial"
          and "m9" in out.get("models_missing", []),
          "status=%s missing=%s" % (out.get("status"),
                                    out.get("models_missing")))


def t_query_route_refuses_unknown_model(m3_db):
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/query", {"model": "m9", "query": "NODES"})
    check("POST /query names the models it does have when refusing",
          out.get("status") == "error" and "m3" in out.get("error", ""),
          repr(out.get("error"))[:60])
```

Register all three inside the `if m3_db:` branch.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_gui.py`
Expected: FAIL — `HTTP Error 501: Unsupported method ('POST')`

- [ ] **Step 3: Implement `do_POST`**

Inside `_Handler`, after `do_GET`:

```python
        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self):
            routes = {"/search": server.mesh_search,
                      "/query": server.query,
                      "/neighbors": server.mesh_neighbors}
            fn = routes.get(self.path)
            if fn is None:
                self._send({"error": "no route %r" % self.path}, 404)
                return
            try:
                args = self._read_json()
            except ValueError as exc:
                self._send({"error": "bad JSON: %s" % exc}, 400)
                return
            try:
                # Returned verbatim. `status`, `warnings` and `models_missing`
                # are the answer's own account of how complete it is, and a
                # transport that summarised them would be deciding something.
                self._send(fn(**args))
            except TypeError as exc:
                self._send({"error": "bad arguments: %s" % exc}, 400)
```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_gui.py`
Expected: PASS, ten checks.

- [ ] **Step 5: Commit**

```bash
git add homegraph/gui.py tests/test_gui.py
git commit -m "Route search and query to the answer layer, verbatim

The response body is whatever Server returned, untouched: status, warnings
and models_missing are the answer's account of its own completeness, and a
transport that summarised them would be deciding something. The gate proves
a missing model still surfaces as partial with the model named."
```

---

## Task 4: `POST /path` — the schematic's bridges, capped and honest

**Files:**
- Modify: `homegraph/gui.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `Server.mesh_path`
- Produces: `gui.DEFAULT_MAX_DEPTH`, `gui.DEFAULT_CAP`, and `POST /path` accepting `{"src": key, "dsts": [key, ...], "max_depth": int, "cap": int}` returning `{"src", "bridges": [{"dst", "path"}], "unreachable": [key], "max_depth", "cap", "truncated": bool}`

Use the branch Task 0 selected for the two constants.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui.py`:

```python
def t_path_reports_unreachable_rather_than_omitting(m3_db):
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
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/path",
                    {"src": linked[0], "dsts": [linked[1]] + lonely})
    total = len(out.get("bridges", [])) + len(out.get("unreachable", []))
    check("an unreachable hit is named, not dropped",
          lonely[0] in out.get("unreachable", []) and total == 2,
          "unreachable=%s of %d dst(s)" % (len(out.get("unreachable", [])),
                                           total))


def t_path_cap_is_reported(m3_db):
    """A truncated view must say it was truncated."""
    payload = gui.graph_payload({"m3": m3_db})
    keys = [n["key"] for n in payload["nodes"]][:gui.DEFAULT_CAP + 5]
    with _running({"m3": m3_db}) as port:
        out = _post(port, "/path", {"src": keys[0], "dsts": keys[1:]})
    total = len(out.get("bridges", [])) + len(out.get("unreachable", []))
    check("over the cap, the response says truncated and how many it took",
          out.get("truncated") is True and total == gui.DEFAULT_CAP,
          "cap=%s took=%d truncated=%s" % (out.get("cap"), total,
                                           out.get("truncated")))
```

Register both inside the `if m3_db:` branch.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_gui.py`
Expected: FAIL — the response is `{"error": "no route '/path'"}` with status 404.

- [ ] **Step 3: Implement the loop**

Add beneath `NO_LIMIT` in `homegraph/gui.py`, with the values Task 0 selected:

```python
# Both set by the Task 0 measurement, not by taste. `DEFAULT_CAP` bounds how
# many bridges one click pays for; `truncated` in the response is what stops a
# capped view from reading as a complete one.
DEFAULT_MAX_DEPTH = 4          # <-- replace with the measured branch
DEFAULT_CAP = 20               # <-- replace with the measured branch
```

Add the module-level helper:

```python
def bridges(server, args):
    """Every path from one node to the other hits, capped and honest about it.

    The loop lives here rather than in the page for the same reason every
    other decision does: what Python decides is under test. `mesh_path`
    returns null outside `max_depth`, and that null becomes a named entry in
    `unreachable` -- a hit with no bridge is a finding, and dropping it would
    let the schematic draw four of five and look complete.
    """
    src = args["src"]
    dsts = list(args.get("dsts") or [])
    depth = int(args.get("max_depth") or DEFAULT_MAX_DEPTH)
    cap = int(args.get("cap") or DEFAULT_CAP)
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
```

And route to it as the first branch of `do_POST`:

```python
        def do_POST(self):
            if self.path == "/path":
                try:
                    args = self._read_json()
                except ValueError as exc:
                    self._send({"error": "bad JSON: %s" % exc}, 400)
                    return
                self._send(bridges(server, args))
                return
            routes = {"/search": server.mesh_search,
                      ...
```

- [ ] **Step 4: Run the tests**

Run: `python3 tests/test_gui.py`
Expected: PASS, twelve checks.

- [ ] **Step 5: Commit**

```bash
git add homegraph/gui.py tests/test_gui.py
git commit -m "Compute the schematic's bridges server-side, capped and honest

A hit with no path comes back named in `unreachable` rather than dropped:
absence of a path is an answer, and a schematic that quietly drew four of
five hits would look complete. Over the cap the response says truncated."
```

---

## Task 5: The page, the CLI subcommand, and the package data

**Files:**
- Create: `homegraph/assets/gui.html`
- Modify: `homegraph/gui.py` (serve the page at `/`)
- Modify: `homegraph/cli.py` (add `cmd_gui` near `cmd_visualize` at line 356, and the subparser near line 1455)
- Modify: `pyproject.toml:30`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: every route from Tasks 2–4
- Produces: `homegraph gui --model NAME=PATH [--mesh-db PATH] [--port N] [--no-browser]`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gui.py`:

```python
def t_page_is_shipped_and_self_contained(m3_db):
    """No CDN, no external fetch. The page must work with the network down."""
    import re
    import urllib.request
    page = os.path.join(os.path.dirname(gui.__file__), "assets", "gui.html")
    check("assets/gui.html ships with the package", os.path.exists(page), page)
    if not os.path.exists(page):
        return
    text = open(page, encoding="utf-8").read()
    external = re.findall(r"""(?:src|href)\s*=\s*["']https?://[^"']+""", text)
    check("the page references no external host",
          not external, "%d external reference(s)" % len(external))
    with _running({"m3": m3_db}) as port:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/" % port, timeout=10) as resp:
            served = resp.read().decode("utf-8")
    check("GET / serves that same page", served == text,
          "%d byte(s) served, %d on disk" % (len(served), len(text)))
```

Register it inside the `if m3_db:` branch.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 tests/test_gui.py`
Expected: FAIL — the assets path does not exist.

- [ ] **Step 3: Write the page**

Create `homegraph/assets/gui.html`: one file, inline `<style>`, inline `<script>`, `<canvas>` for the graph, `<svg>` for the schematic. No external references of any kind.

```html
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<title>homegraph</title>
<style>
  html, body { margin: 0; height: 100%; font: 13px/1.4 system-ui, sans-serif; }
  #app { display: grid; height: 100%;
         grid-template-columns: 20% 60% 20%;
         grid-template-areas: "h1 mid h3"; }
  #h1 { grid-area: h1; overflow: auto; border-right: 1px solid #ccc; }
  #mid { grid-area: mid; display: grid; grid-template-rows: 50% 50%; }
  #h3 { grid-area: h3; overflow: auto; border-left: 1px solid #ccc; }
  #v1, #v2 { position: relative; overflow: hidden; }
  #v2 { border-top: 1px solid #ccc; }
  .waiting { display: grid; place-items: center; height: 100%; color: #888; }
  .partial { background: #fff3cd; padding: 4px 8px; }
</style>
</head>
<body>
<div id="app">
  <div id="h1">
    <input id="q" placeholder="søk">
    <select id="mode"><option value="search">hybrid</option>
                      <option value="query">lukket språk</option></select>
    <div id="summary"></div>
  </div>
  <div id="mid">
    <div id="v1"><canvas id="graph"></canvas></div>
    <div id="v2"><div class="waiting">kjør et søk først</div></div>
  </div>
  <div id="h3"><div id="status"></div><ol id="hits"></ol></div>
</div>
<script>
const state = { filter: null, search: null, selection: null };
// The page draws. Every decision -- which nodes are files, which are isolated,
// which path won, what was truncated -- arrived already made from Python.
</script>
</body>
</html>
```

Fill in the script to:

- fetch `/graph` once on load and lay the nodes out **once**, never again;
- render the isolated band along the bottom of `#v1` with its count and share, from `payload.isolated`;
- show `payload.truncated` and `payload.missing` in `#status` with the `.partial` class when either is non-empty;
- on search, POST `/search` (or `/query` per `#mode`), recolour hits and dim the rest **without recomputing any layout**;
- on click, set `state.selection`, POST `/path` with the other hits as `dsts`, and render `bridges` in `#v2` — drawing every `unreachable` entry as a detached node rather than omitting it, and showing `truncated` when set;
- keep `#v2` in its waiting state until `state.search` is non-empty **and** `state.selection` is set, naming which of the two is missing.

- [ ] **Step 4: Serve it**

Extend `do_GET` in `homegraph/gui.py`:

```python
        def do_GET(self):
            if self.path == "/graph":
                self._send(payload)
            elif self.path in ("/", "/index.html"):
                page = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "assets", "gui.html")
                with open(page, "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send({"error": "no route %r" % self.path}, 404)
```

- [ ] **Step 5: Wire the CLI**

In `homegraph/cli.py`, next to `cmd_visualize`:

```python
def cmd_gui(args):
    from .gui import serve

    serve(parse_model_specs(args.model), mesh_db=args.mesh_db,
          port=args.port, open_browser=not args.no_browser)
    return 0
```

Use whichever model-spec parser the neighbouring commands use — `cmd_mcp` shows the established call. Add the subparser next to the `visualize` one:

```python
    p = sub.add_parser("gui", help="explore the corpus in a browser; "
                                   "foreground, stops on Ctrl-C")
    p.add_argument("--model", action="append", required=True,
                   metavar="NAME=PATH")
    p.add_argument("--mesh-db", dest="mesh_db", default=None,
                   help="also load the code inventory and cross-model edges")
    p.add_argument("--port", type=int, default=0,
                   help="0 picks a free one and prints it")
    p.add_argument("--no-browser", dest="no_browser", action="store_true",
                   help="print the URL instead of opening it")
    p.set_defaults(func=cmd_gui)
```

There is deliberately no `--host`.

- [ ] **Step 6: Ship the page as package data**

In `pyproject.toml`, line 30:

```toml
homegraph = ["rules/*.toml", "assets/*.html"]
```

- [ ] **Step 7: Run the tests**

Run: `python3 tests/test_gui.py`
Expected: PASS, fifteen checks.

- [ ] **Step 8: Run it for real and look at it**

Run: `python3 -m homegraph gui --model m3=$HOME/.homegraph/real-m3.db --mesh-db $HOME/.homegraph/real-mesh.db`
Expected: a URL, a browser, 602 nodes with 315 in the isolated band. Search, click a hit, see bridges. Ctrl-C stops it and nothing survives.

- [ ] **Step 9: Commit**

```bash
git add homegraph/assets/gui.html homegraph/gui.py homegraph/cli.py pyproject.toml tests/test_gui.py
git commit -m "Ship the page and the gui subcommand

One file, no external host, no build step. dependencies = [] survives. There
is no --host flag: this serves a whole home directory's corpus, and a flag
that could publish it is a flag somebody passes by accident."
```

---

## Task 6: The mutation harness

**Files:**
- Create: `tests/mutate_gui.py`

**Interfaces:**
- Consumes: `tests/test_gui.py` — each mutation names the check that must kill it
- Produces: nothing importable; a harness, run standalone

- [ ] **Step 1: Write the harness**

Create `tests/mutate_gui.py`:

```python
#!/usr/bin/env python3
"""Mutation test for CP-GUI.

Five of these aim at one class of defect: a transport that quietly improves
the answer it is carrying. That is the failure the whole design is arranged
against, and it is invisible to any check that only counts results.

Run:
    python3 tests/mutate_gui.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # The measured defect. At 2000, M3's 602 files lose 325 of themselves to
    # its 6035 sections -- and the picture still looks like a corpus.
    ("the read is capped again",
     "homegraph/gui.py",
     "NO_LIMIT = 10 ** 9",
     "NO_LIMIT = 2000  # mutated: cap the read",
     "every M3 file node reaches the payload"),

    # A ceiling that does not announce itself is the defect, not the ceiling.
    ("the truncation guard always reports nothing",
     "homegraph/gui.py",
     "    truncated = sorted(m for m, c in raw_per_model.items()\n"
     "                       if c >= limit_per_model)",
     "    truncated = []  # mutated",
     "a capped read reports which model was cut"),

    # 6035 section nodes back in the payload.
    ("FILE_KINDS gains section",
     "homegraph/gui.py",
     'FILE_KINDS = frozenset({"file", "document", "image", "code"})',
     'FILE_KINDS = frozenset({"file", "document", "image", "code", "section"})',
     "FILE_KINDS is exactly the four file-bearing kinds"),

    # Provenance swallowed. The response still looks like a good answer.
    ("status is normalised to complete on the way out",
     "homegraph/gui.py",
     "                self._send(fn(**args))",
     "                _r = fn(**args); _r['status'] = 'complete'; self._send(_r)",
     "a missing model makes the answer partial and names itself"),

    # An unreachable hit dropped instead of named: four of five hits drawn,
    # and the schematic looks complete.
    ("unreachable hits are dropped rather than named",
     "homegraph/gui.py",
     "            unreachable.append(dst)",
     "            pass  # mutated: silently drop the hit with no bridge",
     "an unreachable hit is named, not dropped"),

    # Truncation that does not announce itself, on the other axis.
    ("the path cap stops announcing itself",
     "homegraph/gui.py",
     "    truncated = len(dsts) > cap",
     "    truncated = False  # mutated",
     "over the cap, the response says truncated and how many it took"),

    # The host flag that must not exist.
    ("serve() gains a host argument",
     "homegraph/gui.py",
     "def serve(model_paths, mesh_db=None, port=0, open_browser=True):",
     "def serve(model_paths, mesh_db=None, port=0, open_browser=True,"
     " host='0.0.0.0'):",
     "serve() hardcodes 127.0.0.1 and takes no host argument"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                             # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_gui.py", prefix="mutgui-", timeout=300))
```

The driver call is copied from `tests/mutate_cp2.py`'s last line in shape: the test filename carries **no** `tests/` prefix, and both `prefix` and `timeout` are passed.

- [ ] **Step 2: Run it**

Run: `python3 tests/mutate_gui.py`
Expected: 7/7 killed, 0 survivors, 0 misattributed. **A survivor is a finding, not a nuisance** — it means the named check does not actually bite, and the fix is the check, not the mutation.

- [ ] **Step 3: Run the whole suite and the linters**

```bash
for f in tests/test_*.py; do python3 "$f" >/dev/null || echo "RED: $f"; done
uvx ruff check homegraph/ tests/
uvx mypy homegraph/
```

Expected: no `RED:` lines except `test_no_real_paths.py` when the unpublished material is absent, ruff clean, mypy clean.

- [ ] **Step 4: Record the result in TODO.md**

Fill in the CP-GUI section from Task 0 with the outcome — checks passed, mutations killed, suites green — with the numbers, not a tick.

- [ ] **Step 5: Commit**

```bash
git add tests/mutate_gui.py TODO.md
git commit -m "Gate the GUI against the defect class it could introduce

Seven mutations, five of them aimed at a transport quietly improving the
answer it carries: the read capped again, a truncation guard that reports
nothing, status normalised to complete, an unreachable hit dropped, a path
cap that stops announcing itself."
```

---

## Self-review notes

**Spec coverage.** Purpose and panes → Task 5; architecture and route table → Tasks 2–4; run mode → Task 2; state and data flow → Task 5; v1 file level and isolated band → Tasks 1, 5; v2 bridges and the two-condition gate → Tasks 4–5; error handling → Task 3 (`partial`), Task 4 (no path, cap), Tasks 1–2 (truncated read, loopback); gates → Tasks 1–6; the one named unknown → Task 0.

**Spec fidelity restored.** An earlier draft modified `visualize.py` to push a kind filter into SQL. Measurement showed the filter was never the problem — the per-model cap was — and reading uncapped costs the same 0,06 s. `visualize.py` and `mcp_server.py` are now touched by no task, as the spec says.

**Known gap, stated rather than hidden.** The spec's ceiling — that nothing here tests whether the page *draws* correctly — is unchanged. Task 5 Step 8 is a human looking at it, which is not a gate. Task 6's mutations reach the payloads and the routes, never the canvas.

**Deliberately ungated:** the h1 corpus summary is rendered from `payload["counts"]`, a sum over the same list the isolated cross-check validates. If it grows a rule of its own, it needs a gate of its own.
