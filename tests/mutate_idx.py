#!/usr/bin/env python3
"""Mutation test for CP-IDX -- the category `index.md` assigns.

Three of the six are the ones this checkpoint exists for.

Mutation 2 is the reason K2 is written the way it is: filing every article
under the *first* heading produces exactly the same edge count, the same
category count, and the same report. Only a check that asks which category a
named article landed in can tell it from the correct rule.

Mutation 3 is the opposite failure and the more tempting one -- categorise
everything, so nothing is missing. It manufactures curation the author never
wrote, and it is the direction a count would applaud.

Mutation 6 leaves the product correct and breaks only the incremental path.
`md build` still writes the right graph; it is `update` that drifts. Without a
gate for it the feature would have been half-built and looked whole.

Run:
    python3 tests/mutate_idx.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # 1. The edge is never written. The baseline: if this does not go red,
    # nothing below means anything.
    ("the category edge is never written",
     "homegraph/models/m3_build.py",
     "                for heading in headings:\n"
     "                    store.upsert_edge(resolve_target(target, path, index),",
     "                for heading in []:  # mutated: no category edge\n"
     "                    store.upsert_edge(resolve_target(target, path, index),",
     "K1: hver oppført artikkel får en kategorikant"),

    # 2. THE one. Same counts, same report, wrong graph.
    ("every article is filed under the FIRST heading",
     "homegraph/models/m3_markdown.py",
     "    title = None\n"
     "    for sec in sections:\n"
     "        if sec[\"offset\"] >= pos:\n"
     "            break\n"
     "        title = sec[\"title\"]\n"
     "    return title",
     "    return sections[0][\"title\"] if sections else None  # mutated",
     "K2: kategorien er overskriften lenken står under"),

    # 3. Generosity that invents data: a name the index lists but no file
    # answers is filed under the heading anyway, as the `wikilink:` node that
    # already stands in for it.
    #
    # Written as the graph-producing variant on purpose. Dropping the guard
    # outright instead -- `if heading is None:` -- does not reach a gate at
    # all: `resolve_target` returns None, and the store's own endpoint check
    # raises `edge endpoints must exist: None -> 'category:Tools'`. That is the
    # store defending itself, measured 2026-07-31, and a mutation killed only
    # by a crash tells us nothing about whether K3 can say no. This one does.
    ("a name with no file behind it is filed under the heading",
     "homegraph/models/m3_build.py",
     "                if target not in index:\n"
     "                    continue\n"
     "                for heading in headings:\n"
     "                    store.upsert_edge(resolve_target(target, path, index),",
     "                for heading in headings:  # mutated: dead names filed too\n"
     "                    store.upsert_edge(resolve_target(target, path, index)\n"
     "                                      if target in index\n"
     '                                      else "wikilink:%s" % target,',
     "K3: verken uoppført artikkel eller død lenke får kategori"),

    # 4. The category consumes the link instead of accompanying it. Every
    # count still looks healthy; the graph loses 39 edges on the real wiki.
    ("the category replaces the wikilink instead of joining it",
     "homegraph/models/m3_build.py",
     "        for target in data[\"wikilinks\"]:\n"
     "            hits = index.get(target)",
     "        for target in ([] if index_key and os.path.realpath(path) ==\n"
     "                       index_key else data[\"wikilinks\"]):  # mutated\n"
     "            hits = index.get(target)",
     "K4: WIKILINKS_TO er uendret av kategoriene"),

    # 5. The report counts headings rather than categories, so an index with
    # decorative headings claims classifications that have no members.
    ("the report counts every heading as a category",
     "homegraph/models/m3_build.py",
     "    return dict.fromkeys(\n"
     "        heading for target, headings in data[\"wikilink_headings\"].items()\n"
     "        if target in index for heading in headings)",
     "    return dict.fromkeys(  # mutated\n"
     "        sec[\"title\"] for sec in data[\"sections\"])",
     "K5: rapporten stemmer med lageret (4 kanter, 3 kategorier)"),

    # 6. The product stays right and only `update` drifts: the index and the
    # pages it files stop being rebuilt together.
    ("index.md and its articles stop being rebuilt together",
     "homegraph/update.py",
     "    extra = _index_and_its_articles(store, changes, all_paths)",
     "    extra = set()  # mutated: index no longer drags its articles in",
     "K6: omfiling i index.md lander der full ombygging lander"),
    # 7-10 are the four codex found in the first version of this checkpoint.
    # Each leaves every count healthy and the store subtly wrong, which is why
    # they are here rather than in a changelog.

    # 7. An edited article is rebuilt without the index, so `forget` deletes
    # its category and nothing writes it back. Ordinary editing, silent loss.
    ("an edited article is rebuilt without its index",
     "homegraph/update.py",
     "    return {index_file} if touched & filed else set()",
     "    return set()  # mutated: the article rebuilds alone",
     "K7: redigert artikkel beholder kategorien sin"),

    # 8. The index is deleted and its edges outlive it: nothing else writes
    # them, so nothing else deletes them either.
    ("a deleted index leaves its categories behind",
     "homegraph/update.py",
     "        return filed\n",
     "        return set()  # mutated: categories outlive the index\n",
     "K8: slettet index.md tar kategoriene med seg"),

    # 9. Aimed at the mechanism K9 actually rests on. A branch of my own
    # for this case was written and measured REDUNDANT -- its mutation
    # survived -- so it was deleted. What carries the case is the caller's
    # existing expansion over `wikilink:` targets, and this breaks that.
    ("broken link targets stop expanding the rebuild set",
     "homegraph/update.py",
     "        name = (dst[len(\"wikilink:\"):] if dst.startswith(\"wikilink:\")\n"
     "                else page_name(dst))",
     "        if dst.startswith(\"wikilink:\"):  # mutated: broken targets skipped\n"
     "            continue\n"
     "        name = page_name(dst)",
     "K9: død lenke som får en fil, får kategorien sin"),

    # 10. Back to one heading per target -- `wikilinks`' dedupe shape applied
    # to something that is not a link but a classification.
    ("a page listed twice keeps only its first category",
     "homegraph/models/m3_markdown.py",
     "            if heading is not None and heading not in for_target:\n"
     "                for_target.append(heading)",
     "            if heading is not None and not for_target:  # mutated\n"
     "                for_target.append(heading)",
     "K10: side oppført under to overskrifter får begge"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_idx.py", prefix="mutidx-", timeout=300))
