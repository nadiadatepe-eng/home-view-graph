#!/usr/bin/env python3
"""Apply a filesystem diff to a built model, instead of rebuilding it.

`incremental.py` already answers *what changed*. This is the half that was
missing: what to do about it.

The claim is narrow and it is the only one worth making --

    a store that was built on corpus A and updated to corpus B must be
    indistinguishable from a store built on corpus B from scratch.

Anything weaker than that gives you a graph whose contents depend on how you
got there, and nobody can reason about such a thing. CP-8 checks it as a set
comparison over nodes and edges, not as a count: counts are equal when one node
has been swapped for another, which is precisely the failure an incremental
path produces.

Five outcomes, and each costs something different:

    added      built
    changed    forgotten, then built
    touched    one UPDATE of the stored mtime. New mtime, identical bytes --
               a formatter or a sync tool. No reparse, no re-embed.
    unchanged  nothing at all
    removed    forgotten

**Removal deletes, and it deletes history.** The alternative -- a tombstone, or
a node whose `last_seen` stops advancing, which is what the edge table does for
relations -- was considered and rejected. It cannot coexist with the
equivalence claim above: a full rebuild of B has no node for a file that is not
in B, so keeping one would make `update` and `build` diverge permanently and
irreversibly. The temporal layer already discards daily observations after 90
days; discarding a deleted file's history is that same policy applied to a file
rather than to a date. It is a real loss and it is written here rather than
discovered later: after `update`, `edges_as_of()` cannot show you a relation
that only a deleted file participated in.

**Deleting a node deletes its edges**, and not by a new mechanism: `edges.src`
and `edges.dst` are `ON DELETE CASCADE` with `PRAGMA foreign_keys = ON`, and
`Store.delete_node` additionally removes the FTS row, which has no foreign keys
of its own. A dangling endpoint is not possible; an orphaned FTS row is, which
is why CP-1 looks for them.

**M4 is refused, not approximated.** Its cold-state rollup is an aggregation
over the whole corpus -- one node standing for many files -- so applying a
per-file diff to it produces rollup rows that no longer reconcile with the
files they claim to summarise. CP-5's cross-validation would catch that, which
is the point: the honest move is to say so and rebuild, rather than to ship an
update path whose arithmetic quietly stops adding up.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from . import incremental
from .config import home_root


class NotBuilt(RuntimeError):
    """There is nothing to update. Refused rather than silently built."""


class ConfigChanged(RuntimeError):
    """The layout changed, so this is not a file diff. Refused."""


class CannotUpdate(RuntimeError):
    """This model has no correct incremental path. Named, not approximated."""


@dataclass(frozen=True)
class ModelSpec:
    name: str
    label: str          # the corpus category this model owns
    kind: str           # the `kind` its file-backed nodes carry
    use_hash: bool      # False for M2: hashing means reading
    # Models whose extraction depends on the rest of the corpus need a wider
    # rebuild set than the files that changed. M3 does, because its wikilink
    # index is global. M1 does too, since REFERENCES_FILE only draws an edge
    # when the file named in prose is already a node -- so a document's own
    # graph depends on which OTHER documents exist. M2 reads one file at a
    # time and does not.
    #
    # This line said "M1 and M2 read one file at a time and do not" for a day
    # after REFERENCES_FILE shipped, and it was the only place the assumption
    # was written down. An adversarial audit found it by building corpus A,
    # updating to B, and comparing edge sets -- the equivalence CP-8 asserts,
    # on the model CP-8 does not cover.
    affected: Callable[..., list[str]] | None = None


def _m1(store, paths, as_of):
    from .models.m1_build import build
    return build(store, paths, as_of)


def _m2(store, paths, as_of):
    from .models.m2_build import build
    return build(store, paths, as_of)


def _m3(store, paths, as_of, all_paths=None, cfg=None):
    from .models.m3_build import build, index_file_for, rules_from_config
    # The name index has to see the WHOLE corpus even when three files are
    # being rebuilt. Without that a partial build declares every link broken --
    # the targets are real, they were just not in the batch.
    return build(store, paths, as_of, index_paths=all_paths or paths,
                 rules=rules_from_config(cfg) if cfg is not None else None,
                 index_file=index_file_for(home_root()))


def _m3_affected(store, changes, all_paths):
    """Files whose link resolution can change because other files came or went.

    M3 resolves `[[name]]` against a flat index over the whole corpus, so
    adding or removing a file changes the answer for every file that links to
    that NAME -- both ways: a new page makes a broken link resolve, and a
    deleted page makes a resolved link break. Rebuilding only the files that
    changed on disk leaves those neighbours holding an edge a full rebuild
    would not produce.

    Two sources, and they are not the same:

      * anything with a `WIKILINKS_TO` edge to a node carrying one of the
        affected page names, resolved or broken;
      * anything whose body mentions one of the affected paths, which is what
        `MENTIONS_PATH` and relative links resolve against.

    This is a heuristic, and it is labelled one. What makes it safe to ship is
    that CP-8 does not take its word for anything: it compares the updated
    store against a full rebuild as sets. If the expansion is too narrow the
    equivalence gate goes red, which is exactly how the missing index
    population was found.

    Called BEFORE anything is forgotten -- the edges it reads belong to the
    nodes about to be deleted.
    """
    from .models.m3_markdown import page_name

    # Computed before the early return below: editing `index.md` in place is a
    # `changed`, not an `added`/`removed`, and it is the ordinary case. Leaving
    # it after the guard meant re-filing a page under a new heading updated
    # nothing at all.
    extra = _index_and_its_articles(store, changes, all_paths)
    moved = list(changes.added) + list(changes.removed)
    if not moved:
        current = set(all_paths)
        return sorted((extra & current) - set(changes.added)
                      - set(changes.changed) - set(changes.removed))
    names = {page_name(p) for p in moved}
    for row in store.db.execute(
            "SELECT s.node_key src, d.node_key dst FROM edges e "
            "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst "
            "WHERE e.rel = 'WIKILINKS_TO'"):
        dst = row["dst"]
        name = (dst[len("wikilink:"):] if dst.startswith("wikilink:")
                else page_name(dst))
        if name in names:
            extra.add(row["src"])
    for row in store.db.execute(
            "SELECT node_key, body FROM nodes "
            "WHERE body IS NOT NULL AND kind = 'file'"):
        if any(p in row["body"] for p in moved):
            extra.add(row["node_key"])
    current = set(all_paths)
    return sorted((extra & current) - set(changes.added)
                  - set(changes.changed) - set(changes.removed))


def _index_and_its_articles(store, changes, all_paths):
    """`index.md` and the pages it files are rebuilt together, or not at all.

    `CATEGORIZED_UNDER` runs article -> category, but only the index's own
    build writes it, and `forget` deletes edges by `src`. Neither half survives
    alone: rebuilding the article drops the edge with nobody left to rewrite
    it, and rebuilding the index leaves behind the edges of articles it no
    longer lists. So either half drags the other in.

    Costs nothing on a corpus with no root `index.md`, which is every home
    directory -- the query never runs.
    """
    from .models.m3_build import index_file_for

    filed = {row["src"] for row in store.db.execute(
        "SELECT s.node_key src FROM edges e JOIN nodes s ON s.id = e.src "
        "WHERE e.rel = 'CATEGORIZED_UNDER'")}
    index_file = index_file_for(home_root())
    if index_file is None or index_file not in set(all_paths):
        # The index is gone. Its edges are not: nothing else writes them and
        # nothing else deletes them, so the pages it used to file keep a
        # category no file assigns any more. Rebuilding them is what removes
        # the edges -- `forget` deletes by src -- and `prune` then takes the
        # emptied `category:` nodes, the same way it takes an emptied `tag:`.
        return filed
    touched = set(changes.added) | set(changes.changed) | set(changes.removed)
    if index_file in touched:
        return filed | {index_file}
    # Arrivals and departures are NOT handled here, deliberately. A name the
    # index lists with no file behind it gets its category the day that file
    # appears -- and the caller's existing expansion already drags the index in
    # for exactly that case, because the index holds a `WIKILINKS_TO` edge to
    # the `wikilink:` node standing in for the missing page. A branch here for
    # it was written, measured redundant (its mutation survived, 2026-07-31),
    # and removed. K9 stays as the gate that says so.
    return {index_file} if touched & filed else set()


def _m1_affected(store, changes, all_paths):
    """Documents whose REFERENCES_FILE answer changes because a file moved.

    Narrower than M3's, because the relation is narrower: M1 resolves a
    mention against paths, not against a flat name index, so only a document
    whose text contains one of the moved paths -- in any of the three forms
    `_resolve_mention` accepts -- can gain or lose an edge.

    Both directions matter. An added file makes an unresolved mention resolve;
    a removed one leaves a document holding an edge a full rebuild would not
    draw. Called BEFORE anything is forgotten, so the bodies it reads are the
    ones about to be replaced.

    A heuristic, like M3's, and safe for the same reason: nothing takes its
    word for it. The equivalence gate compares the updated store against a
    full rebuild as sets, so an expansion that is too narrow shows up red.
    """
    moved = list(changes.added) + list(changes.removed)
    if not moved:
        return []
    # The written forms: the absolute path, the corpus-relative one, and the
    # bare name a sibling reference uses. Whether a given document's mention
    # resolves is `_references_file`'s question -- this only has to be wide
    # enough not to miss it.
    root = home_root()
    forms = set()
    for path in moved:
        forms.add(path)
        rel = os.path.relpath(path, root)
        if not rel.startswith(".."):
            forms.add(rel)
        forms.add(os.path.basename(path))
    extra = set()
    for row in store.db.execute(
            "SELECT node_key, body FROM nodes "
            "WHERE body IS NOT NULL AND kind = 'document'"):
        if any(f in row["body"] for f in forms):
            extra.add(row["node_key"])
    current = set(all_paths)
    return sorted((extra & current) - set(changes.added)
                  - set(changes.changed) - set(changes.removed))


SPECS = {
    "m1": ModelSpec("m1", "document", "document", True, _m1_affected),
    # use_hash=False is not an optimisation. Hashing an image means opening it,
    # and M2's entire safety argument is that it never does.
    "m2": ModelSpec("m2", "image", "image", False),
    "m3": ModelSpec("m3", "markdown", "file", True, _m3_affected),
}
BUILDERS = {"m1": _m1, "m2": _m2, "m3": _m3}

# Named so the refusal can explain itself, rather than reporting "unknown
# model" for something that exists and is deliberately excluded.
NO_INCREMENTAL = {
    "m4": "M4's cold-state rollup aggregates the whole corpus into single "
          "nodes, so a per-file diff cannot be applied to it without breaking "
          "the reconciliation CP-5 checks. Rebuild M4.",
}


@dataclass
class UpdateReport:
    model: str
    changes: dict[str, int] = field(default_factory=dict)
    rebuilt: int = 0            # files actually parsed again
    neighbours: int = 0         # rebuilt only because the corpus around them moved
    forgotten: int = 0          # file nodes removed
    pruned: int = 0             # derived nodes left with nothing pointing at them
    restatted: int = 0          # `touched`: a stat and an UPDATE, no parse
    nodes_before: int = 0
    nodes_after: int = 0
    edges_before: int = 0
    edges_after: int = 0
    # Files the builder was asked to rebuild and could not read -- deleted or
    # chmod'd between the diff and the build. Their old edges are already gone
    # by then, so a silent skip leaves a node holding stale text with no
    # relations, and the run still reports success. Surfaced instead.
    unreadable: list = field(default_factory=list)
    # What the 90-day retention rolled up and deleted on this run. Reported
    # rather than silent: it is the one part of `update` that removes rows
    # nobody asked it to touch, and an empty dict is what a store younger than
    # the window correctly produces.
    retention: dict = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        # Annotated because the values are genuinely mixed: counts are int,
        # "nodes"/"edges" are pre-formatted "%d -> %d" strings. Inferred from
        # the first entry alone this is dict[str, str], and every later update()
        # is then a type error. Consumers only print or compare these.
        d: dict[str, object] = {"model": self.model}
        d.update(self.changes)
        d.update({"rebuilt": self.rebuilt, "neighbours": self.neighbours,
                  "forgotten": self.forgotten,
                  "pruned": self.pruned, "restatted": self.restatted,
                  "nodes": "%d -> %d" % (self.nodes_before, self.nodes_after),
                  "edges": "%d -> %d" % (self.edges_before, self.edges_after)})
        if self.unreadable:
            d["unreadable"] = len(self.unreadable)
        if self.retention.get("deleted_rows"):
            d["retention"] = "%d row(s) rolled into %d month(s)" % (
                self.retention["deleted_rows"], self.retention["rolled_up"])
        return d


# -- the config is part of the store's identity ----------------------------

FINGERPRINT_KEY = "config_fingerprint"


def fingerprint(config) -> str:
    """A stable digest of the layout this store was built under.

    Changing the image role is not a file change. It moves the boundary between
    two models, so files that were `image` become `EXCLUDED` and vice versa,
    and no diff over paths can see that -- the paths did not move. Left
    unchecked, `update` would happily produce a store holding half of one
    configuration and half of another, which would look like a build that
    worked.
    """
    payload = json.dumps(
        {"root": config.root,
         "roles": {k: sorted(v) for k, v in sorted(config.roles.items())}},
        sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def read_fingerprint(store) -> str | None:
    row = store.db.execute("SELECT value FROM metadata WHERE key = ?",
                           (FINGERPRINT_KEY,)).fetchone()
    return row["value"] if row else None


def write_fingerprint(store, value: str) -> None:
    store.db.execute(
        "INSERT INTO metadata(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (FINGERPRINT_KEY, value))


# -- forgetting -------------------------------------------------------------

def forget(store, path: str, keep_self: bool = False) -> int:
    """Remove a file and everything that exists only because of it.

    `keep_self` is for a file that changed rather than vanished: its derived
    nodes go, its own node stays and is overwritten in place by the rebuild.
    Deleting and recreating it would reset `first_seen` and drop every
    observation, which is history that the file did not lose -- it is still the
    same file, with different contents.

    Everything a build derives *from one file* carries that file's path --
    section nodes are `<path>#<n>` with `path` set -- so one query finds them
    all. Everything a build derives from *several* files (a tag, a wikilink
    target, a collection, an author) is pathless and shared, and is not removed
    here: it is removed by `prune()` if, and only if, nothing points at it any
    more. Deciding that per file would mean this function knowing which shared
    nodes each model invents, which is the same as reimplementing every builder.
    """
    keys = [r["node_key"] for r in store.db.execute(
        "SELECT node_key FROM nodes WHERE path = ?", (path,))
        if not (keep_self and r["node_key"] == path)]
    for key in keys:
        store.delete_node(key)
    if keep_self:
        # The node survives, so its edges are not cascaded away -- and a file
        # that dropped a tag or a link would keep pointing at it, indefinitely
        # and invisibly. Outbound only: an inbound edge belongs to whichever
        # file asserted it, and that file is not being rebuilt here.
        nid = store.node_id(path)
        if nid is not None:
            store.db.execute("DELETE FROM edges WHERE src = ?", (nid,))
            # ...and its VECTOR, for exactly the same reason one step further.
            # The node keeps its id and its history; its text does not. An
            # embedding is a claim about text, so once the rebuild overwrites
            # the body, the old vector describes something that is gone.
            #
            # This was reachable and reproduced: a file edited from "retrieval
            # search ranking" to "onion onion onion" kept the vector pointing at
            # `retrieval` and went on ranking confidently for a query it no
            # longer answers. Missing vectors make a node unfindable, which is
            # visible; a stale one makes it findable for the WRONG thing, which
            # is not. Deleting is right rather than re-embedding here: `update`
            # has no embedder and must not acquire one -- the provider is opt-in
            # and may cost a network round trip per node, which is not a thing a
            # filesystem diff gets to decide. `embed` fills the hole afterwards,
            # and now knows to look for it.
            store.db.execute("DELETE FROM embeddings WHERE node_id = ?", (nid,))
    return len(keys)


def prune(store) -> int:
    """Delete pathless nodes that nothing points at any more.

    A tag whose last tagged file is gone, a wikilink target whose last linker
    is gone, an image collection that has emptied. A pathless node with no
    edges in either direction represents nothing; a full rebuild would never
    have created it, so leaving it behind is exactly the divergence this
    module exists to prevent.

    Repeated until it reaches a fixed point, because removing one orphan can
    orphan another.
    """
    total = 0
    while True:
        keys = [r["node_key"] for r in store.db.execute(
            "SELECT node_key FROM nodes n WHERE n.path IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = n.id) "
            "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.dst = n.id)")]
        if not keys:
            return total
        for key in keys:
            store.delete_node(key)
        total += len(keys)


# -- the update itself ------------------------------------------------------

def update(store, model: str, paths, as_of, config, *,
           builder=None, allow_config_change: bool = False) -> UpdateReport:
    """Bring `store` from whatever it holds to what `paths` says it should."""
    if model in NO_INCREMENTAL:
        raise CannotUpdate("%s: %s" % (model, NO_INCREMENTAL[model]))
    spec = SPECS.get(model)
    if spec is None:
        raise CannotUpdate("unknown model %r; known: %s"
                           % (model, ", ".join(sorted(SPECS))))
    builder = builder or BUILDERS[model]

    report = UpdateReport(model=model)
    report.nodes_before = store.node_count()
    report.edges_before = store.edge_count()
    if report.nodes_before == 0:
        # An update of nothing is not a no-op that exits 0. It is a build that
        # never happened, and reporting success for it is the failure this
        # project keeps designing against.
        raise NotBuilt(
            "%s holds no nodes -- there is nothing to update. Build it first."
            % store.path)

    want = fingerprint(config)
    have = read_fingerprint(store)
    if have is not None and have != want and not allow_config_change:
        raise ConfigChanged(
            "%s was built under a different layout (%s, now %s). Changing a "
            "role moves the boundary between models, which no file diff can "
            "see -- rebuild rather than update." % (store.path, have, want))

    current = incremental.scan(paths)
    changes = incremental.diff(store, current, use_hash=spec.use_hash,
                               kinds=(spec.kind,))
    report.changes = changes.summary()

    # Computed before anything is forgotten: it reads edges belonging to the
    # nodes that are about to be deleted.
    extra = (list(spec.affected(store, changes, list(current)))
             if spec.affected else [])
    report.neighbours = len(extra)

    for key in changes.removed:
        report.forgotten += 1
        forget(store, key)
    # Forgotten before rebuilding, not after: a file that lost a heading must
    # lose the section node too, and `upsert` alone would leave it behind
    # looking exactly like a section that still exists. `keep_self` because the
    # file itself did not vanish, and its history is not the rebuild's to drop.
    for key in list(changes.changed) + extra:
        forget(store, key, keep_self=True)

    rebuild = sorted(set(changes.added) | set(changes.changed) | set(extra))
    report.rebuilt = len(rebuild)
    if rebuild:
        # Signature inspection rather than try/except TypeError: catching the
        # exception would also swallow a genuine TypeError raised inside the
        # builder and then run it a second time.
        # The config travels with the build. `generated_dirs` used to be read
        # from the file and then never handed to the extractor, so a directory
        # the user had configured still produced the default subtype -- through
        # this path AND through the CLI, while the checkpoint that "proved" it
        # worked called the builder directly with rules of its own.
        kwargs = {}
        params = inspect.signature(builder).parameters
        if "all_paths" in params:
            kwargs["all_paths"] = sorted(current)
        if "cfg" in params:
            kwargs["cfg"] = config
        build_report = builder(store, rebuild, as_of, **kwargs)
        # `getattr`, because a caller may pass its own builder -- CP-8 does --
        # and a builder that reports nothing must not look like one that read
        # everything.
        report.unreadable = list(getattr(build_report, "unreadable", ()) or ())

    for key in changes.touched:
        state = current[key]
        # `node_key`, not `path`: section nodes carry their parent's path and
        # no mtime of their own, and writing one into them made an updated
        # store differ from a rebuilt one by a column nobody was looking at.
        store.db.execute(
            "UPDATE nodes SET mtime = ?, last_seen = ? WHERE node_key = ?",
            (state.mtime, as_of, key))
        report.restatted += 1

    # An edge that remains asserted in corpus B was observed in B, even when
    # its source file did not change: `edges_as_of()` filters on `last_seen >=
    # when`, so leaving it at A makes a still-present wikilink vanish from a
    # query for B's date.
    #
    # The scope matters more than the update. Advancing EVERY edge resurrects
    # the ones that are supposed to be expired -- schema note: "a removed
    # wikilink is an edge whose last_seen has stopped advancing" -- so a link
    # deleted before this run came back, and `edges_as_of` counted it again.
    # Rebuilt files are not the problem; their edges were just written with
    # `as_of`. The files that need this are the ones nothing reparsed, and for
    # those the only evidence of what they still assert is the timestamp of
    # their own last parse: the newest `last_seen` among their outgoing edges.
    # Anything older than that was already expired then and stays expired.
    #
    # first_seen is never touched. That is history.
    store.db.execute(
        "UPDATE edges SET last_seen = ? "
        "WHERE last_seen = (SELECT MAX(e2.last_seen) FROM edges e2 "
        "                   WHERE e2.src = edges.src)", (as_of,))

    # The bitmask's integer is meaningful only together with its anchor.  A
    # full build anchors each file at B; retain update history, but re-encode
    # it at B so cohort comparisons never combine masks for different days.
    # Pathless derived nodes deliberately have no datelist and stay NULL.
    #
    # Through `refresh_all_datelists`, not a loop written out again here. The
    # loop that used to sit inline was a second implementation of the function
    # documented as the mechanism for exactly this -- so the named one had no
    # caller outside the tests while its job was being done by a copy.
    from .temporal import refresh_all_datelists
    refresh_all_datelists(store, as_of, only_anchored=True, commit=False)

    # Retention runs here, and until now it ran nowhere. `apply_retention` was
    # written, documented in three places as the reason the observations table
    # has a ceiling, and called only from CP-1 -- so on a real installation the
    # table grew without bound and `observations_monthly` stayed empty forever.
    # DECISIONS.md section 16 goes further and cites the 90-day policy as the
    # precedent for deleting a removed file's history, which made a reader
    # checking that argument agree with a mechanism that was not running.
    #
    # `update` is the right place: it is the only command that runs repeatedly
    # over the same store, which is the only way the table gets large. A build
    # writes one day's observations and has nothing to roll up.
    from .temporal import apply_retention
    report.retention = apply_retention(store, as_of, commit=False)

    report.pruned = prune(store)
    store.rebuild_fts()
    write_fingerprint(store, want)
    report.nodes_after = store.node_count()
    report.edges_after = store.edge_count()
    return report


def refresh_mesh(model_paths, mesh_db, as_of, code_paths=None):
    """Rebuild the federation after the models beneath it moved.

    Mesh mirrors every model node as a stub so cross-model edges have
    endpoints. Those stubs are derived, so a stub for a node that no longer
    exists is the federation answering about a world that is gone -- and it
    would answer confidently, since the stub carries a title and a path.
    `build_edges(prune=True)` drops every stub the mirror did not just write.

    The code inventory travels with it for the same reason: pruning without
    one would delete the code stubs CITES_CODE hangs off, so `build_edges`
    refuses that combination outright rather than quietly shrinking the graph.
    """
    from .mesh import Mesh
    with Mesh(model_paths, mesh_db=mesh_db) as mesh:
        return mesh.build_edges(as_of, prune=True, code_paths=code_paths)


def corpus_paths(root, label, config=None, cap=None):
    """(paths this model owns, what the walk left out).

    Not a second opinion about what belongs where: the same function the build
    used, called again. An update that selected files by a rule of its own
    would drift from the build within one release.

    The report is returned rather than optional because an optional one is a
    report nobody passes. `explain()` is called instead of `classify()` at no
    extra cost -- `classify` is `explain(...).label` -- so the layer that
    owned each exclusion is known for free, and "99.09% excluded" stops being
    a number with nothing behind it.
    """
    from .corpus import Classifier, ExclusionReport, TOP_DIRS
    clf = Classifier(home=root, config=config)
    report = ExclusionReport(root, cap=TOP_DIRS if cap is None else cap)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                if report.record(full, clf.explain(full)).label == label:
                    out.append(full)
            except OSError:
                continue
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                dirnames.remove(name)
                # A directory symlink is excluded by the symlink layer, and
                # it is dropped here rather than classified. Left unrecorded,
                # `[symlinks]` owns nothing in any report and CP-0's per-layer
                # gate has a layer it can never see.
                try:
                    report.record(full, clf.explain(full, is_symlink=True))
                except OSError:
                    pass
    return sorted(out), report
