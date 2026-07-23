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
import os
import re
import typing
import sqlite3

from .config import home_root
from .search import RRF_K, fts_query
from .store import Store


class ModelUnavailable(Exception):
    pass


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

        hits = self._rrf(rankings, limit)
        if missing:
            warnings.insert(0, "PARTIAL RESULT -- %s did not answer. Counts "
                                "and ranking are incomplete."
                            % ", ".join(sorted(set(missing))))
        return MeshResult(hits, queried, sorted(set(missing)), warnings)

    @staticmethod
    def _fts_rows(store, expr, limit, as_of, include_all, kind=None):
        """One FTS query, one place. The models and the code stubs share it.

        Written out twice, the `transcript` filter and the `as_of` predicate
        would agree until one of them was edited -- and the one that stopped
        being edited would be the one nobody reads. `kind` is the only thing
        the two callers differ by.
        """
        sql = ("SELECT n.id node_id, n.node_key, n.title, n.subtype, "
               "n.path, n.content_hash, bm25(nodes_fts) score "
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

    def build_edges(self, as_of, prune=False, code_paths=None):
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
        mesh.rebuild_fts()
        mesh.close()
        return {"edges": dict(report), "models": sorted(loaded),
                "missing": sorted(missing), "stubs_removed": removed,
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
                            "SELECT d.node_key k, e.rel, e.method, "
                            "e.confidence FROM edges e "
                            "JOIN nodes d ON d.id=e.dst WHERE e.src=?", (nid,)):
                        out.append(Neighbour(key, row["rel"], row["k"],
                                             row["method"], row["confidence"]))
                        nxt.append(row["k"])
                    for row in mesh.db.execute(
                            "SELECT s.node_key k, e.rel, e.method, "
                            "e.confidence FROM edges e "
                            "JOIN nodes s ON s.id=e.src WHERE e.dst=?", (nid,)):
                        out.append(Neighbour(row["k"], row["rel"], key,
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
