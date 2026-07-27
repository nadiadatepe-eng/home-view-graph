#!/usr/bin/env python3
"""Mutation test for CP-X1. Three of its checks report a ZERO, which is the
hardest kind of number to trust.

A gate that passes because a retriever found nothing looks identical to a gate
that passes because the suite never asked it anything. Two of the mutations
here exist to tell those apart: make the shortlist stop being the bottleneck
and the zero has to become a one; make the embedder useless and the ceiling has
to fall. If either stays green, the number it reports is not evidence.

Run:
    python3 tests/mutate_h3_crosslingual.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # The whole finding, inverted. `vector_search` reranks the OR-FTS
    # shortlist; here it reranks the corpus instead -- the very change CP-X
    # leaves as an open question rather than making. Under it, every
    # no-overlap pair becomes answerable and the reported zero must move.
    # This is the mutation that proves the zero is the SHORTLIST's doing and
    # not a broken fixture, a dead embedder or an empty store.
    ("the vector path scans the corpus instead of the shortlist",
     "homegraph/search.py",
     "    shortlist = _vector_shortlist(store, query, pool, hidden_subtypes)",
     "    shortlist = [r[\"id\"] for r in store.db.execute(  # mutated: scans\n"
     "        \"SELECT id FROM nodes\")]",
     "no-overlap: the SHIPPED vector path scores zero too"),

    # The ceiling is only a ceiling if the vectors mean something. A constant
    # embedding makes every cosine identical, the ranking collapses to id
    # order, and r@1 over 12 documents stops being 1.0.
    ("every text embeds to the same vector",
     "homegraph/providers/static_embed.py",
     "        return [x / norm for x in acc]",
     "        return [1.0] + [0.0] * (len(acc) - 1)  # mutated: constant",
     "no-overlap: the CEILING answers every pair"),

    # The leakage guard, defanged. It is the control the other numbers rest on
    # -- a "cross-language" pair sharing a word with its answer would lift
    # every score without anything semantic happening -- so it needs a
    # mutation of its own. Test-side, because the guard is test-side: the same
    # shape as mutate_cp2's gold-target needle.
    #
    # The FIRST version of this mutation SURVIVED, and that was the useful
    # result: emptying the leak list left the suite green, because a guard that
    # only fires on bad data proves nothing against good data. The check now
    # carries a planted leaking pair it must catch, and this mutation kills the
    # comparison both of them run through.
    ("the cross-language guard stops comparing against the stored body",
     "tests/test_h3_crosslingual.py",
     "    def overlap(query, target):\n"
     "        return sorted(set(query.lower().split()) & body[ids[target]])",
     "    def overlap(query, target):\n"
     "        return []  # mutated: the guard compares nothing",
     "no-overlap pairs share NO token -- and a planted one is caught"),

    # The one-token win, taken away. With cosine constant the vector path can
    # only reproduce the order it was handed, which is BM25's -- decoy first.
    # This is the mutation the earlier, vacuous version of that check could not
    # have: it had no ranking headroom to lose.
    ("cosine is neutralised, so the order collapses to BM25's",
     "homegraph/search.py",
     "    return sum(x * y for x, y in zip(a, b))",
     "    return 1.0  # mutated: every document is equally close",
     "one-token: cosine corrects the order -- the measured win"),
]

# Every check in the file that reports a number is now killed by one of the
# four above. The three that report a ZERO each have a mutation that must turn
# it into a one, and the one that reports a WIN has a mutation that takes the
# win away. What remains unmutated are the two fixture-shape checks -- "one
# token, not zero and not two" and "every word shares its topic's vector" --
# which describe the data rather than the code, and would be mutated by editing
# the very lists they check.

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h3_crosslingual.py",
                 prefix="mutxl-", timeout=300))
