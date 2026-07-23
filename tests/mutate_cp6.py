#!/usr/bin/env python3
"""Mutation test for CP-6.

The mesh fails by omission: a model that drops out unannounced, a fusion that
ranks by the wrong quantity, an edge invented between things that share nothing.
None of those raise. Each mutation manufactures one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900

MUTATIONS = [
    ("a refused prune has already written by the time it refuses",
     "homegraph/mesh.py",
     "        if prune:\n"
     "            refusal = self._unsafe_prune(mesh, code_paths, missing)\n"
     "            if refusal:\n"
     "                mesh.close(commit=False)\n"
     "                raise ModelUnavailable(refusal)",
     "        pass  # mutated: the refusal moves back after the mirror loop",
     "a refused prune leaves the store exactly as it was"),

    ("a filename glued inside a longer one counts as naming it",
     "homegraph/mesh.py",
     "        return (form in body\n"
     "                and cls._boundary(form, is_path).search(body) is not None)",
     "        return form in body  # mutated: runner.py matches live_runner.py",
     "CITES_CODE is exactly the declared set, with its methods"),

    # -- CITES_CODE -------------------------------------------------------
    ("an ambiguous basename is resolved to the first match",
     "homegraph/mesh.py",
     "        unique = {name: paths[0] for name, paths in by_basename.items()\n"
     "                  if len(paths) == 1}",
     "        unique = {name: paths[0] for name, paths in by_basename.items()}"
     "  # mutated: a coin flip wearing 0.6",
     "an ambiguous basename names no file, so draws no basename edge"),

    ("a missing inventory reports zero instead of absent",
     "homegraph/mesh.py",
     '                "code_inventory": ("absent" if code_index is None\n'
     "                                   else len(code_index))}",
     '                "code_inventory": len(code_index or {})}'
     "  # mutated: not asked reads as asked and empty",
     "without an inventory CITES_CODE is absent, not zero"),

    ("a full-path mention is downgraded to a basename guess",
     "homegraph/mesh.py",
     '                    mesh.upsert_edge(src, code_index[path], "CITES_CODE",\n'
     '                                     as_of, method="mention")',
     '                    mesh.upsert_edge(src, code_index[path], "CITES_CODE",\n'
     '                                     as_of, method="basename")'
     "  # mutated: the method stops distinguishing the evidence",
     "CITES_CODE is exactly the declared set, with its methods"),

    ("prose naming a source file by its project path is missed",
     "homegraph/mesh.py",
     "            rel = os.path.relpath(path, root)\n"
     '            if not rel.startswith(".."):\n'
     "                written[path].append(rel)",
     "            pass  # mutated: only absolute paths count as a mention",
     "CITES_CODE is exactly the declared set, with its methods"),

    ("the code stubs are pruned away without an inventory",
     "homegraph/mesh.py",
     "        if code_paths is None and self._has_code_stubs(mesh):",
     "        if False:  # mutated: prune deletes what it was not given",
     "pruning without an inventory is refused, not silent"),

    # -- the MCP surface --------------------------------------------------
    #
    # An agent calling these tools never sees the model list, so every claim
    # about honesty over the wire matters more here than in the CLI. None of
    # it had a mutation: 27 of CP-6's 38 checks were untargeted, and the whole
    # protocol layer sat inside that gap.
    ("initialize answers without a protocol version",
     "homegraph/mcp_server.py",
     '            result = {"protocolVersion": PROTOCOL_VERSION,',
     '            result = {  # mutated: version dropped',
     "MCP initialize returns a protocol version"),

    ("a tool is implemented but never advertised",
     "homegraph/mcp_server.py",
     "            result = {\"tools\": TOOLS}",
     "            result = {\"tools\": TOOLS[:-1]}  # mutated: one tool hidden",
     "all four mesh tools are advertised"),

    ("tools are advertised without their schema",
     "homegraph/mcp_server.py",
     "            result = {\"tools\": TOOLS}",
     "            result = {\"tools\": [  # mutated: schemas stripped\n"
     "                {k: v for k, v in t.items() if k != 'inputSchema'}\n"
     "                for t in TOOLS]}",
     "every tool declares a schema"),

    # The failure an agent cannot detect: a partial answer that looks whole.
    ("a partial answer loses its label on the way out",
     "homegraph/mcp_server.py",
     "def _text(payload):",
     "def _text(payload):\n"
     "    if isinstance(payload, dict) and 'status' in payload:  # mutated\n"
     "        payload = dict(payload, status='complete')",
     "a partial answer is labelled partial over MCP"),

    ("an unknown tool is guessed at instead of refused",
     "homegraph/mcp_server.py",
     "            if fn is None:\n"
     "                return self._error(rid, -32601, \"unknown tool %r\" % name)",
     "            if fn is None:  # mutated: fall back to search\n"
     "                fn = self.mesh_search",
     "an unknown tool is refused, not guessed"),

    ("bad arguments crash the session instead of erroring",
     "homegraph/mcp_server.py",
     "            except TypeError as exc:\n"
     '                return self._error(rid, -32602, "bad arguments: %s" % exc)',
     "            except TypeError:  # mutated: protocol error becomes a crash\n"
     "                raise",
     "missing arguments are a protocol error, not a crash"),

    ("a notification gets an answer nobody asked for",
     "homegraph/mcp_server.py",
     "            return None                       # notification: no reply at all",
     "            result = {}  # mutated: notifications now reply",
     "notifications get no reply"),

    ("one malformed line ends the session",
     "homegraph/mcp_server.py",
     "            except json.JSONDecodeError:\n"
     '                response = self._error(None, -32700, "parse error")',
     "            except json.JSONDecodeError:  # mutated: give up\n"
     "                raise",
     "a malformed line does not kill the session"),

    ("mesh_search over stdio returns nothing",
     "homegraph/mcp_server.py",
     '            fn = {"mesh_search": self.mesh_search,',
     '            fn = {"mesh_search": (lambda **kw: {  # mutated: no hits\n'
     '                "hits": [], "status": "complete", "warnings": [],\n'
     '                "models_missing": []}),',
     "mesh_search answers over stdio"),

    # -- the visualisation ------------------------------------------------
    ("the layout stops being reproducible",
     "homegraph/visualize.py",
     "    rng = random.Random(seed)  # noqa: S311",
     "    rng = random.Random()  # mutated: a new picture every run",
     "layout is deterministic"),

    # DECISIONS: D3 was dropped so the page works offline and the package has
    # no runtime dependency. Nothing stopped a future edit from adding one.
    ("the page learns to fetch a library from a CDN",
     "homegraph/visualize.py",
     '        fh.write(_PAGE.replace("__TITLE__", html.escape(title))',
     '        fh.write(\'<script src="https://cdn.example.com/d3.v7.js">'
     "</script>')  # mutated\n"
     '        fh.write(_PAGE.replace("__TITLE__", html.escape(title))',
     "the page fetches nothing"),

    ("a missing model is left out of the page instead of declared",
     "homegraph/visualize.py",
     '            missing.append(model)',
     '            pass  # mutated: the gap is not recorded',
     "a missing model is declared in the page"),

    # -- read-only means read-only ---------------------------------------
    #
    # The original defect: `Store(self.mesh_db)` in a query path. sqlite3
    # creates the file and the migration fills it in, so a read against a mesh
    # that did not exist answered `count: 0` and left a database behind. An MCP
    # server runs unattended, which is the wrong place to be roughly right.
    ("a graph query creates the store it cannot find",
     "homegraph/mesh.py",
     "    def neighbours(self, node_key, depth=1):\n"
     "        mesh = self._read_mesh()",
     "    def neighbours(self, node_key, depth=1):\n"
     "        mesh = Store(self.mesh_db)  # mutated: creates on read",
     "a query against a mesh that does not exist refuses"),

    ("the missing-mesh check accepts a path that is not there",
     "homegraph/mesh.py",
     "        if not os.path.exists(self.mesh_db):",
     "        if False:  # mutated: absent is as good as present",
     "and it creates no database while refusing"),

    ("mesh_path keeps its own way of opening the store",
     "homegraph/mesh.py",
     '        """Shortest path between two mesh nodes. Breadth-first, cycle-safe."""\n'
     "        mesh = self._read_mesh()",
     '        """Shortest path between two mesh nodes. Breadth-first, cycle-safe."""\n'
     "        mesh = Store(self.mesh_db)  # mutated: second way in",
     "mesh_path refuses on a missing mesh too"),

    # -- the federation ---------------------------------------------------
    # The difference between "this model is not here" and "this model found
    # nothing" is the whole reason `partial` exists. Collapsing them is the
    # single most consequential silent-wrong-answer available to this layer.
    ("a model that cannot be opened is reported as one that found nothing",
     "homegraph/mesh.py",
     '        if not path or not os.path.exists(path):\n'
     '            self._failed[model] = "no store at %s" % path\n'
     "            raise ModelUnavailable(self._failed[model])",
     "        if not path or not os.path.exists(path):\n"
     "            import tempfile as _tf  # mutated: an empty store instead\n"
     "            return Store(os.path.join(_tf.mkdtemp(), 'empty.db'))",
     "an unavailable model raises rather than returning empty"),

    # -- what the removed dead guards used to stand in front of -----------
    #
    # `_layout` had `max(len(models), 1)` and `max(n, 1)` around two divisions.
    # Neither could fire: the early return excludes zero. Removing them was
    # only safe if that return is real, so break it and watch.
    ("the empty-graph early return is removed",
     "homegraph/visualize.py",
     "    n = len(nodes)\n    if n == 0:\n        return []",
     "    n = len(nodes)\n    if False:  # mutated: fall through on zero\n"
     "        return []",
     "an empty graph lays out to nothing"),

    # The mesh basename index used to be guarded by `if row["path"]`, which the
    # query's own WHERE clause made unreachable. The invariant now lives in the
    # SQL alone, so the SQL is what a mutation must break.
    ("the mesh mirror stops filtering out path-less nodes",
     "homegraph/mesh.py",
     '"subtype, datelist_int FROM nodes WHERE path IS NOT NULL"',
     '"subtype, datelist_int FROM nodes"',
     "a node with no path is not mirrored into mesh"),

    # A model that silently drops files raises nothing anywhere. The only place
    # the loss surfaces is the cross-model arithmetic, which is why that gate
    # needs a mutation of its own rather than inheriting one.
    ("a model silently loses files",
     "homegraph/models/m2_build.py",
     "        report.images += 1",
     "        report.images += 0  # mutated: images vanish between the counts",
     "every non-excluded file is handled by exactly one model"),

    ("a missing model is not reported",
     "homegraph/mesh.py",
     "            except ModelUnavailable as exc:\n"
     "                missing.append(model)",
     "            except ModelUnavailable as exc:\n"
     "                pass  # mutated: silent drop",
     "missing model yields a partial result"),

    ("partial results lose their warning",
     "homegraph/mesh.py",
     '            warnings.insert(0, "PARTIAL RESULT -- %s did not answer. Counts "',
     '            warnings.insert(0, "note: %s did not answer. Counts "',
     "the warning is unmissable"),

    ("fusion ranks by raw BM25 score",
     "homegraph/mesh.py",
     '                slot["score"] += 1.0 / (RRF_K + rank)',
     '                slot["score"] += -float(row.get("score") or 0)',
     "RRF disagrees with raw score, correctly"),

    ("fusion keys by model again",
     "homegraph/mesh.py",
     '        if row.get("content_hash"):\n'
     '            return "hash:%s" % row["content_hash"]\n'
     '        if row.get("path"):\n'
     '            return "path:%s" % os.path.normpath(row["path"])',
     '        pass  # mutated: identity collapses to model+key',
     "agreement between models accumulates"),

    ("FIGURE_FOR matches loosely",
     "homegraph/mesh.py",
     "                    if name in body:",
     "                    if name.split('.')[0][:4] in body:",
     "a name that does not exist creates NO edge"),

    ("FIGURE_FOR never fires",
     "homegraph/mesh.py",
     "        for model in (\"m3\", \"m1\"):",
     "        for model in ():  # mutated: no note ever links to an image",
     "FIGURE_FOR links a note to the image it names"),

    ("time travel ignores as_of",
     "homegraph/mesh.py",
     '                sql += " AND n.first_seen <= ?"\n                args.append(as_of)',
     "                pass  # mutated: as_of has no effect",
     "as-of filters by first_seen"),

    ("a corrupt store crashes the federation",
     "homegraph/mesh.py",
     "        except (sqlite3.Error, OSError) as exc:\n"
     "            self._failed[model] = repr(exc)\n"
     "            raise ModelUnavailable(self._failed[model])",
     "        except ZeroDivisionError as exc:  # mutated: real errors escape\n"
     "            raise ModelUnavailable(repr(exc))",
     "a corrupt model does not take down the rest"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp6.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        # A mutation that makes the suite die before reaching its assertions
        # is DETECTED, but not by any gate. Kept separate from a real kill:
        # counting it as one made the `expected` field decorative, and an
        # injected mutation that only broke an import was reported as killed.
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut6-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-44s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-44s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-44s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                # Red, but not the gate that was supposed to catch it. Still a
                # kill; the attribution is wrong and worth seeing.
                print("misattrib %-44s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                # Detected only because the process died. No gate said no.
                print("CRASH     %-44s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-44s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
