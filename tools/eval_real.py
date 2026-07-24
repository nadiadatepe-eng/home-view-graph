#!/usr/bin/env python3
"""Run the CP-H3 stop-rule on a real m3 store: does the vector path earn its keep?

BUILD-TIME / local, like distill_matrix.py -- it reads a private store and a
matrix data file, neither of which ships. Stdlib only (it drives the package),
so it needs no `uv --with`.

    python3 tools/eval_real.py --store ~/.homegraph/real-m3.db \
                               --embeddings ~/.homegraph/matrix-<name>.json

It measures FILE-level retrieval over leakage-guarded heading->file pairs
(build_eval.build_pairs), comparing four retrievers with identical file-node
filtering so the comparison is fair:

  AND-FTS   strict lexical -- the term-overlap baseline
  OR-BM25   the strongest lexical ranker, and the pool the vector path reranks
  vector    cosine over the OR shortlist
  hybrid    RRF(AND-FTS, vector)

The stop-rule (harvest-plan): keep vector/hybrid as the DEFAULT only if it beats
the lexical baseline. It will not, on this eval -- the heading is verbatim in the
file, so this is a LEXICAL task and semantic reranking can only add noise. That
is the honest reading: this eval decides "don't fuse embeddings into every
search", not "embeddings are worthless". Their value is on paraphrase and
cross-lingual queries, which this auto-generated eval cannot label -- see the
qualitative probe in the CP-H3 writeup. So embeddings stay OPT-IN (off by
default, `search --embeddings`), which is exactly how the package ships them.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.providers import static_embed as se
from homegraph.search import (
    DEFAULT_HIDDEN_SUBTYPES, _vector_shortlist, fts_search,
    hybrid_search, vector_search)
from homegraph.store import Store
from tests.eval.build_eval import build_pairs
from tests.eval.scoreboard import evaluate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="an embedded m3 store")
    ap.add_argument("--embeddings", required=True, help="the matrix data file")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    emb = se.load(args.embeddings)
    with Store(args.store,
               embeddings={"provider": "static", "model": emb.model}) as s:
        if s.embedding_count(*emb.namespace) == 0:
            print("ERROR: nothing embedded under namespace %s -- run "
                  "`homegraph embed` first." % (emb.namespace,), file=sys.stderr)
            return 2
        file_ids = {r["id"] for r in s.db.execute(
            "SELECT id FROM nodes WHERE kind='file'")}

        def files_only(ids):
            return [i for i in ids if i in file_ids]

        pairs = build_pairs(s)
        if not pairs:
            print("ERROR: 0 eval pairs -- the store has no usable headings.",
                  file=sys.stderr)
            return 2
        print("heading->file eval pairs (leakage-guarded): %d" % len(pairs))
        print("(expected is a FILE; results filtered to file nodes)\n")

        lim = args.limit
        methods = {
            "AND-FTS": lambda q: files_only(
                [h["node_id"] for h in fts_search(s, q, limit=lim)]),
            "OR-BM25": lambda q: files_only(
                _vector_shortlist(s, q, lim, DEFAULT_HIDDEN_SUBTYPES)),
            "vector": lambda q: files_only(
                [h["node_id"] for h in (
                    vector_search(s, q, limit=lim, embedder=emb) or [])]),
            "hybrid": lambda q: files_only(
                [h["node_id"] for h in hybrid_search(
                    s, q, limit=lim, embedder=emb).hits]),
        }
        print("%-9s recall@k / mrr" % "method")
        for name, fn in methods.items():
            print("%-9s %s" % (name, evaluate(pairs, fn, ks=(1, 5, 10)).line()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
