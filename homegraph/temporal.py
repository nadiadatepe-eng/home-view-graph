#!/usr/bin/env python3
"""The time layer: observations, activity date lists, retention.

Borrowed from DataExpert-io/data-engineer-handbook's cumulative table design --
the idea, not the code. Build one row per entity per period and accumulate, so
history is never rescanned and never lost.

The shape is a fact/dimension split. `nodes` are dimensions that change slowly.
`observations` are facts: one row per node per day it was seen changed. From
those, two derived forms live on the node itself:

  `activity_datelist`  every date the file changed, as JSON. Readable, exact,
                       and useless for cross-node set arithmetic at scale.
  `datelist_int`       the last 32 days as a bitmask. This is the one M5's
                       TEMPORAL_COHORT needs: "which files changed on the same
                       days as this one" becomes a bitwise AND instead of a
                       join over arrays.

Bit convention, fixed here and depended on by M5: **bit i is the anchor date
minus i days**, so bit 0 is the anchor itself and bit 31 is 31 days earlier.
Anything outside that window is not representable and is dropped on encode --
`datelist_int` is a fast index over recent activity, not a second copy of the
history. `activity_datelist` remains the authority.

Retention: full daily observations for 90 days, then a monthly rollup. Without
it, 154k files times unbounded days grows without a ceiling, and the database
swells for history nobody queries at day granularity.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:                    # import cycle: store imports nothing here
    from .store import Store

WINDOW_DAYS = 32
RETENTION_DAYS = 90


def _d(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


# -- bitmask ---------------------------------------------------------------

def encode_datelist(dates: Iterable[date | str],
                    anchor: date | str) -> int:
    """Dates -> 32-bit mask. Dates outside the window are dropped silently.

    Silent is correct here and only here: the window is a stated property of
    the encoding, not a failure. `activity_datelist` still holds everything.
    """
    anchor = _d(anchor)
    mask = 0
    for value in dates:
        delta = (anchor - _d(value)).days
        if 0 <= delta < WINDOW_DAYS:
            mask |= 1 << delta
    return mask


def decode_datelist(mask: int, anchor: date | str) -> list[str]:
    """Mask -> sorted list of ISO dates. Exact inverse of encode within window."""
    anchor = _d(anchor)
    out = []
    for i in range(WINDOW_DAYS):
        if mask & (1 << i):
            out.append((anchor - timedelta(days=i)).isoformat())
    return sorted(out)


def cohort_overlap(mask_a: int, mask_b: int) -> int:
    """Days two nodes were both active. The whole point of the bitmask.

    Both masks must share an anchor -- comparing masks anchored on different
    dates compares different days and returns a number that looks fine.
    """
    return bin(mask_a & mask_b).count("1")


# -- observations ----------------------------------------------------------

def record_observation(store: "Store", node_id: int, seen_date: date | str,
                       content_hash: str | None = None,
                       size: int | None = None) -> None:
    """One fact row per node per day. Re-recording the same day is a no-op
    update, not a duplicate -- a build run twice in one day must not double."""
    store.db.execute(
        """INSERT INTO observations(node_id, seen_date, content_hash, size)
           VALUES (?,?,?,?)
           ON CONFLICT(node_id, seen_date)
           DO UPDATE SET content_hash=excluded.content_hash,
                         size=excluded.size""",
        (node_id, _d(seen_date).isoformat(), content_hash, size))


def observation_dates(store: "Store", node_id: int) -> list[str]:
    return [r["seen_date"] for r in store.db.execute(
        "SELECT seen_date FROM observations WHERE node_id=? ORDER BY seen_date",
        (node_id,))]


def refresh_datelist(store: "Store", node_id: int, anchor: date | str) -> int:
    """Recompute both derived forms for one node from its observations."""
    anchor = _d(anchor)
    dates = observation_dates(store, node_id)
    mask = encode_datelist(dates, anchor)
    store.db.execute(
        "UPDATE nodes SET activity_datelist=?, datelist_int=?, "
        "datelist_anchor=? WHERE id=?",
        (json.dumps(dates), mask, anchor.isoformat(), node_id))
    return mask


def refresh_all_datelists(store: "Store", anchor: date | str) -> None:
    """Every node's mask must share one anchor, or cohort_overlap is nonsense."""
    anchor = _d(anchor)
    for row in store.db.execute("SELECT id FROM nodes"):
        refresh_datelist(store, row["id"], anchor)
    store.db.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES "
        "('datelist_anchor', ?)", (anchor.isoformat(),))
    store.commit()


# -- retention -------------------------------------------------------------

def apply_retention(store: "Store", as_of: date | str,
                    retention_days: int = RETENTION_DAYS) -> dict[str, object]:
    """Roll observations older than the window into monthly aggregates.

    The daily rows are deleted afterwards. That is the point -- a rollup that
    leaves the source rows in place saves nothing and CP-1 checks for it.
    """
    cutoff = (_d(as_of) - timedelta(days=retention_days)).isoformat()
    rows = store.db.execute(
        """SELECT node_id, substr(seen_date, 1, 7) month,
                  COUNT(*) change_days, MAX(size) max_size
           FROM observations WHERE seen_date < ?
           GROUP BY node_id, month""", (cutoff,)).fetchall()
    for r in rows:
        last = store.db.execute(
            """SELECT content_hash FROM observations
               WHERE node_id=? AND substr(seen_date,1,7)=? AND seen_date < ?
               ORDER BY seen_date DESC LIMIT 1""",
            (r["node_id"], r["month"], cutoff)).fetchone()
        store.db.execute(
            """INSERT INTO observations_monthly
                   (node_id, month, change_days, last_hash, max_size)
               VALUES (?,?,?,?,?)
               ON CONFLICT(node_id, month) DO UPDATE SET
                   change_days = excluded.change_days,
                   last_hash   = excluded.last_hash,
                   max_size    = excluded.max_size""",
            (r["node_id"], r["month"], r["change_days"],
             last["content_hash"] if last else None, r["max_size"]))
    deleted = store.db.execute(
        "DELETE FROM observations WHERE seen_date < ?", (cutoff,)).rowcount
    store.commit()
    return {"cutoff": cutoff, "rolled_up": len(rows), "deleted_rows": deleted}
