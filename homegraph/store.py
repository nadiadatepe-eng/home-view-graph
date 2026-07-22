#!/usr/bin/env python3
"""SQLite substrate: schema, migration chain, nodes, edges, FTS.

Two decisions here are load-bearing and both come from other people's scars.

**The migration chain starts at v1, not at "we'll add one later."** Retrofitting
versioning onto a database that already has data means guessing what version the
existing rows are. `schema_version` exists before the first node does.

**FTS5 is filled by an explicit `rebuild_fts()` call and by nothing else.** No
triggers, no implicit population on insert. The failure this avoids is the one
from code-review-graph: an index that was never built, a search path that
silently returned nothing, and a headline number nobody could reproduce. An
index that is only ever filled on purpose cannot be half-filled by accident,
and `fts_is_stale()` makes the gap visible rather than quiet.

Time is not bolted on either. `nodes` are slowly-changing dimensions; the facts
live in `observations` (see temporal.py). Edges carry `first_seen`/`last_seen`,
so removing a wikilink expires an edge instead of deleting a row -- otherwise
the answer to "which links did this note have last week" is gone forever, which
for a graph over your own home is the whole point.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema: nodes, edges, observations, fts, embeddings", """
CREATE TABLE nodes (
    id                INTEGER PRIMARY KEY,
    node_key          TEXT    NOT NULL UNIQUE,
    kind              TEXT    NOT NULL,
    subtype           TEXT,
    path              TEXT,
    title             TEXT,
    body              TEXT,
    size              INTEGER,
    mtime             REAL,
    -- NULL for M2 by design: hashing requires reading, and M2 never reads.
    -- Mesh therefore matches image nodes on normalised path instead.
    content_hash      TEXT,
    first_seen        TEXT    NOT NULL,
    last_seen         TEXT    NOT NULL,
    activity_datelist TEXT    NOT NULL DEFAULT '[]',
    datelist_int      INTEGER NOT NULL DEFAULT 0,
    datelist_anchor   TEXT
);
CREATE INDEX idx_nodes_path ON nodes(path);
CREATE INDEX idx_nodes_kind ON nodes(kind, subtype);

-- Edges are versioned, never deleted. A removed wikilink is an edge whose
-- last_seen has stopped advancing.
CREATE TABLE edges (
    id         INTEGER PRIMARY KEY,
    src        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    rel        TEXT    NOT NULL,
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL,
    UNIQUE(src, dst, rel)
);
CREATE INDEX idx_edges_src ON edges(src, rel);
CREATE INDEX idx_edges_dst ON edges(dst, rel);
CREATE INDEX idx_edges_seen ON edges(first_seen, last_seen);

-- Facts. One row per node per day it was observed changed.
CREATE TABLE observations (
    id           INTEGER PRIMARY KEY,
    node_id      INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    seen_date    TEXT    NOT NULL,
    content_hash TEXT,
    size         INTEGER,
    UNIQUE(node_id, seen_date)
);
CREATE INDEX idx_obs_date ON observations(seen_date);

-- What survives retention: monthly rollup, unbounded history without unbounded
-- rows. 154k files x 365 days is not a table anyone wants.
CREATE TABLE observations_monthly (
    node_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    month       TEXT    NOT NULL,
    change_days INTEGER NOT NULL,
    last_hash   TEXT,
    max_size    INTEGER,
    PRIMARY KEY (node_id, month)
);

CREATE TABLE embeddings (
    node_id  INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);

CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Standalone FTS5, rowid == nodes.id. No external-content table and no
-- triggers: population is an explicit postprocessing step, always.
CREATE VIRTUAL TABLE nodes_fts USING fts5(title, body, tokenize='unicode61');
"""),
]


def today() -> str:
    return date.today().isoformat()


class Store:
    def __init__(self, path: str | Path, model: str = "unknown",
                 embeddings: dict[str, str] | None = None) -> None:
        """embeddings: None means off.

        Off is the default and stays the default. A build path that quietly
        loads an embedding model is a build path that quietly costs money --
        that was the bug in code-review-graph #711. Turning it on requires
        naming both provider and model, so it can never happen by omission.
        """
        self.path = str(path)
        self.model = model
        if embeddings is not None:
            if not embeddings.get("provider") or not embeddings.get("model"):
                raise ValueError(
                    "embeddings must name both provider and model, or be None")
        self.embeddings = embeddings
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    # -- migrations -------------------------------------------------------

    def migrate(self) -> None:
        self.db.execute("""CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, note TEXT)""")
        cur = self.db.execute("SELECT COALESCE(MAX(version), 0) v "
                              "FROM schema_version")
        current: int = cur.fetchone()["v"]
        for version, note, sql in MIGRATIONS:
            if version > current:
                self.db.executescript(sql)
                self.db.execute(
                    "INSERT INTO schema_version(version, applied_at, note) "
                    "VALUES (?,?,?)", (version, today(), note))
        # Reopening a store must not rename it. The default "unknown" means
        # "caller did not say", not "this store has no model" -- writing it
        # unconditionally silently relabelled an existing database every time
        # a read-only command like `status` opened it.
        if self.model != "unknown":
            self.db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) "
                "VALUES ('model', ?)", (self.model,))
        else:
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key = 'model'").fetchone()
            if row:
                self.model = row["value"]
        self.db.commit()

    @property
    def version(self) -> int:
        version: int = self.db.execute(
            "SELECT COALESCE(MAX(version), 0) v FROM schema_version"
        ).fetchone()["v"]
        return version

    # -- nodes ------------------------------------------------------------

    def upsert_node(self, node_key: str, kind: str, *,
                    subtype: str | None = None, path: str | None = None,
                    title: str | None = None, body: str | None = None,
                    size: int | None = None, mtime: float | None = None,
                    content_hash: str | None = None,
                    as_of: str | None = None) -> int:
        as_of = as_of or today()
        row = self.db.execute("SELECT id FROM nodes WHERE node_key = ?",
                              (node_key,)).fetchone()
        if row is None:
            cur = self.db.execute(
                """INSERT INTO nodes(node_key, kind, subtype, path, title, body,
                                     size, mtime, content_hash,
                                     first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (node_key, kind, subtype, path, title, body, size, mtime,
                 content_hash, as_of, as_of))
            if cur.lastrowid is None:
                # Not defensive padding: sqlite3 genuinely returns None when a
                # statement inserted no row, and every caller uses this id as
                # an edge endpoint. Silently passing None on would surface far
                # away as a foreign-key failure with no trace of the cause.
                raise RuntimeError("insert produced no rowid for %r" % node_key)
            return cur.lastrowid
        self.db.execute(
            """UPDATE nodes SET kind=?, subtype=?, path=?, title=?, body=?,
                                size=?, mtime=?, content_hash=?, last_seen=?
               WHERE id=?""",
            (kind, subtype, path, title, body, size, mtime, content_hash,
             as_of, row["id"]))
        return int(row["id"])

    def node_id(self, node_key: str) -> int | None:
        row = self.db.execute("SELECT id FROM nodes WHERE node_key = ?",
                              (node_key,)).fetchone()
        return int(row["id"]) if row else None

    def get_node(self, node_key: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM nodes WHERE node_key = ?", (node_key,)).fetchone()
        return row

    def delete_node(self, node_key: str) -> bool:
        """Remove a node, its edges, observations, embedding and FTS row.

        FTS5 has no foreign keys, so its row has to go by hand. Forgetting this
        is exactly how an index accumulates orphans that still match queries and
        point at nothing -- CP-1 checks for them.
        """
        nid = self.node_id(node_key)
        if nid is None:
            return False
        self.db.execute("DELETE FROM nodes_fts WHERE rowid = ?", (nid,))
        self.db.execute("DELETE FROM nodes WHERE id = ?", (nid,))
        return True

    def node_count(self) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) c FROM nodes").fetchone()["c"])

    # -- edges ------------------------------------------------------------

    def upsert_edge(self, src_key: str, dst_key: str, rel: str,
                    as_of: str | None = None) -> None:
        as_of = as_of or today()
        src, dst = self.node_id(src_key), self.node_id(dst_key)
        if src is None or dst is None:
            raise KeyError("edge endpoints must exist: %r -> %r"
                           % (src_key, dst_key))
        row = self.db.execute(
            "SELECT id FROM edges WHERE src=? AND dst=? AND rel=?",
            (src, dst, rel)).fetchone()
        if row is None:
            self.db.execute(
                """INSERT INTO edges(src, dst, rel, first_seen, last_seen)
                   VALUES (?,?,?,?,?)""", (src, dst, rel, as_of, as_of))
        else:
            self.db.execute("UPDATE edges SET last_seen=? WHERE id=?",
                            (as_of, row["id"]))

    def edges_as_of(self, when: str,
                    rel: str | None = None) -> list[sqlite3.Row]:
        """The edges that existed on `when`.

        An edge is alive on a date if it was first seen on or before it and
        last seen on or after it. Time travel is this one predicate; nothing
        else in the system needs to know about history.
        """
        sql = ("SELECT e.*, s.node_key src_key, d.node_key dst_key "
               "FROM edges e JOIN nodes s ON s.id = e.src "
               "JOIN nodes d ON d.id = e.dst "
               "WHERE e.first_seen <= ? AND e.last_seen >= ?")
        args = [when, when]
        if rel:
            sql += " AND e.rel = ?"
            args.append(rel)
        return self.db.execute(sql, args).fetchall()

    def edge_count(self) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) c FROM edges").fetchone()["c"])

    # -- FTS postprocessing -----------------------------------------------

    def rebuild_fts(self) -> int:
        """Populate FTS5 from nodes. The only thing that ever writes to it."""
        self.db.execute("DELETE FROM nodes_fts")
        self.db.execute(
            "INSERT INTO nodes_fts(rowid, title, body) "
            "SELECT id, COALESCE(title, ''), COALESCE(body, '') FROM nodes")
        self.db.execute(
            "INSERT OR REPLACE INTO metadata(key, value) "
            "VALUES ('fts_built_at', ?)", (today(),))
        self.db.commit()
        return self.fts_count()

    def fts_count(self) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) c FROM nodes_fts").fetchone()["c"])

    def fts_is_stale(self) -> bool:
        """True when the index does not cover every node.

        Callers are expected to act on this loudly. A search over a stale index
        answers with silence, and silence is indistinguishable from 'no match'.
        """
        return self.fts_count() != self.node_count()

    def fts_orphans(self) -> list[int]:
        return [r["rowid"] for r in self.db.execute(
            "SELECT rowid FROM nodes_fts WHERE rowid NOT IN "
            "(SELECT id FROM nodes)")]

    # -- misc -------------------------------------------------------------

    def status(self) -> dict[str, object]:
        return {
            "path": self.path,
            "model": self.model,
            "schema_version": self.version,
            "nodes": self.node_count(),
            "edges": self.edge_count(),
            "observations": self.db.execute(
                "SELECT COUNT(*) c FROM observations").fetchone()["c"],
            "observations_monthly": self.db.execute(
                "SELECT COUNT(*) c FROM observations_monthly").fetchone()["c"],
            "fts_rows": self.fts_count(),
            "fts_stale": self.fts_is_stale(),
            "embeddings": self.db.execute(
                "SELECT COUNT(*) c FROM embeddings").fetchone()["c"],
            "embeddings_enabled": self.embeddings is not None,
        }

    def commit(self) -> None:
        self.db.commit()

    def close(self, commit: bool = True) -> None:
        if commit:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type: object, *rest: object) -> None:
        """Commit on a clean exit, roll back on any exception.

        This used to commit unconditionally. An update interrupted between
        `forget(..., keep_self=True)` and `rebuild_fts()` therefore committed
        the half it had done: edges deleted, new ones not yet written, FTS
        holding the previous text. For a markdown file with no sections or
        tags the node and FTS counts are unchanged by that, so `status`
        reported `fts_stale=False` over a store that was neither current nor
        consistent -- the failure looked like success from every angle
        available to the user.

        KeyboardInterrupt is a BaseException and reaches here as `exc_type`
        like any other, which is the case that matters: Ctrl-C during a long
        rebuild is the realistic way this happens.
        """
        self.close(commit=exc_type is None)
