#!/usr/bin/env python3
"""Mutation test for CP-1: prove each gate can fail.

CP-1 passed 35/35 on its first run. So did CP-0 -- and CP-0 still contained a
duplicated invariant that made its negative control unable to fire. A green
checkpoint is evidence about the tests only once you have watched them go red.

Each mutation below reverts one real behaviour while leaving the test suite
untouched. A mutation that does not turn some check red means that check was
never testing that behaviour.

Run:
    python3 tests/mutate_cp1.py
"""
from __future__ import annotations

import os
import sys

# (name, relative file, needle, replacement, check that must go red)
MUTATIONS = [
    # -- fusion: ranks in, ranks out --------------------------------------
    #
    # DECISIONS.md section 8 says scores are never compared, only ranks. The
    # gates that hold it had no mutation, so "ignores incoming raw scores"
    # was a sentence rather than a measurement.
    ("fusion adds the incoming score instead of the rank",
     "homegraph/search.py",
     "            slot[\"score\"] += 1.0 / (k + rank)",
     "            slot[\"score\"] += float(hit.get(\"score\") or 0.0)"
     "  # mutated: raw scores again",
     "RRF ignores incoming raw scores"),

    ("fusion forgets which source found a hit",
     "homegraph/search.py",
     '            slot["sources"].append("%s#%d" % (source, rank))',
     '            pass  # mutated: provenance dropped',
     "RRF keeps provenance per hit"),

    # Agreement across sources is the entire reason to fuse. Overwriting
    # instead of accumulating leaves every hit with one source's score, so a
    # result found by both ranks no higher than one found by either.
    ("agreement stops accumulating",
     "homegraph/search.py",
     '            slot["score"] += 1.0 / (k + rank)',
     '            slot["score"] = 1.0 / (k + rank)  # mutated: last write wins',
     "RRF ranks the doubly-found item first"),

    # -- the time layer ----------------------------------------------------
    ("the date window shifts by one day",
     "homegraph/temporal.py",
     "        if 0 <= delta < WINDOW_DAYS:",
     "        if 0 <= delta <= WINDOW_DAYS:  # mutated: off by one",
     "the window boundary is exact"),

    ("decoding walks a different window than encoding",
     "homegraph/temporal.py",
     "    for i in range(WINDOW_DAYS):",
     "    for i in range(WINDOW_DAYS - 1):  # mutated: last day unreadable",
     "10 constructed date sets survive a round trip"),

    ("cohort overlap counts either instead of both",
     "homegraph/temporal.py",
     "def cohort_overlap(mask_a: int, mask_b: int) -> int:",
     "def cohort_overlap(mask_a: int, mask_b: int) -> int:\n"
     "    return bin(mask_a | mask_b).count('1')  # mutated: union, not shared",
     "cohort overlap counts shared days"),

    # -- the index that can be silently behind -----------------------------
    ("a stale FTS index reports itself as fresh",
     "homegraph/store.py",
     "    def fts_is_stale(self) -> bool:",
     "    def fts_is_stale(self) -> bool:\n"
     "        return False  # mutated: never admits to being behind",
     "stale index is detectable"),

    ("search stops warning that it covered only part of the store",
     "homegraph/search.py",
     '    if store.fts_is_stale():\n        warnings.append(',
     '    if False:  # mutated: incomplete results, no warning\n'
     '        warnings.append(',
     "a search over a stale index says so"),

    # -- embeddings are off, and turning them half-on is refused -----------
    ("a half-configured embedding provider is accepted",
     "homegraph/store.py",
     '            if not embeddings.get("provider") or not embeddings.get("model"):',
     '            if False:  # mutated: provider without model is fine now',
     "embeddings need provider AND model"),

    ("embeddings default to on",
     "homegraph/store.py",
     "        self.embeddings = embeddings",
     '        self.embeddings = embeddings or {"provider": "x", "model": "y"}'
     "  # mutated",
     "embeddings are off by default"),

    # -- incremental: touched, changed, removed ---------------------------
    ("a rewrite with identical bytes is reported as changed",
     "homegraph/incremental.py",
     "        new_hash = state.content_hash or _safe_hash(state.path)",
     "        new_hash = 'always-different-%s' % state.mtime  # mutated",
     "mtime-only change is 'touched', not 'changed'"),

    ("deletions are not noticed",
     "homegraph/incremental.py",
     "    for key, state in current.items():",
     "    stored = dict(stored) if False else stored  # mutated marker\n"
     "    for key in list(stored):\n"
     "        if key not in current:\n"
     "            stored.pop(key)\n"
     "    for key, state in current.items():",
     "deleted file is reported removed"),

    # -- time travel -------------------------------------------------------
    #
    # The shape round three found the hard way: a broad, plausible "cleanup"
    # that removes the schema's own mechanism for a vanished link. Deleting
    # stale edges makes today's graph right and last week's unrecoverable.
    ("stale edges are tidied away instead of left closed",
     "homegraph/store.py",
     "            self.db.execute(\n"
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=?", (as_of, method, confidence, row["id"]))',
     "            self.db.execute(\n"
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=?", (as_of, method, confidence, row["id"]))\n'
     "            self.db.execute(  # mutated: history deleted as housekeeping\n"
     '                "DELETE FROM edges WHERE last_seen < ?", (as_of,))',
     "last week's graph still has the removed link"),

    # And the other half of the same claim: an edge that stopped being seen
    # must keep the date it was last seen, or `as_of` cannot place it in time.
    ("every edge's last_seen is refreshed, seen or not",
     "homegraph/store.py",
     '        row = self.db.execute(\n'
     '            "SELECT id FROM edges WHERE src=? AND dst=? AND rel=?",\n'
     "            (src, dst, rel)).fetchone()",
     '        self.db.execute(  # mutated: no WHERE, every edge looks current\n'
     '            "UPDATE edges SET last_seen=?", (as_of,))\n'
     '        row = self.db.execute(\n'
     '            "SELECT id FROM edges WHERE src=? AND dst=? AND rel=?",\n'
     "            (src, dst, rel)).fetchone()",
     "today's graph lost the removed link"),

    ("FTS filled implicitly on insert",
     "homegraph/store.py",
     'self.db.execute("DELETE FROM nodes_fts")',
     'pass  # mutated: index never cleared',
     "rebuild_fts is idempotent"),

    ("FTS terms ORed instead of ANDed",
     "homegraph/search.py",
     'def fts_query(text: str | None, op: str = "AND")',
     'def fts_query(text: str | None, op: str = "OR")',
     "sentence query without embeddings returns 0"),

    ("time travel ignores last_seen",
     "homegraph/store.py",
     '"WHERE e.first_seen <= ? AND e.last_seen >= ?")',
     '"WHERE e.first_seen <= ? AND ? IS NOT NULL")',
     "today's graph lost the removed link"),

    ("datelist bit convention shifted by one",
     "homegraph/temporal.py",
     "mask |= 1 << delta",
     "mask |= 1 << (delta + 1)",
     "bitmask encodes to known values"),

    ("retention rolls up but never deletes",
     "homegraph/temporal.py",
     'deleted = store.db.execute(\n        "DELETE FROM observations WHERE seen_date < ?", (cutoff,)).rowcount',
     'deleted = 0  # mutated: daily rows left in place',
     "daily rows older than 90 days are gone"),

    ("hash confirmation skipped, mtime is final",
     "homegraph/incremental.py",
     'if new_hash == prior["content_hash"]:',
     'if False:  # mutated: every touch counts as a change',
     "exactly 1 file reported changed"),

    ("RRF weights rank the wrong way",
     "homegraph/search.py",
     'slot["score"] += 1.0 / (k + rank)',
     'slot["score"] += 1.0 * rank',
     "RRF rewards better ranks"),

    ("deleting a node leaves its FTS row",
     "homegraph/store.py",
     'self.db.execute("DELETE FROM nodes_fts WHERE rowid = ?", (nid,))',
     'pass  # mutated: FTS row orphaned',
     "no FTS orphans after deletion"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

# 900 s fordi denne harnessen var den ENESTE av 22 uten noen timeout i det hele
# tatt -- `subprocess.run` uten `timeout=`. En mutasjon som fikk suiten til å henge
# ville stoppet hele sveipet i stedet for å bli rapportert som `<timeout>`. Romslig
# satt, så ingenting som fullfører i dag begynner å tidsavbryte.
if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp1.py", prefix="mut-", timeout=900,
                 ignore=("*.tsv", ".homegraph")))
