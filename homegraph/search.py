#!/usr/bin/env python3
"""Search: FTS5, optional vectors, RRF fusion -- and honesty about which ran.

`hybrid_search` always reports `_out_mode`: which retrieval path actually
produced the answer. That field exists because of a specific failure in
code-review-graph (#711): the vector index was never built, hybrid search
quietly degraded to lexical-only, and a headline evaluation number could not be
reproduced afterwards. Nothing crashed. Nothing warned. The result just meant
something other than what it claimed.

So the contract here is:

  * A query that finds nothing returns nothing. BM25 will happily rank
    documents for a term none of them contain if you let it match loosely --
    terms are ANDed, so it cannot.
  * A natural-language sentence with embeddings off returns **0 hits**, with
    `_out_mode == "fts"` and a warning. That is not a bug to be smoothed over;
    it is the honest answer to a semantic question asked of a lexical index,
    and CP-1 asserts it.
  * Fusion is RRF over ranks, never a comparison of raw scores. BM25 scores and
    cosine similarities are not commensurable, and averaging them produces a
    ranking that looks plausible and is wrong. That is the single most likely
    silent failure in the whole project.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .store import Store


class Embedder(Protocol):
    """The one thing vector_search needs from a provider: turn text into a
    vector and name the namespace those vectors live in. `providers.static_embed`
    satisfies it; a network provider would too, without this file changing."""

    @property
    def namespace(self) -> tuple[str, str, int]: ...

    def embed(self, text: str) -> list[float]: ...

# A hit is a loose row: FTS columns plus whatever the fuser adds. Typed as a
# mapping rather than a dataclass because it flows straight out of sqlite3.Row
# and gaining a class here would mean copying every row twice.
Hit = dict[str, Any]

RRF_K = 60
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class SearchResult:
    hits: list[Hit]     # {node_id, node_key, title, rank, score, sources}
    # "fts" when the search was lexical only -- no embedder passed, or one
    # passed but nothing embedded under the current namespace (the vector path
    # returned None). "hybrid" when the vector path RAN and its ranking was
    # fused with the lexical one, whether or not cosine surfaced anything: the
    # honest report is that both retrievers ran, not that one of them scored. A
    # bare "vector" mode is not produced here because the lexical search always
    # runs first when there is a query; it is left in the annotation as the
    # value a vector-only caller would set. "none" is written nowhere.
    out_mode: str       # "fts" | "hybrid" (see above); "vector" reserved
    warnings: list[str] = field(default_factory=list)

    # The plan names this field `_out_mode`; expose both so neither spelling
    # silently returns None at a call site.
    @property
    def _out_mode(self) -> str:
        return self.out_mode

    def __len__(self) -> int:
        return len(self.hits)


def fts_query(text: str | None, op: str = "AND") -> str:
    """Turn free text into an FTS5 expression, terms joined by `op`.

    Punctuation is stripped rather than escaped: an unquoted `?` or `-` is FTS5
    *syntax*, so passing a user's sentence through raw either raises or, worse,
    silently changes what was asked.

    `op` is "AND" everywhere the RESULT is shown to a user: a search that
    returns documents missing half the query is a search that lies about what it
    found. It is "OR" in exactly one place -- the vector path's candidate
    shortlist -- where the point is the opposite: gather every document sharing
    ANY term so the embedder can rerank a wider net than strict AND would admit,
    which is where a paraphrase that shares one word with its target gets its
    chance. The OR pool is never shown; it is only reordered by cosine.
    """
    terms = _WORD.findall(text or "")
    return (" %s " % op).join('"%s"' % t.replace('"', '""') for t in terms)


# Subtypes hidden unless the caller asks for everything. `transcript` is here
# because agent logs would otherwise bury hand-written notes by sheer count --
# the plan expected 3 247 of them against 42 wiki pages. (That count turned out
# to be wrong; see m3_markdown.py. The filter stands regardless: a default that
# only works at one corpus ratio is not a default.)
DEFAULT_HIDDEN_SUBTYPES = ("transcript",)


def fts_search(store: "Store", query: str, limit: int = 20,
               hidden_subtypes: Sequence[str] = DEFAULT_HIDDEN_SUBTYPES
               ) -> list[Hit]:
    expr = fts_query(query)
    if not expr:
        return []
    sql = ("""SELECT n.id node_id, n.node_key, n.title, n.title_confidence,
                     n.subtype, bm25(nodes_fts) score
              FROM nodes_fts JOIN nodes n ON n.id = nodes_fts.rowid
              WHERE nodes_fts MATCH ?""")
    args: list[object] = [expr]
    if hidden_subtypes:
        sql += " AND (n.subtype IS NULL OR n.subtype NOT IN (%s))" % (
            ",".join("?" * len(hidden_subtypes)))
        args.extend(hidden_subtypes)
    sql += " ORDER BY score LIMIT ?"
    args.append(limit)
    return [dict(r) for r in store.db.execute(sql, args).fetchall()]


# How many candidates the OR-shortlist pulls before cosine reranks them. A
# multiple of the result limit, floored so a small query still gets a real pool:
# the shortlist is meant to be WIDER than the answer, so cosine has losers to
# rank below the winners, but bounded, so a semantic search never becomes a scan
# of every vector in the store. That bound is the invariant -- the union that
# feeds cosine must be a proper subset, never "the whole corpus because the
# shortlist came back empty".
SHORTLIST_FACTOR = 5
SHORTLIST_FLOOR = 50


def _vector_shortlist(store: "Store", query: str, limit: int,
                      hidden_subtypes: Sequence[str]) -> list[int]:
    """node_ids of documents sharing ANY query term, best BM25 first.

    OR, not AND, and this is the whole reason the vector path can beat the
    lexical baseline: a paraphrase that shares a single word with its target is
    invisible to the AND search that ranks the shown results, but lands in this
    pool, where cosine can lift it. The pool is capped at `limit`, so cosine
    runs over a bounded shortlist and never over the corpus.
    """
    expr = fts_query(query, op="OR")
    if not expr:
        return []
    sql = ("SELECT n.id node_id FROM nodes_fts JOIN nodes n "
           "ON n.id = nodes_fts.rowid WHERE nodes_fts MATCH ?")
    args: list[object] = [expr]
    if hidden_subtypes:
        sql += " AND (n.subtype IS NULL OR n.subtype NOT IN (%s))" % (
            ",".join("?" * len(hidden_subtypes)))
        args.extend(hidden_subtypes)
    sql += " ORDER BY bm25(nodes_fts) LIMIT ?"
    args.append(limit)
    return [r["node_id"] for r in store.db.execute(sql, args).fetchall()]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    # Both operands are L2-normalised vectors from the embedder, so cosine is
    # their dot; a zero vector (a query of only out-of-vocabulary words) dots to
    # 0 and ranks nothing, which is the honest answer, not a crash.
    return sum(x * y for x, y in zip(a, b))


def vector_search(store: "Store", query: str, limit: int = 20,
                  embedder: "Embedder | None" = None,
                  hidden_subtypes: Sequence[str] = DEFAULT_HIDDEN_SUBTYPES
                  ) -> list[Hit] | None:
    """Rerank an FTS shortlist by cosine. Returns None when it did NOT run.

    The None/[] distinction is the entire point and is preserved through every
    early exit:

      * embeddings off               -> None  (never ran; the caller warns)
      * configured, no embedder given -> raise (a caller enabled the path and
                                          withheld the means; refusing beats a
                                          silent empty, same as before)
      * nothing embedded in THIS namespace -> None  (a model switch left the old
                                          vectors under another namespace; they
                                          are ignored, and the honest report is
                                          "did not run -- re-embed", not [])
      * ran, but no document shared a term -> []    (searched, found nothing)

    What it never does is score every vector in the store. Cosine sees only the
    OR-shortlist, a bounded pool; a document that shares no query term is not in
    it and cannot be returned, even if its vector is identical to the query's.
    That is the cost of not scanning the corpus, and it is deliberate.
    """
    if store.embeddings is None:
        return None
    if embedder is None:
        raise NotImplementedError(
            "embeddings are configured but no embedder was supplied; refusing "
            "to return a silently empty vector result")
    provider, model, dim = embedder.namespace
    if store.embedding_count(provider, model, dim) == 0:
        # Configured, and vectors may even exist -- but not in this namespace.
        # Never ran here; the caller should re-embed under the current model.
        return None

    pool = max(limit * SHORTLIST_FACTOR, SHORTLIST_FLOOR)
    shortlist = _vector_shortlist(store, query, pool, hidden_subtypes)
    if not shortlist:
        return []                       # ran; nothing lexically overlapping
    qvec = embedder.embed(query)
    vecs = store.read_embeddings(shortlist, provider, model, dim)
    # A shortlisted node with no vector in THIS namespace is skipped, not scored.
    # If the count above was >0 but none of the shortlisted nodes are embedded
    # here -- partial coverage across a namespace boundary -- `scored` ends
    # empty and this returns []. That is honest ("ran, nothing rankable"), and
    # in normal full-embed operation it does not arise: every FTS-indexed node
    # has title/body and is therefore embedded. (sim-auditor finding 6.)
    scored: list[tuple[float, int]] = []
    for nid in shortlist:
        v = vecs.get(nid)
        if v is not None:
            scored.append((_cosine(qvec, v), nid))
    scored.sort(key=lambda t: -t[0])
    ranked_ids = [nid for _, nid in scored[:limit]]
    return _hits_for(store, ranked_ids)


def _hits_for(store: "Store", node_ids: Sequence[int]) -> list[Hit]:
    """Display rows for `node_ids`, in the order given (cosine order preserved).

    Carries the same columns fts_search does -- including `title_confidence`, so
    an inferred title stays flagged through the vector path exactly as CP-H2
    made it through the lexical one -- so the RRF fuser and the CLI see one hit
    shape regardless of which retriever produced it.
    """
    if not node_ids:
        return []
    placeholders = ",".join("?" * len(node_ids))
    rows = {r["node_id"]: dict(r) for r in store.db.execute(
        "SELECT id node_id, node_key, title, title_confidence, subtype "
        "FROM nodes WHERE id IN (%s)" % placeholders, list(node_ids))}
    return [rows[nid] for nid in node_ids if nid in rows]


def rrf_fuse(rankings: Mapping[str, Sequence[Hit]], k: int = RRF_K,
             limit: int = 20) -> list[Hit]:
    """Reciprocal rank fusion. Consumes ranks, never scores.

    rankings: {source_name: [hit, ...]} already ordered best-first.
    """
    fused: dict[int, Hit] = {}
    for source, hits in rankings.items():
        for rank, hit in enumerate(hits, start=1):
            slot = fused.setdefault(hit["node_id"], {
                "node_id": hit["node_id"],
                "node_key": hit.get("node_key"),
                "title": hit.get("title"),
                "score": 0.0,
                "sources": [],
            })
            slot["score"] += 1.0 / (k + rank)
            slot["sources"].append("%s#%d" % (source, rank))
    out = sorted(fused.values(), key=lambda h: -h["score"])[:limit]
    for i, hit in enumerate(out, start=1):
        hit["rank"] = i
    return out


def hybrid_search(store: "Store", query: str, limit: int = 20,
                  include_all: bool = False,
                  hidden_subtypes: Sequence[str] = DEFAULT_HIDDEN_SUBTYPES,
                  embedder: "Embedder | None" = None
                  ) -> SearchResult:
    """include_all=True is the `--all` escape hatch: nothing is hidden.

    `embedder` is how the vector path is turned on: without one, embeddings are
    off and this is a lexical search (out_mode "fts"); with one, the query is
    embedded, an FTS shortlist is reranked by cosine, and the two rankings are
    fused by RRF (out_mode "hybrid"). It is passed in rather than built here so
    that `search`, which by contract never reads the machine config, stays that
    way -- the caller that has the data file names it.
    """
    warnings = []
    hidden = () if include_all else tuple(hidden_subtypes)
    if hidden:
        warnings.append(
            "hiding subtype(s) %s; pass include_all=True to search everything."
            % ", ".join(hidden))
    if store.fts_is_stale():
        warnings.append(
            "FTS index covers %d of %d nodes -- results are incomplete. "
            "Run rebuild_fts()." % (store.fts_count(), store.node_count()))

    lex = fts_search(store, query, limit=limit, hidden_subtypes=hidden)
    vec = vector_search(store, query, limit=limit, embedder=embedder,
                        hidden_subtypes=hidden)

    if vec is None:
        if store.embeddings is None:
            warnings.append(
                "embeddings are OFF, so this was a lexical search only. A "
                "natural-language question will return few or no hits, and "
                "that is the honest answer -- not a bug. Enable embeddings "
                "explicitly with a provider and model to change it.")
        else:
            warnings.append(
                "embeddings are configured but nothing is embedded under the "
                "current model's namespace; the vector path did not run. Run "
                "`homegraph embed` (a model change invalidates old vectors).")
        # Both arms of the old conditional said "fts". Whether the lexical
        # side found anything is already carried by `hits`; inventing a
        # branch that cannot differ only made the mode look computed.
        mode = "fts"
        return SearchResult(hits=_ranked(lex), out_mode=mode, warnings=warnings)

    fused = rrf_fuse({"fts": lex, "vector": vec}, limit=limit)
    return SearchResult(hits=fused, out_mode="hybrid", warnings=warnings)


def _ranked(hits: Sequence[Hit]) -> list[Hit]:
    out = []
    for i, h in enumerate(hits, start=1):
        item = dict(h)
        item["rank"] = i
        item["sources"] = ["fts#%d" % i]
        out.append(item)
    return out
