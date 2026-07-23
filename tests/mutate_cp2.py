#!/usr/bin/env python3
"""Mutation test for CP-2. Same discipline as CP-1, for the same reason.

CP-1's mutation run found three checks that tested nothing. CP-2 already had a
fourth before this file ran: the known-answer rows compare basenames, so they
could not distinguish `wiki/raw/shared-note.md` from
`wiki/wiki/summaries/shared-note.md` and "nearest wins" resolution was
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
    # -- code spans: the finding this model was rewritten around ----------
    #
    # DECISIONS.md section 7 exists because every "broken" link in the real
    # wiki/ turned out to be inside backticks, documenting the syntax. The two
    # gates that hold that line had no mutation against them until now.
    ("inline code stops being blanked",
     "homegraph/models/m3_markdown.py",
     "    return RE_INLINE_CODE.sub(blank, RE_FENCE.sub(blank, text))",
     "    return RE_FENCE.sub(blank, text)  # mutated: `[[beta]]` is a link",
     "inline code span is not a link"),

    # Dropping the fence pass entirely. This survived until the fixture gained
    # a `~~~` fence: RE_INLINE_CODE is DOTALL and matches a backtick run not
    # followed by another backtick, so it was already blanking every ``` fence
    # on its own and RE_FENCE was covered by an overlap rather than tested.
    ("fenced blocks stop being blanked",
     "homegraph/models/m3_markdown.py",
     "    return RE_INLINE_CODE.sub(blank, RE_FENCE.sub(blank, text))",
     "    return RE_INLINE_CODE.sub(blank, text)  # mutated: fences are links",
     "a tilde fence is not a link either"),

    # Blanking with the empty string instead of spaces. The link list is
    # identical and every offset after a code span is wrong -- the failure the
    # same-length rule exists to prevent, invisible to any link count.
    ("code is deleted rather than blanked, so offsets shift",
     "homegraph/models/m3_markdown.py",
     '        return re.sub(r"\\S", " ", m.group(0))',
     '        return ""  # mutated: same links, every later offset wrong',
     "blanking preserves every offset"),

    # The alias is stripped by the wikilink regex itself -- group 1 excludes
    # `|` -- not by any later pass, so a mutation has to widen the capture.
    ("the wikilink capture swallows the pipe alias",
     "homegraph/models/m3_markdown.py",
     'RE_WIKILINK = re.compile(r"!?\\[\\[([^\\]|#]+?)(?:#[^\\]|]*)?(?:\\|[^\\]]*)?\\]\\]")',
     'RE_WIKILINK = re.compile(r"!?\\[\\[([^\\]#]+?)(?:#[^\\]|]*)?\\]\\]")'
     "  # mutated: alias kept",
     "real links survive"),

    # -- time travel over edges, through the command a user runs ----------
    #
    # `Store.edges_as_of` was reachable only from a test: the schema carried
    # first_seen and last_seen on every edge, the docstring named "which links
    # did this note have last week" as the reason, and no command asked.
    ("--as-of is accepted and then ignored",
     "homegraph/cli.py",
     "        found, note = backlinks(s, os.path.abspath(args.path), as_of=as_of)",
     "        found, note = backlinks(s, os.path.abspath(args.path))  # mutated",
     "--as-of hides a link that did not exist yet"),

    ("the flag never reaches the parser",
     "homegraph/cli.py",
     '    q.add_argument("--as-of", dest="as_of", default=None,\n'
     '                   metavar="YYYY-MM-DD",\n'
     '                   help="which files linked here on that date")\n'
     "    q.set_defaults(func=cmd_md_backlinks)",
     "    q.set_defaults(func=cmd_md_backlinks)  # mutated: flag unwired",
     "--as-of hides a link that did not exist yet"),

    # The predicate itself, in the one place that owns it. A `backlinks` that
    # wrote its own dates into SQL would keep this green while giving the
    # system a second opinion about what "alive on a date" means.
    # OR, not a dropped clause: the arity has to stay at two placeholders, or
    # sqlite3 raises and the harness scores a crash instead of a refusal.
    # A mutation that cannot produce a WRONG ANSWER only tests error handling.
    ("the as-of predicate forgets when an edge started",
     "homegraph/store.py",
     '               "WHERE e.first_seen <= ? AND e.last_seen >= ?")',
     '               "WHERE e.first_seen <= ? OR e.last_seen >= ?")  # mutated',
     "--as-of hides a link that did not exist yet"),

    # -- what the build produces ------------------------------------------
    ("markdown files are read but not all stored",
     "homegraph/models/m3_build.py",
     "def build(store, paths, as_of, rules=None, report=None, index_paths=None):",
     "def build(store, paths, as_of, rules=None, report=None, index_paths=None):\n"
     "    paths = list(paths)[:-3]  # mutated: quietly drop the last three",
     "every classified markdown file is a node in the graph"),

    ("markdown loses an extension and the corpus shrinks",
     "homegraph/rules/categories.toml",
     'extensions = ["md", "markdown", "mdx"]',
     'extensions = ["markdown", "mdx"]  # mutated: .md is no longer markdown',
     "the markdown corpus is the declared size"),

    ("broken links are folded in with the resolved ones",
     "homegraph/models/m3_build.py",
     '        "SELECT title FROM nodes WHERE kind=\'wikilink\' AND subtype=\'broken\' "',
     '        "SELECT title FROM nodes WHERE kind=\'wikilink\' AND subtype=\'\' "'
     "  # mutated",
     "broken links are nodes, not dropped"),

    # A stored backlink table is a second copy of the edge table that can
    # disagree with it. The module docstring says so; nothing tested it.
    ("backlinks get their own table, which can drift",
     "homegraph/models/m3_build.py",
     "def backlinks(store, node_key, rel=\"WIKILINKS_TO\", as_of=None):",
     "def backlinks(store, node_key, rel=\"WIKILINKS_TO\", as_of=None):\n"
     "    store.db.execute(  # mutated: a second, drifting copy\n"
     "        'CREATE TABLE IF NOT EXISTS backlink_cache (src TEXT, dst TEXT)')",
     "backlinks are derived, not stored"),

    ("hidden subtypes are hidden without saying so",
     "homegraph/search.py",
     '        warnings.append(\n'
     '            "hiding subtype(s) %s; pass include_all=True to search everything."',
     '        warnings.append(  # mutated: silent filtering\n'
     '            "%s"',
     "the hiding is announced"),

    ("--all stops revealing what the filter hid",
     "homegraph/search.py",
     "    hidden = () if include_all else tuple(hidden_subtypes)",
     "    hidden = tuple(hidden_subtypes)  # mutated: --all does nothing",
     "--all reveals it"),

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
