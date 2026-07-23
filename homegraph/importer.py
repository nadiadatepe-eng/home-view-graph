#!/usr/bin/env python3
"""Read a portable artifact back into stores. The mirror of `export.py`.

Named `importer` because `import` is a keyword; the command is `import`.

**Writes through `Store.upsert_node` and `Store.upsert_edge`, never raw SQL.**
That is the whole reason the artifact is text rather than a copy of the
database: an imported graph goes through the same writer as a built one --
including `method` as a required keyword, the migration chain, and every
invariant the builders inherit -- so it cannot diverge from what a build on
the same corpus would produce. Two writers agree until one of them is edited.

Two narrow exceptions, both named: `restore_node_history` and
`restore_edge_history` put back dates the upsert path stamps with today by
design. A builder knows when it ran; an import knows when the graph was first
seen, on the machine that saw it.

**This module never opens a store.** It is handed stores that are already open
behind the write barrier, the same way `_build_model` is -- a writer that
opens `Store` itself is a writer outside the barrier, and CP-11 parses for
exactly that rather than trusting a sentence like this one.

**A refused import writes nothing.** The refusals below raise, the caller's
`with Store(...)` rolls back, and a half-applied artifact never reaches disk.
That is not a happy accident: a digest can only be checked once the rows it
covers have been read, so the alternative to rolling back is importing first
and discovering afterwards.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
from typing import Any

from .export import FORMAT_VERSION, NODE_COLUMNS
from .portable import to_local
from .store import Store


class ImportError_(RuntimeError):
    """Refused, with the reason named. Never a traceback at the user."""


def read_manifest(path: str) -> dict[str, Any]:
    """The manifest alone, without importing anything.

    `homegraph inspect` is this function: **you should be able to see what an
    artifact carries before you take it in.** An artifact is a file that
    arrives from somewhere else, and "run it to find out what it holds" is the
    wrong shape for that.
    """
    try:
        with lzma.open(path, "rt", encoding="utf-8") as fh:
            first = fh.readline()
    except (lzma.LZMAError, EOFError, OSError) as exc:
        raise ImportError_("not a readable artifact: %s (%r)"
                           % (path, exc)) from exc
    if not first.strip():
        raise ImportError_("empty artifact: %s" % path)
    try:
        manifest = json.loads(first)
    except json.JSONDecodeError as exc:
        raise ImportError_("first line is not a manifest: %s" % path) from exc
    if manifest.get("t") != "manifest":
        raise ImportError_(
            "first line is %r, not a manifest -- an artifact states what it "
            "is before it states what it holds" % manifest.get("t"))
    return manifest


def _check_compatible(manifest: dict[str, Any], stores: dict[str, Store],
                      ) -> None:
    fmt = manifest.get("format")
    if fmt != FORMAT_VERSION:
        raise ImportError_(
            "artifact format %r, this build reads %d. Refused rather than "
            "guessed: a format is a promise about what the fields mean."
            % (fmt, FORMAT_VERSION))
    schema = manifest.get("schema", 0)
    for model, store in sorted(stores.items()):
        if schema > store.version:
            # Refused, not migrated. A converter here would be a second
            # migration chain beside `store.py`'s, and the two would disagree
            # the first time either was extended.
            raise ImportError_(
                "artifact was written at schema v%s and %s reads v%s. "
                "Upgrade Home-view-graph; this build will not guess what a "
                "newer schema meant." % (schema, model, store.version))
    missing = sorted(set(manifest.get("models", [])) - set(stores))
    if missing:
        raise ImportError_(
            "the artifact holds %s and no destination was named for %s. "
            "Importing part of it silently would produce a graph with edges "
            "into models that are not there."
            % (", ".join(manifest.get("models", [])), ", ".join(missing)))


def load(path: str, stores: dict[str, Store], root: str) -> dict[str, Any]:
    """Import `path` into `stores`, rooted at `root`. Returns what it did.

    Nodes arrive before edges by construction, because `upsert_edge` refuses
    an edge whose endpoints do not exist yet. That refusal is worth keeping:
    creating the endpoints here would invent nodes the artifact never
    described.
    """
    manifest = read_manifest(path)
    _check_compatible(manifest, stores)
    root = os.path.abspath(os.path.expanduser(root))

    digest = hashlib.sha256()
    seen = {"nodes": 0, "edges": 0}
    claimed: str | None = None
    skipped_models: dict[str, int] = {}

    with lzma.open(path, "rt", encoding="utf-8") as fh:
        # The manifest counts towards the digest: it carries the redaction
        # level and the counts, and an unprotected manifest can be relabelled
        # after the fact while the body still verifies.
        digest.update(fh.readline().encode("utf-8"))
        for lineno, line in enumerate(fh, start=2):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ImportError_("line %d is not JSON: %r"
                                   % (lineno, exc)) from exc
            kind = row.get("t")
            if kind == "digest":
                claimed = row.get("sha256")
                continue
            # Digested BEFORE anything is done with it. The digest line
            # itself is the one exclusion -- a checksum cannot cover the line
            # that announces it.
            digest.update(line.encode("utf-8"))
            model = row.get("model")
            store = stores.get(model)
            if store is None:
                skipped_models[model] = skipped_models.get(model, 0) + 1
                continue
            if kind == "node":
                _node(store, row, root)
                seen["nodes"] += 1
            elif kind == "edge":
                _edge(store, row, root)
                seen["edges"] += 1
            else:
                raise ImportError_("line %d has unknown type %r"
                                   % (lineno, kind))

    if claimed is None:
        raise ImportError_(
            "no digest line: the artifact is truncated, or was written by "
            "something that does not finish what it starts. An incomplete "
            "artifact imports as a smaller graph, which looks exactly like a "
            "smaller corpus.")
    if claimed != digest.hexdigest():
        raise ImportError_(
            "digest mismatch: the artifact says %s, its contents are %s. "
            "Refused." % (claimed[:16], digest.hexdigest()[:16]))

    for store in stores.values():
        store.rebuild_fts()

    return {"models": sorted(stores), "imported": seen,
            "redaction": manifest.get("redaction"),
            "declared": manifest.get("counts"),
            "root": root,
            "skipped_models": skipped_models}


def _node(store: Store, row: dict[str, Any], root: str) -> None:
    key = to_local(row["node_key"], root)
    path = to_local(row["path"], root) if row.get("path") is not None else None
    store.upsert_node(
        key, kind=row.get("kind") or "file", subtype=row.get("subtype"),
        path=path, title=row.get("title"), body=row.get("body"),
        size=row.get("size"), mtime=row.get("mtime"),
        content_hash=row.get("content_hash"),
        as_of=row.get("first_seen"))
    store.restore_node_history(
        key, first_seen=row["first_seen"], last_seen=row["last_seen"],
        activity_datelist=row.get("activity_datelist"),
        datelist_int=row.get("datelist_int"),
        datelist_anchor=row.get("datelist_anchor"))


def _edge(store: Store, row: dict[str, Any], root: str) -> None:
    src = to_local(row["src"], root)
    dst = to_local(row["dst"], root)
    try:
        store.upsert_edge(src, dst, row["rel"], row["first_seen"],
                          method=row["method"])
    except KeyError as exc:
        raise ImportError_(
            "an edge names an endpoint the artifact never described: %s. "
            "Endpoints are not invented here." % exc) from exc
    store.restore_edge_history(src, dst, row["rel"],
                               first_seen=row["first_seen"],
                               last_seen=row["last_seen"])


# Re-exported so a reader of this module can see what a node row carries
# without opening the exporter. Not a second definition -- the same tuple.
__all__ = ["ImportError_", "load", "read_manifest", "NODE_COLUMNS"]
