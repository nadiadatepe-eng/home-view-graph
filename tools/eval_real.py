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
    DEFAULT_HIDDEN_SUBTYPES, _WORD, _vector_shortlist, fts_search,
    hybrid_search, vector_search)
from homegraph.store import Store
from tests.eval.build_eval import build_pairs
from tests.eval.scoreboard import evaluate


def _ceiling(store, emb, query, limit):
    """Cosine over every file node -- the retriever this package does NOT ship.

    CP-X1 showed why it has to be here: without it, a zero from the shipped
    path is equally consistent with "the shortlist could not reach it" and
    "these vectors are useless for this query", and only the first is a fact
    about the design.
    """
    qv = emb.embed(query)
    rows = [r["id"] for r in store.db.execute(
        "SELECT id FROM nodes WHERE kind='file' ORDER BY id")]
    vecs = store.read_embeddings(rows, *emb.namespace)
    scored = [(sum(a * b for a, b in zip(qv, v)), nid)
              for nid, v in vecs.items()]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [nid for _, nid in scored[:limit]]


def _crosslingual(s, emb, file_ids, files_only, limit) -> int:
    """CP-X2. The pairs are hand-labelled; the labels are re-checked here.

    Two properties decide whether the numbers mean anything, and neither is
    taken on trust from the pair file: a NO_OVERLAP query must share not one
    token with the document it should find, and a ONE_ANCHOR query must share
    exactly one. Both are measured through the FTS index -- which covers the
    title as well as the body, and splits terms the way retrieval does -- so a
    corpus that moved under the labels is caught rather than averaged in.
    """
    try:
        from tests.gold import crosslingual_pairs as xp
    except ImportError:
        print("ERROR: tests/gold/crosslingual_pairs.py is not distributed "
              "(it names real documents). See CP-X2 in TODO.md.",
              file=sys.stderr)
        return 2

    rows = list(s.db.execute(
        "SELECT id, node_key, body FROM nodes WHERE kind='file'"))
    vocab = set(getattr(emb, "vectors", {}) or {})
    def _terms(text):
        """The query's terms as RETRIEVAL splits them.

        `search._WORD` is the tokenizer `fts_query` uses, so the overlap
        verdict is computed over the same term set the retriever will use.
        A bespoke regex here agreed with it on this pair file and would not
        have on camelCase or underscores -- and then a pair would be labelled
        cross-language by a rule nothing else in the package applies.
        """
        return [t.lower() for t in _WORD.findall(text or "")]

    def overlap_terms(query, node_id):
        """Query terms FTS itself matches in this document.

        Asked of the index rather than of a regex over `body`, on codex's
        finding: a bespoke tokenizer can disagree with the one retrieval uses
        (camelCase, underscores, Unicode), and then a pair is labelled
        cross-language by a rule the retriever does not share. Going through
        the index also covers the TITLE, which is indexed and which the first
        version of this check ignored -- a query matching the title outright
        would have passed as "no overlap".
        """
        found = []
        for term in _terms(query):
            if s.db.execute("SELECT 1 FROM nodes_fts WHERE nodes_fts MATCH ? "
                            "AND rowid = ?",
                            ('"%s"' % term, node_id)).fetchone():
                found.append(term)
        return sorted(set(found))

    def resolve(label, spec, want, need_vocab=False):
        pairs, bad = [], []
        for query, suffix in spec:
            hits = [r for r in rows if r["node_key"].endswith(suffix)]
            if len(hits) != 1:
                bad.append((suffix, "%d files match" % len(hits)))
                continue
            overlap = overlap_terms(query, hits[0]["id"])
            if len(overlap) != want:
                bad.append((suffix, "overlap %d, want %d %s"
                            % (len(overlap), want, overlap[:4])))
                continue
            # The in-vocab control is only a control if it is actually in
            # vocabulary. Unchecked, "full coverage" could quietly be false and
            # the comparison would measure two things at once. (codex finding.)
            if need_vocab and vocab:
                oov = [w for w in _terms(query) if w not in vocab]
                if oov:
                    bad.append((suffix, "not in-vocab: %s" % oov))
                    continue
            pairs.append((query, hits[0]["id"]))
        # A set that is empty -- in the file, or after resolution -- reports
        # nothing, and a run that quietly drops a third of its evidence looks
        # exactly like a run that never had it. (codex finding.)
        if not spec:
            print("NOTE: %s is empty; that set is not reported." % label,
                  file=sys.stderr)
        elif not pairs and not bad:
            bad.append((label, "0 pairs resolved from %d rows" % len(spec)))
        if bad:
            print("REFUSED: %d %s pair(s) are not what they claim:"
                  % (len(bad), label), file=sys.stderr)
            for suffix, why in bad:
                print("  %s -- %s" % (suffix, why), file=sys.stderr)
        return pairs, bad

    no_overlap, bad_a = resolve("NO_OVERLAP", xp.NO_OVERLAP, 0)
    one_anchor, bad_b = resolve("ONE_ANCHOR", xp.ONE_ANCHOR, 1)
    in_vocab, bad_c = resolve("IN_VOCAB", getattr(xp, "IN_VOCAB", []), 0,
                          need_vocab=True)
    if bad_a or bad_b or bad_c:
        # Refuse rather than score the survivors: a set that silently shrinks
        # reports a number for a different eval than the one named.
        return 2

    methods = {
        "AND-FTS": lambda q: files_only(
            [h["node_id"] for h in fts_search(s, q, limit=limit)]),
        "OR-BM25": lambda q: files_only(
            _vector_shortlist(s, q, limit, DEFAULT_HIDDEN_SUBTYPES)),
        "shipped": lambda q: files_only(
            [h["node_id"] for h in (
                vector_search(s, q, limit=limit, embedder=emb) or [])]),
        "ceiling": lambda q: _ceiling(s, emb, q, limit),
    }
    # Why the numbers came out the way they did, measured in the same run
    # rather than argued afterwards. A zero is produced by three different
    # failures and they need different fixes: the model cannot align the two
    # languages (a model problem), the matrix has no row for the query's words
    # (a DISTILLATION problem -- the vocabulary is the corpus's, and this
    # corpus is 89% English), or the vectors are fine and the shortlist never
    # offered the document (an architecture decision).
    words = [w for q, _ in xp.NO_OVERLAP + xp.ONE_ANCHOR for w in _terms(q)]
    missing = [w for w in words if w not in vocab] if vocab else []
    if vocab:
        print("\nquery-word coverage: %d of %d words are NOT in the matrix "
              "(%.0f%%)" % (len(missing), len(words),
                            100.0 * len(missing) / len(words)))

    probe = [(a, b) for a, b in getattr(xp, "TRANSLATION_PROBE", [])
             if not vocab or (a in vocab and b in vocab)]
    if probe:
        def cos(u, v):
            nu = sum(x * x for x in u) ** 0.5
            nv = sum(x * x for x in v) ** 0.5
            return sum(x * y for x, y in zip(u, v)) / (nu * nv) if nu and nv else 0.0
        aligned = [cos(emb.embed(a), emb.embed(b)) for a, b in probe]
        # The baseline belongs in the same run: 0.7 means nothing without
        # knowing what two unrelated words score. Seeded, so two runs of the
        # same matrix report the same number.
        import random
        rng = random.Random(7)
        sample = rng.sample(sorted(vocab), min(400, len(vocab)))
        half = len(sample) // 2
        rnd = [cos(emb.embed(a), emb.embed(b))
               for a, b in zip(sample[:half], sample[half:])]
        identical = sum(1 for a, b in probe if a == b)
        print("translation pairs present in the matrix: %d of %d "
              "(%d identical tokens), mean cosine %.3f; random word pairs "
              "%.3f" % (len(probe), len(getattr(xp, "TRANSLATION_PROBE", [])),
                        identical, sum(aligned) / len(aligned),
                        sum(rnd) / len(rnd)))

    for label, pairs in (("no-overlap", no_overlap), ("one-anchor", one_anchor),
                         ("in-vocab", in_vocab)):
        if not pairs:
            continue
        print("\n%s (n=%d, Norwegian query -> English document)"
              % (label, len(pairs)))
        for name, fn in methods.items():
            print("  %-9s %s" % (name, evaluate(pairs, fn, ks=(1, 5, 10)).line()))
        empty = sum(1 for q, _ in pairs
                    if not _vector_shortlist(s, q, limit,
                                             DEFAULT_HIDDEN_SUBTYPES))
        print("  shortlist empty for %d of %d queries" % (empty, len(pairs)))
        # r@10 is 0 for most of this eval, so the useful number is WHERE the
        # answer landed, not whether it made the top ten. A rank of 16 out of
        # 602 and a rank of 600 are both "zero" to recall@10, and they say
        # opposite things about whether the vectors carry any signal at all.
        ranks = []
        for q, expected in pairs:
            order = _ceiling(s, emb, q, 10_000)
            ranks.append(order.index(expected) + 1 if expected in order else None)
        print("  ceiling rank of the answer: %s (of %d files)"
              % (", ".join(str(r) for r in ranks), len(file_ids)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--store", required=True, help="an embedded m3 store")
    ap.add_argument("--embeddings", required=True, help="the matrix data file")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--crosslingual", action="store_true",
                    help="CP-X2: hand-labelled Norwegian->English pairs from "
                         "tests/gold/crosslingual_pairs.py (not distributed) "
                         "instead of the auto-generated heading->file set")
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

        if args.crosslingual:
            return _crosslingual(s, emb, file_ids, files_only, args.limit)

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
