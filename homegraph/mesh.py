#!/usr/bin/env python3
"""M5 -- the mesh. Federates the models; never merges them.

There is no combined database. Mesh opens each model's store, queries them, and
fuses in memory. One corrupt or missing model degrades the answer and says so;
it does not take the others down.

`mesh.db` holds the cross-model edges **and a stub for every node they can
point at** -- a key, a path, a title, and an FTS row. It has to: an edge needs
endpoints, and a graph query has to be able to name what it found without
reopening five stores. This used to say "only cross-model edges", three lines
under "never merges them", so the sentence read as a guarantee about separation
when it was a description of what the file is for. The separation guarantee is
real and is elsewhere: no model's rows are copied into another model's store,
the stubs carry no bodies, and `search` always reads the models themselves.

Two things here are easy to get silently wrong, so both are made explicit.

**Identity.** M1, M3, M4 and code are matched by content hash. M2 has no
content hash -- it never reads an image, so it cannot have one -- and is
matched on normalised path instead. The consequence is that two identical
images in two directories stay two nodes, joined by LIKELY_COPY rather than
merged. That is the right trade, and it is written down here because in six
months it will look like a deduplication bug.

**Fusion.** BM25 scores from five separate FTS indexes are not commensurable.
Comparing them directly produces a ranking that looks entirely plausible and is
wrong, with nothing to indicate it -- the most likely silent failure in the
project. Fusion is RRF over ranks, and `mesh_explain` will show which model
supplied each hit and at what rank.

Partial results are labelled. A federated search that quietly drops a model
returns fewer results and no warning, which is indistinguishable from a corpus
that simply has less in it.
"""
from __future__ import annotations

import collections
import functools
import itertools
import os
import re
import subprocess
import typing
import sqlite3

from . import incremental
from .config import home_root
from .search import RRF_K, fts_query
from .store import Store


class ModelUnavailable(Exception):
    pass


class AmbiguousKey(Exception):
    """A bare key resolved under more than one model prefix -- refused, not guessed.

    Measured against real-mesh.db (9 125 nodes): 0 paths appear under more
    than one model prefix. The corpus classifier gives each path exactly one
    partition, which makes collision structurally rare rather than merely
    unlucky -- and rare is the reason to refuse loudly the day it happens,
    not a reason to skip the check. Picking the first candidate would be
    right for whichever caller meant that model and silently wrong for the
    other, with nothing in the answer to say so -- the same shape of
    confident-wrong-answer this package measures `mesh_neighbors` against
    elsewhere in this file.
    """


class Neighbour(typing.NamedTuple):
    """One traversed edge, with how it was derived.

    A NamedTuple so `for src, rel, dst in neighbours(...)` keeps working for
    three-field unpacking while `row["method"]` reaches the provenance --
    which is what `provenance_note` reads, so the caller does not have to
    know which shape it was handed.
    """
    src: str
    rel: str
    dst: str
    method: str
    confidence: float

    def __getitem__(self, key):
        if isinstance(key, str):
            return getattr(self, key)
        return tuple.__getitem__(self, key)


class MeshResult:
    def __init__(self, hits, models_queried, models_missing, warnings,
                 centrality: int | str = "absent"):
        self.hits = hits
        self.models_queried = models_queried
        self.models_missing = models_missing
        self.warnings = warnings
        # How many positions in the returned window the centrality tie-break
        # actually moved, or "absent" when there was no mesh to count edges in.
        # Not 0: a fusion that ran without a tie-break and one where every tie
        # already sat in degree order are different answers, and
        # `code_inventory` learned that distinction first.
        self.centrality = centrality

    @property
    def partial(self):
        return bool(self.models_missing)

    @property
    def status(self):
        return "partial" if self.partial else "complete"

    def __len__(self):
        return len(self.hits)

    def __repr__(self):
        return "<MeshResult %s %d hit(s) from %s%s>" % (
            self.status, len(self.hits), ",".join(self.models_queried),
            " missing=%s" % ",".join(self.models_missing)
            if self.models_missing else "")


class Mesh:
    def __init__(self, model_paths, mesh_db=None):
        """model_paths: {"m1": "/path/m1.db", ...}. Missing files are fine."""
        self.model_paths = dict(model_paths)
        self.mesh_db = mesh_db
        self._open = {}
        self._failed = {}

    def store(self, model):
        if model in self._open:
            return self._open[model]
        if model in self._failed:
            raise ModelUnavailable(self._failed[model])
        path = self.model_paths.get(model)
        if not path or not os.path.exists(path):
            self._failed[model] = "no store at %s" % path
            raise ModelUnavailable(self._failed[model])
        try:
            s = Store(path)
            s.db.execute("SELECT COUNT(*) FROM nodes").fetchone()
        except (sqlite3.Error, OSError) as exc:
            self._failed[model] = repr(exc)
            raise ModelUnavailable(self._failed[model])
        self._open[model] = s
        return s

    def close(self):
        for s in self._open.values():
            try:
                s.close()
            except sqlite3.Error:
                pass
        self._open.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- search -----------------------------------------------------------

    def search(self, query, limit=20, as_of=None, include_all=False):
        expr = fts_query(query)
        rankings, queried, missing, warnings = {}, [], [], []

        for model in self.model_paths:
            try:
                s = self.store(model)
            except ModelUnavailable as exc:
                missing.append(model)
                warnings.append("%s unavailable: %s" % (model, exc))
                continue
            queried.append(model)
            if not expr:
                continue
            try:
                rows = self._fts_rows(s, expr, limit, as_of, include_all)
            except sqlite3.Error as exc:
                missing.append(model)
                warnings.append("%s failed mid-query: %r" % (model, exc))
                continue
            rankings[model] = [dict(r, model=model) for r in rows]

        if expr:
            self._search_code(expr, limit, as_of, include_all,
                              rankings, queried, warnings)

        # CP-H6. Centrality is the TIE-BREAK, not a fourth list. Fed as a list
        # it would decide everything: with RRF_K = 60 one contribution at
        # position 1 is worth 1/61 = 0.016393 while two adjacent positions are
        # 0.000264 apart, so a single extra entry outweighs some 63 places.
        # Measured 2026-08-02 over 20 real queries, that design left 1 of 20
        # unchanged and introduced hits that had been below the cut in 7.
        # tests/gold/FASIT-h6.md has the numbers and the revision.
        degrees = self.centrality_degrees(rankings)

        hits = self._rrf(rankings, limit, degrees)
        moved = "absent"
        if degrees is not None:
            plain = self._rrf(rankings, limit)
            moved = sum(1 for a, b in zip(plain, hits) if a["key"] != b["key"])
        # CP-H7. Counts the RETURNED WINDOW, never the corpus: the stores here
        # hold 3 207 paths that are gone, so a corpus banner would fire on every
        # query forever, and a warning that always fires is one nobody reads.
        # Zero affected hits produce zero warnings -- that is what lets this
        # gate say no.
        self._annotate_status(hits)
        affected = collections.Counter(
            h["staleness"] for h in hits
            if h["staleness"] in incremental.AFFECTED)
        if affected:
            warnings.append(
                "%s among %d hit(s) -- reindex to refresh"
                % (", ".join("%d %s" % (affected[s], s)
                             for s in incremental.AFFECTED if affected[s]),
                   len(hits)))
        if missing:
            warnings.insert(0, "PARTIAL RESULT -- %s did not answer. Counts "
                                "and ranking are incomplete."
                            % ", ".join(sorted(set(missing))))
        return MeshResult(hits, queried, sorted(set(missing)), warnings,
                          centrality=moved)

    @staticmethod
    def _fts_rows(store, expr, limit, as_of, include_all, kind=None):
        """One FTS query, one place. The models and the code stubs share it.

        Written out twice, the `transcript` filter and the `as_of` predicate
        would agree until one of them was edited -- and the one that stopped
        being edited would be the one nobody reads. `kind` is the only thing
        the two callers differ by.
        """
        # `kind` is selected for `_fusion_key`, which refuses to fuse sections
        # on their digest. Without it here the guard reads None for every row
        # and never fires -- the column being absent is exactly as silent as
        # the guard being absent.
        sql = ("SELECT n.id node_id, n.node_key, n.kind, n.title, "
               "n.title_confidence, "
               "n.subtype, n.path, n.content_hash, bm25(nodes_fts) score "
               "FROM nodes_fts JOIN nodes n ON n.id = nodes_fts.rowid "
               "WHERE nodes_fts MATCH ?")
        args: list[object] = [expr]
        if not include_all:
            sql += " AND (n.subtype IS NULL OR n.subtype != 'transcript')"
        if kind is not None:
            sql += " AND n.kind = ?"
            args.append(kind)
        if as_of:
            # Time travel: a node counts as it stood on that date.
            sql += " AND n.first_seen <= ?"
            args.append(as_of)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        return store.db.execute(sql, args).fetchall()

    def _search_code(self, expr, limit, as_of, include_all,
                     rankings, queried, warnings):
        """Search the code stubs, which live in mesh.db and in no model.

        `code` is a corpus category with no store -- reading source is
        `code-review-graph`'s job -- so the federation cannot search code the
        way it searches the four models. What it can do is answer **which
        file**, by name and path, because that is exactly what a stub carries.
        A CITES_CODE edge could name a file that no search could then find,
        which made the graph able to point at something the user could not
        look up.

        Three states, and they are kept apart:

          * no `mesh_db` at all -- the caller asked for a search over the
            models it named, and gets one. Not a partial result, since no
            model failed, but it SAYS SO: the header otherwise reads
            `COMPLETE -- n hit(s) from m1, m3, m4` while code was never
            consulted, and a user who has just built an inventory has no way
            to tell that from "your file is not in the corpus". This was
            silent for exactly one session, and that session produced the
            question "why does searching for my file show nothing".
          * a mesh with code stubs -- `code` joins the ranking as a source.
          * a mesh with none -- a warning naming the fix, because zero hits
            from an inventory nobody built looks exactly like a corpus with
            no source files in it. Same distinction `build_edges` draws
            between `absent` and 0.

        A stub cannot answer for its CONTENTS, and this is careful not to
        imply otherwise: the body is the basename. Searching for a function
        name will not find the file that defines it, and that is a real limit
        rather than a bug -- see DECISIONS.md section 26.
        """
        if not self.mesh_db:
            warnings.append(
                "code was not consulted: no --mesh-db, so the code inventory "
                "was not available to this search. Source files live in no "
                "model.")
            return
        if not os.path.exists(self.mesh_db):
            warnings.append(
                "code was not consulted: no federation at %s. Run "
                "`mesh build --code-root DIR`." % self.mesh_db)
            return
        try:
            mesh = Store(self.mesh_db)
        except (sqlite3.Error, OSError) as exc:
            warnings.append("code stubs unavailable: %r" % exc)
            return
        try:
            rows = self._fts_rows(mesh, expr, limit, as_of, include_all,
                                  kind=self.CODE_MODEL)
            if not self._has_code_stubs(mesh):
                warnings.append(
                    "no code inventory in this federation: source files are "
                    "not searchable until `mesh build --code-root DIR` has "
                    "run. This is not 'no matches'.")
                return
            queried.append(self.CODE_MODEL)
            rankings[self.CODE_MODEL] = [dict(r, model=self.CODE_MODEL)
                                         for r in rows]
        except sqlite3.Error as exc:
            warnings.append("code stubs failed mid-query: %r" % exc)
        finally:
            mesh.close(commit=False)

    @staticmethod
    def _fusion_key(row):
        """Identity for fusion: content hash, else path, else the node key.

        This is the ONLY identity function in the mesh. There was briefly a
        second one -- a `Mesh.identity()` method that computed the same thing
        from a model name -- which vulture found sitting unused. Two
        implementations of identity is the exact failure DECISIONS.md warns
        about in another context: they agree until they do not, and nothing
        announces the divergence.

        Content hash covers M1, M3, M4 and code. M2 has none and can have
        none -- it never reads an image -- so it falls through to path, which
        is why two identical images in two directories stay two nodes.

        Keying on model+node_key was a real bug and a subtle one: a document
        two models both returned scored as two separate entries with one
        contribution each, so cross-model agreement -- the entire reason to
        federate -- never accumulated. The models partition the corpus today,
        so it rarely fires; the day one file is indexed twice it would have
        halved that file's rank silently.
        """
        # A section's hash is a digest of a FRAGMENT, not of a document, and
        # two files that share a paragraph are not one node. Until CP-H4 every
        # section carried NULL here and fell through to path; the day sections
        # gained a digest, `_fusion_key` started merging them across files and
        # dropping one side entirely. Measured on the day it was added: two
        # files with the same paragraph collapsed to one entry, and the
        # survivor took both contributions. Over `~/homegraph` + `~/.claude` +
        # `~/wiki` -- 33 010 section nodes, 14 615 distinct digests -- 90 % of
        # sections share a digest with at least one other, most of them the
        # digest of a single newline.
        #
        # Excluded by KIND rather than by "does this look like a fragment",
        # because the next model to store a partial-content hash should have to
        # come here and say so.
        if row.get("content_hash") and row.get("kind") != "section":
            return "hash:%s" % row["content_hash"]
        if row.get("path"):
            return "path:%s" % os.path.normpath(row["path"])
        return "key:%s:%s" % (row.get("model"), row["node_key"])

    def _annotate_status(self, hits):
        """Per-hit `staleness` and `embedding_status`. CP-H7, rules R4 and R5.

        Only the returned window is stated. A corpus-wide pass costs 0.01 s and
        would still be the wrong thing on a search: the reader is about to use
        these hits, not the other 8 500 rows. `mesh_explain` carries the
        corpus numbers, where someone has asked for them.

        Never raises. `stat` failures become `absent` inside `node_state`, and a
        store that cannot be opened leaves its hits `absent` rather than taking
        the search down -- a status line is not worth a failed query.
        """
        by_model = {}
        for hit in hits:
            hit["staleness"] = incremental.ABSENT
            hit["embedding_status"] = "unknown"
            by_model.setdefault(hit["model"], []).append(hit)
        for model, group in by_model.items():
            try:
                store = (self._code_store() if model == self.CODE_MODEL
                         else self.store(model))
                self._annotate_group(store, group)
            except (ModelUnavailable, sqlite3.Error, OSError, ValueError):
                # The whole per-model body, not only the open. The SQL used to
                # sit outside this and a store without an `embeddings` table
                # took the search down for a status line. CP-H7 R4 says the
                # search never raises because of this feature; it now does not.
                continue

    # SQLite's default parameter ceiling is 999. The window is normally ten, so
    # this is a bound rather than a limit anyone will feel -- but `limit` comes
    # from the caller, and an unbounded IN-list is how a status line turns into
    # `too many SQL variables` on someone else's query.
    _CHUNK = 400

    def _annotate_group(self, store, group):
        """One model's hits, annotated in place. CP-H7."""
        rows = {}
        keys = [h["node_key"] for h in group]
        for i in range(0, len(keys), self._CHUNK):
            block = keys[i:i + self._CHUNK]
            rows.update({r["node_key"]: r for r in store.db.execute(
                "SELECT node_key, kind, id, path, size, mtime FROM nodes "
                "WHERE node_key IN (%s)" % ",".join("?" * len(block)), block)})

        # Sections carry their parent's path and no stat of their own, so
        # asking the filesystem about them directly answers `ABSENT` -- which
        # is what `search` did while `reconcile` said `STALE` about the very
        # same node. Two halves of one feature disagreeing about one node is
        # worse than either answer; found by codex 2026-08-02. One extra query,
        # bounded by the window, buys the agreement.
        # A section with a stat of its own cannot be written today: every
        # writer of `kind="section"` omits size and mtime, so the two operands
        # cannot be varied apart. Aim a mutation here the day one does.
        # condition-coverage: no writer produces a stat-bearing section
        orphans = [r["path"] for r in rows.values()
                   if r["kind"] == "section" and r["size"] is None
                   and r["path"]]
        parents = {}
        for i in range(0, len(orphans), self._CHUNK):
            block = orphans[i:i + self._CHUNK]
            for r in store.db.execute(
                    "SELECT path, size, mtime FROM nodes WHERE path IN (%s) "
                    "AND size IS NOT NULL AND mtime IS NOT NULL"
                    % ",".join("?" * len(block)), block):
                parents[r["path"]] = incremental.worst(
                    parents.get(r["path"]),
                    incremental.node_state(r["path"], r["size"], r["mtime"]))

        any_vectors = store.db.execute(
            "SELECT 1 FROM embeddings LIMIT 1").fetchone() is not None
        ids = [r["id"] for r in rows.values()]
        embedded = set()
        if any_vectors:
            for i in range(0, len(ids), self._CHUNK):
                block = ids[i:i + self._CHUNK]
                embedded |= {r["node_id"] for r in store.db.execute(
                    "SELECT node_id FROM embeddings WHERE node_id IN (%s)"
                    % ",".join("?" * len(block)), block)}

        for hit in group:
            row = rows.get(hit["node_key"])
            if row is None:
                continue
            if row["kind"] == "section" and row["size"] is None:
                state = parents.get(
                    row["path"],
                    incremental.node_state(row["path"], None, None))
            else:
                state = incremental.node_state(row["path"], row["size"],
                                               row["mtime"])
            hit["staleness"] = state
            hit["embedding_status"] = self._embedding_status(
                state, any_vectors, row["id"] in embedded)

    def _code_store(self):
        """The mesh, opened once and closed with everything else.

        `_read_mesh()` hands back a NEW connection on every call, and the
        annotation path calls it per search -- one leaked SQLite handle each
        time, unreachable from `close()` because it was never registered.
        Found by codex 2026-08-02.
        """
        # Inverting this raises KeyError on the first call, so a mutation
        # could only ever be crash-only -- the weakest verdict this project
        # accepts, and not worth a needle.
        # condition-coverage: inverting it is crash-only, not a gate finding
        if self.CODE_MODEL not in self._open:
            self._open[self.CODE_MODEL] = self._read_mesh()
        return self._open[self.CODE_MODEL]

    @staticmethod
    def _embedding_status(state, any_vectors, has_vector):
        """Derived from the FILE, because the vector cannot answer. CP-H7 R5.

        `embeddings` is `(node_id, provider, model, dim, vec)` -- no content
        hash -- so a vector written before an edit is indistinguishable from one
        written after. What the file says is therefore the only evidence there
        is. Adding the hash is a migration, and this package spent 2026-08-01
        learning what a newly filled column does to whoever keys on it.

        **Existence, not usability, and the two differ after a model switch.**
        This asks whether a vector exists at all; it does not filter on the
        `(provider, model, dim)` namespace every other vector read filters on
        (`store.py`, "-- embeddings"). So a node whose vector was written under
        an abandoned namespace reports `current` here while `vector_search`
        finds nothing for it. That is not a guess this method may make:
        `embedding_coverage` faced the same question and refused it in the same
        words -- the store is opened without a namespace, so reporting each one
        is the honest shape and the caller decides which line matters. The
        per-namespace picture is therefore published beside the corpus counts
        in `mesh_explain`, not folded into this one word. Found by audit
        2026-08-02; the key says so under R5.
        """
        if not any_vectors:
            return "off"
        if not has_vector:
            return "none"
        if state == incremental.ABSENT:
            return "unknown"
        return incremental.CURRENT if state == incremental.CURRENT \
            else incremental.STALE

    @staticmethod
    def _mesh_node_key(model, node_key):
        """The key a candidate has in `mesh.db`, which is not always qualified.

        A model's rows carry that model's own key and are qualified here. Code
        rows are different: `_search_code` reads the stubs out of `mesh.db`
        itself, so their `node_key` is ALREADY `code::<path>` -- the form
        `_code_index` wrote -- and qualifying it again asks for
        `code::code::<path>`, a node that has never existed.

        Measured 2026-08-02, found by codex: every code candidate scored 0,
        including `main.py` at degree 69, the highest in the store. The lookup
        failed silently because a missing row is a legitimate zero.
        """
        if model == Mesh.CODE_MODEL:
            return node_key
        return "%s::%s" % (model, node_key)

    def centrality_degrees(self, rankings) -> dict[str, int] | None:
        """`fanIn + fanOut` for the candidates already found, by fusion key.

        **Only the candidates.** Scoring every node instead would make a
        globally central file a peer of the query's own best match in every
        search: on the real mesh 8 621 of 9 125 nodes have no edge at all, so
        94.5 % of such a ranking is tied at zero behind a handful of popular
        files. That is a fixed result injected into every query, not a signal.

        Keyed by FUSION key, because that is what the sort orders. One
        document can reach the fusion through several models, and each of those
        is its own node in the mesh with its own edges; the **highest** of them
        is used rather than the sum, so a document found three times does not
        collect three degrees on top of the three RRF contributions it already
        has.

        Returns `None` when there is no mesh to count edges in -- "no mesh" and
        "everything scored zero" are different facts, and only one is worth
        telling anyone about.
        """
        if not self.mesh_db or not os.path.exists(self.mesh_db):
            return None
        wanted: dict[str, str] = {}
        for model, rows in rankings.items():
            for row in rows:
                wanted.setdefault(self._mesh_node_key(model, row["node_key"]),
                                  self._fusion_key(row))
        if not wanted:
            return {}
        degrees: dict[str, int] = {}
        try:
            mesh = Store(self.mesh_db)
        except (sqlite3.Error, OSError):
            # An unreadable mesh must not take federated search down with it.
            # `_search_code` already catches this one and continues with a
            # warning; opening it again here and raising would turn an
            # optional reordering into a failed query.
            return None
        try:
            for mesh_key, fusion_key in wanted.items():
                row = mesh.db.execute(
                    "SELECT (SELECT COUNT(*) FROM edges WHERE dst = n.id) "
                    "     + (SELECT COUNT(*) FROM edges WHERE src = n.id) d "
                    "FROM nodes n WHERE n.node_key = ?", (mesh_key,)).fetchone()
                degree = row["d"] if row else 0
                if degree > degrees.get(fusion_key, -1):
                    degrees[fusion_key] = degree
        finally:
            mesh.close()
        return degrees

    @staticmethod
    def _rrf(rankings, limit, degrees=None):
        """RRF across models. Ranks only -- never the BM25 scores.

        BM25 is index-relative: -99 in one model and -0.5 in another say
        nothing about each other. Ordering by raw score therefore ranks by
        which index happens to produce larger magnitudes, which looks like a
        relevance ordering and is not one.

        `degrees` breaks ties between hits the fusion scored EQUALLY, and does
        nothing else: it cannot move a hit past one that scored differently,
        and it cannot make a candidate out of something no model returned.
        CP-H6. `key` stays last in the sort so that equal score AND equal
        degree still cannot swap between runs.

        It CAN change which of two equally-scored hits falls inside `limit`
        when the tie straddles the cutoff -- and from outside that looks like a
        hit appearing. Not a defect: the alternative is a cutoff decided by
        alphabet. Stated because "it cannot add or remove one" was written
        here first and was too strong.
        """
        fused = {}
        for model, rows in rankings.items():
            for rank, row in enumerate(rows, start=1):
                key = Mesh._fusion_key(row)
                slot = fused.setdefault(key, {
                    "key": key, "model": model, "node_key": row["node_key"],
                    "title": row.get("title"),
                    "title_confidence": row.get("title_confidence"),
                    "path": row.get("path"),
                    "score": 0.0, "sources": [], "models": []})
                slot["score"] += 1.0 / (RRF_K + rank)
                slot["sources"].append("%s#%d" % (model, rank))
                if model not in slot["models"]:
                    slot["models"].append(model)
        # Reported per hit, not only used: a reordering nobody can see is worse
        # than one nobody asked for.
        for key, slot in fused.items():
            slot["degree"] = None if degrees is None else degrees.get(key, 0)
        out = sorted(fused.values(),
                     key=lambda h: (-h["score"],
                                    -(h["degree"] or 0), h["key"]))
        for i, hit in enumerate(out[:limit], start=1):
            hit["rank"] = i
        return out[:limit]

    def corpus_staleness(self):
        """{model: {state: count}} over every stored path. CP-H7.

        The whole-corpus pass the search deliberately does not do. Measured
        2026-08-02 it costs 0.01 s over 8 564 paths, so the reason it is not on
        the search path is not cost -- it is that a number about 8 500 rows is
        not an answer to a query that returned ten.

        A model that cannot be opened is omitted rather than reported as zero:
        an empty count and an unread store are the same shape and different
        facts.
        """
        out = {}
        for model in list(self.model_paths) + [self.CODE_MODEL]:
            try:
                store = (self._code_store() if model == self.CODE_MODEL
                         else self.store(model))
            except (ModelUnavailable, sqlite3.Error, OSError, ValueError):
                continue
            counts = collections.Counter(
                incremental.reconcile(store).values())
            if counts:
                entry: dict[str, object] = dict(counts)
                # Per namespace, because `embedding_status` on a hit cannot
                # name one: a node with a vector under an abandoned namespace
                # reports `current` there while `vector_search` finds nothing.
                # The store already answers this shape; reuse it rather than
                # guess a current namespace here.
                try:
                    coverage = store.embedding_coverage()
                except (sqlite3.Error, AttributeError):
                    coverage = []
                if coverage:
                    entry["embeddings"] = coverage
                out[model] = entry
        return out

    def explain(self, query, limit=20):
        """Per-model breakdown of who answered and at what rank."""
        result = self.search(query, limit=limit)
        # Centrality is not a store and is not counted here. It orders hits the
        # fusion scored equally; it answers no query and finds no document, so
        # a per-model breakdown that named it would report a source that has
        # nothing in it. It is reported on its own line instead, because a
        # reordering nobody can see is worse than one nobody asked for.
        by_model = collections.Counter(h["model"] for h in result.hits)
        return {
            "query": query,
            "status": result.status,
            "models_queried": result.models_queried,
            "models_missing": result.models_missing,
            "hits_per_model": dict(by_model),
            "centrality": result.centrality,
            # CP-H7: the corpus-wide numbers live HERE, not in the search
            # warning, because this is where someone has asked why the answer
            # looks the way it does.
            "staleness": self.corpus_staleness(),
            "fusion": "reciprocal rank fusion, k=%d; BM25 scores are never "
                      "compared across models; equal scores are ordered by "
                      "fanIn+fanOut, then by key" % RRF_K,
            "warnings": result.warnings,
        }

    # -- cross-model graph -------------------------------------------------

    CO_CHANGE_MIN = 3

    @staticmethod
    def repo_top(repo_root):
        """The repository's top level, or a refusal naming which case failed.

        `git log` writes paths relative to the TOP of the repository, never to
        the directory `-C` was given. Joining them onto the directory the user
        named produces paths that do not exist the moment that directory is not
        the top -- no error, no warning, every co-change edge silently not
        drawn, and a report saying the repository was read. A vault inside a
        monorepo is the ordinary case, not a corner one.

        "Not a git repository" and "a git repository with no commits" are also
        different facts, and only this call can tell them apart.
        """
        top = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=60)
        if top.returncode != 0:
            raise ModelUnavailable(
                "not a git repository: %s"
                % (top.stderr.strip().splitlines() or [""])[-1])
        return os.path.realpath(top.stdout.strip())

    @staticmethod
    def co_change_pairs(repo_root):
        """`{(a, b): (shared, union)}` over one repository's history.

        Repo-relative paths, sorted within each pair. Counted over COMMITS, not
        over lines: `--numstat` reports line counts, and a one-character fix
        committed together is the same evidence of coupling as a rewrite.

        **`-z`, and it is not a preference.** Without it `core.quotepath` -- on
        by default -- returns a quoted, octal-escaped name for `hoest.md` with
        a Norwegian vowel in it, which matches no node, so every file with a
        non-ASCII name silently leaves the graph. Without it a rename prints as
        the single field `sub/{old.md => new.md}`: a third name that is neither
        half and exists nowhere. With `-z` the rename arrives as three
        NUL-separated fields and the NEW name is taken -- older commits already
        counted the old one under its own name, which is the honest reading of
        a history nobody asked `--follow` about.

        Merge commits contribute nothing -- `--numstat` prints no file lines for
        them without `-m`. A merge touches files because two branches did, not
        because anyone changed them together.
        """
        # `timeout=` is not padding. `mutate_cp1` was once the only harness in
        # this repo without one, and a subprocess that never returns takes the
        # caller with it -- here that caller is `mesh build`, which a user runs
        # and waits on. Ten minutes is far above any real history and far below
        # forever.
        top = Mesh.repo_top(repo_root)
        out = subprocess.run(
            ["git", "-C", top, "log", "-z", "--numstat", "--format=%H"],
            capture_output=True, text=True, timeout=600)
        if out.returncode != 0:
            # An empty repository is not a broken one. This is the last of five
            # relations `build_edges` computes, so raising would throw away four
            # that succeeded because an optional source had no commits yet.
            if "does not have any commits" in out.stderr:
                return {}
            raise ModelUnavailable(
                "git log failed in %s: %s"
                % (top, (out.stderr.strip().splitlines() or [""])[-1]))
        pairs: dict = collections.Counter()
        touched: dict = collections.defaultdict(set)
        sha, files = None, set()

        def flush():
            for a, b in itertools.combinations(sorted(files), 2):
                pairs[(a, b)] += 1
            for name in files:
                touched[name].add(sha)
            files.clear()

        fields = out.stdout.split("\0")
        i = 0
        while i < len(fields):
            field = fields[i]
            if "\t" not in field:
                # A commit hash. A rename's two path fields carry no tab
                # either, which is why they are consumed below rather than
                # reaching this branch and being read as commits.
                if field.strip():
                    flush()
                    sha = field.strip()
                i += 1
                continue
            path = field.split("\t")[-1]
            if path == "":
                # `add\tdel\t` with an empty path is a rename: the next two
                # fields are the old and the new name.
                if i + 2 < len(fields):
                    files.add(fields[i + 2])
                i += 3
                continue
            files.add(path.lstrip("\n"))
            i += 1
        flush()
        return {p: (n, len(touched[p[0]] | touched[p[1]]))
                for p, n in pairs.items()}

    def _co_changed(self, mesh, loaded, as_of, report, repo_root):
        """Files committed together become one edge, when both are nodes.

        The "when both are nodes" half is the one worth guarding. Dropping it
        produces MORE edges and leaves every count looking healthy -- and in
        the answer key's fixture the two edges it would wrongly add outrank the
        one real edge, because a file committed only alongside one other has a
        higher ratio than two files with lives of their own.
        """
        by_path = {}
        for model, s in loaded.items():
            for row in s.db.execute(
                    "SELECT node_key, path FROM nodes WHERE path IS NOT NULL "
                    "AND kind IN ('file','document','image')"):
                # `realpath`, not `abspath`, on BOTH sides. `abspath` normalises
                # `..` and makes a path absolute; it does not resolve symlinks.
                # A repository reached through a symlink -- or a store whose
                # paths were recorded through one -- then produces two spellings
                # of one file, the lookup misses, and every co-change edge for
                # that tree silently fails to be drawn. No error, no zero worth
                # noticing: just fewer edges than there should be.
                by_path[os.path.realpath(row["path"])] = "%s::%s" % (
                    model, row["node_key"])
        # The TOP of the repository, not the directory the caller named --
        # `git log` writes its paths relative to the top. See `repo_top`.
        root = self.repo_top(repo_root)
        for (a, b), (shared, union) in self.co_change_pairs(root).items():
            if shared < self.CO_CHANGE_MIN:
                continue
            src = by_path.get(os.path.realpath(os.path.join(root, a)))
            dst = by_path.get(os.path.realpath(os.path.join(root, b)))
            if src is None or dst is None:
                continue
            # Sorted endpoints, one row. The relation is symmetric, and two
            # rows for one fact would double the pair's weight in any traversal
            # that counts edges.
            lo, hi = sorted((src, dst))
            # `confidence` is NOT passed: `upsert_edge` looks it up from the
            # method so it cannot drift from what it describes. The ratio is
            # computed and reported, not stored -- see the correction in
            # `tests/gold/FASIT-h5.md`.
            mesh.upsert_edge(lo, hi, "CO_CHANGED_WITH", as_of,
                             method="co-change")
            report["CO_CHANGED_WITH"] += 1

    def build_edges(self, as_of, prune=False, code_paths=None,
                    repo_root=None):
        """Compute cross-model edges into mesh.db. Never touches model stores.

        `prune` drops every stub the mirror below did not just write. It is off
        by default, because a partial build -- one model unreadable -- would
        otherwise delete the stubs of a model that is merely absent today. It
        is on for `homegraph update`, where the federation has to stop
        answering about files that are gone: a stale stub carries a title and a
        path and answers confidently about a world that no longer exists.

        `code_paths` is the code inventory CITES_CODE needs, and passing it is
        the caller's decision because there is no code STORE to read it from.
        `code` is a corpus category with no model behind it -- the plan's code
        model is `code-review-graph`, a separate tool with its own database --
        so the paths arrive from the same corpus walk the models are built
        from. Omit it and CITES_CODE is not computed, which the report says in
        as many words rather than reporting zero edges.
        """
        if not self.mesh_db:
            raise ValueError("mesh_db path required to build edges")
        mesh = Store(self.mesh_db, model="m5")
        report = collections.Counter()
        missing = []

        loaded = {}
        for model in self.model_paths:
            try:
                loaded[model] = self.store(model)
            except ModelUnavailable:
                missing.append(model)

        # Both refusals happen HERE, before the first write. They used to sit
        # after the mirror loop, where `mesh.close()` commits by default, so a
        # refused prune advanced `last_seen` on every mirrored node and
        # committed it -- a store announcing it did nothing while leaving
        # nodes dated later than the edges they carry, a state neither `build`
        # nor a completed `update` can produce. **A refusal that has already
        # written is not a refusal**, and the ordering is what makes it one;
        # the rollback below is the second lock on the same door.
        if prune:
            refusal = self._unsafe_prune(mesh, code_paths, missing)
            if refusal:
                mesh.close(commit=False)
                raise ModelUnavailable(refusal)

        # Mirror every model node into mesh as a lightweight stub, so edges
        # have endpoints without copying bodies or duplicating the indexes.
        by_basename = collections.defaultdict(list)
        mirrored = set()
        for model, s in loaded.items():
            for row in s.db.execute(
                    "SELECT node_key, title, title_method, path, content_hash, "
                    "kind, subtype, datelist_int FROM nodes "
                    "WHERE path IS NOT NULL"):
                key = "%s::%s" % (model, row["node_key"])
                mirrored.add(key)
                # Carry title_method through: without it an inferred title is
                # laundered to NULL in the federation the MCP serves, so the
                # guess reappears unflagged. (sim-auditor CP-H2 #2.)
                mesh.upsert_node(key, kind=row["kind"] or "file",
                                 subtype="%s/%s" % (model, row["subtype"] or ""),
                                 path=row["path"], title=row["title"],
                                 title_method=row["title_method"],
                                 body=row["title"] or "", as_of=as_of)
                # No `if row["path"]` here: the query already says
                # `WHERE path IS NOT NULL`, so the branch could not be taken.
                # The invariant lives in the SQL, one place, and the mutation
                # harness breaks it there rather than in a dead guard that
                # made the code look more careful than it was.
                by_basename[os.path.basename(row["path"])].append(
                    (model, row["node_key"]))

        code_index = self._mirror_code(mesh, code_paths, as_of, mirrored)

        removed = 0
        if prune:
            for row in mesh.db.execute("SELECT node_key FROM nodes").fetchall():
                if row["node_key"] not in mirrored:
                    mesh.delete_node(row["node_key"])
                    removed += 1

        self._figure_for(mesh, loaded, by_basename, as_of, report)
        self._mentions_file(mesh, loaded, as_of, report)
        self._cites_code(mesh, loaded, code_index, as_of, report)
        self._temporal_cohort(mesh, loaded, as_of, report)
        # Named in the report whether it ran or not. "0 edges" and "no
        # repository was given" are different facts, and CITES_CODE learned
        # that the hard way -- see `code_inventory` below.
        if repo_root:
            self._co_changed(mesh, loaded, as_of, report, repo_root)
        mesh.rebuild_fts()
        mesh.close()
        return {"edges": dict(report), "models": sorted(loaded),
                "missing": sorted(missing), "stubs_removed": removed,
                # The path actually read, not the one passed: a caller who
                # names a subdirectory should see which repository answered.
                "co_change": ("absent" if not repo_root
                              else self.repo_top(repo_root)),
                "code_inventory": ("absent" if code_index is None
                                   else len(code_index))}

    def _figure_for(self, mesh, loaded, by_basename, as_of, report):
        """A document or note naming an image file points at that image.

        Matched on filename, because M2 has no content hash to match on. The
        gate that matters is the negative one: a name that does not exist must
        produce no edge at all, rather than a fuzzy nearest match.
        """
        if "m2" not in loaded:
            return
        image_names = {}
        for row in loaded["m2"].db.execute(
                "SELECT node_key, path FROM nodes WHERE kind='image'"):
            image_names[os.path.basename(row["path"])] = row["node_key"]
        if not image_names:
            return
        for model in ("m3", "m1"):
            if model not in loaded:
                continue
            for row in loaded[model].db.execute(
                    "SELECT node_key, body FROM nodes WHERE body IS NOT NULL "
                    "AND kind IN ('file','document')"):
                body = row["body"]
                for name, image_key in image_names.items():
                    if name in body:
                        mesh.upsert_edge("%s::%s" % (model, row["node_key"]),
                                         "m2::%s" % image_key,
                                         "FIGURE_FOR", as_of,
                                         method="basename")
                        report["FIGURE_FOR"] += 1

    CODE_MODEL = "code"

    @staticmethod
    @functools.lru_cache(maxsize=8192)
    def _boundary(form, is_path):
        """`form` where it is a name of its own, not glued inside a longer one.

        Substring containment is not naming. Measured on the real corpus before
        this existed: 89 of 1 253 basename edges rested on a name that occurs
        ONLY inside a longer filename -- `runner.py` found in `live_runner.py`,
        `bridge.py` in `signoz-bridge.py` -- each one a confident 0.6 pointing
        at a file the text never mentions.

        The right side excludes word characters either way, so `main.py` never
        matches `main.pyc`. A trailing dot is allowed: prose ends sentences.

        The left side differs by what is being matched, and the difference is
        the whole point:

          * a BARE NAME may follow a slash. `orchestrator/bin/memory.py` names
            `memory.py`, and when that basename is unique in the inventory it
            names exactly one file. Blocking it was the first version of this
            rule and it cost 663 of 1 233 edges on the real corpus -- caught
            by counting them, not by reading the regex.
          * a PATH may not. `proj/api/handler.js` inside
            `vendor/proj/api/handler.js` is a different tree that happens to
            end the same way, and the path forms exist precisely to be more
            specific than the name.
        """
        left = r"(?<![\w.\-/])" if is_path else r"(?<![\w.\-])"
        return re.compile(left + re.escape(form) + r"(?![\w])")

    @classmethod
    def _names(cls, form, body, is_path=False):
        """Cheap containment first, then the boundary. Same answer, less work:
        the regex runs only where the plain substring already matched."""
        return (form in body
                and cls._boundary(form, is_path).search(body) is not None)

    def _unsafe_prune(self, mesh, code_paths, missing):
        """Why this prune must not run, or None. Asks only; writes nothing.

        Both cases are the same argument: a stub whose source was not consulted
        is not stale, it is unlisted, and deleting it turns a missing argument
        or a temporary outage into permanent data loss.
        """
        if code_paths is None and self._has_code_stubs(mesh):
            return ("cannot prune the federation without a code inventory: "
                    "the code stubs already here would be deleted as stale "
                    "when they are merely unlisted. Pass --code-root.")
        if missing:
            return ("cannot prune the federation while %s is unreadable"
                    % ", ".join(sorted(missing)))
        return None

    def _has_code_stubs(self, mesh):
        row = mesh.db.execute(
            "SELECT 1 FROM nodes WHERE kind='code' LIMIT 1").fetchone()
        return row is not None

    def _mirror_code(self, mesh, code_paths, as_of, mirrored):
        """Mirror the code inventory as stubs. Returns the index, or None.

        Stubs and not a model: a code node here carries a path and a name and
        nothing else -- no body, no sections, no FTS content of its own -- so
        the mesh cannot be mistaken for a place where code is indexed. Reading
        code is `code-review-graph`'s job, and the federation's claim about
        code is exactly one relation wide.

        `None` (no inventory offered) and `{}` (an inventory with no code in
        it) are kept apart all the way to the report, because "not asked" and
        "asked, found nothing" are the two answers a zero would merge.
        """
        if code_paths is None:
            return None
        index = {}
        for path in code_paths:
            path = os.path.normpath(path)
            key = "%s::%s" % (self.CODE_MODEL, path)
            mesh.upsert_node(key, kind="code", subtype="code/inventory",
                             path=path, title=os.path.basename(path),
                             body=os.path.basename(path), as_of=as_of)
            mirrored.add(key)
            index[path] = key
        return index

    def _cites_code(self, mesh, loaded, code_index, as_of, report):
        """CITES_CODE: prose that names a source file.

        Two ways to say it, weighted differently and never merged:

          * the full path -- `method="mention"` (0.5), the same weight
            MENTIONS_FILE gives the same evidence;
          * the bare filename, and ONLY when that filename is unique in the
            inventory -- `method="basename"` (0.6).

        The uniqueness condition is the whole gate. A tree of any size holds a
        dozen `index.ts` and thirty `__init__.py`, and a note saying "see
        utils.py" names none of them in particular. Emitting an edge to
        whichever one sorted first would be a coin flip wearing a confidence
        of 0.6, which is worse than the missing edge it replaces: a gap is
        visible and a wrong edge is not.

        M2 and M4 are not sources here. M2 bodies are filenames by
        construction -- every image would "mention" a code file whose name it
        shares -- and M4 bodies are a basename plus, for a database, its table
        names. Neither is prose, and this relation is about prose.
        """
        if not code_index:
            return
        by_basename = collections.defaultdict(list)
        for path in code_index:
            by_basename[os.path.basename(path)].append(path)
        unique = {name: paths[0] for name, paths in by_basename.items()
                  if len(paths) == 1}

        # How a path is WRITTEN, which is not how it is stored. The store
        # holds an absolute path; prose says `proj/api/handler.js`, relative
        # to the corpus root, because that is what the project calls it. Both
        # forms count as naming the path -- and only those two. Matching any
        # suffix would make `api/handler.js` and then `handler.js` a "path"
        # mention, which is the basename case wearing a different label and
        # skipping the uniqueness condition that makes it safe.
        root = home_root()
        written = collections.defaultdict(list)
        for path in code_index:
            written[path].append(path)
            rel = os.path.relpath(path, root)
            if not rel.startswith(".."):
                written[path].append(rel)

        for model in ("m1", "m3"):
            if model not in loaded:
                continue
            for row in loaded[model].db.execute(
                    "SELECT node_key, body FROM nodes WHERE body IS NOT NULL "
                    "AND kind IN ('file','document')"):
                body = row["body"]
                src = "%s::%s" % (model, row["node_key"])
                hit_paths = [p for p, forms in written.items()
                             if any(self._names(f, body, is_path=True)
                                    for f in forms)]
                for path in hit_paths:
                    mesh.upsert_edge(src, code_index[path], "CITES_CODE",
                                     as_of, method="mention")
                    report["CITES_CODE"] += 1
                named = set(hit_paths)
                for name, path in unique.items():
                    if path in named or not self._names(name, body):
                        continue
                    mesh.upsert_edge(src, code_index[path], "CITES_CODE",
                                     as_of, method="basename")
                    report["CITES_CODE"] += 1

    def _mentions_file(self, mesh, loaded, as_of, report):
        """A node whose text contains another model's file path."""
        paths = {}
        for model, s in loaded.items():
            for row in s.db.execute(
                    "SELECT node_key, path FROM nodes WHERE path IS NOT NULL"):
                paths[row["path"]] = (model, row["node_key"])
        for model, s in loaded.items():
            if model == "m2":
                continue  # M2 bodies are filenames, not prose
            for row in s.db.execute(
                    "SELECT node_key, body FROM nodes WHERE body IS NOT NULL "
                    "AND length(body) > 20 AND kind IN ('file','document')"):
                for path, (target_model, target_key) in paths.items():
                    if target_model == model:
                        continue
                    if path in row["body"]:
                        mesh.upsert_edge("%s::%s" % (model, row["node_key"]),
                                         "%s::%s" % (target_model, target_key),
                                         "MENTIONS_FILE", as_of,
                                         method="mention")
                        report["MENTIONS_FILE"] += 1

    def _temporal_cohort(self, mesh, loaded, as_of, report, min_days=2):
        """Files whose activity masks are IDENTICAL, within one anchor.

        Not `cohort_overlap`, and not a bitwise AND -- the docstring used to
        say both, and neither was ever what this did. Grouping by equal mask is
        the deliberate choice: it is a dict lookup rather than a comparison per
        pair, and the alternative was `cohort_overlap(mask, mask)`, a value
        compared against itself. The cost is real and worth stating plainly: two
        files sharing five of six days get no edge. This relation is "changed on
        exactly the same days", which is narrower than the name suggests.

        The anchor is part of the key, and that is a fix rather than a detail.
        `datelist_int` bit i means "anchor minus i days", so two masks are only
        comparable when they were encoded against the same anchor -- and this
        loop reads across models, which are built by separate commands on
        whatever days they happened to run. Grouping on the mask alone silently
        equated bit 3 of a model built on Monday with bit 3 of one built on
        Friday and emitted a confident cross-model edge from it. Nothing
        enforced a shared anchor: `refresh_all_datelists` exists for exactly
        that and has never been called outside a test, so the discipline the
        old docstring credited it with was not running.
        """
        masks = []
        for model, s in loaded.items():
            for row in s.db.execute(
                    "SELECT node_key, datelist_int, datelist_anchor FROM nodes "
                    "WHERE datelist_int != 0 AND path IS NOT NULL"):
                masks.append((model, row["node_key"], row["datelist_int"],
                              row["datelist_anchor"]))
        by_mask = collections.defaultdict(list)
        for model, key, mask, anchor in masks:
            by_mask[(anchor, mask)].append((model, key))
        for (_anchor, mask), members in by_mask.items():
            if bin(mask).count("1") < min_days or len(members) < 2:
                continue
            cross = {m for m, _ in members}
            if len(cross) < 2:
                continue  # a cohort inside one model is not a mesh fact
            first = members[0]
            for other in members[1:]:
                if other[0] == first[0]:
                    continue
                # Members of `by_mask` share one mask AND one anchor by
                # construction, so the comparison is settled before this point
                # and the edge is unconditional. Re-checking it here with
                # `cohort_overlap(mask, mask)` would compare a value against
                # itself -- the shape that hid a dead cross-validation in CP-5.
                mesh.upsert_edge("%s::%s" % first, "%s::%s" % other,
                                 "TEMPORAL_COHORT", as_of, method="cohort")
                report["TEMPORAL_COHORT"] += 1

    # -- graph queries -----------------------------------------------------

    def _read_mesh(self):
        """Open the mesh store for reading, or refuse.

        `Store(path)` connects with sqlite3, which CREATES the file, and then
        migrates it -- so a read query against a mesh that does not exist used
        to leave a fully-formed empty database behind and answer `count: 0`.
        With `mesh_db=None` the path became the string "None" and the file
        landed in whatever directory the process happened to start in.

        That is the opposite of what mcp_server.py promises: an MCP server is
        driven unattended, and "read-only by construction" has to hold for the
        query paths, not only for the ones that take a `--mesh-db` to write.
        `build_edges` already refused on a missing path; these did not.

        Refusing is also the more useful answer. An empty mesh and an absent
        mesh give the same zero, and only one of them is worth telling a user
        about.
        """
        if not self.mesh_db:
            raise ModelUnavailable(
                "no mesh database configured; pass --mesh-db or run "
                "`homegraph mesh build`")
        if not os.path.exists(self.mesh_db):
            raise ModelUnavailable("no mesh database at %s; run "
                                   "`homegraph mesh build`" % self.mesh_db)
        return Store(self.mesh_db)

    def _resolve_key(self, mesh, key):
        """A bare or qualified key, resolved to mesh.db's `<model>::<path>` form.

        `mesh_search` hands back `node_key` as a bare path with `model` as a
        separate field, but the mesh keys nodes `<model>::<path>`. A caller
        that chains a search hit straight into `neighbours`/`path` therefore
        holds a key the store never indexed under -- measured against
        real-mesh.db (9 125 nodes): 0 of 7 search-hit keys resolved as given,
        7 of 7 resolved once qualified, and `mesh_neighbors` on the unresolved
        form answered `count=0, status=complete` -- a confident wrong answer,
        not a visible miss.

        Exact match wins first, so a caller that already holds a qualified
        key -- or a key from mid-traversal, which is always qualified because
        it came out of `edges` -- pays nothing extra. Failing that, every
        model prefix actually present in the mesh is tried, read off the
        stored `node_key`s rather than off `self.model_paths`: `code` stubs
        live in mesh.db with no entry in `self.model_paths`, and a literal
        `m1..m4, code` list drifts the day a sixth partition lands.

        Two or more prefixes resolving raises `AmbiguousKey` instead of
        picking one -- see that class for the measurement backing the
        refusal. Nothing resolving returns `None`: absent is absent, same as
        before this resolver existed.
        """
        if mesh.node_id(key) is not None:
            return key
        prefixes = [row[0] for row in mesh.db.execute(
            "SELECT DISTINCT substr(node_key, 1, instr(node_key, '::') - 1) "
            "FROM nodes WHERE node_key LIKE '%::%'")]
        candidates = [q for q in ("%s::%s" % (model, key)
                                   for model in sorted(prefixes))
                      if mesh.node_id(q) is not None]
        if len(candidates) > 1:
            raise AmbiguousKey(
                "%r resolves under more than one model prefix: %s"
                % (key, ", ".join(candidates)))
        return candidates[0] if candidates else None

    def neighbours(self, node_key, depth=1):
        mesh = self._read_mesh()
        try:
            seen, frontier, out = set(), [node_key], []
            for _ in range(depth):
                nxt = []
                for key in frontier:
                    # Resolved BEFORE the seen-check, not after: `key` starts
                    # bare (a caller's node_key) and every later frontier
                    # entry is already qualified (pulled from `edges`), so
                    # memoising the unresolved form let the origin re-enter
                    # under its qualified spelling at the next depth and get
                    # expanded a second time -- measured on a three-node
                    # chain, bare and qualified starts agreed at depth 1-2
                    # and diverged (5 vs 4 edges) at depth 3.
                    resolved = self._resolve_key(mesh, key)
                    if resolved is None or resolved in seen:
                        continue
                    seen.add(resolved)
                    nid = mesh.node_id(resolved)
                    for row in mesh.db.execute(
                            "SELECT d.node_key k, e.rel, e.method, "
                            "e.confidence FROM edges e "
                            "JOIN nodes d ON d.id=e.dst WHERE e.src=?", (nid,)):
                        out.append(Neighbour(resolved, row["rel"], row["k"],
                                             row["method"], row["confidence"]))
                        nxt.append(row["k"])
                    for row in mesh.db.execute(
                            "SELECT s.node_key k, e.rel, e.method, "
                            "e.confidence FROM edges e "
                            "JOIN nodes s ON s.id=e.src WHERE e.dst=?", (nid,)):
                        out.append(Neighbour(row["k"], row["rel"], resolved,
                                             row["method"], row["confidence"]))
                        nxt.append(row["k"])
                frontier = nxt
            return out
        finally:
            mesh.close()

    def path(self, src, dst, max_depth=4):
        """Shortest path between two mesh nodes. Breadth-first, cycle-safe."""
        mesh = self._read_mesh()
        try:
            rsrc = self._resolve_key(mesh, src)
            rdst = self._resolve_key(mesh, dst)
            if rsrc is None or rdst is None:
                return None
            seen, queue = {rsrc}, collections.deque([[rsrc]])
            while queue:
                trail = queue.popleft()
                if len(trail) > max_depth:
                    return None
                nid = mesh.node_id(trail[-1])
                for row in mesh.db.execute(
                        "SELECT d.node_key k FROM edges e "
                        "JOIN nodes d ON d.id=e.dst WHERE e.src=? "
                        "UNION SELECT s.node_key k FROM edges e "
                        "JOIN nodes s ON s.id=e.src WHERE e.dst=?",
                        (nid, nid)):
                    if row["k"] == rdst:
                        return trail + [rdst]
                    if row["k"] not in seen:
                        seen.add(row["k"])
                        queue.append(trail + [row["k"]])
            return None
        finally:
            mesh.close()
