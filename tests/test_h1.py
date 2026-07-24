#!/usr/bin/env python3
"""CP-H1 -- the retrieval scoreboard's metric core, verified by hand.

The scoreboard is the fasit for CP-H3 (semantic search), so it is graded here
against numbers computed by hand BEFORE the code ran -- not against its own
output. Every check below names a value derivable without running Python, so a
metric that quietly always scores high reddens a gate.

The load-bearing claim is the negative one: a `search_fn` that never returns the
expected node scores **0**, and an empty eval **raises** rather than reporting
the vacuous 100%. A scoreboard that cannot report failure measures nothing.

Run:
    python3 tests/test_h1.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.eval.scoreboard import (                            # noqa: E402
    evaluate, recall_at_k, reciprocal_rank)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print("%s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))


# -- recall_at_k, by hand ---------------------------------------------------
def t_recall_at_k():
    # expected 10 sits at position 3 of [5, 6, 10, 7].
    r = [5, 6, 10, 7]
    check("recall@1 misses a rank-3 hit", recall_at_k(r, 10, 1) == 0.0)
    check("recall@3 catches a rank-3 hit", recall_at_k(r, 10, 3) == 1.0)
    check("recall@k is 0 when the id is absent", recall_at_k(r, 99, 10) == 0.0)
    # k larger than the list must not error and must still find it.
    check("recall@k tolerates k > len", recall_at_k([10], 10, 20) == 1.0)
    try:
        recall_at_k(r, 10, 0)
        check("recall@0 is rejected", False, "no raise")
    except ValueError:
        check("recall@0 is rejected", True)


# -- reciprocal rank, by hand -----------------------------------------------
def t_reciprocal_rank():
    check("rr of a rank-1 hit is 1.0", reciprocal_rank([10, 20], 10) == 1.0)
    check("rr of a rank-4 hit is 0.25", reciprocal_rank([1, 2, 3, 10], 10) == 0.25)
    check("rr of an absent id is 0.0", reciprocal_rank([1, 2, 3], 10) == 0.0)


# -- evaluate over a hand-computed eval -------------------------------------
def t_evaluate_hand():
    """Fasit, computed before the code:

        q1 -> [10, 5, 6]        expected 10  rank 1   r@1=1 r@5=1  rr=1.0
        q2 -> [5, 6, 20]        expected 20  rank 3   r@1=0 r@5=1  rr=1/3
        q3 -> [1,2,3,4,5]       expected 30  absent   r@1=0 r@5=0  rr=0

        recall@1 = (1+0+0)/3 = 0.3333...
        recall@5 = (1+1+0)/3 = 0.6666...
        mrr      = (1.0 + 1/3 + 0)/3 = 0.4444...
    """
    table = {
        "q1": [10, 5, 6],
        "q2": [5, 6, 20],
        "q3": [1, 2, 3, 4, 5],
    }
    pairs = [("q1", 10), ("q2", 20), ("q3", 30)]
    s = evaluate(pairs, lambda q: table[q], ks=(1, 5))
    check("n counts every pair", s.n == 3, "n=%d" % s.n)
    check("recall@1 = 1/3", abs(s.recall[1] - 1 / 3) < 1e-9, "%.4f" % s.recall[1])
    check("recall@5 = 2/3", abs(s.recall[5] - 2 / 3) < 1e-9, "%.4f" % s.recall[5])
    check("mrr = 0.4444", abs(s.mrr - (1.0 + 1 / 3) / 3) < 1e-9, "%.4f" % s.mrr)


# -- the negative controls: the scoreboard can say NO ------------------------
def t_can_report_zero():
    pairs = [("a", 1), ("b", 2)]
    s = evaluate(pairs, lambda q: [], ks=(1, 10))    # never returns anything
    check("a search that finds nothing scores recall@1 = 0", s.recall[1] == 0.0)
    check("a search that finds nothing scores recall@10 = 0", s.recall[10] == 0.0)
    check("a search that finds nothing scores mrr = 0", s.mrr == 0.0)


def t_empty_eval_raises():
    try:
        evaluate([], lambda q: [1, 2, 3])
        check("an empty eval raises, never reports 100%", False, "no raise")
    except ValueError:
        check("an empty eval raises, never reports 100%", True)


def main() -> int:
    t_recall_at_k()
    t_reciprocal_rank()
    t_evaluate_hand()
    t_can_report_zero()
    t_empty_eval_raises()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h1():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
