#!/usr/bin/env python3
"""Mutation test for CP-H3 (graph) -- the in-page semantic search.

The gate's whole value is that the JavaScript port cannot drift from the Python
embedder without going red. Each mutation breaks the port or the packing in a
way that would otherwise ship silently -- a dropped per-row scale, a tokenizer
that stops splitting identifiers -- and names the check that must fail.

Run:
    python3 tests/mutate_h3_graph.py
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
    # The per-row scale is dropped: int8 becomes round(x) with no scale, so the
    # small components round to zero and the direction shifts. Faithful
    # quantisation cannot be told from garbage without this.
    ("the int8 quantiser drops its per-row scale",
     "homegraph/visualize.py",
     "        scale = peak / 127.0 if peak > 0 else 1.0",
     "        scale = 1.0  # mutated: no per-row scale",
     "emEmbed matches static_embed.embed within quantisation"),

    # The JS tokenizer stops splitting acronyms, so it diverges from the Python
    # splitter on HTMLParser -- a query tokenised differently silently misses
    # the vocabulary.
    ("the ported tokenizer stops splitting acronym boundaries",
     "homegraph/visualize.py",
     r".replace(/([A-Z]+)([A-Z][a-z])/g,'$1 $2')",
     r".replace(/([A-Z]+)([A-Z][a-z])/g,'$1$2')",
     "emSplit matches split_identifiers"),

    # The mean-pool loses its L2 normalisation in the JS port, so cosine is no
    # longer comparable and drifts from the Python embedder.
    ("the ported embedder skips L2 normalisation",
     "homegraph/visualize.py",
     "  nn=Math.sqrt(nn); if(nn>0) for(var d=0;d<EM.dim;d++) acc[d]/=nn;",
     "  nn=Math.sqrt(nn); // mutated: no L2 normalisation",
     "emEmbed returns L2-normalised (unit) vectors"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_h3_graph.py")],
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
        tree = tempfile.mkdtemp(prefix="muth3g-",
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
