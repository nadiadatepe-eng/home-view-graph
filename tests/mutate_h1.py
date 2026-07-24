#!/usr/bin/env python3
"""Mutation test for CP-H1 -- the retrieval scoreboard.

Every gate in a scoreboard is a claim about a number. A metric that always
scores high, or a leakage guard that lets the query encode its answer, looks
exactly like a working eval. Each mutation below manufactures one and names the
check that must go red for it.

Run:
    python3 tests/mutate_h1.py
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
    # -- the metric core (scoreboard.py) ----------------------------------
    ("recall ignores k, so every hit is recall@1",
     "tests/eval/scoreboard.py",
     "return 1.0 if expected in ranked[:k] else 0.0",
     "return 1.0 if expected in ranked else 0.0  # mutated: k ignored",
     "recall@1 misses a rank-3 hit"),

    ("reciprocal rank is flat, so rank stops mattering",
     "tests/eval/scoreboard.py",
     "            return 1.0 / i",
     "            return 1.0  # mutated: rank ignored",
     "rr of a rank-4 hit is 0.25"),

    ("the empty-eval guard is gone, so nothing scores 100%",
     "tests/eval/scoreboard.py",
     "    if not pairs:",
     "    if False:  # mutated: empty eval no longer refused",
     "an empty eval raises, never reports 100%"),

    # -- the leakage guards (build_eval.py) -------------------------------
    ("ambiguous headings are kept, one parent picked arbitrarily",
     "tests/eval/build_eval.py",
     "        if len(parents) != 1:                 # ambiguous -> drop, never guess\n"
     "            continue\n"
     "        (path,) = tuple(parents)",
     "        path = min(parents)  # mutated: pick one instead of dropping",
     "the generator emits exactly the unique multi-word headings"),

    ("the one-word guard is gone, so 'Notes' becomes a query",
     "tests/eval/build_eval.py",
     "        if len(head.split()) < min_words:      # too short to mean anything",
     "        if False:  # mutated: one-word headings kept",
     "the one-word heading is dropped"),

    ("the title-self-match guard is gone, so titles leak in",
     "tests/eval/build_eval.py",
     "        if head == f[\"title\"] or not head:     # title self-match / empty",
     "        if not head:  # mutated: title self-match kept",
     "the generator emits exactly the unique multi-word headings"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_h1.py")],
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
        tree = tempfile.mkdtemp(prefix="muth1-",
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
