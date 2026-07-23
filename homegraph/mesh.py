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
import os
import sqlite3

from .search import RRF_K, fts_query
from .store import Store


class ModelUnavailable(Exception):
    pass


class MeshResult:
    def __init__(self, hits, models_queried, models_missing, warnings):
        self.hits = hits
        self.models_queried = models_queried
        self.models_missing = models_missing
        self.warnings = warnings

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
            sql = ("SELECT n.id node_id, n.node_key, n.title, n.subtype, "
                   "n.path, n.content_hash, bm25(nodes_fts) score "
                   "FROM nodes_fts JOIN nodes n ON n.id = nodes_fts.rowid "
                   "WHERE nodes_fts MATCH ?")
            args = [expr]
            if not include_all:
                sql += " AND (n.subtype IS NULL OR n.subtype != 'transcript')"
            if as_of:
                # Time travel: a node counts as it stood on that date.
                sql += " AND n.first_seen <= ?"
                args.append(as_of)
            sql += " ORDER BY score LIMIT ?"
            args.append(limit)
            try:
                rows = s.db.execute(sql, args).fetchall()
            except sqlite3.Error as exc:
                missing.append(model)
                warnings.append("%s failed mid-query: %r" % (model, exc))
                continue
            rankings[model] = [dict(r, model=model) for r in rows]

        hits = self._rrf(rankings, limit)
        if missing:
            warnings.insert(0, "PARTIAL RESULT -- %s did not answer. Counts "
                                "and ranking are incomplete."
                            % ", ".join(sorted(set(missing))))
        return MeshResult(hits, queried, sorted(set(missing)), warnings)

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
        if row.get("content_hash"):
            return "hash:%s" % row["content_hash"]
        if row.get("path"):
            return "path:%s" % os.path.normpath(row["path"])
        return "key:%s:%s" % (row.get("model"), row["node_key"])

    @staticmethod
    def _rrf(rankings, limit):
        """RRF across models. Ranks only -- never the BM25 scores.

        BM25 is index-relative: -99 in one model and -0.5 in another say
        nothing about each other. Ordering by raw score therefore ranks by
        which index happens to produce larger magnitudes, which looks like a
        relevance ordering and is not one.
        """
        fused = {}
        for model, rows in rankings.items():
            for rank, row in enumerate(rows, start=1):
                key = Mesh._fusion_key(row)
                slot = fused.setdefault(key, {
                    "key": key, "model": model, "node_key": row["node_key"],
                    "title": row.get("title"), "path": row.get("path"),
                    "score": 0.0, "sources": [], "models": []})
                slot["score"] += 1.0 / (RRF_K + rank)
                slot["sources"].append("%s#%d" % (model, rank))
                if model not in slot["models"]:
                    slot["models"].append(model)
        out = sorted(fused.values(), key=lambda h: (-h["score"], h["key"]))
        for i, hit in enumerate(out[:limit], start=1):
            hit["rank"] = i
        return out[:limit]

    def explain(self, query, limit=20):
        """Per-model breakdown of who answered and at what rank."""
        result = self.search(query, limit=limit)
        by_model = collections.Counter(h["model"] for h in result.hits)
        return {
            "query": query,
            "status": result.status,
            "models_queried": result.models_queried,
            "models_missing": result.models_missing,
            "hits_per_model": dict(by_model),
            "fusion": "reciprocal rank fusion, k=%d; BM25 scores are never "
                      "compared across models" % RRF_K,
            "warnings": result.warnings,
        }

    # -- cross-model graph -------------------------------------------------

    def build_edges(self, as_of, prune=False):
        """Compute cross-model edges into mesh.db. Never touches model stores.

        `prune` drops every stub the mirror below did not just write. It is off
        by default, because a partial build -- one model unreadable -- would
        otherwise delete the stubs of a model that is merely absent today. It
        is on for `homegraph update`, where the federation has to stop
        answering about files that are gone: a stale stub carries a title and a
        path and answers confidently about a world that no longer exists.
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

        # Mirror every model node into mesh as a lightweight stub, so edges
        # have endpoints without copying bodies or duplicating the indexes.
        by_basename = collections.defaultdict(list)
        mirrored = set()
        for model, s in loaded.items():
            for row in s.db.execute(
                    "SELECT node_key, title, path, content_hash, kind, "
                    "subtype, datelist_int FROM nodes WHERE path IS NOT NULL"):
                key = "%s::%s" % (model, row["node_key"])
                mirrored.add(key)
                mesh.upsert_node(key, kind=row["kind"] or "file",
                                 subtype="%s/%s" % (model, row["subtype"] or ""),
                                 path=row["path"], title=row["title"],
                                 body=row["title"] or "", as_of=as_of)
                # No `if row["path"]` here: the query already says
                # `WHERE path IS NOT NULL`, so the branch could not be taken.
                # The invariant lives in the SQL, one place, and the mutation
                # harness breaks it there rather than in a dead guard that
                # made the code look more careful than it was.
                by_basename[os.path.basename(row["path"])].append(
                    (model, row["node_key"]))

        removed = 0
        if prune:
            if missing:
                # Refusing rather than pruning: with a model unreadable its
                # stubs are not stale, they are unqueried, and deleting them
                # would turn a temporary outage into permanent data loss.
                mesh.close()
                raise ModelUnavailable(
                    "cannot prune the federation while %s is unreadable"
                    % ", ".join(sorted(missing)))
            for row in mesh.db.execute("SELECT node_key FROM nodes").fetchall():
                if row["node_key"] not in mirrored:
                    mesh.delete_node(row["node_key"])
                    removed += 1

        self._figure_for(mesh, loaded, by_basename, as_of, report)
        self._mentions_file(mesh, loaded, as_of, report)
        self._temporal_cohort(mesh, loaded, as_of, report)
        mesh.rebuild_fts()
        mesh.close()
        return {"edges": dict(report), "models": sorted(loaded),
                "missing": sorted(missing), "stubs_removed": removed}

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
                                         "FIGURE_FOR", as_of)
                        report["FIGURE_FOR"] += 1

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
                                         "MENTIONS_FILE", as_of)
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
                                 "TEMPORAL_COHORT", as_of)
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

    def neighbours(self, node_key, depth=1):
        mesh = self._read_mesh()
        try:
            seen, frontier, out = set(), [node_key], []
            for _ in range(depth):
                nxt = []
                for key in frontier:
                    if key in seen:
                        continue
                    seen.add(key)
                    nid = mesh.node_id(key)
                    if nid is None:
                        continue
                    for row in mesh.db.execute(
                            "SELECT d.node_key k, e.rel FROM edges e "
                            "JOIN nodes d ON d.id=e.dst WHERE e.src=?", (nid,)):
                        out.append((key, row["rel"], row["k"]))
                        nxt.append(row["k"])
                    for row in mesh.db.execute(
                            "SELECT s.node_key k, e.rel FROM edges e "
                            "JOIN nodes s ON s.id=e.src WHERE e.dst=?", (nid,)):
                        out.append((row["k"], row["rel"], key))
                        nxt.append(row["k"])
                frontier = nxt
            return out
        finally:
            mesh.close()

    def path(self, src, dst, max_depth=4):
        """Shortest path between two mesh nodes. Breadth-first, cycle-safe."""
        mesh = self._read_mesh()
        try:
            if mesh.node_id(src) is None or mesh.node_id(dst) is None:
                return None
            seen, queue = {src}, collections.deque([[src]])
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
                    if row["k"] == dst:
                        return trail + [dst]
                    if row["k"] not in seen:
                        seen.add(row["k"])
                        queue.append(trail + [row["k"]])
            return None
        finally:
            mesh.close()
