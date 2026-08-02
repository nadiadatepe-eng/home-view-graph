#!/usr/bin/env python3
"""CP-H7 against the real stores: the four predictions, with the numbers.

Same reason `tools/h6_real_mesh.py` exists -- an answer key that cites "3 207
paths are gone" and keeps the query set in someone's terminal history is a key
nobody can check, including its author next month.

Reads only. The stores are copied to a temp directory first.

    python3 tools/h7_real_mesh.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import incremental as inc                     # noqa: E402
from homegraph.mesh import Mesh                              # noqa: E402
from homegraph.store import Store                            # noqa: E402

HOME = os.path.expanduser("~/.homegraph")
QUERIES = ["mutation harness", "embedding", "checkpoint gate", "sqlite store",
           "mesh edges", "provenance", "recall", "memory index"]

CORPUS_PASS_CEILING = 0.10        # prediction 3, seconds for a full pass
WINDOW_CEILING = 0.001            # prediction 3, seconds of STAT per search
# Prediction 2 is two-sided. Without a ceiling, a reconciliation that declares
# the whole corpus gone -- the unmounted-network-path case R4 worries about --
# passes "missing in the hundreds" more convincingly than a correct one.
MISSING_SHARE_CEILING = 0.60


def main() -> int:
    if not os.path.exists(os.path.join(HOME, "real-mesh.db")):
        # Not 0. A tool that reports success when it measured nothing is the
        # empty-gate shape this repo has a name for -- and in CI, where
        # `~/.homegraph` does not exist, every prediction here would have been
        # "passing" without a single file being read.
        print("no real stores at %s -- nothing was measured" % HOME)
        return 2
    work = tempfile.mkdtemp(prefix="h7-real-")
    try:
        paths = {}
        for name in ("m1", "m2", "m3", "m4"):
            src = os.path.join(HOME, "real-%s.db" % name)
            if os.path.exists(src):
                dst = os.path.join(work, "%s.db" % name)
                shutil.copy2(src, dst)
                paths[name] = dst
        mesh_db = os.path.join(work, "mesh.db")
        shutil.copy2(os.path.join(HOME, "real-mesh.db"), mesh_db)

        print("== per-store reconciliation ==")
        started = time.time()
        totals = {}
        for name, path in sorted(paths.items()):
            with Store(path) as store:
                counts: dict[str, int] = {}
                for state in inc.reconcile(store).values():
                    counts[state] = counts.get(state, 0) + 1
            totals[name] = counts
            print("   %-4s %s" % (name, counts))
        elapsed = time.time() - started

        missing = {n: c.get(inc.MISSING, 0) for n, c in totals.items()}
        hundreds = [n for n, v in missing.items() if v >= 100]
        print()
        share = {n: (c.get(inc.MISSING, 0) / max(1, sum(c.values())))
                 for n, c in totals.items()}
        blown = [n for n, v in share.items() if v > MISSING_SHARE_CEILING]
        print("== prediction 2: missing runs into the hundreds, 3 of 4 stores ==")
        print("   %s -- %d of %d stores over 100 missing: %r"
              % ("OK" if len(hundreds) >= 3 else "BREACH", len(hundreds),
                 len(totals), missing))
        print("   %s -- no store is more than %d%% missing: %s"
              % ("OK" if not blown else "BREACH",
                 int(MISSING_SHARE_CEILING * 100),
                 ", ".join("%s %.0f%%" % (n, v * 100)
                           for n, v in sorted(share.items()))))

        print("== prediction 3: a corpus pass stays under %.2f s =="
              % CORPUS_PASS_CEILING)
        print("   %s -- %.3f s over %d stores"
              % ("OK" if elapsed < CORPUS_PASS_CEILING else "BREACH",
                 elapsed, len(totals)))

        # The STAT window, not the whole search: timing `mesh.search` would
        # measure FTS and fusion too, and the prediction is about the syscalls
        # this checkpoint added. Measured by counting the calls the annotation
        # makes and timing those alone.
        mesh = Mesh(paths, mesh_db=mesh_db)
        real = inc.node_state
        spent = [0.0]

        def timed(*a, **kw):
            at = time.time()
            try:
                return real(*a, **kw)
            finally:
                spent[0] += time.time() - at

        inc.node_state = timed
        try:
            for q in QUERIES:
                mesh.search(q, limit=10)
        finally:
            inc.node_state = real
        per_query = spent[0] / len(QUERIES)
        print("   %s -- %.6f s of stat per search (ceiling %.6f)"
              % ("OK" if per_query < WINDOW_CEILING else "BREACH",
                 per_query, WINDOW_CEILING))

        print()
        print("== prediction 4: a search commits nothing ==")
        # `PRAGMA data_version`, not the file mtime: WAL puts a commit in
        # `db-wal` and leaves the main file alone, so an mtime check cannot see
        # a write at all. Found by audit 2026-08-02, after the mtime version
        # passed a search that wrote to every stale node.
        watchers = {}
        for p in paths.values():
            conn = sqlite3.connect(p)
            conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            watchers[p] = conn
        before = {p: c.execute("PRAGMA data_version").fetchone()[0]
                  for p, c in watchers.items()}
        for q in QUERIES:
            mesh.search(q, limit=10)
        after = {p: c.execute("PRAGMA data_version").fetchone()[0]
                 for p, c in watchers.items()}
        for c in watchers.values():
            c.close()
        print("   %s -- %d of %d stores saw no commit"
              % ("OK" if before == after else "BREACH",
                 sum(1 for p in before if before[p] == after[p]), len(before)))

        print()
        print("%-20s %5s %-38s" % ("query", "hits", "warning"))
        for q in QUERIES:
            res = mesh.search(q, limit=10)
            banner = next((w for w in res.warnings if "reindex" in w), "-")
            print("%-20s %5d %-38s" % (q, len(res.hits), banner[:38]))

        breaches = ((len(hundreds) < 3) + (elapsed >= CORPUS_PASS_CEILING)
                    + (before != after) + bool(blown)
                    + (per_query >= WINDOW_CEILING))
        return 1 if breaches else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
