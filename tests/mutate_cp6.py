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
