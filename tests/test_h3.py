#!/usr/bin/env python3
"""CP-H3 -- static lookup-table embeddings and hybrid semantic search.

Borrowed idea (idea harvest 2026-07-24, codegraph-ai/CodeGraph, the model2vec
pattern): an embedding can be arithmetic over a distilled `vocab x dim` matrix --
tokenise, look rows up, weighted mean-pool, L2-normalise -- with no neural net at
inference and no download. That is what lets homegraph have semantic search while
keeping `dependencies = []`. It is measured against CP-H1's scoreboard: the claim
is not "we added embeddings" but "the vector path beats the lexical baseline on
the queries where lexical retrieval provably cannot win".

**The matrix here is SYNTHETIC and that is load-bearing.** Real distilled vectors
differ in their last decimal places from one machine's BLAS to another's, so a
gate that embedded real text would assert against numbers no other machine
reproduces -- a test that is green only where it was written. Instead a tiny
hand-authored matrix with known vectors (a "fake embedder") makes every cosine
below computable by hand, before the code runs. The real matrix plugs into the
same loader later; nothing in the mechanism changes. (sim-auditor focus:
fake-embedder gate.)

The load-bearing checks are the ones that can say NO:

  * a document that shares NO query term is never returned, even when its vector
    is identical to the query's -- the vector path reranks a bounded FTS
    shortlist, it does not scan the corpus (sim-auditor: no whole-corpus scan);
  * switching the model returns None, not the old model's vectors served silently
    under the new name (sim-auditor: namespace invalidation);
  * `None` (did not run) and `[]` (ran, found nothing) stay distinct through
    every early exit (sim-auditor: None vs []);
  * the embedder reads a data FILE and never the network (sim-auditor: data file
    not network).

Run:
    python3 tests/test_h3.py
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.models.m3_build import build as m3_build            # noqa: E402
from homegraph.providers import static_embed as se                 # noqa: E402
from homegraph.search import (                                      # noqa: E402
    DEFAULT_HIDDEN_SUBTYPES, _vector_shortlist, fts_search,
    hybrid_search, vector_search)
from homegraph.store import Store                                   # noqa: E402
from tests.eval.scoreboard import evaluate                          # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print("%s  %-58s %s" % ("PASS" if ok else "FAIL", name, detail))


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="h3-", dir=os.path.expanduser("~/.homegraph"))


# -- the synthetic matrix, declared -----------------------------------------
#
# Two orthogonal topic axes. Every token points along exactly one, so a
# document's vector is the (weighted) mean of its tokens' axes and every cosine
# below is a fraction anyone can check with a calculator.
#
#   X = the query's topic (retrieval)   [1,0,0]
#   Y = an off-topic dilutant           [0,1,0]
#   (a third axis exists in `dim` but no corpus token uses it)
#
# The vocabulary is chosen so that TERM OVERLAP and COSINE DISAGREE -- the point
# the first cut of this gate missed. The decoy shares MORE query terms than the
# target, so a purely lexical ranker (OR-BM25) ranks the decoy first; the target
# is a purer topical match, so cosine ranks IT first. Only a working cosine can
# produce the target-first order, which is what makes the gate test the
# embedding and not just OR-recall. (sim-auditor CP-H3 finding 1.)
_X, _Y = [1.0, 0, 0], [0, 1.0, 0]
_VOCAB = {
    "query": _X, "index": _X, "retrieval": _X, "search": _X, "ranking": _X,
    "onion": _Y,
}
_DIM = 3
_MODEL = "syn-a"


def _write_matrix(path: str, model: str = _MODEL,
                  weights: list[float] | None = None) -> str:
    tokens = list(_VOCAB)
    data = {"provider": "static", "model": model, "dim": _DIM,
            "tokens": tokens, "matrix": [_VOCAB[t] for t in tokens]}
    if weights is not None:
        data["weights"] = weights
    json.dump(data, open(path, "w"))
    return path


# -- the mini-corpus, declared ----------------------------------------------
#
# Heading-less on purpose: a file with a heading grows a `section` node whose
# body is just the heading, and that section can out-cosine its own file and
# muddy the ranking claim. With no heading each file is one `file` node, so the
# eval is over documents and nothing else. (Verified in test_h1's corpus probe.)
_CORPUS = {
    # all X -> [1,0,0]. Shares exactly ONE query term ({retrieval}).
    "target.md": "retrieval search ranking\n",
    # query(X), index(X), onion(Y) -> [2/3,1/3,0]. Shares TWO query terms
    # ({query, index}) -- more than the target -- but is diluted off-topic.
    "decoy.md": "query index onion\n",
    # all X -> [1,0,0], the SAME direction as the query, cosine 1.0 -- but shares
    # NOT ONE term with the query, so it is never shortlisted: the no-scan
    # tripwire.
    "zero.md": "search ranking\n",
}
# The query, all three tokens on topic X -> [1,0,0].
#   AND-FTS:  query AND index AND retrieval -> matches nothing (recall 0)
#   OR-BM25:  decoy (2 terms) ranks ABOVE target (1 term)   <- lexical order
#   cosine:   target [1,0,0]=1.000  >  decoy [.894,.447,0]=.894   <- topical order
#   zero:     [1,0,0] cosine 1.000 but NOT in the shortlist (0 shared terms)
# The lexical order and the cosine order DISAGREE, and the target only wins under
# cosine -- so the win is the embedding's, not OR-recall's.
_QUERY = "query index retrieval"
_EXPECTED = "target.md"


def _build_embedded(d: str, model: str = _MODEL,
                    weights: list[float] | None = None
                    ) -> tuple[str, dict[str, int], "se.Embedder"]:
    """Build the mini-corpus, write the matrix, embed every node. Returns
    (db path, {basename: file node id}, embedder)."""
    paths = []
    for name, text in _CORPUS.items():
        p = os.path.join(d, name)
        open(p, "w").write(text)
        paths.append(p)
    db = os.path.join(d, "m3.db")
    with Store(db, model="m3") as s:
        m3_build(s, sorted(paths), "2026-07-22")
        s.rebuild_fts()

    emb = se.load(_write_matrix(os.path.join(d, "m.json"), model, weights))
    prov, mdl, dim = emb.namespace
    with Store(db, model="m3") as s:
        s.begin_immediate()
        for r in s.db.execute("SELECT id, title, body FROM nodes").fetchall():
            text = " ".join(p for p in (r["title"], r["body"]) if p).strip()
            if text:
                s.upsert_embedding(r["id"], prov, mdl, dim, emb.embed(text))

    with Store(db) as s:
        ids = {os.path.basename(r["node_key"]): r["id"] for r in s.db.execute(
            "SELECT id, node_key FROM nodes WHERE kind='file'")}
    return db, ids, emb


def _open(db: str, model: str = _MODEL) -> Store:
    return Store(db, embeddings={"provider": "static", "model": model})


# -- tokenizer, by hand -----------------------------------------------------
def t_tokenizer():
    check("camelCase splits into words",
          se.split_identifiers("getUserById") == ["get", "user", "by", "id"])
    check("an acronym boundary is respected (HTMLParser -> html parser)",
          se.split_identifiers("HTMLParser") == ["html", "parser"])
    check("snake_case, kebab-case and dotted paths all split",
          se.split_identifiers("a_b-c.d") == ["a", "b", "c", "d"])
    check("case is folded so a query matches text written either way",
          se.split_identifiers("Fusion FUSING")
          == ["fusion", "fusing"])


# -- embed, against hand-computed vectors -----------------------------------
def t_embed_by_hand():
    d = _tmp()
    try:
        # A plain mean of two orthogonal unit axes, then L2-normalised:
        #   query(X) + onion(Y) = [1,1,0] / 2 = [.5,.5,0]
        #   / norm(.7071) = [.7071,.7071,0]
        emb = se.load(_write_matrix(os.path.join(d, "m.json")))
        v = emb.embed("query onion")
        h = 1 / math.sqrt(2)
        check("a two-token mean is L2-normalised to the hand value",
              all(abs(a - b) < 1e-9 for a, b in zip(v, [h, h, 0])),
              "%r" % [round(x, 4) for x in v])
        check("the returned vector is a unit vector",
              abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-9)

        # Out-of-vocabulary tokens contribute nothing; all-OOV is the zero
        # vector, not a crash and not a lucky direction.
        z = emb.embed("wholly unknown gibberish")
        check("a string of only unknown tokens embeds to zeros",
              z == [0.0, 0.0, 0.0], "%r" % z)

        # Weighted mean-pool: query weighted 3, onion weighted 1.
        #   3X + 1Y = [3,1,0] / 4 = [.75,.25,0] / norm(.7906)
        #   = [.9487,.3162,0]
        tokens = list(_VOCAB)
        w = [3.0 if t == "query" else 1.0 for t in tokens]
        embw = se.load(_write_matrix(os.path.join(d, "mw.json"), weights=w))
        vw = embw.embed("query onion")
        check("weights tilt the mean toward the heavier token, by the hand value",
              abs(vw[0] - 0.9487) < 1e-3 and abs(vw[1] - 0.3162) < 1e-3,
              "%r" % [round(x, 4) for x in vw])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- the store namespace, round-trip and isolation --------------------------
def t_store_namespace():
    d = _tmp()
    try:
        with Store(os.path.join(d, "s.db"), model="m3") as s:
            nid = s.upsert_node("k", "file", title="t", body="b")
            s.upsert_embedding(nid, "static", "A", 4, [0.1, 0.2, 0.3, 0.4])
            got = s.read_embeddings([nid], "static", "A", 4)
            check("a vector round-trips through float32 within tolerance",
                  nid in got and all(abs(a - b) < 1e-6 for a, b in
                                     zip(got[nid], [0.1, 0.2, 0.3, 0.4])),
                  "%r" % got.get(nid))

            # A vector written under one model is invisible to another's
            # namespace -- the whole of the invalidation guarantee, at the read.
            check("count is per-namespace, not per-table",
                  s.embedding_count("static", "A", 4) == 1
                  and s.embedding_count("static", "B", 4) == 0)
            check("read_embeddings skips other namespaces",
                  s.read_embeddings([nid], "static", "B", 4) == {})

            # A wrong-length vector is refused at the write, where the caller
            # bug is, not later as a silent short cosine.
            try:
                s.upsert_embedding(nid, "static", "A", 4, [1.0, 2.0])
                check("a vector whose length != dim is refused", False, "no raise")
            except ValueError:
                check("a vector whose length != dim is refused", True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- the headline: COSINE beats the strongest lexical baseline --------------
def t_semantic_beats_lexical():
    """The one claim the checkpoint is named for, tested against the RIGHT
    opponent. The first cut of this gate compared the vector path to strict
    AND-FTS, which the OR shortlist beats on lexical recall alone -- so the
    "semantic win" was really an AND->OR win and cosine was never exercised
    (sim-auditor finding 1). The honest opponent is OR-BM25: the very shortlist
    the vector path reranks, before cosine touches it. The target beats the
    decoy ONLY under cosine, so a win here is the embedding's doing.
    """
    d = _tmp()
    try:
        db, ids, emb = _build_embedded(d)
        pair = [(_QUERY, ids[_EXPECTED])]

        with _open(db) as s:
            # AND-FTS still misses entirely -- kept as a labelled fact about
            # OR-recall, NOT as the baseline the semantic claim rides on.
            check("strict AND-FTS misses the query (an OR-recall fact, not the "
                  "semantic one)",
                  fts_search(s, _QUERY) == [])

            # OR-BM25: the shortlist in its own BM25 order, no cosine. This is
            # the strongest lexical ranker, and it puts the DECOY first because
            # the decoy matches two query terms and the target one.
            def or_bm25_fn(q):
                return _vector_shortlist(s, q, 10, DEFAULT_HIDDEN_SUBTYPES)

            def vec_fn(q):
                return [h["node_id"] for h in (
                    vector_search(s, q, limit=10, embedder=emb) or [])]

            base = evaluate(pair, or_bm25_fn, ks=(1,))
            vec = evaluate(pair, vec_fn, ks=(1,))
            check("the strongest lexical baseline (OR-BM25) ranks the DECOY "
                  "first, missing the target",
                  base.recall[1] == 0.0,
                  "or-bm25 r@1=%.2f (decoy=%d target=%d, shortlist=%r)"
                  % (base.recall[1], ids["decoy.md"], ids["target.md"],
                     or_bm25_fn(_QUERY)))
            check("cosine reranking lifts the target to first",
                  vec.recall[1] == 1.0, "vector r@1=%.2f" % vec.recall[1])
            check("so the win is COSINE's, not OR-recall's (beats OR-BM25)",
                  vec.recall[1] > base.recall[1],
                  "vector r@1=%.2f > or-bm25 r@1=%.2f"
                  % (vec.recall[1], base.recall[1]))

            # And name it directly: the decoy shares MORE query terms, yet cosine
            # ranks the target above it. Term count lost to topical direction.
            order = vec_fn(_QUERY)
            # Membership guarded before .index so a mutation that empties the
            # result FAILs cleanly here rather than raising ValueError.
            both = ids["target.md"] in order and ids["decoy.md"] in order
            check("cosine, not term overlap, sets the order (target before decoy)",
                  both and order.index(ids["target.md"])
                  < order.index(ids["decoy.md"]),
                  "order=%r" % [("target" if o == ids["target.md"]
                                 else "decoy" if o == ids["decoy.md"] else o)
                                for o in order])

            res = hybrid_search(s, _QUERY, embedder=emb)
            check("hybrid_search reports out_mode 'hybrid' when the vector ran",
                  res._out_mode == "hybrid", res._out_mode)
            check("hybrid ranks the target first",
                  bool(res.hits) and res.hits[0]["node_id"] == ids["target.md"])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- no whole-corpus scan ----------------------------------------------------
def t_no_whole_corpus_scan():
    """`zero.md` has the highest cosine of any document (its vector equals the
    query's direction) and it must NOT be returned, because it shares no term
    with the query and so is not in the FTS shortlist. If it ever appears, the
    vector path stopped reranking a shortlist and started scanning the corpus --
    the exact cost this design refuses to pay. (sim-auditor invariant.)
    """
    d = _tmp()
    try:
        db, ids, emb = _build_embedded(d)
        with _open(db) as s:
            hits = vector_search(s, _QUERY, embedder=emb)
            returned = {h["node_id"] for h in hits}
            check("the highest-cosine zero-overlap doc is NOT returned (no scan)",
                  ids["zero.md"] not in returned,
                  "zero.md %s in results"
                  % ("IS" if ids["zero.md"] in returned else "not"))
            # And to be sure the tripwire is armed: its cosine really is the
            # top one, so its absence is the shortlist's doing, not a low score.
            qv = emb.embed(_QUERY)
            zv = s.read_embeddings([ids["zero.md"]], *emb.namespace)[ids["zero.md"]]
            # 1e-6, not tighter: zv was stored and re-read as float32, so its
            # cosine with the float64 query lands a few 1e-8 off exact 1.0.
            check("zero.md's cosine really is ~1.0 (so the tripwire is armed)",
                  abs(se.cosine(qv, zv) - 1.0) < 1e-6,
                  "cosine=%.7f" % se.cosine(qv, zv))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- None (did not run) vs [] (ran, empty) ----------------------------------
def t_none_vs_empty():
    d = _tmp()
    try:
        db, ids, emb = _build_embedded(d)

        # embeddings off: the path never ran.
        with Store(db) as s:
            check("embeddings off -> None (did not run), never []",
                  vector_search(s, _QUERY, embedder=emb) is None)

        with _open(db) as s:
            # configured, embedded, but no document shares a term with the
            # query: the path RAN and found nothing.
            empty = vector_search(s, "zzzqqq wwwvvv", embedder=emb)
            check("a query overlapping nothing -> [] (ran, empty), never None",
                  empty == [], "%r" % empty)

            # configured but no embedder handed in: refuse rather than silently
            # return an empty vector result -- the module's standing contract.
            try:
                vector_search(s, _QUERY, embedder=None)
                check("configured but no embedder -> refuses, not silent empty",
                      False, "no raise")
            except NotImplementedError:
                check("configured but no embedder -> refuses, not silent empty",
                      True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- namespace invalidation on a model switch --------------------------------
def t_namespace_invalidation():
    """Embed under `syn-a`, then search as `syn-b`. The old vectors are still in
    the table, but under a different namespace, and the honest answer is "did not
    run -- re-embed", not those vectors served silently under the new name.
    (sim-auditor invariant.)
    """
    d = _tmp()
    try:
        db, ids, _ = _build_embedded(d, model="syn-a")
        emb_b = se.load(_write_matrix(os.path.join(d, "mb.json"), model="syn-b"))
        # Opened as syn-b to match the switched model.
        with _open(db, model="syn-b") as s:
            check("vectors exist in the table but not in the new namespace",
                  s.embedding_count("static", "syn-a", _DIM) > 0
                  and s.embedding_count("static", "syn-b", _DIM) == 0)
            check("a model switch returns None (old vectors are NOT served)",
                  vector_search(s, _QUERY, embedder=emb_b) is None)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- the data file, and only the data file ----------------------------------
def t_data_file_not_network():
    d = _tmp()
    try:
        # A missing matrix is a distinct, catchable error -- the CLI turns it
        # into exit 2, never a traceback and never a silent skip.
        try:
            se.from_config({"provider": "static", "model": "x",
                            "path": os.path.join(d, "nope.json")})
            check("a missing matrix raises EmbedderDataMissing", False, "no raise")
        except se.EmbedderDataMissing:
            check("a missing matrix raises EmbedderDataMissing", True)

        # A config whose declared model disagrees with the matrix is refused,
        # so vectors cannot be written under one name and searched under another.
        se.load(_write_matrix(os.path.join(d, "m.json"), model="syn-a"))
        try:
            se.from_config({"provider": "static", "model": "MISMATCH",
                            "path": os.path.join(d, "m.json")})
            check("a config/matrix model mismatch is refused", False, "no raise")
        except se.StaticEmbedError:
            check("a config/matrix model mismatch is refused", True)

        # The mechanism is a file read, not a fetch. A tripwire over the
        # provider's own source, catching the obvious routes: a network import,
        # a DYNAMIC import (__import__/importlib) that a plain import-line grep
        # would miss, a urlopen, or a raw socket. It inspects THIS file, not the
        # whole call graph, so it is a guard against the change that adds a fetch
        # -- not a proof there can never be one. (sim-auditor finding 5.)
        src = open(se.__file__).read()
        net = re.search(
            r"^\s*(import|from)\s+(socket|urllib|http|requests|asyncio|ssl)\b"
            r"|__import__|importlib|urlopen|socket\.socket|\.connect\(",
            src, re.M)
        check("the static embedder reaches for no network (data file, not net)",
              net is None, net.group(0) if net else "clean")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- the embed command refuses honestly, and works ---------------------------
def t_embed_command():
    from homegraph import cli

    d = _tmp()
    try:
        db, ids, _ = _build_embedded(d)   # builds the corpus + matrix at m.json
        # Wipe the vectors _build_embedded wrote, so the command's own work is
        # what the count below measures.
        with Store(db, model="m3") as s:
            s.begin_immediate()
            s.db.execute("DELETE FROM embeddings")

        def _cfg(body: str) -> str:
            p = os.path.join(d, "config.toml")
            open(p, "w").write(body)
            return p

        class Args:
            def __init__(self, config, model):
                self.config, self.model = config, model

        # No [embeddings] block -> exit 2, not a green no-op.
        cfg_off = _cfg('root = "%s"\n[roles]\nimage = []\n' % d)
        rc = cli.cmd_embed(Args(cfg_off, ["m3=%s" % db]))
        check("embed with no [embeddings] block exits 2", rc == 2, "rc=%d" % rc)

        # A block pointing at a missing matrix -> exit 2.
        cfg_missing = _cfg(
            'root = "%s"\n[roles]\nimage = []\n[embeddings]\n'
            'provider = "static"\nmodel = "syn-a"\ndim = %d\n'
            'path = "%s"\n' % (d, _DIM, os.path.join(d, "gone.json")))
        rc = cli.cmd_embed(Args(cfg_missing, ["m3=%s" % db]))
        check("embed with a missing matrix exits 2", rc == 2, "rc=%d" % rc)

        # A valid block -> vectors written, exit 0.
        cfg_ok = _cfg(
            'root = "%s"\n[roles]\nimage = []\n[embeddings]\n'
            'provider = "static"\nmodel = "syn-a"\ndim = %d\n'
            'path = "%s"\n' % (d, _DIM, os.path.join(d, "m.json")))
        rc = cli.cmd_embed(Args(cfg_ok, ["m3=%s" % db]))
        with Store(db) as s:
            n = s.embedding_count("static", "syn-a", _DIM)
        check("embed with a valid config writes vectors and exits 0",
              rc == 0 and n > 0, "rc=%d embedded=%d" % (rc, n))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_search_modes():
    """--mode picks the retriever, and each mode is honest about which ran.

    'vector' is the reason this exists: pure cosine order, no RRF fusion, so a
    strong lexical match cannot ride up next to the semantic ones (the wart the
    real-corpus stop-rule surfaced). The load-bearing checks are that vector
    mode does NOT fuse and has NO lexical fallback -- a silent fallback would
    answer a different question than the one asked.
    """
    from homegraph import cli

    d = _tmp()
    try:
        db, ids, emb = _build_embedded(d)
        with _open(db) as s:
            r = hybrid_search(s, _QUERY, embedder=emb, mode="vector")
            check("mode=vector reports out_mode 'vector'",
                  r._out_mode == "vector", r._out_mode)
            check("mode=vector ranks by cosine (target first)",
                  bool(r.hits) and r.hits[0]["node_id"] == ids["target.md"])
            check("mode=vector does not fuse (every source is vector-only)",
                  all(all(src.startswith("vector#") for src in h.get("sources", []))
                      for h in r.hits) and bool(r.hits))

            # fts mode ignores the embedder entirely.
            rf = hybrid_search(s, "retrieval", embedder=emb, mode="fts")
            check("mode=fts reports out_mode 'fts' even with an embedder passed",
                  rf._out_mode == "fts", rf._out_mode)
            check("mode=fts returns the lexical hit",
                  ids["target.md"] in [h["node_id"] for h in rf.hits])

            try:
                hybrid_search(s, _QUERY, embedder=emb, mode="magic")
                check("an unknown mode is refused", False, "no raise")
            except ValueError:
                check("an unknown mode is refused", True)

        # vector mode with embeddings OFF: nothing, and it says why -- no
        # silent fall-through to lexical.
        with Store(db) as s:
            r = hybrid_search(s, _QUERY, embedder=emb, mode="vector")
            check("mode=vector with embeddings off returns nothing, and says why",
                  r._out_mode == "vector" and r.hits == []
                  and any("vector mode" in w for w in r.warnings),
                  "%r %r" % (r._out_mode, r.warnings))

        # CLI refuses vector mode without a matrix, up front.
        class Args:
            def __init__(self):
                self.db, self.query, self.limit = db, [_QUERY], 20
                self.embeddings, self.mode = None, "vector"
        check("CLI: --mode vector without --embeddings exits 2",
              cli.cmd_search(Args()) == 2)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    t_tokenizer()
    t_embed_by_hand()
    t_store_namespace()
    t_semantic_beats_lexical()
    t_no_whole_corpus_scan()
    t_none_vs_empty()
    t_namespace_invalidation()
    t_data_file_not_network()
    t_embed_command()
    t_search_modes()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h3():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
