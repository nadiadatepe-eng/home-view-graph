#!/usr/bin/env python3
"""Distil a teacher embedder into the static word-matrix H3 loads. BUILD-TIME.

This is NOT part of the package. It is a tool you run ONCE, offline, to produce
the data file `providers/static_embed.load()` reads -- and it is the only place
in this repository that is allowed to touch a model or the network. `homegraph`
itself stays `dependencies = []`; this script depends on `model2vec` and is run
outside the package, e.g.

    uv run --no-project --with model2vec python3 tools/distill_matrix.py \
        --store ~/.homegraph/real-m3.db \
        --out   ~/.homegraph/matrix-potion-multilingual.json

Borrowed idea (idea harvest 2026-07-24, codegraph-ai/CodeGraph, the model2vec
pattern by minishlab): an embedding at inference can be a lookup into a
`vocab x dim` matrix distilled once from a teacher. model2vec does this at
SUBWORD granularity with its own tokenizer. This tool adapts it to the
WORD-level lookup H3's embedder already uses: for every word that actually
occurs in the corpus, ask the (pre-distilled) model2vec model for that word's
vector -- model2vec composes it from its subwords, so even a word outside its
own vocabulary gets a real vector -- and store word -> vector. At query time
`static_embed` splits identifiers the same way, looks each word up, mean-pools,
L2-normalises. The teacher never runs again.

Why word-level and corpus-scoped:
  * `static_embed` is pure stdlib; reproducing model2vec's subword tokenizer in
    the package would drag a real tokenizer into a runtime that has no
    dependencies. A word -> vector dict needs only the identifier splitter that
    is already there.
  * The matrix is subset to the corpus vocabulary, so the data file is bounded
    by the words you actually have (tens of thousands), not the teacher's full
    subword vocabulary (hundreds of thousands). The file is a LOCAL artifact --
    it is not committed; it is machine-specific and large, like the stores in
    ~/.homegraph.

The per-word rows are stored raw, not pre-normalised: model2vec bakes a
zipf/idf down-weighting into the vectors themselves, so common words already
contribute less to a mean-pool. `static_embed` does the final L2-normalisation
on the pooled document/query vector, which is where it belongs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

# Run from the repo root so the package import resolves; the tool reads the
# corpus through the same identifier splitter the embedder uses at query time,
# so the vocabulary it distils and the tokens a query produces cannot drift.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.providers.static_embed import split_identifiers  # noqa: E402
from homegraph.store import Store                                # noqa: E402


def corpus_vocab(store: Store, min_count: int, max_vocab: int) -> list[str]:
    """Words in the corpus, by identifier-split of every node's title+body.

    Frequency-filtered and capped: a word seen once is usually a typo or a hash
    fragment, and it costs a full row; the cap bounds the data file. Both are
    reported by the caller so a silent truncation cannot read as full coverage.
    """
    counts: Counter[str] = Counter()
    for r in store.db.execute("SELECT title, body FROM nodes"):
        text = " ".join(p for p in (r["title"], r["body"]) if p)
        counts.update(split_identifiers(text))
    kept = [w for w, n in counts.most_common() if n >= min_count]
    return kept[:max_vocab]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True,
                    help="an m3 store; its node text is the vocabulary source")
    ap.add_argument("--out", required=True, help="the matrix JSON to write")
    ap.add_argument("--model", default="minishlab/potion-multilingual-128M",
                    help="a model2vec static model (default: multilingual, "
                         "since a home corpus is rarely one language)")
    ap.add_argument("--name", default=None,
                    help="namespace model name in the data file; defaults to "
                         "the model's basename")
    ap.add_argument("--min-count", type=int, default=2,
                    help="drop words rarer than this (default 2)")
    ap.add_argument("--max-vocab", type=int, default=60000,
                    help="cap the vocabulary size (default 60000)")
    ap.add_argument("--round", type=int, default=6,
                    help="decimal places per component (default 6)")
    args = ap.parse_args()

    from model2vec import StaticModel

    with Store(args.store) as store:
        vocab = corpus_vocab(store, args.min_count, args.max_vocab)
    if not vocab:
        print("ERROR: the store yielded 0 vocabulary words -- is it an m3 store "
              "with node text?", file=sys.stderr)
        return 2
    print("vocabulary: %d words (min_count=%d, cap=%d)"
          % (len(vocab), args.min_count, args.max_vocab), file=sys.stderr)

    model = StaticModel.from_pretrained(args.model)
    vecs = model.encode(vocab, show_progress_bar=True)
    dim = int(vecs.shape[1])
    name = args.name or args.model.rsplit("/", 1)[-1]

    matrix = [[round(float(x), args.round) for x in row] for row in vecs]
    data = {
        "provider": "static",
        "model": name,
        "dim": dim,
        "tokens": vocab,
        "matrix": matrix,
    }
    # Written to a sibling and renamed, so an interrupted run never leaves a
    # half-written matrix that json.load would then choke on mid-embed.
    tmp = "%s.tmp-%d" % (args.out, os.getpid())
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, args.out)
    size = os.path.getsize(args.out)
    print("wrote %s: %d words x %d dim (%.1f MB) under namespace static:%s:%d"
          % (args.out, len(vocab), dim, size / 1e6, name, dim), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
