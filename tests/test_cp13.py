#!/usr/bin/env python3
"""CP-13 -- the foreground watch.

The borrowed idea (measured against colbymchenry/codegraph, 2026-07-23) is
*react to OS filesystem events instead of polling* -- WITHOUT codegraph's
daemon, which homegraph deliberately has no equivalent of. So `homegraph watch`
is opt-in and non-persistent, and the load-bearing claims are two:

    1. one settled burst of changes fires exactly one update, and
    2. an update never triggers itself -- the store writes it produces are
       ignored, or the first change updates forever.

Both are decided by pure code (`relevant`, `watch_loop`) driven here by a
scripted event source, so the gate is deterministic and not a race. Beside them
sit two live checks -- the real inotify source against a real temp tree, and
`homegraph watch` through the CLI with Ctrl-C -- because nine green in-process
checks have missed what one real run found before in this project.

The fasit is hand-computed and written above each check, before the code it
runs, and each gate is named narrowly so a mutation that breaks one property
reddens that gate and not another.

Run:
    python3 tests/test_cp13.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import watch as w                               # noqa: E402

ROOTDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE = ["/store/m3.db"]
EV = ("/corpus/file.md", w.IN_CLOSE_WRITE)          # a real corpus change
DB = ("/store/m3.db", w.IN_MODIFY)                  # a store write we caused
WAL = ("/store/m3.db-wal", w.IN_MODIFY)             # its SQLite sibling

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


class Scripted(w.Source):
    """Return each scripted batch on successive reads, then stop the loop.

    `watch_loop` reads with `None` for the first event of a burst and with the
    debounce timeout while coalescing; a batch of `[]` models the quiet period
    elapsing. When the script runs dry the source raises KeyboardInterrupt --
    the same signal a real Ctrl-C sends -- so the loop exits the way it does in
    production rather than on a special sentinel.
    """

    def __init__(self, batches):
        self.batches = list(batches)
        self.reads = []

    def read(self, timeout):
        self.reads.append(timeout)
        if not self.batches:
            raise KeyboardInterrupt
        return self.batches.pop(0)


def count_triggers(batches):
    fired = {"n": 0}
    src = Scripted(batches)

    def trigger():
        fired["n"] += 1

    try:
        w.watch_loop(src, trigger, ignore=IGNORE, debounce=0.01)
    except KeyboardInterrupt:
        pass
    return fired["n"], src


# --------------------------------------------------------------------------

def t_guard():
    """Fasit for the self-trigger guard, by hand:

        /corpus/file.md      relevant   (a corpus change)  -> True
        /store/m3.db         ignored    (the store itself)  -> False
        /store/m3.db-wal     ignored    (SQLite sibling)    -> False
        /store/m3.db-shm     ignored    (SQLite sibling)    -> False
    """
    check("a corpus file is a change worth updating",
          w.relevant("/corpus/file.md", IGNORE) is True)
    check("the store file itself is ignored",
          w.relevant("/store/m3.db", IGNORE) is False)
    check("the store's -wal and -shm siblings are ignored",
          w.relevant("/store/m3.db-wal", IGNORE) is False
          and w.relevant("/store/m3.db-shm", IGNORE) is False)


def t_coalesce():
    """Fasit: three corpus events with no quiet gap between them, then a quiet
    debounce window, is ONE update -- one save touching three files is one
    rebuild, not three."""
    n, _ = count_triggers([[EV], [EV], [EV], []])
    check("three changes in one burst fire one update", n == 1,
          "fired %d" % n)


def t_separated():
    """Fasit: a burst, a full quiet window, then another burst is TWO updates.
    The debounce coalesces within a burst; it does not merge burst to burst."""
    n, _ = count_triggers([[EV], [], [EV], []])
    check("two bursts separated by quiet fire two updates", n == 2,
          "fired %d" % n)


def t_self_trigger():
    """Fasit: events that are only store writes fire ZERO updates. This is the
    correctness gate -- without it the first update's own writes would arrive
    as changes and it would update forever."""
    n, _ = count_triggers([[DB], [DB, WAL], []])
    check("store writes alone never trigger an update", n == 0,
          "fired %d" % n)


def t_no_retrigger():
    """Fasit: a corpus change fires one update; the store writes that update
    produces then arrive as the next batch and must NOT fire a second. Exactly
    one update, not two."""
    n, _ = count_triggers([[EV], [], [DB, WAL], [DB]])
    check("an update does not trigger itself", n == 1, "fired %d" % n)


def t_real_inotify():
    """The pure loop above trusts a source; this proves the real one delivers.

    A real inotify watch over a real temp tree must report a file created in
    it -- and a file created in a subdirectory made AFTER the watch started,
    since recursion is maintained by hand and a missing re-watch loses exactly
    those.
    """
    try:
        src = w.Inotify()
    except w.InotifyUnavailable as exc:
        check("real inotify reports a created file", True,
              "SKIPPED -- %s" % exc)
        check("real inotify follows a new subdirectory", True, "SKIPPED")
        return
    d = tempfile.mkdtemp(prefix="cp13-ino-")
    try:
        src.add_tree(d)
        seen = set()

        def make():
            time.sleep(0.2)
            open(os.path.join(d, "top.txt"), "w").write("x")
            sub = os.path.join(d, "sub")
            os.mkdir(sub)
            time.sleep(0.15)
            open(os.path.join(sub, "deep.md"), "w").write("y")

        threading.Thread(target=make).start()
        deadline = time.time() + 5
        while time.time() < deadline and not {"top.txt", "sub/deep.md"} <= seen:
            for path, _mask in src.read(0.5):
                seen.add(os.path.relpath(path, d))
        check("real inotify reports a created file", "top.txt" in seen,
              "saw %s" % sorted(seen))
        check("real inotify follows a new subdirectory",
              "sub/deep.md" in seen, "saw %s" % sorted(seen))
    finally:
        src.close()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def t_prune():
    """A watch over a real home would arm ~50k inotify watches on `.cache`,
    `.venv` and the rest -- churn the corpus already excludes. `add_tree` takes
    a prune predicate (the corpus classifier, in production) so those trees are
    never watched.

    Fasit: with a predicate that prunes any directory named `skip`, a file
    created under `keep/` is reported and a file created under `skip/` is not.
    The predicate is injected, so this tests the mechanism without the
    classifier -- the classifier's own rules are cp7's.
    """
    try:
        src = w.Inotify()
    except w.InotifyUnavailable as exc:
        check("a pruned directory is not watched", True, "SKIPPED -- %s" % exc)
        return
    d = tempfile.mkdtemp(prefix="cp13-prune-")
    try:
        os.mkdir(os.path.join(d, "keep"))
        os.mkdir(os.path.join(d, "skip"))
        src.add_tree(d, prune=lambda p: os.path.basename(p) == "skip")
        seen = set()

        def make():
            time.sleep(0.2)
            open(os.path.join(d, "keep", "a.txt"), "w").write("x")
            open(os.path.join(d, "skip", "b.txt"), "w").write("y")

        threading.Thread(target=make).start()
        deadline = time.time() + 3
        while time.time() < deadline:
            for path, _mask in src.read(0.5):
                seen.add(os.path.relpath(path, d))
        check("a pruned directory is not watched",
              "keep/a.txt" in seen and "skip/b.txt" not in seen,
              "saw %s" % sorted(seen))
    finally:
        src.close()
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def t_through_the_cli():
    """The path the user walks: `homegraph watch`, a real change, Ctrl-C.

    A gate that called `cmd_watch` directly would pass with the argparse
    wiring disconnected -- that failure has happened in this project. So this
    spawns the CLI, touches a file, and reads the process's own output. It
    asserts the WATCH wiring (inotify started, a change reached the trigger,
    Ctrl-C exits clean), not the update internals, which cp8 owns -- so the
    gate does not ride on the update's fingerprint bookkeeping.
    """
    if _inotify_absent():
        check("watch starts, reacts to a change, and stops on Ctrl-C", True,
              "SKIPPED -- inotify unavailable")
        return

    from tests.fixtures import synthetic as syn
    from tests.test_cp12 import build_corpus

    tmp = tempfile.mkdtemp(prefix="cp13-cli-",
                           dir=os.path.expanduser("~/.homegraph"))
    proc = None
    try:
        root = os.path.join(tmp, "korpus")
        db = build_corpus(root, syn)
        cfg = syn._config_for(root)
        env = dict(os.environ, PYTHONPATH=ROOTDIR)
        proc = subprocess.Popen(
            [sys.executable, "-m", "homegraph.cli", "watch",
             "--model", "m4=%s" % db, "--root", root,
             "--config", cfg, "--debounce", "0.3"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            cwd=ROOTDIR, env=env)

        lines: list[str] = []
        lock = threading.Lock()

        def pump():
            assert proc.stdout is not None
            for ln in proc.stdout:
                with lock:
                    lines.append(ln.rstrip("\n"))

        threading.Thread(target=pump, daemon=True).start()

        def wait_for(substr, timeout):
            end = time.time() + timeout
            while time.time() < end:
                with lock:
                    if any(substr in ln for ln in lines):
                        return True
                if proc.poll() is not None:
                    return False
                time.sleep(0.05)
            return False

        started = wait_for("watching", 10)
        # Give inotify a beat to arm before the change, then make one.
        time.sleep(0.3)
        open(os.path.join(root, "watch-probe.txt"), "w").write("a change\n")
        reacted = wait_for("-> update", 10)

        proc.send_signal(subprocess.signal.SIGINT)
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait(timeout=5)
        with lock:
            blob = "\n".join(lines)
        stopped = "stopped. nothing persists." in blob

        check("watch starts, reacts to a change, and stops on Ctrl-C",
              started and reacted and stopped and rc == 0,
              "started=%s reacted=%s stopped=%s exit=%s"
              % (started, reacted, stopped, rc))
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        from tests.fixtures import synthetic as syn
        syn.use_config(syn.CONFIG)


def _inotify_absent():
    try:
        w.Inotify().close()
        return False
    except w.InotifyUnavailable:
        return True


def main():
    t_guard()
    t_coalesce()
    t_separated()
    t_self_trigger()
    t_no_retrigger()
    t_real_inotify()
    t_prune()
    t_through_the_cli()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_cp13():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
