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


# The four states of a stored node against the filesystem. CP-H7.
#
# `ABSENT` is not `CURRENT`, for the reason `code_inventory` and the H6
# centrality report already give: "we did not look" and "we looked and found
# nothing wrong" are different answers, and collapsing them is how a
# reconciliation reports a clean corpus it never checked.
CURRENT = "current"
STALE = "stale"
MISSING = "missing"
ABSENT = "absent"

# Only these count as trouble. `ABSENT` deliberately does not: a node written
# without a stat is not evidence of drift, and counting it would make the
# warning permanent furniture the first time one appears.
AFFECTED = (STALE, MISSING)

# Worst-first, so two answers about the same path resolve the same way every
# time and in the direction that cannot flatter the corpus.
_SEVERITY = {MISSING: 3, STALE: 2, ABSENT: 1, CURRENT: 0}


def worst(a: str | None, b: str) -> str:
    """The more serious of two states. `None` means "nothing said yet"."""
    if a is None:
        return b
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


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
    """Stat a list of paths into FileStates. Never hashes.

    `use_hash` is accepted and ignored, and the docstring used to advertise it
    as "never hashes unless asked" -- an offer the body does not honour, since
    it never reads the parameter and always leaves `content_hash` unset. A
    caller who passed `use_hash=True` on the strength of that sentence got the
    stat-only scan back without a word.

    Harmless today only because `diff()` computes the hash itself, on the
    files where the cheap check was inconclusive: `state.content_hash or
    _safe_hash(state.path)`. That is also the better place for it -- hashing
    every path up front would read files the cheap check was about to clear.
    The parameter stays because `update` and the model specs pass it around to
    say which models CAN be hash-confirmed, and `diff()` is where it means
    something.
    """
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
            # One `access()` before believing the cheap check. Revoking read
            # permission changes neither size nor mtime, so `unchanged` was the
            # answer and the model went on serving text from a file it can no
            # longer open -- while a full rebuild would have dropped it. This
            # is a stat-level probe, not a read: it costs one syscall per
            # otherwise-unchanged file and buys agreement with `build`.
            #
            # It is also a time-of-check/time-of-use race, unavoidably, and the
            # right answer is to bound the damage rather than to pretend it is
            # closed. Three ways it lies:
            #
            #   1. Permissions can change between this call and the builder's
            #      open(), in either direction. There is no atomic "is this
            #      still readable when I get there" -- only opening it is, and
            #      opening every unchanged file is the full rebuild this exists
            #      to avoid.
            #   2. `os.access` asks about the REAL uid; the open will use the
            #      effective one. Under setuid they differ, and the probe then
            #      answers a question nobody asked.
            #   3. As root it is nearly always True, chmod 000 included, so on
            #      a root run this branch never fires. test_cp8.py says so out
            #      loud rather than skipping quietly.
            #
            # What keeps it safe is that both outcomes are recoverable and
            # neither is silent. A false `changed` costs one wasted reparse,
            # and the builder -- which does open the file -- reaches the same
            # conclusion `build` would. A false `unchanged` leaves one stale
            # node until the next run, which the next run fixes, and the
            # equivalence gate in CP-8 compares `update` against a full build
            # so a drift that persists is caught rather than assumed away.
            # This is a fast path with a correct slow path behind it; a race
            # here degrades performance, not the answer.
            if use_hash and not os.access(state.path, os.R_OK):
                changes.changed.append(key)
                continue
            changes.unchanged.append(key)
            continue
        if not use_hash:
            # M2's path: the cheap check is the only check, by design.
            changes.changed.append(key)
            continue
        new_hash = state.content_hash or _safe_hash(state.path)
        if new_hash is None:
            # Unreadable now, readable when it was built: a permission change
            # leaves size and mtime alone, so the cheap check said `unchanged`
            # and the model kept serving text it can no longer read. A full
            # build would skip the file entirely, so calling it changed is what
            # makes update agree with build -- the builder will skip it and the
            # node will go, which is the same store either way.
            changes.changed.append(key)
            continue
        if new_hash == prior["content_hash"]:
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


def node_state(path: str | None, size: int | None,
               mtime: float | None, *,
               mtime_tolerance: float = 1e-6) -> str:
    """One node's state against the filesystem. CP-H7, rule R1.

    Never raises. A `stat` that fails for any reason other than the file being
    gone is `ABSENT`, not `STALE`: an unreadable directory says nothing about
    whether the content drifted, and reporting drift on a permissions problem
    sends the reader to the wrong repair.
    """
    if not path:
        # A node with no path at all -- an author, a tag, a wikilink. The
        # filesystem has no opinion about it, and `os.stat(None)` raises
        # TypeError, which R4 forbids reaching the caller. Found 2026-08-02 by
        # CP-6's MCP gate, which searches a corpus that has such nodes.
        return ABSENT
    try:
        st = os.stat(path)
    except FileNotFoundError:
        # Answerable for ANY path, stat or no stat. Reporting `ABSENT` for a
        # file that is demonstrably gone would hide the one fact a stub-only
        # node exists to carry -- measured 2026-08-02, that was all 9 125 mesh
        # stubs at once.
        return MISSING
    except OSError:
        return ABSENT
    if size is None or mtime is None:
        return ABSENT
    if st.st_size == size and abs(st.st_mtime - mtime) <= mtime_tolerance:
        return CURRENT
    return STALE


def reconcile(store: "Store", *, mtime_tolerance: float = 1e-6) -> dict[str, str]:
    """{node_key: state} for every node the store holds a path for. CP-H7.

    **File-level nodes are the only ones asked.** A `section` carries its
    parent's path and no stat of its own -- measured 2026-08-02, that is 6 035
    of m3's 6 928 nodes and 618 of m1's 885 -- so asking it directly answers
    `ABSENT` for most of the corpus, which is true and useless. It inherits its
    file's state instead, which is rule R2 and the only way a section is ever
    `STALE`.

    Cheap on purpose: one `stat` per file-level node, no hashing. Measured over
    8 564 stored paths, 0.01 s. Whether the bytes really differ when `size` and
    `mtime` do is `diff()`'s second stage, and H7 reports rather than
    re-deriving it.
    """
    rows = list(store.db.execute(
        "SELECT node_key, kind, path, size, mtime FROM nodes "
        "WHERE path IS NOT NULL"))
    states: dict[str, str] = {}
    by_path: dict[str, str] = {}
    deferred = []
    for row in rows:
        if row["size"] is None or row["mtime"] is None:
            deferred.append(row)
            continue
        state = node_state(row["path"], row["size"], row["mtime"],
                           mtime_tolerance=mtime_tolerance)
        states[row["node_key"]] = state
        # Two stat-bearing nodes CAN share a path with different stored stats --
        # one indexed before an edit and one after -- and then the two states
        # disagree. The worst wins, deterministically, rather than whichever row
        # SQLite happened to return last: a section inheriting "current" from a
        # stale sibling is the confident-wrong answer this whole checkpoint is
        # about. An earlier comment here claimed the states always agree; codex
        # showed they need not, 2026-08-02.
        by_path[row["path"]] = worst(by_path.get(row["path"]), state)
    for row in deferred:
        if row["kind"] == "section":
            # R2: a section is not a file, so the filesystem is the wrong place
            # to ask whether it DRIFTED. Whether its path still exists is a
            # different question and is answerable -- an orphan section whose
            # file the store never held would otherwise report `ABSENT` for a
            # file that is demonstrably gone.
            inherited = by_path.get(row["path"])
            states[row["node_key"]] = (
                inherited if inherited is not None
                else node_state(row["path"], None, None))
        else:
            # A stub with no stat can still be asked whether its path is there.
            states[row["node_key"]] = node_state(row["path"], None, None)
    return states
