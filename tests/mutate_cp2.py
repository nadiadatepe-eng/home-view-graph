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
import sys

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
                                  "WIKILINKS_TO", as_of, method="exact")
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

    # -- CP-G: which edge counts as one note reaching another --------------
    #
    # Every one of these four is a decision the query makes silently. The
    # count comes out plausible under all of them -- 52.3% is not a number
    # anyone can eyeball -- so the only thing standing between a wrong rule
    # and a published figure is a gate that names the answer in advance.
    # Adding "TAGGED" to LINKING_RELS was the fourth mutation here and it
    # SURVIVED, green. Not a hole in the gate: `m.kind = 'file'` already
    # excludes every tag node, so the mutation is equivalent -- it cannot
    # change an answer. Removed rather than kept as a permanent red mark,
    # and the finding written into the constant's comment instead: the
    # relation list documents the rule, the kind filter enforces it. A
    # mutation nothing can observe is not evidence about a check.

    # Only outbound edges count. A note nothing links FROM but that several
    # notes link TO reads as isolated -- over-reporting, which sends a reader
    # to fix a note that is fine. The endpoint half of the gold check exists
    # for this one: with only the sources checked it survived.
    # The gold check's own guard, mutated. Resolution yields nothing, the
    # target half of the check is silently empty, and a gate that no longer
    # looks at targets must still say no. This one mutates the TEST, not the
    # code: `bool(targets)` is the only thing standing between "checked both
    # ends" and "checked one and reported both".
    ("gold LINK targets stop resolving",
     "tests/test_cp2.py",
     '                "WHERE sN.node_key=? AND e.rel=\'WIKILINKS_TO\' "\n'
     '                "AND d.kind=\'file\'", (src,))',
     '                "WHERE sN.node_key=? AND e.rel=\'NOSUCHREL\' "\n'
     '                "AND d.kind=\'file\'", (src,))  # mutated',
     "no gold LINK endpoint is called isolated"),

    ("being linked to does not count, only linking out",
     "homegraph/models/m3_build.py",
     '        "  WHERE (e.src = n.id OR e.dst = n.id) AND e.rel IN (%s)"',
     '        "  WHERE e.src = n.id AND e.rel IN (%s)"  # mutated: one way',
     "no gold LINK endpoint is called isolated"),

    ("every file-to-file edge counts, whatever the relation",
     "homegraph/models/m3_build.py",
     'LINKING_RELS = ("WIKILINKS_TO", "LINKS_TO", "MENTIONS_PATH")',
     'LINKING_RELS = ("WIKILINKS_TO", "LINKS_TO", "MENTIONS_PATH", "EMBEDS")'
     "  # mutated: an embed connects",
     "relation filter matches the known answer"),

    # The mutation t_isolated_rels was written for. The real corpus has no
    # LINKS_TO and no MENTIONS_PATH edge, so this one is invisible to every
    # check that runs against it -- it needs the hand-built store.
    ("only wikilinks count, markdown links and path mentions do not",
     "homegraph/models/m3_build.py",
     'LINKING_RELS = ("WIKILINKS_TO", "LINKS_TO", "MENTIONS_PATH")',
     'LINKING_RELS = ("WIKILINKS_TO",)  # mutated: two relations ignored',
     "relation filter matches the known answer"),

    # Dropping the kind filter lets an edge into a `broken` wikilink stub
    # read as a connection -- which is exactly the note this command exists
    # to find, reported as healthy.
    ("a link into the void counts as a connection",
     "homegraph/models/m3_build.py",
     '        "    AND m.kind = \'file\' AND m.id <> n.id) "',
     '        "    AND m.id <> n.id) "  # mutated: broken stubs connect',
     "relation filter matches the known answer"),

    ("a note that names itself counts as connected",
     "homegraph/models/m3_build.py",
     '        "    AND m.kind = \'file\' AND m.id <> n.id) "',
     '        "    AND m.kind = \'file\') "  # mutated: self-link connects',
     "relation filter matches the known answer"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp2.py", prefix="mut2-", timeout=300))
