#!/usr/bin/env python3
"""Mutation test for CP-13 -- the foreground watch.

Every gate in a watch is a claim that either fires too little (a change is
missed) or too much (an update triggers itself and never stops). Neither
raises; both look like a working watch. Each mutation below manufactures one
and names the gate that must go red for it.

Run:
    python3 tests/mutate_cp13.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # -- the self-trigger guard (correctness) -----------------------------
    ("nothing is ignored, so the stores look like corpus changes",
     "homegraph/watch.py",
     "        if p == db or p.startswith(db + \"-\") or p.startswith(db + \".\"):\n"
     "            return False\n"
     "    return True",
     "        pass  # mutated: nothing is ignored\n"
     "    return True",
     "store writes alone never trigger an update"),

    ("only the store file is ignored, not its -wal/-shm siblings",
     "homegraph/watch.py",
     "        if p == db or p.startswith(db + \"-\") or p.startswith(db + \".\"):",
     "        if p == db:  # mutated: siblings look like corpus changes",
     "an update does not trigger itself"),

    ("SQLite siblings are ignored but the writer's own .lock is not",
     "homegraph/watch.py",
     "        if p == db or p.startswith(db + \"-\") or p.startswith(db + \".\"):",
     "        if p == db or p.startswith(db + \"-\"):  # mutated: .lock relevant",
     "the writer's own .lock and .tmp are ignored"),

    # -- debounce / coalescing --------------------------------------------
    ("the debounce stops coalescing, so each event is its own update",
     "homegraph/watch.py",
     "        while _any_relevant(source.read(debounce), keep):\n"
     "            pass",
     "        if _any_relevant(source.read(debounce), keep):  "
     "# mutated: no coalescing\n"
     "            pass",
     "three changes in one burst fire one update"),

    ("any event starts a burst, so store writes update forever",
     "homegraph/watch.py",
     "        if not _any_relevant(batch, keep):",
     "        if False:  # mutated: any event starts a burst",
     "store writes alone never trigger an update"),

    ("the corpus verdict is ignored, so excluded churn triggers rebuilds",
     "homegraph/watch.py",
     "    return relevant(path, ignore) and not is_excluded(path)",
     "    return relevant(path, ignore)  # mutated: excluded files count",
     "a corpus-excluded churny file does not count"),

    ("the irrelevant-burst backoff is gone, so store writes spin the loop",
     "homegraph/watch.py",
     "            time.sleep(debounce)",
     "            pass  # mutated: no backoff, the loop spins",
     "an irrelevant-only burst backs off instead of spinning"),

    # -- the store-dir prune (the ~40MB flood at its source) --------------
    ("the store dir is not pruned, so the watch arms on its own output",
     "homegraph/watch.py",
     "        return any(d == sd or d.startswith(sd + os.sep) "
     "for sd in store_dirs)",
     "        return False  # mutated: stores are watched",
     "the directory holding a store is pruned from the watch"),

    ("store_prune uses abspath, so a symlinked store escapes the prune",
     "homegraph/watch.py",
     "                  (os.path.dirname(os.path.realpath(p)) for p in stores)",
     "                  (os.path.dirname(os.path.abspath(p)) for p in stores)"
     "  # mutated: symlink not resolved",
     "a store reached via a symlink prunes its real directory"),

    # -- the debounce guard (negative crashes, zero re-spins) -------------
    ("a non-positive debounce is not rejected, so the watch runs anyway",
     "homegraph/cli.py",
     "    if args.debounce <= 0:",
     "    if False:  # mutated: no debounce validation",
     "a non-positive --debounce is refused, not run"),

    # -- the real inotify source ------------------------------------------
    ("new subdirectories are not re-watched",
     "homegraph/watch.py",
     "                if self._prune is None or not self._prune(path):\n"
     "                    self._walk_add(path, self._prune)",
     "                pass  # mutated: new subdirectories are not followed",
     "real inotify follows a new subdirectory"),

    # IN_CREATE gone from the mask means the kernel never reports a mkdir as
    # IN_CREATE|IN_ISDIR, so the re-watch never fires -- a different code site
    # than the `if False` above, caught by the same subdirectory gate. (A
    # created-and-written FILE still surfaces via IN_CLOSE_WRITE, so this does
    # not touch the created-file gate.)
    ("file creation is dropped from the watch mask, so a new directory is "
     "never seen",
     "homegraph/watch.py",
     "_WATCH_MASK = (IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM",
     "_WATCH_MASK = (IN_DELETE | IN_MODIFY | IN_MOVED_FROM  "
     "# mutated: creations unseen",
     "real inotify follows a new subdirectory"),

    ("the prune predicate is ignored, so excluded trees are watched",
     "homegraph/watch.py",
     "                if prune is not None and prune(full):\n"
     "                    continue",
     "                if False:  # mutated: nothing is pruned\n"
     "                    continue",
     "a pruned directory is not watched"),

    # -- the CLI wiring ---------------------------------------------------
    # The corpus check lives in watch.py (mutated above), but cmd_watch is what
    # injects the REAL classifier into it. Disable that injection -- keep falls
    # back to the store guard alone -- and every helper-level check stays green;
    # only a gate that drives the real classifier through the CLI can go red.
    ("the classifier is not wired in, so excluded churn triggers through the CLI",
     "homegraph/cli.py",
     "            path, ignore, lambda p: clf.explain(p).label == EXCLUDED)",
     "            path, ignore, lambda p: False)  # mutated: classifier unwired",
     "a corpus-excluded churny file, through the CLI, fires no update"),

    ("the trigger prints nothing, so a change is silent",
     "homegraph/cli.py",
     "        print(\"[%s] change -> update\" % stamp, flush=True)",
     "        pass  # mutated: silent trigger",
     "watch starts, reacts to a change, and stops on Ctrl-C"),

    ("Ctrl-C leaves without saying the watch stopped cleanly",
     "homegraph/cli.py",
     "        print(\"\\nstopped. nothing persists.\", flush=True)",
     "        pass  # mutated: no stop message",
     "watch starts, reacts to a change, and stops on Ctrl-C"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp13.py", prefix="mut13-", timeout=600))
