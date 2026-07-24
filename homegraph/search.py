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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

# A hit is a loose row: FTS columns plus whatever the fuser adds. Typed as a
# mapping rather than a dataclass because it flows straight out of sqlite3.Row
# and gaining a class here would mean copying every row twice.
Hit = dict[str, Any]

RRF_K = 60
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class SearchResult:
    hits: list[Hit]     # {node_id, node_key, title, rank, score, sources}
    # Today this is always "fts", and that is the honest value rather than a
    # placeholder: `vector_search` returns None when embeddings are off and
    # raises NotImplementedError when they are configured, so no run reaches
    # the hybrid branch. The annotation used to list four values as if the
    # field were computed, which invites a reader to look for a hybrid state
    # that cannot occur. "vector" and "hybrid" become reachable the day a
    # provider is wired up; "none" is written nowhere and may never be.
    out_mode: str       # "fts" today; "vector" | "hybrid" once embeddings exist
    warnings: list[str] = field(default_factory=list)

    # The plan names this field `_out_mode`; expose both so neither spelling
    # silently returns None at a call site.
    @property
    def _out_mode(self) -> str:
        return self.out_mode

    def __len__(self) -> int:
        return len(self.hits)


def fts_query(text: str | None) -> str:
    """Turn free text into an FTS5 expression with terms ANDed.

    Punctuation is stripped rather than escaped: an unquoted `?` or `-` is FTS5
    *syntax*, so passing a user's sentence through raw either raises or, worse,
    silently changes what was asked.
    """
    terms = _WORD.findall(text or "")
    return " AND ".join('"%s"' % t.replace('"', '""') for t in terms)


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


def vector_search(store: "Store", query: str,
                  limit: int = 20) -> list[Hit] | None:
    """Returns None when the vector path is unavailable -- not [].

    The distinction is the entire point. `[]` means "searched, found nothing";
    `None` means "never ran", and only the second one deserves a warning.
    """
    if store.embeddings is None:
        return None
    if store.db.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 0:
        return None
    raise NotImplementedError(
        "embeddings are configured but no provider is wired up yet; "
        "refusing to return a silently empty vector result")


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
                  hidden_subtypes: Sequence[str] = DEFAULT_HIDDEN_SUBTYPES
                  ) -> SearchResult:
    """include_all=True is the `--all` escape hatch: nothing is hidden."""
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
    vec = vector_search(store, query, limit=limit)

    if vec is None:
        if store.embeddings is None:
            warnings.append(
                "embeddings are OFF, so this was a lexical search only. A "
                "natural-language question will return few or no hits, and "
                "that is the honest answer -- not a bug. Enable embeddings "
                "explicitly with a provider and model to change it.")
        else:
            warnings.append(
                "embeddings are configured but the index is empty; the vector "
                "path did not run.")
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
