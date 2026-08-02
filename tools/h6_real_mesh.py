#!/usr/bin/env python3
"""CP-H6 against the real mesh: the twenty queries, and the four predictions.

Every count in `tests/gold/FASIT-h6.md` about real data comes from this file.
It exists because an audit pointed out on 2026-08-02 that the key cited "1 of
20", "7 of 20", "3 of 20" and "10 of 20" while the twenty queries were written
down nowhere -- so nobody, including its author next month, could check them.

Reads only. The stores are copied to a temp directory first, so a bug here
cannot touch `~/.homegraph`.

    python3 tools/h6_real_mesh.py

The control disables ONLY the tie-break. `mesh_db=None` will not do: it also
switches off `_search_code`, and then what you measure is the code list. That
mistake was made once, on 2026-08-02, and it reversed the conclusion.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.mesh import Mesh                               # noqa: E402

HOME = os.path.expanduser("~/.homegraph")

# Prose terms, chosen to span the corpus rather than to flatter the feature.
QUERIES = [
    "mutation harness", "embedding", "rrf fusion", "checkpoint gate",
    "sqlite store", "obsidian vault", "mesh edges", "worktree",
    "centrality", "provenance", "tokenizer", "backup restore",
    "graph layout", "watch inotify", "privacy gate", "codex review",
    "chunking", "recall", "harvest plan", "memory index",
]

# Filename-shaped terms, which is the only way the code stubs answer at all.
# Without these the double-qualification bug that scored every code candidate
# zero -- `main.py`, degree 69, the highest in the store -- is invisible.
CODE_QUERIES = ["main", "archify", "mesh", "indexer", "store", "layout",
                "api", "embed"]

MIN_MOVED_QUERIES = 2       # prediction 3 in the key


def copy_corpus(work):
    paths = {}
    for name in ("m1", "m2", "m3", "m4"):
        src = os.path.join(HOME, "real-%s.db" % name)
        if os.path.exists(src):
            dst = os.path.join(work, "%s.db" % name)
            shutil.copy2(src, dst)
            paths[name] = dst
    mesh_db = os.path.join(work, "mesh.db")
    shutil.copy2(os.path.join(HOME, "real-mesh.db"), mesh_db)
    return paths, mesh_db


def measure(queries, paths, mesh_db):
    on = Mesh(paths, mesh_db=mesh_db)
    off = Mesh(paths, mesh_db=mesh_db)
    off.centrality_degrees = lambda rankings: None
    out = []
    for q in queries:
        a, b = off.search(q, limit=10), on.search(q, limit=10)
        ka = [h["key"] for h in a.hits]
        kb = [h["key"] for h in b.hits]
        out.append({
            "query": q,
            "hits": len(kb),
            "introduced": sorted(set(kb) - set(ka)),
            "scores_differ": [round(h["score"], 12) for h in a.hits]
                             != [round(h["score"], 12) for h in b.hits],
            "reordered": ka != kb,
            "moved": b.centrality,
            "max_degree": max([h["degree"] or 0 for h in b.hits] or [0]),
        })
    return out


def main() -> int:
    if not os.path.exists(os.path.join(HOME, "real-mesh.db")):
        print("no real mesh at %s -- nothing to measure" % HOME)
        return 0
    work = tempfile.mkdtemp(prefix="h6-real-")
    try:
        paths, mesh_db = copy_corpus(work)
        rows = measure(QUERIES, paths, mesh_db)
        code_rows = measure(CODE_QUERIES, paths, mesh_db)

        introduced = [r for r in rows if r["introduced"]]
        rescored = [r for r in rows if r["scores_differ"]]
        moved = [r for r in rows if r["reordered"]]
        zero = [r for r in rows if r["max_degree"] == 0]
        zero_unchanged = [r for r in zero if not r["reordered"]]

        print("== prediction 1: no candidate crosses a score boundary ==")
        print("   %s -- %d of %d queries have a different score sequence"
              % ("OK" if not rescored else "BREACH", len(rescored), len(rows)))
        print("== prediction 2: nothing absent before appears after ==")
        print("   %s -- %d of %d queries introduced a hit"
              % ("OK" if not introduced else "BREACH", len(introduced),
                 len(rows)))
        for r in introduced:
            print("      %-20s %s" % (r["query"], r["introduced"]))
        print("== prediction 3: at least %d queries move ==" % MIN_MOVED_QUERIES)
        print("   %s -- %d of %d reordered"
              % ("OK" if len(moved) >= MIN_MOVED_QUERIES else "BREACH",
                 len(moved), len(rows)))
        print("== prediction 4: an all-degree-0 query is unchanged ==")
        print("   %s -- %d such queries, %d unchanged"
              % ("OK" if len(zero) == len(zero_unchanged) else "BREACH",
                 len(zero), len(zero_unchanged)))

        print("\n%-20s %5s %8s %7s %8s" % ("query", "hits", "reordered",
                                           "moved", "maxdeg"))
        for r in rows + code_rows:
            print("%-20s %5d %8s %7s %8s"
                  % (r["query"], r["hits"], "yes" if r["reordered"] else "-",
                     r["moved"], r["max_degree"]))

        code_seen = max(r["max_degree"] for r in code_rows)
        print("\nhighest degree reached by a filename query: %d" % code_seen)
        print("(0 here means the code stubs are being looked up under the "
              "wrong key -- see Mesh._mesh_node_key)")

        breaches = bool(rescored) + bool(introduced) \
            + (len(moved) < MIN_MOVED_QUERIES) \
            + (len(zero) != len(zero_unchanged))
        return 1 if breaches else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
