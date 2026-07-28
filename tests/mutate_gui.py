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
     "            self._send(result)",
     "            if isinstance(result, dict) and \"status\" in result:\n"
     "                result[\"status\"] = \"complete\"  # mutated\n"
     "            self._send(result)",
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
     "    import random as _random  # mutated: M2, a fresh seed every call\n"
     "    laid = _layout([out_nodes[i] for i in order],\n"
     "                   [(slot[a], slot[b]) for a, b, *_ in out_edges],\n"
     "                   seed=_random.randrange(99))",
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
     "                with open(page, \"rb\") as fh:\n"
     "                    body = fh.read()",
     "                with open(page, \"rb\") as fh:\n"
     "                    body = fh.read()[:-1]  # mutated: M5",
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
     "the schematic names which of its two conditions is missing"),

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
     "an apostrophe in a filename survives into data-key"),

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
     "the schematic names which of its two conditions is missing"),

    ("<title> becomes a sibling of the self-closed unreachable source circle",
     "homegraph/assets/gui.html",
     "      svg += \"<circle cx='20' cy='\" + y + \"' r='5' fill='#1a73e8'>\" +\n"
     "             \"<title>\" + esc(bridgeOut.src) + \"</title></circle>\" +",
     "      svg += \"<circle cx='20' cy='\" + y + \"' r='5' fill='#1a73e8'/>\" +"
     "  // mutated: M17b\n"
     "             \"<title>\" + esc(bridgeOut.src) + \"</title>\" +",
     "the schematic names which of its two conditions is missing"),

    ("the page fetches a script from a CDN",
     "homegraph/assets/gui.html",
     "const state = { filter: null, search: null, selection: null };",
     "const state = { filter: null, search: null, selection: null };\n"
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
     "        def do_GET(self):\n"
     "            if self.path == \"/graph\":\n"
     "                self._send(payload)",
     "        def do_GET(self):\n"
     "            if self.path == \"/graph\":\n"
     "                self._send({k: v for k, v in payload.items()\n"
     "                           if k not in (\"missing\", \"truncated\")})"
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
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                             # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_gui.py", prefix="mutgui-", timeout=300))
