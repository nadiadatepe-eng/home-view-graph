#!/usr/bin/env python3
"""Mutation test for CP-11 -- the write barrier.

A refusal gate is the easiest thing in this package to write and the hardest
to trust: it can pass because the barrier works, or because the second process
failed for an unrelated reason, or because the two processes never contended
at all. So most of the mutations below sever one specific wire and check that
a *named* gate notices -- not that the suite goes red, which a broken import
also achieves.

Two of them are the ones worth having. Removing the start-time check leaves a
lock that works perfectly until a pid is reused, which is a bug that reproduces
once a year and never in a test. Letting the second writer wait instead of
refusing keeps every store correct and every exit code zero, and turns a
scripted `update` into something that silently blocks for the length of a
rebuild.

Run:
    python3 tests/mutate_cp11.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900

MUTATIONS = [
    # -- the barrier itself ---------------------------------------------
    #
    # The state the package was in before CP-11: writers open the store and
    # go. Every single-writer behaviour is unchanged, which is why no other
    # checkpoint can see this.
    ("no lock is taken at all",
     "homegraph/cli.py",
     "    barrier = StoreLock(db_path, fingerprint=fingerprint, on_stale=note)\n"
     "    try:\n"
     "        barrier.acquire()",
     "    barrier = StoreLock(db_path, fingerprint=fingerprint, on_stale=note)\n"
     "    try:\n"
     "        pass  # mutated: barrier never acquired",
     "a second writer exits 2"),

    ("the second writer waits instead of refusing",
     "homegraph/lock.py",
     "            if live:\n"
     "                raise Locked(self.store_path, holder)",
     "            if live:\n"
     "                import time; time.sleep(0.05)  # mutated: queue, do not refuse\n"
     "                continue",
     "a second writer exits 2"),

    # -- the refusal has to be about contention -------------------------
    ("the refusal no longer names the holder",
     "homegraph/cli.py",
     '        print("REFUSED  %s\\n(waiting is not offered: re-run when that "\n'
     '              "process is done)" % exc, file=sys.stderr)',
     '        print("REFUSED  the store is busy", file=sys.stderr)  # mutated',
     "the refusal names the holding pid"),

    # -- liveness: a pid is not a process -------------------------------
    #
    # Works until a pid is reused, then the store is unwritable until someone
    # deletes the file by hand. The gate plants exactly that state.
    ("a live pid is enough; start time is not checked",
     "homegraph/lock.py",
     "    if now == recorded:\n"
     "        return True, \"running\"",
     "    if now is not None:\n"
     "        return True, \"running\"  # mutated: pid alone decides",
     "a live pid with the wrong start time is stale"),

    ("a dead holder's lock is treated as live",
     "homegraph/lock.py",
     "    if now is None:\n"
     "        return False, \"no such process\"",
     "    if now is None:\n"
     "        return True, \"no such process\"  # mutated",
     "a build behind an orphan lock succeeds"),

    ("an unparseable lock blocks every writer",
     "homegraph/lock.py",
     '        return False, "unreadable lock file"',
     '        return True, "unreadable lock file"  # mutated',
     "an unparseable lock does not block a writer"),

    # -- clearing an orphan must be announced ---------------------------
    ("orphans are cleared in silence",
     "homegraph/lock.py",
     "            if self.on_stale is not None:\n"
     "                self.on_stale(\"cleared a stale lock left by pid %s (%s)\"\n"
     "                              % (holder.get(\"pid\", \"?\"), why))",
     "            if False:  # mutated: clear quietly\n"
     "                pass",
     "clearing an orphan is announced, not silent"),

    # -- the lock must not outlive the writer ---------------------------
    ("the lock is never released",
     "homegraph/lock.py",
     "        try:\n"
     "            os.unlink(self.path)\n"
     "        except FileNotFoundError:\n"
     "            pass\n\n"
     "    def __enter__",
     "        pass  # mutated: lock file left behind\n\n"
     "    def __enter__",
     "no lock file survives a clean run"),

    ("release unlinks whatever lock is there, not only its own",
     "homegraph/lock.py",
     "        holder = _read(self.path)\n"
     "        if holder.get(\"nonce\") != self.nonce:\n"
     "            # Not ours any more: ours was cleared as an orphan and a later\n"
     "            # writer took the file. Unlinking here would hand that writer's\n"
     "            # store to a third one.\n"
     "            return",
     "        holder = _read(self.path)  # mutated: release anyone's lock",
     "release does not unlink a lock taken by someone else"),

    # -- readers must not be caught by the barrier ----------------------
    ("readers take the write barrier too",
     "homegraph/cli.py",
     "def cmd_status(args):",
     "def cmd_status(args):\n"
     "    from .lock import StoreLock\n"
     "    StoreLock(args.db).acquire()  # mutated: reader takes the lock",
     "status answers while a writer holds the lock"),

    # -- WAL has to be in force, not merely requested -------------------
    ("the journal mode is reported rather than read",
     "homegraph/store.py",
     'row = self.db.execute("PRAGMA journal_mode = WAL").fetchone()\n'
     '        self.journal_mode = (row[0] if row else "unknown").lower()',
     'self.db.execute("PRAGMA journal_mode = DELETE")\n'
     '        self.journal_mode = "wal"  # mutated: claim it without asking',
     "WAL is in force on a local file"),

    # -- the refusal must cost the writer nothing -----------------------
    #
    # Ordering, not logic: the store is opened before the barrier, so a
    # refused writer has already created the database file. Every exit code
    # stays correct and every single-writer run is unchanged.
    ("the store is opened before the barrier is taken",
     "homegraph/cli.py",
     "    with _barrier(db_path, fingerprint=fingerprint):\n"
     "        with Store(db_path, model=model) as store:",
     "    with Store(db_path, model=model) as store:  # mutated: store first\n"
     "        with _barrier(db_path, fingerprint=fingerprint):",
     "a refusal leaves no store behind"),

    # -- the negative control has to be able to fail --------------------
    #
    # If a lone writer is refused, every refusal gate above is measuring
    # something other than contention.
    ("every writer is refused, contended or not",
     "homegraph/cli.py",
     "        barrier.acquire()",
     "        raise Locked(db_path, {})  # mutated: refuse unconditionally",
     "three sequential writers all exit 0"),

    ("nothing is ever considered live",
     "homegraph/lock.py",
     "    try:\n"
     '        pid = int(holder["pid"])',
     '    return False, "mutated: never live"\n'
     "    try:\n"
     '        pid = int(holder["pid"])',
     "a live pid with the right start time is refused"),

    # -- the lock must not leak on the error path -----------------------
    ("the lock leaks when the writer raises",
     "homegraph/lock.py",
     "    def __exit__(self, *exc: object) -> None:\n"
     "        # Releases on KeyboardInterrupt too, which CP-8's interrupt gate\n"
     "        # depends on: an interrupted update must leave nothing behind, and a\n"
     "        # lock file is something.\n"
     "        self.release()",
     "    def __exit__(self, *exc: object) -> None:\n"
     "        if exc[0] is None:  # mutated: leak the lock on the error path\n"
     "            self.release()",
     "KeyboardInterrupt releases the lock"),

    ("the refusal stops saying that waiting is not offered",
     "homegraph/cli.py",
     '        print("REFUSED  %s\\n(waiting is not offered: re-run when that "\n'
     '              "process is done)" % exc, file=sys.stderr)',
     '        print("REFUSED  %s" % exc, file=sys.stderr)  # mutated',
     "the refusal says waiting is not offered"),

    # -- every model has a build path ------------------------------------
    #
    # The state the package was in before: three models buildable only by
    # importing their modules. Nothing behavioural sees it, because what was
    # reachable worked.
    ("a model loses its build command",
     "homegraph/cli.py",
     '    "m4": ("misc", "m4_misc"),',
     "    # mutated: m4 has no build path",
     "every model update knows about can be built"),

    ("build creates the store and puts nothing in it",
     "homegraph/cli.py",
     "        report = mod.build(store, paths, as_of, **kwargs)",
     "        report = mod.build(store, [], as_of, **kwargs)  # mutated",
     "no model builds an empty store"),

    ("build stops warning about files it could not read",
     "homegraph/cli.py",
     '        stale = getattr(report, "unreadable", None)',
     "        stale = None  # mutated: silence",
     "a build that could not read a file exits 2"),

    # -- the structural gate --------------------------------------------
    #
    # A new writer added without the barrier. This is the shape the gate
    # exists for, and it is invisible to every behavioural check because a
    # lone writer behaves the same either way.
    ("a writer is added outside the barrier",
     "homegraph/cli.py",
     "def cmd_md_build(args):\n"
     "    from datetime import date",
     "def _md_rebuild(args):  # mutated: a new, unguarded writer\n"
     "    from .store import Store\n"
     "    with Store(args.db, model=\"m3\") as s:\n"
     "        s.rebuild_fts()\n"
     "    return 0\n\n\n"
     "def cmd_md_build(args):\n"
     "    from datetime import date",
     "every CLI writer is inside the barrier"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp11.py")],
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
        tree = tempfile.mkdtemp(prefix="mut11-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns(
                                "__pycache__", ".git", ".venv", ".mypy_cache",
                                ".ruff_cache", ".pytest_cache"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
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
            print("  %-52s %s" % (name, why))
    return 1 if (survived or crashes or misattributed) else 0


if __name__ == "__main__":
    sys.exit(main())
