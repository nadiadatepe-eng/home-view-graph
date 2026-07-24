#!/usr/bin/env python3
"""CP-H1 -- the measuring instrument, built before the thing it measures.

Borrowed idea (idea harvest 2026-07-24, codegraph-ai/CodeGraph
`static-embeddings-plan.md`): decide whether semantic search helps by *measuring*
it -- recall@k and MRR over a labelled `(query -> expected node)` eval, with an
explicit stop rule -- not by feel. This module is only the metric core:
`recall_at_k`, `reciprocal_rank`, `evaluate`. The corpus-driven pair generator
(`build_eval.py`) and the FTS/RRF baseline sit on top of it, and the one failure
this file is written to prevent is a scoreboard that can only report a *high*
number.

Two invariants, because both are the empty-gate family ([[empty-gate-patterns]]):

  * An eval with **zero pairs** is an error, never 100%. `mean()` over nothing
    is not "everything passed"; it is "nothing was asked". `evaluate` refuses.
  * recall@k and reciprocal rank are computed from the position of the expected
    id in the ranked list, so a `search_fn` that returns everything does NOT get
    a free recall@1 -- rank still has to be <= k, and MRR still divides by it.

The eval is only honest if the query does not trivially encode its own answer;
that is a property of the *pairs* (build_eval.py's job, and the sim-auditor
gate), not of the arithmetic here.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

# A pair is (query, expected_node_id). One expected node per query keeps the
# metric unambiguous; a query with several acceptable answers is modelled as
# several pairs, not a set, so recall is never inflated by a lucky near-miss.
Pair = tuple[str, int]
SearchFn = Callable[[str], Sequence[int]]  # query -> ranked node_ids, best first


def recall_at_k(ranked: Sequence[int], expected: int, k: int) -> float:
    """1.0 if `expected` is within the first k of `ranked`, else 0.0."""
    if k <= 0:
        raise ValueError("k must be positive, got %d" % k)
    return 1.0 if expected in ranked[:k] else 0.0


def reciprocal_rank(ranked: Sequence[int], expected: int) -> float:
    """1/rank of `expected` (1-indexed), or 0.0 if it never appears."""
    for i, node_id in enumerate(ranked, start=1):
        if node_id == expected:
            return 1.0 / i
    return 0.0


@dataclass
class Score:
    n: int                       # number of pairs evaluated
    recall: dict[int, float]     # k -> mean recall@k
    mrr: float

    def line(self) -> str:
        rs = " ".join("r@%d=%.3f" % (k, v) for k, v in sorted(self.recall.items()))
        return "n=%d  %s  mrr=%.3f" % (self.n, rs, self.mrr)


def evaluate(pairs: Sequence[Pair], search_fn: SearchFn,
             ks: Sequence[int] = (1, 5, 10)) -> Score:
    """Run every pair through `search_fn` and average the metrics.

    Raises on an empty eval: a mean over zero pairs would report 1.0 for every
    k (the sum and count are both 0, and a naive guard returns the vacuous
    "all passed"), which is exactly the reading this checkpoint exists to make
    impossible.
    """
    if not pairs:
        raise ValueError(
            "empty eval: refusing to report a score over zero pairs -- that "
            "reads as 100%% and measures nothing")
    if any(k <= 0 for k in ks):
        raise ValueError("ks must all be positive, got %r" % (tuple(ks),))

    recall_sums = {k: 0.0 for k in ks}
    rr_sum = 0.0
    for query, expected in pairs:
        ranked = list(search_fn(query))
        for k in ks:
            recall_sums[k] += recall_at_k(ranked, expected, k)
        rr_sum += reciprocal_rank(ranked, expected)

    n = len(pairs)
    return Score(
        n=n,
        recall={k: recall_sums[k] / n for k in ks},
        mrr=rr_sum / n,
    )
