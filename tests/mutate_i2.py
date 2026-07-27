#!/usr/bin/env python3
"""Mutation test for CP-I2 -- stale vectors, coverage, and incremental embed.

The three changes in this checkpoint are one change seen from three sides, and
the mutations below are chosen to prove that: neutralising any one of them
reddens a gate that names it, and neutralising the FIRST one would -- in the
real product, not just here -- have made the third one permanently wrong.

Run:
    python3 tests/mutate_i2.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # THE one. The rebuilt node keeps the vector describing text that is gone,
    # which is how this defect shipped in the first place.
    ("a rebuilt node keeps the vector describing its old text",
     "homegraph/update.py",
     '            store.db.execute("DELETE FROM embeddings WHERE node_id = ?", (nid,))',
     "            pass  # mutated: stale vector kept",
     "the stale vector is deleted rather than left behind"),

    # Incremental embed stops being incremental: every run re-embeds the whole
    # store, which is the twenty minutes that made the gap not worth closing.
    ("embed always re-embeds everything, incremental or not",
     "homegraph/cli.py",
     "        if force:\n            rows = store.db.execute(",
     "        if True:  # mutated: always full\n            rows = store.db.execute(",
     "a second run embeds nothing"),

    # ...and the other direction: --force is ignored, so a changed matrix under
    # an unchanged namespace can never be re-embedded at all.
    ("--force is ignored, so a changed matrix can never be re-embedded",
     "homegraph/cli.py",
     "        if force:\n            rows = store.db.execute(",
     "        if False:  # mutated: force ignored\n            rows = store.db.execute(",
     "--force re-embeds every node"),

    # Coverage stops reporting a hole, so a partial namespace looks complete --
    # the state `_out_mode=hybrid` already fails to distinguish.
    ("coverage never reports a namespace as incomplete",
     "homegraph/store.py",
     '"stale": r["c"] < total} for r in rows]',
     '"stale": False} for r in rows]  # mutated',
     "after an update the namespace is marked stale"),

    # The denominator counts nodes that have no text and are skipped on
    # purpose, so full coverage becomes unreachable and the number stops
    # meaning anything -- a metric that can never read 100% gets ignored.
    ("the coverage denominator counts textless nodes too",
     "homegraph/store.py",
     '            "SELECT COUNT(*) c FROM nodes "\n'
     "            \"WHERE COALESCE(title,'') || COALESCE(body,'') != ''\"",
     '            "SELECT COUNT(*) c FROM nodes "\n'
     '            "WHERE 1=1"  # mutated: textless nodes counted as missing',
     "the denominator counts nodes with text, not all nodes"),

    # The cascade that removes a deleted file's vector stops working. There is
    # no line in `delete_node` to neutralise -- it leans on ON DELETE CASCADE --
    # so the mutation turns the pragma that makes the cascade fire at all. That
    # is the real dependency, and it is easy to lose in a refactor: without the
    # pragma SQLite silently ignores every foreign key in the schema.
    ("foreign keys are off, so a deleted node orphans its vector",
     "homegraph/store.py",
     'self.db.execute("PRAGMA foreign_keys = ON")',
     'self.db.execute("PRAGMA foreign_keys = OFF")  # mutated',
     "a removed file leaves no vector behind"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_i2.py", prefix="muti2-", timeout=300))
