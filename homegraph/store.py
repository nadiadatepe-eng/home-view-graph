#!/usr/bin/env python3
"""SQLite substrate: schema, migration chain, nodes, edges, FTS.

Two decisions here are load-bearing and both come from other people's scars.

**The migration chain starts at v1, not at "we'll add one later."** Retrofitting
versioning onto a database that already has data means guessing what version the
existing rows are. `schema_version` exists before the first node does.

**FTS5 is POPULATED by an explicit `rebuild_fts()` call and by nothing else.**
No triggers, no implicit population on insert. The failure this avoids is the
one from code-review-graph: an index that was never built, a search path that
silently returned nothing, and a headline number nobody could reproduce. An
index that is only ever filled on purpose cannot be half-filled by accident,
and `fts_is_stale()` makes the gap visible rather than quiet.

Populated, not written: `delete_node` removes an index row so a deleted node
cannot come back as a search hit. This used to say "and by nothing else" about
writing, three lines above the method that does exactly that.

Time is not bolted on either. `nodes` are slowly-changing dimensions; the facts
live in `observations` (see temporal.py). Edges carry `first_seen`/`last_seen`,
so a wikilink that stops being written expires instead of vanishing -- otherwise
the answer to "which links did this note have last week" is gone forever, which
for a graph over your own home is the whole point. Expiry is what happens when
a link is dropped from a file that stays; it is not what happens when the file
itself is rebuilt or removed, and the schema comment on `edges` says which is
which.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

# How an edge was derived, and how much weight that derivation carries.
#
# **The number is an ordering, not a probability.** Nothing here estimates how
# often `basename` is right; it says a basename match is worth less than a
# resolved path and more than a shared change-day. A continuous scale would
# invite arithmetic nobody can defend -- multiplying two of these together
# produces a number with no meaning at all -- so there are five values, fixed
# per method, and adding a sixth is a decision rather than a tuning knob.
#
# This is the idea borrowed from codebase-memory-mcp, inverted. Its CALLS edges
# carry a confidence too (0.17 and 0.28 on the three that were measurably
# wrong), and the query answer comes out as clean JSON with no warning: a field
# nothing forces you to read is decoration. Here, anything below 1.0 makes the
# answer say `partial`.
EDGE_METHODS: dict[str, float] = {
    # The relation is stated by the data: a section is in its file, a tag is
    # written in the text, a resolved relative link points where it points.
    "exact": 1.0,
    # Two files share a wikilink target name and the nearest common path
    # prefix picked one. A real ambiguity, resolved by a rule.
    "path_prefix": 0.7,
    # Same filename in two directories, or a figure named after a note. No
    # bytes were read -- M2 never opens a file -- so this is a guess about
    # names, which is why the relation is LIKELY_COPY and not SAME_AS.
    "basename": 0.6,
    # One document's text names another file.
    "mention": 0.5,
    # Two nodes changed on the same days. The weakest evidence here, and the
    # one most likely to be coincidence on a busy day.
    "cohort": 0.4,
}

def provenance_note(rows: "Iterable[Any]") -> str | None:
    """One warning naming every derivation below 1.0 in `rows`, or None.

    **This is the whole of the honesty rule, and it lives here once.** Every
    read path that hands back edges calls it; none of them re-implements
    "which of these was a guess". Two copies of that question is how one of
    them ends up always answering no -- the failure this package has now
    found in its own gates four times.

    Takes anything with `method` and `confidence` keys, so it works on a
    `sqlite3.Row` from `edges_as_of` and on a tuple-with-fields from mesh
    traversal without either side converting for the other.
    """
    seen: dict[str, int] = {}
    for row in rows:
        try:
            conf = row["confidence"]
            method = row["method"]
        except (KeyError, IndexError, TypeError):
            continue
        if conf is not None and conf < 1.0:
            seen[method] = seen.get(method, 0) + 1
    if not seen:
        return None
    parts = ", ".join("%d by %s (%.1f)" % (n, m, EDGE_METHODS.get(m, 0.0))
                      for m, n in sorted(seen.items()))
    return ("derived, not stated: %s -- these relations were inferred, and "
            "the inference can be wrong" % parts)


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

-- Edges are versioned. A removed wikilink is an edge whose last_seen has
-- stopped advancing, so `edges_as_of()` can still answer what a note linked
-- to last week.
--
-- "Never deleted" is what this said, and it is not true. `update` deletes:
-- rebuilding a file drops its outbound edges before writing the new ones
-- (update.py, DELETE FROM edges WHERE src = ?), and removing a file takes its
-- edges with it through ON DELETE CASCADE below. DECISIONS.md section 16
-- argues for that deliberately -- removal deletes, and it deletes history.
-- The two statements coexisted, and the false one was the one sitting in the
-- schema, which is where a reader looks to find out whether `edges_as_of()`
-- can be trusted after an update. It cannot, for files that were rebuilt or
-- removed; it can, for links that simply stopped being written.
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

    (2, "edges carry how they were derived", """
-- Provenance. Until this migration an edge derived from a filename collision
-- and one stated outright by the text were the same row: the ambiguity was
-- counted in the build report and lost by the time anyone queried the graph.
-- The aggregate was honest and the individual fact was not.
--
-- The DEFAULTs make the migration total -- every existing edge becomes
-- `exact`/1.0, which is what the code that wrote them believed -- but
-- `upsert_edge` takes `method` as a required keyword, so nothing new can
-- inherit `exact` by omission.
ALTER TABLE edges ADD COLUMN method     TEXT NOT NULL DEFAULT 'exact';
ALTER TABLE edges ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;
CREATE INDEX idx_edges_confidence ON edges(confidence);
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
        # Readers answer while a writer works. Without WAL a single `update`
        # blocks every `search` for its whole run, which on a 588k-file corpus
        # is not a moment.
        #
        # `journal_mode` is the one PRAGMA that can decline: it returns the
        # mode actually in force, and returns "delete" unchanged on a network
        # filesystem or against a database another connection holds open in
        # rollback mode. Reading the answer is the difference between having
        # WAL and believing you asked for it -- and the user config allows an
        # arbitrary root, so the network case is reachable here.
        row = self.db.execute("PRAGMA journal_mode = WAL").fetchone()
        self.journal_mode = (row[0] if row else "unknown").lower()
        # Concurrent readers still contend for brief write locks (FTS rebuild,
        # checkpoint). Five seconds of waiting beats an immediate
        # "database is locked" that the caller reads as corruption.
        self.db.execute("PRAGMA busy_timeout = 5000")
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
                    as_of: str | None = None, *, method: str) -> None:
        """`method` is required and keyword-only, on purpose.

        A default would be inherited by every future edge whose author did not
        think about it, and the ones worth marking are exactly the ones added
        in a hurry. Making it a keyword the language refuses to supply is a
        stronger guarantee than a test that checks for it: there is no green
        run in which someone forgot.
        """
        if method not in EDGE_METHODS:
            raise ValueError("unknown edge method %r; known: %s"
                             % (method, ", ".join(sorted(EDGE_METHODS))))
        confidence = EDGE_METHODS[method]
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
                """INSERT INTO edges(src, dst, rel, first_seen, last_seen,
                                     method, confidence)
                   VALUES (?,?,?,?,?,?,?)""",
                (src, dst, rel, as_of, as_of, method, confidence))
        else:
            # Latest assertion wins, including a downgrade. A link that was
            # unambiguous and now collides with a new file is genuinely less
            # certain than it was, and keeping the old 1.0 because it is
            # higher would freeze a claim the corpus stopped supporting.
            self.db.execute(
                "UPDATE edges SET last_seen=?, method=?, confidence=? "
                "WHERE id=?", (as_of, method, confidence, row["id"]))

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
        # This is postprocessing inside the caller's unit of work.  Committing
        # here would make a later interruption leave new graph rows with a
        # partially rebuilt index; `Store.__exit__` owns the commit boundary.
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

    def begin_immediate(self) -> None:
        """Take SQLite's write lock now rather than at the first INSERT.

        Python's sqlite3 begins a DEFERRED transaction implicitly before the
        first DML statement, so a writer that loses a race discovers it after
        it has already read and computed -- possibly minutes into a rebuild.
        IMMEDIATE moves the refusal to the front.

        Verified rather than assumed: with the default `isolation_level`, an
        explicit BEGIN IMMEDIATE sets `in_transaction`, suppresses the
        implicit BEGIN, and is ended by `commit()` as usual.

        This is the second barrier, not the first. `lock.StoreLock` is what
        stops two `homegraph update` runs; this is what stops anything else
        holding the file, and what makes the failure loud and early.
        """
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")

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
