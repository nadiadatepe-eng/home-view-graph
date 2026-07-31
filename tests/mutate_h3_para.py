#!/usr/bin/env python3
"""Mutation test for CP-H3-PARA -- the paraphrase set and its guard.

The result this checkpoint reports is a **zero**: FTS finds none of the thirty
paraphrases. A zero is the easiest number in the world to produce by accident,
so the mutations here aim at the three ways it could be produced dishonestly.

Mutation 2 is the important one. It makes the lexical control ask a paraphrase
instead of a verbatim phrase, so the control collapses to zero too -- and a
reader comparing "0 against 0" learns nothing about language and everything
about plumbing. K3 exists to catch precisely that, and this proves it does.

Mutation 1 guts the leakage guard. Without it "paraphrase" is a claim about how
the labeller felt while typing, and K1 would pass over a set of verbatim
quotations.

Run:
    python3 tests/mutate_h3_para.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # 1. The guard stops guarding. K1 still passes -- it asks the guard -- so
    # the checkpoint has to prove the guard can say no somewhere else.
    ("the leakage guard never finds an overlap",
     "tests/eval/paraphrase.py",
     "    return sorted(content_words(query) & content_words(body))",
     "    return []  # mutated: the guard stops guarding",
     "K5: vakten flagger en spørring løftet ut av dokumentet"),

    # 2. THE one. The lexical control asks a paraphrase, so it scores zero, and
    # "paraphrase 0 vs lexical 0" reads as a working measurement.
    ("the lexical control asks a paraphrase instead of the document's own words",
     "tests/test_h3_para.py",
     '    words = [w for w in re.findall(r"[^\\W_]+", body or "", re.UNICODE) if len(w) > 4]\n'
     '    return " ".join(words[10:16]) if len(words) >= 16 else None',
     '    return "helt urelatert spørring uten treff"  # mutated',
     "K3: ordrett frase fra dokumentet finnes (instrumentet virker her)"),

    # 3. A pair whose file moved is dropped instead of raising, so the set
    # quietly shrinks and every rate is computed over whatever survived.
    ("a pair whose document moved is dropped instead of refused",
     "tests/eval/paraphrase.py",
     '        if row is None:\n'
     '            raise KeyError("no node for %s -- rebuild the store or drop the pair"\n'
     '                           % item["path"])',
     "        if row is None:\n"
     "            continue  # mutated: shrink silently",
     "K7: et par uten dokument stopper kjøringen"),

    # 4. The headroom claim is made against itself: the paraphrase score is
    # computed from the lexical queries, so K4 compares a number with itself.
    ("the headroom is measured against the lexical queries, not the paraphrases",
     "tests/test_h3_para.py",
     '            para = [(p["query"], p["node_id"]) for p in pairs]',
     "            para = list(lex)  # mutated: compare lexical with itself",
     "K4: FTS finner INGEN av parafrasene (r@10 og MRR er 0)"),

    # 5. The scoreboard's own refusal, which is what stops an empty set from
    # reporting a perfect score. H1 put it there; this keeps it there.
    ("an empty eval is scored instead of refused",
     "tests/eval/scoreboard.py",
     "    if not pairs:",
     "    if False:  # mutated: empty eval accepted",
     "K6: et tomt sett nektes ved navn, ikke ved krasj"),
    # 6. The structural sanity: one pair copied over the whole set. Every rate
    # is then computed over one document while the report says 28.
    ("the set collapses to one pair repeated",
     "tests/test_h3_para.py",
     "            pairs = load(store=s)",
     "            pairs = load(store=s); pairs = [pairs[0]] * len(pairs)  # mutated",
     "K8: hver spørring er egen, mot sitt eget dokument, og ikke tynn"),

    # 7. The inflection probe stops probing, so `katt`/`katter` passes as a
    # paraphrase -- the overlap the exact-token guard was never able to see.
    ("the inflection probe never finds a stem",
     "tests/eval/paraphrase.py",
     "            if len(d) >= floor and (d.startswith(q) or q.startswith(d)):",
     "            if False:  # mutated: stems no longer compared",
     "K9: ingen spørring deler bøyningsstamme, og proben finner en"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h3_para.py", prefix="muth3para-", timeout=600))
