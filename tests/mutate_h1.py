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
import sys

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h1.py", prefix="muth1-", timeout=300))
