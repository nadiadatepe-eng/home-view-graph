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

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.eval.scoreboard import (                            # noqa: E402
    evaluate, recall_at_k, reciprocal_rank)

results, check = reporter(52)


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
        check("an empty eval raises, never reports 100%", False, "returned, no raise")
    except ValueError:
        check("an empty eval raises, never reports 100%", True)
    except Exception as exc:                                    # noqa: BLE001
        # A ZeroDivisionError here means the guard is gone and the mean divided
        # by zero -- still a failure of "refuse cleanly", named rather than a
        # crash that would leave the gate looking untested.
        check("an empty eval raises, never reports 100%", False,
              "raised %s, not a clean refusal" % type(exc).__name__)


# -- the corpus-driven generator, on a controlled mini-corpus ---------------
# Fasit, declared before the code: four markdown files whose H2 headings are the
# only eval source. The expected pairs are exactly the UNIQUE, multi-word, non-
# title headings; everything else must be dropped by a named guard.
_CORPUS = {
    "alpha.md": ("# Alpha Project\nIntro.\n"
                 "## Token Budget Scaling\nHow the budget scales.\n"
                 "## Cache Invalidation Strategy\nWhen the cache is dropped.\n"),
    "beta.md": ("# Beta Notes\n"
                "## Vector Index Layout\nThe layout of the vector index.\n"
                "## Reciprocal Rank Fusion\nFusing rankings without scores.\n"),
    # "Cache Invalidation Strategy" repeats alpha -> ambiguous, dropped.
    "gamma.md": ("# Gamma Guide\n"
                 "## Cache Invalidation Strategy\nA different take.\n"),
    # "Notes" is one word -> dropped; title "Delta" never appears as an H2.
    "delta.md": ("# Delta\n## Notes\nShort.\n"),
}
# Unique, multi-word, non-title, unambiguous:
_EXPECTED_QUERIES = {
    "Token Budget Scaling",      # alpha only
    "Vector Index Layout",       # beta only
    "Reciprocal Rank Fusion",    # beta only
}
# Paraphrases: each is ABOUT a file but shares no term with it, so a term-ANDing
# FTS must miss every one. They are the headroom semantic search (CP-H3) exists
# to close -- and the proof that this scoreboard can register a lexical->semantic
# gap at all, which the verbatim-heading source alone cannot. (sim-auditor #1.)
_PARAPHRASE = [
    ("output length grows with corpus size", "alpha.md"),   # ~ Token Budget Scaling
    ("merging ranked result lists fairly", "beta.md"),      # ~ Reciprocal Rank Fusion
]


def t_corpus_eval():
    import shutil
    import tempfile

    from homegraph.models.m3_build import build as m3_build
    from homegraph.store import Store
    from tests.eval.build_eval import (
        AS_OF, build_pairs, fts_baseline, fts_search_fn)
    from tests.eval.scoreboard import evaluate

    tmp = tempfile.mkdtemp(prefix="h1-corpus-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        paths = []
        for name, text in _CORPUS.items():
            p = os.path.join(tmp, name)
            open(p, "w").write(text)
            paths.append(p)
        db = os.path.join(tmp, "m3.db")
        with Store(db, model="m3") as s:
            m3_build(s, sorted(paths), AS_OF)
            s.rebuild_fts()
        with Store(db) as s:
            pairs = build_pairs(s)
            queries = {q for q, _ in pairs}
            check("the generator emits exactly the unique multi-word headings",
                  queries == _EXPECTED_QUERIES,
                  "got %r" % sorted(queries))
            check("the ambiguous heading is dropped, not guessed",
                  "Cache Invalidation Strategy" not in queries)
            check("the one-word heading is dropped",
                  "Notes" not in queries)

            ids = {os.path.basename(r["node_key"]): r["id"]
                   for r in s.db.execute(
                       "SELECT id, node_key FROM nodes WHERE kind='file'")}
            fn = fts_search_fn(s, limit=10)

            # LEXICAL baseline only: the heading is a verbatim substring of the
            # file body, so this is a self-match. It proves FTS works, NOT that
            # retrieval is hard -- labelled so CP-H3 is never measured against it
            # as if it had headroom. (sim-auditor #1/#4.)
            base = fts_baseline(s, pairs)
            check("lexical baseline: exact-phrase FTS finds the owning file (r@10=1)",
                  base.recall[10] == 1.0, base.line())

            # HEADROOM: paraphrases share no term with their file, so a term-
            # ANDing FTS must miss, and the scoreboard MUST show recall below the
            # lexical baseline. Without this the eval could never register a
            # semantic win. (sim-auditor #1.)
            para = [(q, ids[name]) for q, name in _PARAPHRASE]
            head = evaluate(para, fn, ks=(10,))
            check("paraphrases score below the lexical baseline (headroom exists)",
                  head.recall[10] < base.recall[10],
                  "lexical r@10=%.2f paraphrase r@10=%.2f"
                  % (base.recall[10], head.recall[10]))

            # DISCRIMINATION across FILES, so no shared-file accident hands it a
            # free hit: a query owned by one file, expected in another, misses.
            # (sim-auditor #3.)
            mismatch = [("Token Budget Scaling", ids["beta.md"]),
                        ("Vector Index Layout", ids["alpha.md"])]
            mis = evaluate(mismatch, fn, ks=(10,))
            check("a cross-file mismatch scores 0 (the metric tells right from wrong)",
                  mis.recall[10] == 0.0, "mismatch r@10=%.2f" % mis.recall[10])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    t_recall_at_k()
    t_reciprocal_rank()
    t_evaluate_hand()
    t_can_report_zero()
    t_empty_eval_raises()
    t_corpus_eval()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h1():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
