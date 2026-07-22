#!/usr/bin/env python3
"""Mutation test for CP-1: prove each gate can fail.

CP-1 passed 35/35 on its first run. So did CP-0 -- and CP-0 still contained a
duplicated invariant that made its negative control unable to fire. A green
checkpoint is evidence about the tests only once you have watched them go red.

Each mutation below reverts one real behaviour while leaving the test suite
untouched. A mutation that does not turn some check red means that check was
never testing that behaviour.

Run:
    python3 tests/mutate_cp1.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (name, relative file, needle, replacement, check that must go red)
MUTATIONS = [
    ("FTS filled implicitly on insert",
     "homegraph/store.py",
     'self.db.execute("DELETE FROM nodes_fts")',
     'pass  # mutated: index never cleared',
     "rebuild_fts is idempotent"),

    ("FTS terms ORed instead of ANDed",
     "homegraph/search.py",
     'return " AND ".join',
     'return " OR ".join',
     "sentence query without embeddings returns 0"),

    ("time travel ignores last_seen",
     "homegraph/store.py",
     '"WHERE e.first_seen <= ? AND e.last_seen >= ?")',
     '"WHERE e.first_seen <= ? AND ? IS NOT NULL")',
     "today's graph lost the removed link"),

    ("datelist bit convention shifted by one",
     "homegraph/temporal.py",
     "mask |= 1 << delta",
     "mask |= 1 << (delta + 1)",
     "bitmask encodes to known values"),

    ("retention rolls up but never deletes",
     "homegraph/temporal.py",
     'deleted = store.db.execute(\n        "DELETE FROM observations WHERE seen_date < ?", (cutoff,)).rowcount',
     'deleted = 0  # mutated: daily rows left in place',
     "daily rows older than 90 days are gone"),

    ("hash confirmation skipped, mtime is final",
     "homegraph/incremental.py",
     'if new_hash == prior["content_hash"]:',
     'if False:  # mutated: every touch counts as a change',
     "exactly 1 file reported changed"),

    ("RRF weights rank the wrong way",
     "homegraph/search.py",
     'slot["score"] += 1.0 / (k + rank)',
     'slot["score"] += 1.0 * rank',
     "RRF rewards better ranks"),

    ("deleting a node leaves its FTS row",
     "homegraph/store.py",
     'self.db.execute("DELETE FROM nodes_fts WHERE rowid = ?", (nid,))',
     'pass  # mutated: FTS row orphaned',
     "no FTS orphans after deletion"),
]


def run_suite(tree):
    proc = subprocess.run(
        [sys.executable, os.path.join(tree, "tests", "test_cp1.py")],
        capture_output=True, text=True, cwd=tree)
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut-", dir=os.path.expanduser(
            "~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "*.tsv", ".homegraph"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP  %-44s (needle not found in %s)" % (name, rel))
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
