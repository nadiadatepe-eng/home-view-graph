#!/usr/bin/env python3
"""Mutation test for CP-GUI.

Task 6's brief named seven mutations, written before the GUI existed. Tasks
1-5 then proved roughly twenty more ad hoc while fixing review findings --
each one run once, confirmed red on a named check, and thrown away. This file
is every one of them, consolidated: the brief's seven (rewritten against the
code as it now stands -- `BadArgument`, `_positions`, `band_divider`,
`search_limit` and the shipped page did not exist when the brief was
written), the M1-M20 table from `task-5-report.md`'s two fix rounds, and five
more recovered from the fix rounds in `task-2-report.md` through
`task-4-report.md` that were never written down as a harness entry.

Where a brief mutation and an M-numbered one target the same code, there is
one entry, not two. `visualize.py` and `mcp_server.py` are never targeted --
two mutations that would naturally have lived there (M2's random seed, M10's
`iterations`) are rewritten to mutate `_positions`'s own call site in
`gui.py` instead, since that call site is this module's business and the
functions it calls are not.

M2 counts rather than randomises, and the difference was measured. It used
`random.randrange(99)`, and `_layout` is pure in its seed -- so the two builds
the determinism gate compares came out IDENTICAL whenever the two draws
collided, once in 99 runs. The mutation was still detected, by the HTTP gate
that compares an in-process payload against the server's, so it read as a
misattribution rather than as a survivor: named correctly in six full sweeps on
2026-08-01 and differently in the seventh, on an untouched file. A mutation has
to exercise the defect it names EVERY time, or its verdict is a coin. An
incrementing counter cannot collide.

Run:
    python3 tests/mutate_gui.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # -- the brief's seven, rewritten against the current code ------------

    # The measured defect. At 2000, M3's 602 files lose to its 6035 sections
    # -- the picture still looks like a corpus.
    ("the read is capped again",
     "homegraph/gui.py",
     "NO_LIMIT = 10 ** 9",
     "NO_LIMIT = 2000  # mutated: cap the read",
     "GET /graph serves the real corpus (602 nodes, 315 isolated)"),

    # A ceiling that does not announce itself is the defect, not the ceiling.
    ("the truncation guard always reports nothing",
     "homegraph/gui.py",
     "    truncated = sorted(m for m, c in raw_per_model.items()\n"
     "                       if c >= limit_per_model)",
     "    truncated = []  # mutated",
     "a capped read reports which model was cut"),

    ("FILE_KINDS gains section",
     "homegraph/gui.py",
     'FILE_KINDS = frozenset({"file", "document", "image", "code"})',
     'FILE_KINDS = frozenset({"file", "document", "image", "code", "section"})',
     "FILE_KINDS is exactly the four file-bearing kinds"),

    # Provenance swallowed on the way out. `self._send(result)` is the one
    # place all four routes' answers leave the process -- the brief's needle
    # (`self._send(fn(**args))`) predates `_run` and no longer exists.
    ("status is normalised to complete on the way out",
     "homegraph/gui.py",
     "                if result is not None:\n"
     "                    self._send(result)",
     "                if isinstance(result, dict) and \"status\" in result:\n"
     "                    result[\"status\"] = \"complete\"  # mutated\n"
     "                if result is not None:\n"
     "                    self._send(result)",
     "a missing model makes the answer partial and names itself"),

    ("unreachable hits are dropped rather than named",
     "homegraph/gui.py",
     "            unreachable.append(dst)",
     "            pass  # mutated: silently drop the hit with no bridge",
     "an unreachable hit is named, not dropped"),

    ("the path cap stops announcing itself",
     "homegraph/gui.py",
     "    truncated = len(dsts) > cap",
     "    truncated = False  # mutated",
     "over the cap, the response says truncated and how many it took"),

    ("serve() gains a host argument",
     "homegraph/gui.py",
     "def serve(model_paths, mesh_db=None, port=0, open_browser=True):",
     "def serve(model_paths, mesh_db=None, port=0, open_browser=True,"
     " host='0.0.0.0'):",
     "serve() hardcodes 127.0.0.1 and takes no host argument"),

    # -- M1-M7 (task-5-report.md, "the page, the gui subcommand") ---------

    # M1. Without the band override, an isolated node keeps whatever
    # `_positions` left it at (its (0.0, 0.0) default, since it is not fed to
    # `_layout`) instead of landing in the sorted band below the cloud.
    ("the band override is removed, isolated nodes keep the default position",
     "homegraph/gui.py",
     "    for k, i in enumerate(band):\n"
     "        pos[i] = (round(x0 + (k % cols) * step, 1),\n"
     "                  round(top + (k // cols) * row_height, 1))\n"
     "    return pos, divider",
     "    pass  # mutated: M1 band override removed\n"
     "    return pos, divider",
     "the band sits below every connected node"),

    # M2, rewritten: the seed lives in `visualize._layout`'s default and that
    # file is out of scope, so this mutates the one place `gui.py` controls
    # it -- the call site -- to draw a fresh one every time instead.
    ("_positions calls _layout with a fresh random seed every time",
     "homegraph/gui.py",
     "    laid = _layout([out_nodes[i] for i in order],\n"
     "                   [(slot[a], slot[b]) for a, b, *_ in out_edges])",
     "    import itertools as _it  # mutated: M2, a fresh seed every call\n"
     "    global _MUT_SEED\n"
     "    try:\n"
     "        _MUT_SEED\n"
     "    except NameError:\n"
     "        _MUT_SEED = _it.count()\n"
     "    laid = _layout([out_nodes[i] for i in order],\n"
     "                   [(slot[a], slot[b]) for a, b, *_ in out_edges],\n"
     "                   seed=next(_MUT_SEED))",
     "two builds of the same corpus place every node identically"),

    ("isolated_share hardcoded to 0.0",
     "homegraph/gui.py",
     "    share = round(100.0 * len(isolated) / len(out_nodes), 1) if out_nodes else 0.0",
     "    share = 0.0  # mutated: M3",
     "the payload carries the band's own count and share"),

    ("--host added to the gui subparser",
     "homegraph/cli.py",
     '    p.add_argument("--no-browser", dest="no_browser", action="store_true",\n'
     '                   help="print the URL instead of opening it")\n'
     "    p.set_defaults(func=cmd_gui)",
     '    p.add_argument("--no-browser", dest="no_browser", action="store_true",\n'
     '                   help="print the URL instead of opening it")\n'
     '    p.add_argument("--host", default="127.0.0.1")  # mutated: M4\n'
     "    p.set_defaults(func=cmd_gui)",
     "the gui subcommand forwards its arguments and refuses --host"),

    ("GET / truncates the served file by one byte",
     "homegraph/gui.py",
     "            with open(page, \"rb\") as fh:\n"
     "                body = fh.read()",
     "            with open(page, \"rb\") as fh:\n"
     "                body = fh.read()[:-1]  # mutated: M5",
     "GET / serves that same page"),

    ("the schematic's three waiting states collapse to one message",
     "homegraph/assets/gui.html",
     '    const what = noSearch && noSel ? "kjør et søk, og velg så en node"\n'
     '               : noSearch ? "mangler: et søk med treff (en node er valgt)"\n'
     '               : "mangler: en valgt node (søket har treff)";',
     '    const what = "kjør et søk, og velg så en node";  // mutated: M6',
     "the schematic names which of its two conditions is missing"),

    ("the unreachable push is removed from the schematic",
     "homegraph/assets/gui.html",
     "  for (const d of bridgeOut.unreachable) rows.push({ path: null, dst: d });",
     "  // mutated: M7, unreachable hits dropped from the schematic",
     "the schematic draws every unreachable hit and says when capped"),

    # -- M8-M18 (task-5-report.md fix round 1) -----------------------------

    ("esc() drops quote escaping",
     "homegraph/assets/gui.html",
     '  return d.innerHTML.replace(/\'/g, "&#39;").replace(/"/g, "&quot;");',
     "  return d.innerHTML;  // mutated: M8, quotes no longer escaped",
     "an apostrophe in a filename survives into data-key"),

    ("runSearch drops the status guard on /search",
     "homegraph/assets/gui.html",
     '  const out = await post("/search",\n'
     "                         { query: text, limit: payload.search_limit });\n"
     '  if (out.status !== 200) { renderError(out); return; }',
     '  const out = await post("/search",\n'
     "                         { query: text, limit: payload.search_limit });\n"
     "  // mutated: M9, status guard dropped",
     "a non-200 answer is an error, not an empty result set"),

    ("runSearch drops the status guard on /query",
     "homegraph/assets/gui.html",
     '    const out = await post("/query", { model: $("model").value, query: text });\n'
     '    if (out.status !== 200) { renderError(out); return; }\n'
     "    renderRows(out.body);",
     '    const out = await post("/query", { model: $("model").value, query: text });\n'
     "    renderRows(out.body);  // mutated: M9b, status guard dropped on /query",
     "a non-200 answer is an error, not an empty result set"),

    # M10, rewritten the same way as M2: `iterations` is retuned at the
    # `_layout` call site in `gui.py`, not in `visualize.py`.
    ("_positions calls _layout with a retuned iteration count",
     "homegraph/gui.py",
     "    laid = _layout([out_nodes[i] for i in order],\n"
     "                   [(slot[a], slot[b]) for a, b, *_ in out_edges])",
     "    laid = _layout([out_nodes[i] for i in order],\n"
     "                   [(slot[a], slot[b]) for a, b, *_ in out_edges],\n"
     "                   iterations=90)  # mutated: M10",
     "the layout still places the fixture where it was measured"),

    ("BAND_GAP retuned to 40",
     "homegraph/gui.py",
     "BAND_GAP = 140.0",
     "BAND_GAP = 40.0  # mutated: M11",
     "the layout still places the fixture where it was measured"),

    ("models_missing push deleted from renderHits",
     "homegraph/assets/gui.html",
     '  if (missing.length) parts.push("søket nådde ikke " + missing.join(", "));',
     "  // mutated: M12, models_missing push deleted",
     "warnings, models_missing and the payload's own limit are used"),

    ("warnings loop deleted from renderHits",
     "homegraph/assets/gui.html",
     "  for (const w of warn) parts.push(w);",
     "  // mutated: M13, warnings loop deleted",
     "warnings, models_missing and the payload's own limit are used"),

    ("the model::node_key join is broken in renderHits",
     "homegraph/assets/gui.html",
     '    const key = h.model + "::" + h.node_key;',
     '    const key = h.node_key;  // mutated: M14, model prefix dropped',
     "a hit's key is joined as model::node_key"),

    ("the k !== key self-exclusion guard is dropped from select()",
     "homegraph/assets/gui.html",
     "    if (k !== key) dsts.push(k);",
     "    dsts.push(k);  // mutated: M15, no self-exclusion guard",
     "a click asks for bridges to the OTHER hits, and only those"),

    ("the /search limit is hardcoded to 20 instead of read from the payload",
     "homegraph/assets/gui.html",
     "                         { query: text, limit: payload.search_limit });",
     "                         { query: text, limit: 20 });  // mutated: M16",
     "warnings, models_missing and the payload's own limit are used"),

    ("<title> becomes a sibling of a self-closed bridge circle",
     "homegraph/assets/gui.html",
     "        svg += \"<circle cx='\" + x + \"' cy='\" + y + \"' r='5' fill='\" +\n"
     "               (j === 0 ? \"#1a73e8\" : j === r.path.length - 1 ? \"#d64545\"\n"
     "                                    : \"#8899aa\") + \"'>\" +\n"
     "               \"<title>\" + esc(k) + \"</title></circle>\" +",
     "        svg += \"<circle cx='\" + x + \"' cy='\" + y + \"' r='5' fill='\" +\n"
     "               (j === 0 ? \"#1a73e8\" : j === r.path.length - 1 ? \"#d64545\"\n"
     "                                    : \"#8899aa\") + \"'/>\" +  // mutated: M17\n"
     "               \"<title>\" + esc(k) + \"</title>\" +",
     "every schematic circle carries its key in a child <title>"),

    ("<title> becomes a sibling of the self-closed unreachable source circle",
     "homegraph/assets/gui.html",
     "      svg += \"<circle cx='20' cy='\" + y + \"' r='5' fill='#1a73e8'>\" +\n"
     "             \"<title>\" + esc(bridgeOut.src) + \"</title></circle>\" +",
     "      svg += \"<circle cx='20' cy='\" + y + \"' r='5' fill='#1a73e8'/>\" +"
     "  // mutated: M17b\n"
     "             \"<title>\" + esc(bridgeOut.src) + \"</title>\" +",
     "every schematic circle carries its key in a child <title>"),

    ("the page fetches a script from a CDN",
     "homegraph/assets/gui.html",
     "const state = { filter: { models: new Set(), kinds: new Set() },\n"
     "                search: null, selection: null };",
     "const state = { filter: { models: new Set(), kinds: new Set() },\n"
     "                search: null, selection: null };\n"
     'const _cdn = "https://cdn.example.com/d3.v7.js";  // mutated: M18',
     "the page references no external host"),

    # -- M19-M20 (task-5-report.md fix round 2) -----------------------------

    ("boot()'s status guard on /graph is removed",
     "homegraph/assets/gui.html",
     "  if (resp.status !== 200) {",
     "  if (false) {  // mutated: M19, boot() never checks status",
     "a /graph that fails says so instead of drawing nothing"),

    ("the #schematic sizing rule is reinstated",
     "homegraph/assets/gui.html",
     "  .lead { color: #555; margin: 0 0 4px 0; }",
     "  #schematic { width: 100%; height: 100%; }  /* mutated: M20 */\n"
     "  .lead { color: #555; margin: 0 0 4px 0; }",
     "no CSS rule overrides the schematic's own size"),

    # -- recovered from task-2/3/4 fix rounds, never written down as a
    # -- harness entry -------------------------------------------------------

    # task-2 fix round 1: `missing`/`truncated` were only ever asserted
    # non-empty after the round that added a full-body equality check --
    # nothing before this proved the route could not quietly drop them.
    ("GET /graph drops missing and truncated on the way out",
     "homegraph/gui.py",
     "            if self.path == \"/graph\":\n"
     "                self._run(lambda: payload)",
     "            if self.path == \"/graph\":\n"
     "                self._run(lambda: {k: v for k, v in payload.items()\n"
     "                                   if k not in (\"missing\", \"truncated\")})"
     "  # mutated",
     "GET /graph carries missing and truncated over the wire, not just nodes"),

    # task-3 fix round 1, Important 1: before the pre-call `bind()` step
    # existed, an unknown keyword reached the tool body and raised a
    # `TypeError` the same broad `except` used to blame on the caller. This
    # reopens the gap by deleting the bind step outright.
    ("do_POST's pre-call argument binding is removed",
     "homegraph/gui.py",
     "            try:\n"
     "                args = self._read_json()\n"
     "            except ValueError as exc:\n"
     "                self._send({\"error\": \"bad JSON: %s\" % exc}, 400)\n"
     "                return\n"
     "            # Bindingen prøves for seg (samme løsning som mcp_server.py sitt\n"
     "            # tools/call): da `except TypeError` lå rundt selve kallet,\n"
     "            # dekket den hele verktøykroppen, og en TypeError dypt inne i\n"
     "            # f.eks. mesh_search ble rapportert som klientens skyld. Nå kan\n"
     "            # bare en ekte signaturmismatch gi \"bad arguments\".\n"
     "            try:\n"
     "                inspect.signature(fn).bind(**args)\n"
     "            except TypeError as exc:\n"
     "                self._send({\"error\": \"bad arguments: %s\" % exc}, 400)\n"
     "                return\n"
     "            self._run(lambda: fn(**args))",
     "            try:\n"
     "                args = self._read_json()\n"
     "            except ValueError as exc:\n"
     "                self._send({\"error\": \"bad JSON: %s\" % exc}, 400)\n"
     "                return\n"
     "            self._run(lambda: fn(**args))"
     "  # mutated: no pre-call binding check",
     "an unknown argument name is a 400 with an error, not a tool result"),

    # task-4 fix round 1, Important 2: `/path` used to bypass the shared
    # binding path entirely and read `args["src"]` directly, so a missing
    # `src` raised `KeyError` deep inside and came back a 500. Giving `src`
    # a default reopens the same shape of bug through today's signature.
    ("bridges()'s src parameter gets a default, reopening missing-src-as-500",
     "homegraph/gui.py",
     "def bridges(server, src, dsts=None, max_depth=None, cap=None):",
     "def bridges(server, src=None, dsts=None, max_depth=None, cap=None):"
     "  # mutated",
     "a /path call missing src is a 400, not a 500"),

    # task-4 fix round 1, Important 3: the 500 branch used to print nothing
    # to stderr, so an internal failure left no trace anywhere in the
    # process's own output once `log_message` was silenced.
    ("traceback.print_exc() is removed from the 500 branch",
     "homegraph/gui.py",
     "            except Exception as exc:                            # noqa: BLE001\n"
     "                traceback.print_exc()",
     "            except Exception as exc:                            # noqa: BLE001\n"
     "                pass  # mutated: no traceback printed",
     "a TypeError from inside a route body is a 500 with a traceback, not a 400"),

    # task-4 fix round 2, "New Important": catching TypeError alongside
    # BadArgument reopens a9b8022 for every route -- a genuine bug inside a
    # route body is reported as the caller's fault again.
    ("_run catches TypeError alongside BadArgument, reopening a9b8022",
     "homegraph/gui.py",
     "            except BadArgument as exc:",
     "            except (BadArgument, TypeError) as exc:"
     "  # mutated: reopens a9b8022",
     "a TypeError from inside a route body is a 500 with a traceback, not a 400"),

    # -- fix round 1 of this task: two proven-but-unguarded findings --------

    # task-3 fix round 1, Important 2: the checks only ever read three of
    # `mesh_search`'s five keys. A transport that drops `warnings` and
    # `models_queried` on the way out passed every one of them.
    ("do_POST drops warnings and models_queried from the response",
     "homegraph/gui.py",
     "                if result is not None:\n"
     "                    self._send(result)",
     "                if isinstance(result, dict):\n"
     "                    result = {k: v for k, v in result.items()\n"
     "                             if k not in (\"warnings\", \"models_queried\")}"
     "  # mutated\n"
     "                if result is not None:\n"
     "                    self._send(result)",
     "POST /search returns mesh_search's response verbatim, all five keys"),

    # task-4 fix round 2, the isinstance guard: without it, `dsts: "abc"`
    # silently became three one-character destinations and `dsts: 5` was a
    # 500, not a 400.
    ("bridges()'s isinstance(dsts, list) guard is removed",
     "homegraph/gui.py",
     "    if dsts is None:\n"
     "        dsts = []\n"
     "    elif not isinstance(dsts, list):\n"
     "        # `list(\"abc\")` silently becomes three one-character destinations\n"
     "        # instead of refusing a caller who meant one; `list(5)` raises\n"
     "        # `TypeError`, which past this point reads as an internal bug, not a\n"
     "        # caller mistake. Both are wrong shapes, checked explicitly instead\n"
     "        # of relying on either accident.\n"
     "        raise BadArgument(\"dsts must be a list of node keys, got %s\"\n"
     "                          % type(dsts).__name__)",
     "    dsts = list(dsts or [])  # mutated: no isinstance guard",
     "a /path call with a non-list dsts is a 400, not a 500"),

    # -- the final review's three must-fixes, plus the gate on `counts` -----

    # renderRows was the third writer to #status and the one that forgot the
    # corpus banner. Reinstated here as the private `parts` it used to build.
    ("renderRows stops carrying the corpus banner",
     "homegraph/assets/gui.html",
     "  const parts = payloadParts();\n"
     "  if (out.status === \"error\" || out.status === \"refused\") {",
     "  const parts = [];  // mutated: renderRows drops missing/truncated\n"
     "  if (out.status === \"error\" || out.status === \"refused\") {",
     "the closed language's table counts its rows and keeps the banner"),

    # GET's missing error path: back to the bare `_send` that gave the client
    # `RemoteDisconnected` with no status and no body.
    ("do_GET answers /graph without a guard around it",
     "homegraph/gui.py",
     "            if self.path == \"/graph\":\n"
     "                self._run(lambda: payload)",
     "            if self.path == \"/graph\":\n"
     "                self._send(payload)  # mutated: no error path on GET",
     "GET /graph that cannot be serialised is a 500 with a body"),

    # The schematic answering about a node it does not name -- the defect the
    # band's 3,65 px node spacing makes routine rather than theoretical.
    ("the schematic's lead stops naming its source node",
     "homegraph/assets/gui.html",
     "  const lead = bridgeOut.src + \" — \" + bridgeOut.bridges.length + \" bro(er), \" +",
     "  const lead = bridgeOut.bridges.length + \" bro(er), \" +"
     "  // mutated: lead no longer names src",
     "the schematic's lead names the node the bridges start from"),

    # `counts` is the whole of h1's summary and went 19 commits with nothing
    # asserting it. One per model regardless of how many nodes there are.
    ("counts reports one node per model",
     "homegraph/gui.py",
     "        counts[n[\"model\"]] = counts.get(n[\"model\"], 0) + 1",
     "        counts[n[\"model\"]] = 1  # mutated",
     "counts is the per-model node count h1's summary prints"),

    # -- step 2a, the filters in h1 ----------------------------------------

    ("kind_counts reports one node per kind",
     "homegraph/gui.py",
     "        kind_counts[n[\"kind\"]] = kind_counts.get(n[\"kind\"], 0) + 1",
     "        kind_counts[n[\"kind\"]] = 1  # mutated",
     "kind_counts is the per-kind node count, and kinds names them"),

    # The filter that filters nothing -- the shape a "hides everything"
    # check could not tell from a working one, which is why the page harness
    # runs two models.
    ("visible() stops consulting the filter",
     "homegraph/assets/gui.html",
     "  return !state.filter.models.has(n.model) && !state.filter.kinds.has(n.kind);",
     "  return true;  // mutated: the filter hides nothing",
     "the filter hides the model it is given and keeps the others"),

    ("the filter stops announcing what it hides",
     "homegraph/assets/gui.html",
     "  const hidden = hiddenCount();\n"
     "  if (hidden)\n"
     '    parts.push("filteret skjuler " + hidden + " av " + payload.nodes.length +\n'
     '               " noder");',
     "  // mutated: the filtered view says nothing about being filtered",
     "the filter says on the status line how much it hides"),

    ("nodeAt stops skipping filtered-out nodes",
     "homegraph/assets/gui.html",
     "    if (!visible(n)) continue;\n"
     "    const d = Math.hypot(sx(n.x) - px, sy(n.y) - py);",
     "    const d = Math.hypot(sx(n.x) - px, sy(n.y) - py);  // mutated",
     "a filtered-out node cannot be clicked"),

    ("toggling a filter re-lays-out the graph",
     "homegraph/assets/gui.html",
     "  if (state.search) renderHits(); else renderStatus();\n"
     "  draw();",
     "  if (state.search) renderHits(); else renderStatus();\n"
     "  fit();  // mutated: the filter re-scales the view\n"
     "  draw();",
     "the filter triggers no fetch and no re-layout"),

    # -- step 2b, the neighbourhood fallback -------------------------------

    ("neighbourhood drops mesh_neighbors' own answer",
     "homegraph/gui.py",
     "    out[\"incoming\"] = side(edges, \"dst\", \"src\")\n"
     "    out[\"outgoing\"] = side(edges, \"src\", \"dst\")\n"
     "    return out",
     "    return {\"incoming\": side(edges, \"dst\", \"src\"),  # mutated\n"
     "            \"outgoing\": side(edges, \"src\", \"dst\")}",
     "POST /neighbors keeps mesh_neighbors' own keys and adds two"),

    ("the neighbourhood ignores direction and puts every edge on both sides",
     "homegraph/gui.py",
     "                       for e in edges if e[near] == node),",
     "                       for e in edges),  # mutated: direction ignored",
     "the neighbourhood splits edges by direction, from both ends"),

    ("the neighbourhood comes back in mesh-read order",
     "homegraph/gui.py",
     "                      key=lambda e: (e[\"key\"], e[\"rel\"]))",
     "                      key=lambda e: 0)  # mutated: no order",
     "the neighbourhood sorts an input that arrived unsorted"),

    ("every neighbour edge is reported as stated",
     "homegraph/gui.py",
     "                        \"derived\": e[\"confidence\"] is not None\n"
     "                        and e[\"confidence\"] < 1.0}",
     "                        \"derived\": False}  # mutated",
     "a neighbour edge is derived exactly when its confidence is below 1"),

    ("neighbourhood's depth guard is removed",
     "homegraph/gui.py",
     "    try:\n"
     "        depth = int(depth) if depth else 1\n"
     "    except (TypeError, ValueError) as exc:\n"
     "        raise BadArgument(\"depth must be an integer: %s\" % exc) from exc",
     "    depth = int(depth) if depth else 1  # mutated: no BadArgument",
     "a /neighbors call with a non-integer depth is a 400, not a 500"),

    ("the fallback fires on every click, bridge or no bridge",
     "homegraph/assets/gui.html",
     "  if (out.body.bridges.length) { renderSchematic(out.body); return; }",
     "  // mutated: the neighbourhood is fetched even when a bridge was found",
     "no bridge found falls back to /neighbors, and only then"),

    ("the fallback stops naming the hits it found no path to",
     "homegraph/assets/gui.html",
     "  if (bridgeOut && bridgeOut.unreachable.length)\n"
     '    parts.push("ingen sti til " + bridgeOut.unreachable.length +\n'
     '               " treff innen dybde " + bridgeOut.max_depth);',
     "  // mutated: the unreachable hits are not named in the fallback",
     "the fallback still names the hits with no path"),

    ("a derived neighbour edge is drawn like a stated one",
     "homegraph/assets/gui.html",
     "           (e.derived ? \"fill='none' stroke='#c88' stroke-dasharray='3 2'\"\n"
     "                      : \"fill='#8899aa'\") + \">\" +",
     "           \"fill='#8899aa'\" + \">\" +  // mutated: derived looks stated",
     "a derived neighbour edge is drawn differently from a stated one"),

    ("select() drops the status guard on /neighbors",
     "homegraph/assets/gui.html",
     "  if (nb.status !== 200) { renderError(nb); return; }",
     "  // mutated: a 500 from /neighbors is drawn as a neighbourhood",
     "a 500 from /neighbors is an error, not an empty neighbourhood"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                             # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_gui.py", prefix="mutgui-", timeout=300))
