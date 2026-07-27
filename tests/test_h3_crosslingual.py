#!/usr/bin/env python3
"""CP-X1 -- the cross-language half of H3's honest gap, as a number.

H3 shipped with a qualitative claim and said so: a pure Norwegian query against
an English corpus "returned empty", recorded as a probe rather than a
measurement because auto-generated heading->file pairs cannot label a
translation. This file labels them by hand and scores them.

**What is measured is the plumbing, not the model.** The matrix is hand-built so
a Norwegian word and its English translation share the SAME vector exactly -- a
perfect multilingual embedder by construction. Every number below is therefore
about the retrieval path and nothing about `potion-multilingual-128M`, which is
CP-X2's job. That is what makes the ceiling meaningful: if a PERFECT embedder
cannot deliver a cross-language hit through the shipped path, no real one will.

Three retrievers over the same labelled pairs:

    OR-BM25    the lexical baseline, and the control -- a no-overlap pair that
               scores above zero here is a mislabelled pair, not a discovery.
    shipped    `vector_search`: cosine over the OR-FTS shortlist, exactly what
               `search --embeddings` runs.
    ceiling    cosine over every file node, computed here and NOT shipped: what
               the same vectors retrieve when the shortlist is not in the way.

And two labelled sets, because the interesting answer is a boundary and not a
verdict:

    no-overlap   query and answer share not one token. The honest worst case.
    one-token    query and answer share exactly ONE token -- a product name, an
                 identifier, a number. Real bilingual corpora are full of these,
                 and they are the case where the shortlist can still reach the
                 document.

The distance between `shipped` and `ceiling` is the shortlist, and the
shortlist is a decision (`no whole-corpus scan`, DECISIONS §29), not a defect.
Naming it with a number is this checkpoint's job; changing it is not, and is
left as an explicit question rather than answered here.

`zero.md` in test_h3.py already gates the mechanism -- one document with cosine
1.0 that is never shortlisted. This is not a second copy: that check asks
whether a document can be missed, this one asks what a labelled eval scores,
which is the form the open thread asked for.

Run:
    python3 tests/test_h3_crosslingual.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.models.m3_build import build as m3_build           # noqa: E402
from homegraph.providers import static_embed as se                # noqa: E402
from homegraph.search import (                                     # noqa: E402
    DEFAULT_HIDDEN_SUBTYPES, _vector_shortlist, fts_search, vector_search)
from homegraph.store import Store                                  # noqa: E402
from tests.eval.scoreboard import evaluate                         # noqa: E402

results, check = reporter(60)

AS_OF = "2026-07-22"
_MODEL = "xl-syn"

# -- the corpus, one language per topic -------------------------------------
#
# Each topic exists in EXACTLY ONE language, and its query is in the other. The
# first cut of this fixture had both languages of every topic, and both numbers
# it produced were artefacts of that: the same-language file is a lexical decoy
# that fills the shortlist, and under a perfect embedder it TIES with its own
# translation at cosine 1.0, so the ceiling was a coin flip (measured: r@1
# 0.500). One language per topic gives every pair exactly one right answer.
#
# `shared` is the one token the document and the query have in common -- a
# product name, which is how a real bilingual corpus leaks lexical signal
# across a language boundary. None for the no-overlap set.
_TOPICS = {
    # topic:      (doc words,                        query words,                     shared)
    "memory":     (["memory", "sessions", "recall"],
                   ["hukommelse", "økter", "gjenkalling"], None),
    "trails":     (["trails", "hiking", "mountain"],
                   ["stier", "fottur", "fjell"], None),
    "archive":    (["archive", "storage", "photographs"],
                   ["arkiv", "lagring", "fotografier"], None),
    "viewer":     (["viewer", "renders", "windows"],
                   ["framviser", "tegner", "vindu"], None),
    "kart":       (["kart", "rutenett", "koordinater"],
                   ["map", "grid", "coordinates"], None),
    "oppskrift":  (["oppskrift", "ingredienser", "steking"],
                   ["recipe", "ingredients", "baking"], None),
    "regnskap":   (["regnskap", "faktura", "bilag"],
                   ["accounting", "invoice", "voucher"], None),
    "verksted":   (["verksted", "sveising", "dreiebenk"],
                   ["workshop", "welding", "lathe"], None),
    # -- the one-token set. The shared string is a name, untranslated. --
    "obsidian":   (["notatvelger", "hvelv", "lenkegraf"],
                   ["obsidian", "vault", "linkgraph"], "obsidian"),
    "graphify":   (["graphify", "fletting", "delgraf"],
                   ["graphify", "merging", "subgraph"], "graphify"),
    "sqlite":     (["sqlite", "spørreplan", "indekser"],
                   ["sqlite", "queryplan", "indexes"], "sqlite"),
    "ollama":     (["ollama", "endepunkt", "vektorlengde"],
                   ["ollama", "endpoint", "veclength"], "ollama"),
}
# The shared token has to be IN the document, or "one-token overlap" is a label
# and not a fact. `obsidian` sits in the Norwegian document's word list below.
for _t, (_doc, _q, _sh) in _TOPICS.items():
    if _sh and _sh not in _doc:
        _doc.append(_sh)

# One extra axis for decoy padding. Its words appear in no query, which is the
# whole requirement: the first pad reused the trails topic's Norwegian words,
# and the trails query then matched the decoys -- the shortlist stopped being
# empty and the attribution check went red. Correctly: a "no-overlap" query
# that matches a document somewhere is not a no-overlap query.
_NOISE = ["ballast", "fyllstoff"]
_DIM = len(_TOPICS) + 1
_AXES = {t: [1.0 if i == n else 0.0 for i in range(_DIM)]
         for n, t in enumerate(list(_TOPICS) + ["noise"])}
_VOCAB = {w: _AXES[t]
          for t, (doc, q, _) in _TOPICS.items() for w in doc + q}
_VOCAB.update({w: _AXES["noise"] for w in _NOISE})

# Heading-less, for test_h3's reason: a heading grows a `section` node that can
# out-cosine its own file and muddy a document-level claim.
_CORPUS = {"%s.md" % t: " ".join(doc) + "\n"
           for t, (doc, _, _) in _TOPICS.items()}

# -- ranking headroom for the one-token set ---------------------------------
#
# One document per topic left the one-token comparison vacuous: both retrievers
# scored 1.000 because only one document was reachable at all, so "the vector
# path does not beat OR-BM25" was a fact about the fixture, not about cosine
# (codex found it; the first version of this file asserted it anyway).
#
# Each anchor now also appears in a DECOY that is topically wrong: the anchor
# twice, in a short document, padded with another topic's words. BM25 rewards
# term frequency and short documents, so the decoy outranks the real answer
# lexically; the decoy's vector points mostly at the other topic's axis, so
# cosine does not. Term order and cosine order therefore DISAGREE, which is the
# only arrangement in which a win can be credited to the embedding.
_CORPUS.update({
    "decoy-%s.md" % t: "%s %s %s\n" % (sh, sh, " ".join(_NOISE))
    for t, (_, _, sh) in _TOPICS.items() if sh})

_NO_OVERLAP = [(" ".join(q), "%s.md" % t)
               for t, (_, q, sh) in _TOPICS.items() if sh is None]
_ONE_TOKEN = [(" ".join(q), "%s.md" % t)
              for t, (_, q, sh) in _TOPICS.items() if sh is not None]


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="xl-", dir=os.path.expanduser("~/.homegraph"))


def _build(d: str) -> tuple[str, dict[str, int], "se.Embedder"]:
    paths = []
    for name, text in _CORPUS.items():
        p = os.path.join(d, name)
        open(p, "w").write(text)
        paths.append(p)
    db = os.path.join(d, "m3.db")
    with Store(db, model="m3") as s:
        m3_build(s, sorted(paths), AS_OF)
        s.rebuild_fts()

    mpath = os.path.join(d, "m.json")
    tokens = list(_VOCAB)
    json.dump({"provider": "static", "model": _MODEL, "dim": _DIM,
               "tokens": tokens, "matrix": [_VOCAB[t] for t in tokens]},
              open(mpath, "w"))
    emb = se.load(mpath)
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


def _open(db: str) -> Store:
    return Store(db, embeddings={"provider": "static", "model": _MODEL})


def _ceiling(store, emb, query, limit=10):
    """Cosine over every file node. The retriever this package does NOT ship.

    Written here rather than imported from `search`, because importing it would
    mean it exists, and the measured claim is about the path that does.
    """
    qv = emb.embed(query)
    rows = [r["id"] for r in store.db.execute(
        "SELECT id FROM nodes WHERE kind='file' ORDER BY id")]
    prov, mdl, dim = emb.namespace
    vecs = store.read_embeddings(rows, prov, mdl, dim)
    scored = [(sum(a * b for a, b in zip(qv, v)), nid)
              for nid, v in vecs.items()]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [nid for _, nid in scored[:limit]]


def t_pairs_are_labelled_honestly(db, ids):
    """The control the whole file rests on.

    A no-overlap pair that shares a content word with its answer is a lexical
    pair wearing a cross-language label, and it lifts every number below
    without anything semantic happening. Checked against the STORED body, so a
    corpus that drifts from the word lists above is caught too.
    """
    with _open(db) as s:
        body = {r["id"]: set((r["body"] or "").lower().split())
                for r in s.db.execute(
                    "SELECT id, body FROM nodes WHERE kind='file'")}

    def overlap(query, target):
        return sorted(set(query.lower().split()) & body[ids[target]])

    # The guard needs a pair it MUST reject, or it cannot tell "no leaks" from
    # "not looking". Measured: without this line, defanging the comparison to
    # `leaks = []` left the suite green -- the check reported a clean fixture
    # either way. [[empty-gate-patterns]], caught by this file's own mutation.
    planted = "%s and something else" % sorted(body[ids[_NO_OVERLAP[0][1]]])[0]
    leaks = [(t, overlap(q, t)) for q, t in _NO_OVERLAP if overlap(q, t)]
    check("no-overlap pairs share NO token -- and a planted one is caught",
          bool(_NO_OVERLAP) and not leaks
          and bool(overlap(planted, _NO_OVERLAP[0][1])),
          "%d pair(s), %d leaking %s; sentinel -> %s"
          % (len(_NO_OVERLAP), len(leaks), leaks[:2],
             overlap(planted, _NO_OVERLAP[0][1])))

    # The other set has to overlap by exactly one, or it is not measuring the
    # boundary it is named for -- two tokens would make it an ordinary lexical
    # query with an accent.
    sizes = [len(set(q.lower().split()) & body[ids[t]]) for q, t in _ONE_TOKEN]
    check("one-token pairs share EXACTLY one token, not zero and not two",
          bool(sizes) and set(sizes) == {1}, "overlaps %s" % sizes)

    # Every token, not the first of each list: checking `doc[0] == q[0]` passes
    # while any later translation drifts onto another axis, and the ceiling
    # below would then measure a broken fixture rather than a perfect embedder.
    # (codex finding.)
    off = [(t, w) for t, (doc, q, _) in _TOPICS.items()
           for w in doc + q if _VOCAB[w] != _AXES[t]]
    check("EVERY word and translation shares its topic's vector",
          not off, "%d topic(s), dim %d, %d off-axis %s"
          % (len(_TOPICS), _DIM, len(off), off[:2]))


def _retrievers(s, emb, files):
    """AND-FTS, OR-BM25 and the shipped vector path, filtered identically.

    OR-BM25 is `_vector_shortlist` itself -- the pool the vector path reranks,
    BM25-ordered. Measuring against it rather than against AND-FTS is the
    lesson DECISIONS §29 paid for: the first "semantic beats lexical" gate in
    this package compared cosine to a strawman it already beat, and a constant
    `_cosine` left it green.
    """
    def and_fts(q):
        return [h["node_id"] for h in fts_search(
            s, q, limit=10, hidden_subtypes=DEFAULT_HIDDEN_SUBTYPES)
            if h["node_id"] in files]

    def or_bm25(q):
        return [i for i in _vector_shortlist(s, q, 10, DEFAULT_HIDDEN_SUBTYPES)
                if i in files]

    def shipped(q):
        hits = vector_search(s, q, limit=10, embedder=emb)
        return [h["node_id"] for h in (hits or []) if h["node_id"] in files]

    return and_fts, or_bm25, shipped


def t_no_overlap_scores(db, ids, emb):
    """The worst case, with the reason attached."""
    pairs = [(q, ids[t]) for q, t in _NO_OVERLAP]
    files = set(ids.values())
    with _open(db) as s:
        and_fts, or_bm25, shipped = _retrievers(s, emb, files)
        lex = evaluate(pairs, or_bm25)
        ship = evaluate(pairs, shipped)
        ceil = evaluate(pairs, lambda q: _ceiling(s, emb, q))

        check("no-overlap: OR-BM25 scores zero (the control)",
              lex.recall[1] == 0.0 and lex.mrr == 0.0
              and evaluate(pairs, and_fts).recall[1] == 0.0, lex.line())
        check("no-overlap: the SHIPPED vector path scores zero too",
              ship.recall[1] == 0.0 and ship.mrr == 0.0, ship.line())
        check("no-overlap: the CEILING answers every pair",
              ceil.recall[1] == 1.0 and ceil.mrr == 1.0, ceil.line())

        # Attribution. Without this the two zeros are equally consistent with a
        # broken embedder, a broken store, or an empty corpus -- and which one
        # it is IS the finding.
        empty = [q for q, _ in _NO_OVERLAP
                 if not _vector_shortlist(s, q, 40, DEFAULT_HIDDEN_SUBTYPES)]
        check("no-overlap: and the reason is the empty shortlist, every time",
              len(empty) == len(_NO_OVERLAP),
              "%d of %d queries shortlist nothing" % (len(empty),
                                                      len(_NO_OVERLAP)))


def t_one_token_scores(db, ids, emb):
    """The boundary: one shared token is enough for the shortlist to reach it.

    Reach comes from the anchor and ordering comes from cosine, and this
    separates them. Each anchor also sits in a decoy that repeats it twice in a
    short off-topic document, so BM25 -- which rewards term frequency and
    brevity -- puts the decoy FIRST. Measured: OR-BM25 r@1 0.000 (mrr 0.500,
    the answer is second); the shipped path r@1 1.000. The lexical order and
    the cosine order disagree, and only cosine produces the right one, which is
    the arrangement that lets the win be credited to the embedding rather than
    to OR-recall.

    Two earlier versions of this function were wrong and both are worth
    recording. The first asserted the shipped path "answers every pair" and
    called that a cross-language win -- OR-BM25 answered them too. The second
    asserted the two were EQUAL, which was true and vacuous: with one document
    per topic there was nothing for either to get wrong (codex named it). The
    decoy is what made the question answerable.
    """
    pairs = [(q, ids[t]) for q, t in _ONE_TOKEN]
    files = set(ids.values())
    with _open(db) as s:
        and_fts, or_bm25, shipped = _retrievers(s, emb, files)
        strict = evaluate(pairs, and_fts)
        lex = evaluate(pairs, or_bm25)
        ship = evaluate(pairs, shipped)

        check("one-token: AND-FTS still scores zero (one anchor is not three)",
              strict.recall[1] == 0.0, strict.line())
        # The anchor is what makes the answer REACHABLE -- it is in the pool at
        # all -- and BM25 still puts the decoy above it.
        # `mrr == 0.5` is rank TWO exactly -- the decoy first, the answer right
        # behind it. r@1=0 with r@10=1 would also pass at rank 7, and then the
        # claim "BM25 misranks it" would be hiding a fixture where the anchor
        # barely works at all. (codex finding.)
        check("one-token: the anchor reaches the answer, BM25 puts it second",
              lex.recall[1] == 0.0 and lex.mrr == 0.5, lex.line())
        check("one-token: cosine corrects the order -- the measured win",
              ship.recall[1] == 1.0 and ship.mrr > lex.mrr,
              "OR-BM25 %s | shipped %s" % (lex.line(), ship.line()))


def main() -> int:
    d = _tmp()
    try:
        db, ids, emb = _build(d)
        t_pairs_are_labelled_honestly(db, ids)
        t_no_overlap_scores(db, ids, emb)
        t_one_token_scores(db, ids, emb)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    bad = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_checkpoint_h3_crosslingual():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
