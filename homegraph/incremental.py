#!/usr/bin/env python3
"""Incremental update: what actually changed since the last build.

Two-stage on purpose. `mtime` and `size` are free -- they come from the
directory walk that already happened. Hashing is not: it means reading every
byte of every candidate. So the cheap check decides who is a *suspect*, and
only suspects get hashed.

That second stage is what separates "touched" from "changed". A file rewritten
with identical content (a formatter, a sync tool, `touch`) has a new mtime and
the same bytes; treating that as a change means re-extracting and re-embedding
work that produced nothing.

**M2 opts out of hashing entirely** -- `use_hash=False`. Hashing an image means
opening it, and M2's guarantee is that it never does. The cost is real and is
accepted: an image edited without changing its size within the same mtime
resolution is missed. It is written down here rather than discovered later.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

CHUNK = 1 << 20


@dataclass(frozen=True)
class FileState:
    path: str
    size: int
    mtime: float
    content_hash: str | None = None


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)  # new mtime, same bytes
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {k: len(getattr(self, k)) for k in
                ("added", "changed", "touched", "unchanged", "removed")}


def hash_file(path: str, chunk: int = CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def scan(paths: Iterable[str], use_hash: bool = False) -> dict[str, FileState]:
    """Stat a list of paths into FileStates. Never hashes unless asked."""
    out: dict[str, FileState] = {}
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        out[p] = FileState(path=p, size=st.st_size, mtime=st.st_mtime)
    return out


def diff(store: "Store", current: Mapping[str, FileState], *,
         use_hash: bool = True, mtime_tolerance: float = 1e-6,
         kinds: Iterable[str] | None = None) -> Changes:
    """Compare filesystem state against what the store already holds.

    `current` maps node_key -> FileState. For file-backed models the node key is
    the path, but mesh and section nodes key differently, so the caller decides.

    `kinds` restricts the comparison to the node kinds that stand for whole
    files. Without it, section nodes -- which carry their parent's `path` and a
    key of `<path>#<n>` -- are not in `current`, so every one of them lands in
    `removed` and an update deletes the document it just rebuilt. That is not a
    hypothetical: it is why the parameter exists.
    """
    changes = Changes()
    sql = ("SELECT node_key, size, mtime, content_hash FROM nodes "
           "WHERE path IS NOT NULL")
    args: list[str] = []
    if kinds is not None:
        kinds = list(kinds)
        sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args += kinds
    stored = {r["node_key"]: r for r in store.db.execute(sql, args)}

    for key, state in current.items():
        prior = stored.pop(key, None)
        if prior is None:
            changes.added.append(key)
            continue
        same_cheap = (prior["size"] == state.size
                      and prior["mtime"] is not None
                      and abs(prior["mtime"] - state.mtime) <= mtime_tolerance)
        if same_cheap:
            changes.unchanged.append(key)
            continue
        if not use_hash:
            # M2's path: the cheap check is the only check, by design.
            changes.changed.append(key)
            continue
        new_hash = state.content_hash or _safe_hash(state.path)
        if new_hash is not None and new_hash == prior["content_hash"]:
            changes.touched.append(key)
        else:
            changes.changed.append(key)

    changes.removed.extend(stored.keys())
    return changes


def _safe_hash(path: str) -> str | None:
    try:
        return hash_file(path)
    except OSError:
        return None
