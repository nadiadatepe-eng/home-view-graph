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
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 600

MUTATIONS = [
    # -- the self-trigger guard (correctness) -----------------------------
    ("nothing is ignored, so the stores look like corpus changes",
     "homegraph/watch.py",
     "        if p == db or p.startswith(db + \"-\"):\n"
     "            return False\n"
     "    return True",
     "        pass  # mutated: nothing is ignored\n"
     "    return True",
     "store writes alone never trigger an update"),

    ("only the store file is ignored, not its -wal/-shm siblings",
     "homegraph/watch.py",
     "        if p == db or p.startswith(db + \"-\"):",
     "        if p == db:  # mutated: siblings look like corpus changes",
     "an update does not trigger itself"),

    # -- debounce / coalescing --------------------------------------------
    ("the debounce stops coalescing, so each event is its own update",
     "homegraph/watch.py",
     "        while _any_relevant(source.read(debounce), ignore):\n"
     "            pass",
     "        if _any_relevant(source.read(debounce), ignore):  "
     "# mutated: no coalescing\n"
     "            pass",
     "three changes in one burst fire one update"),

    ("any event starts a burst, so store writes update forever",
     "homegraph/watch.py",
     "        if not _any_relevant(batch, ignore):",
     "        if False:  # mutated: any event starts a burst",
     "store writes alone never trigger an update"),

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


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp13.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut13-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, _proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-52s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-52s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-52s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-52s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-52s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
