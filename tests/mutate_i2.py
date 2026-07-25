#!/usr/bin/env python3
"""Mutation test for CP-I2 -- stale vectors, coverage, and incremental embed.

The three changes in this checkpoint are one change seen from three sides, and
the mutations below are chosen to prove that: neutralising any one of them
reddens a gate that names it, and neutralising the FIRST one would -- in the
real product, not just here -- have made the third one permanently wrong.

Run:
    python3 tests/mutate_i2.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 300

MUTATIONS = [
    # THE one. The rebuilt node keeps the vector describing text that is gone,
    # which is how this defect shipped in the first place.
    ("a rebuilt node keeps the vector describing its old text",
     "homegraph/update.py",
     '            store.db.execute("DELETE FROM embeddings WHERE node_id = ?", (nid,))',
     "            pass  # mutated: stale vector kept",
     "the stale vector is deleted rather than left behind"),

    # Incremental embed stops being incremental: every run re-embeds the whole
    # store, which is the twenty minutes that made the gap not worth closing.
    ("embed always re-embeds everything, incremental or not",
     "homegraph/cli.py",
     "        if force:\n            rows = store.db.execute(",
     "        if True:  # mutated: always full\n            rows = store.db.execute(",
     "a second run embeds nothing"),

    # ...and the other direction: --force is ignored, so a changed matrix under
    # an unchanged namespace can never be re-embedded at all.
    ("--force is ignored, so a changed matrix can never be re-embedded",
     "homegraph/cli.py",
     "        if force:\n            rows = store.db.execute(",
     "        if False:  # mutated: force ignored\n            rows = store.db.execute(",
     "--force re-embeds every node"),

    # Coverage stops reporting a hole, so a partial namespace looks complete --
    # the state `_out_mode=hybrid` already fails to distinguish.
    ("coverage never reports a namespace as incomplete",
     "homegraph/store.py",
     '"stale": r["c"] < total} for r in rows]',
     '"stale": False} for r in rows]  # mutated',
     "after an update the namespace is marked stale"),

    # The denominator counts nodes that have no text and are skipped on
    # purpose, so full coverage becomes unreachable and the number stops
    # meaning anything -- a metric that can never read 100% gets ignored.
    ("the coverage denominator counts textless nodes too",
     "homegraph/store.py",
     '            "SELECT COUNT(*) c FROM nodes "\n'
     "            \"WHERE COALESCE(title,'') || COALESCE(body,'') != ''\"",
     '            "SELECT COUNT(*) c FROM nodes "\n'
     '            "WHERE 1=1"  # mutated: textless nodes counted as missing',
     "the denominator counts nodes with text, not all nodes"),

    # The cascade that removes a deleted file's vector stops working. There is
    # no line in `delete_node` to neutralise -- it leans on ON DELETE CASCADE --
    # so the mutation turns the pragma that makes the cascade fire at all. That
    # is the real dependency, and it is easy to lose in a refactor: without the
    # pragma SQLite silently ignores every foreign key in the schema.
    ("foreign keys are off, so a deleted node orphans its vector",
     "homegraph/store.py",
     'self.db.execute("PRAGMA foreign_keys = ON")',
     'self.db.execute("PRAGMA foreign_keys = OFF")  # mutated',
     "a removed file leaves no vector behind"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_i2.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="muti2-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__", ".git"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>" for r in red)
            gate_red = [r for r in red
                        if not r.startswith("<crash>") and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-52s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-52s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-52s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-52s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-52s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d by a different gate, %d crash-only, "
          "%d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
