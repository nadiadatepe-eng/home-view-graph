#!/usr/bin/env python3
"""Mutation test for CP-2. Same discipline as CP-1, for the same reason.

CP-1's mutation run found three checks that tested nothing. CP-2 already had a
fourth before this file ran: the known-answer rows compare basenames, so they
could not distinguish `wiki/raw/llm-wiki-idea.md` from
`wiki/wiki/summaries/llm-wiki-idea.md` and "nearest wins" resolution was
unasserted. That check now exists; these mutations decide whether it and the
rest actually bite.

Run:
    python3 tests/mutate_cp2.py
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
    ("code spans are not blanked",
     "homegraph/models/m3_markdown.py",
     "    return RE_INLINE_CODE.sub(blank, RE_FENCE.sub(blank, text))",
     "    return text  # mutated: code is indistinguishable from prose",
     "known-answer relations"),

    ("ambiguity resolved alphabetically",
     "homegraph/models/m3_markdown.py",
     "    return min(candidates, key=lambda c: (-shared(c), c.count(os.sep), c))",
     "    return candidates[0]  # mutated: sorted-first, semantically blind",
     "ambiguous target resolves to the nearest file"),

    ("piped aliases not stripped",
     "homegraph/models/m3_markdown.py",
     r'RE_WIKILINK = re.compile(r"!?\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")',
     r'RE_WIKILINK = re.compile(r"!?\[\[([^\]#]+?)(?:#[^\]]*)?\]\]")',
     "known-answer relations"),

    # Drops the node AND its edge, so the suite runs to completion and a gate
    # has to say no. The earlier version dropped only the node, which made
    # upsert_edge raise -- detected, but by the process dying rather than by
    # any check, and the harness counted that as a kill.
    ("broken links dropped instead of kept as nodes",
     "homegraph/models/m3_build.py",
     '''            hits = index.get(target)
            if not hits:
                store.upsert_edge(path, "wikilink:%s" % target,
                                  "WIKILINKS_TO", as_of)
                report.broken_links[target] += 1
                report.broken_by_subtype[data["subtype"]] += 1''',
     '''            hits = index.get(target)
            if not hits:
                continue  # mutated: unresolved links vanish entirely''',
     # The node is still created in pass 1, so "broken links are nodes" stays
     # green -- correctly, since they still are. What this mutation destroys is
     # the EDGE, i.e. which file was pointing at the missing page, and that is
     # exactly what the BROKEN known-answer row asserts.
     "known-answer relations"),

    ("broken targets never become nodes",
     "homegraph/models/m3_build.py",
     '''        for target in data["wikilinks"]:
            if target not in index:
                store.upsert_node("wikilink:%s" % target, kind="wikilink",
                                  subtype="broken", title=target, body=target,
                                  as_of=as_of)''',
     "        pass  # mutated: an unresolved link is dropped, not recorded",
     "broken links are nodes, not dropped"),

    ("subtype filter disabled",
     "homegraph/search.py",
     'DEFAULT_HIDDEN_SUBTYPES = ("transcript",)',
     "DEFAULT_HIDDEN_SUBTYPES = ()  # mutated: nothing is ever hidden",
     "subtype gate says no"),

    ("generated markdown folded back into notes",
     "homegraph/models/m3_markdown.py",
     'GENERATED_MARKERS = ("GRAPH_REPORT.md",)',
     "GENERATED_MARKERS = ()  # mutated: machine output looks hand-written",
     "generated markdown is separated from notes"),

    ("malformed frontmatter raises",
     "homegraph/models/m3_markdown.py",
     '            problems.append((lineno, raw.strip()))\n            continue',
     '            raise ValueError("bad frontmatter line %d" % lineno)',
     "malformed frontmatter does not raise"),

    ("traversal forgets where it has been",
     "homegraph/models/m3_build.py",
     "            if key in seen:\n                continue\n            seen.add(key)",
     "            seen.add(key)  # mutated: revisits nodes, cycles re-expand",
     "traversal terminates through the cycle"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp2.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # A mutation that hangs is killed by definition -- that is the failure.
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
        tree = tempfile.mkdtemp(prefix="mut2-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-42s needle missing in %s" % (name, rel))
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
