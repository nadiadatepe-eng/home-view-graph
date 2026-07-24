#!/usr/bin/env python3
"""Mutation test for CP-H2 -- title provenance.

The one failure this checkpoint prevents is a guess recorded as a fact. Each
mutation manufactures a way for that to happen -- an inferred title tagged
`declared`, a confidence that ignores the method, an unknown method waved
through, an untagged title defaulting to a claim of fact -- and names the check
that must go red.

Run:
    python3 tests/mutate_h2.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 180

MUTATIONS = [
    # The guess records itself as a fact -- the core failure.
    ("the first-heading fallback claims to be declared",
     "homegraph/models/m3_markdown.py",
     "            title, title_method = sections[0][\"title\"], \"inferred\"",
     "            title, title_method = sections[0][\"title\"], \"declared\"",
     "a first-heading fallback is inferred (0.5), never a fact"),

    # Confidence stops tracking the method.
    ("confidence is hardcoded, so a guess reads as certain",
     "homegraph/store.py",
     "            title_confidence = TITLE_METHODS[title_method]",
     "            title_confidence = 1.0  # mutated: confidence ignores method",
     "an inferred title carries confidence 0.5"),

    # An unknown method is waved through instead of refused.
    ("an unknown title method is accepted, not refused",
     "homegraph/store.py",
     "            if title_method not in TITLE_METHODS:",
     "            if False:  # mutated: unknown methods accepted",
     "an unknown title method is refused"),

    # A frontmatter title is mislabelled -- declared collapses into verbatim.
    ("a declared title is mislabelled verbatim",
     "homegraph/models/m3_markdown.py",
     "            title, title_method = front[\"title\"], \"declared\"",
     "            title, title_method = front[\"title\"], \"verbatim\"  # mutated",
     "a frontmatter title is declared (1.0)"),

    # The read side goes dark: the guess is served without its confidence.
    ("the mesh hit drops title_confidence, so the MCP consumer cannot see the guess",
     "homegraph/mesh.py",
     "                    \"title_confidence\": row.get(\"title_confidence\"),\n",
     "                    # mutated: title_confidence dropped from the fused hit\n",
     "mesh_search surfaces title_confidence"),

    ("fts_search drops title_confidence from its row",
     "homegraph/search.py",
     "n.node_key, n.title, n.title_confidence,",
     "n.node_key, n.title,",
     "fts_search surfaces title_confidence"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_h2.py")],
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
        tree = tempfile.mkdtemp(prefix="muth2-",
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
