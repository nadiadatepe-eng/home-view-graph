#!/usr/bin/env python3
"""CP-11 -- the write barrier. One claim, and it is a refusal.

    while one process is writing a store, a second one exits 2 and
    names the holder; readers are not blocked; and nothing is left
    behind when the writer is done.

A refusal is the easiest kind of gate to fake. It passes if the lock is never
contended, if the second process fails for some other reason, or if the first
process never really held anything. So the gates here are built to separate
those cases:

  * **Two real processes, not two objects.** The holder is a separate
    interpreter that takes the lock through the same `StoreLock` the CLI uses.
    A test that called `acquire()` twice in one process would pass against a
    lock implemented with a module-level boolean.
  * **The refusal is checked for its reason, not its code.** Exit 2 is what
    argparse returns for an unknown flag, what a missing config returns, and
    what a missing store returns. The gate reads the holder's pid out of
    stderr, so an exit 2 for any other reason fails it.
  * **The first writer's result is compared, not assumed.** A barrier that
    refused the second writer by corrupting the first would pass every check
    above. The store is compared to a solo build, node and edge counts both.
  * **The negative control runs alone.** If a single writer ever sees exit 2,
    the refusal is firing on something other than contention, and every gate
    above is measuring the wrong thing.

`t_every_writer_takes_the_lock` derives the writers from the AST rather than
from a list kept by hand. A list would have to be edited by the same person
who forgot the barrier.

Run:
    python3 tests/test_cp11.py
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.lock import HAVE_PROC, Locked, StoreLock, _start_time  # noqa: E402
from homegraph.store import Store                                     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-54s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# A second interpreter that takes the lock and holds it until told to stop.
# It uses the production class: a holder that wrote the lock file by hand
# would prove that this test can parse its own output and nothing else.
HOLDER = """
import sys
sys.path.insert(0, %r)
from homegraph.lock import StoreLock
lock = StoreLock(sys.argv[1])
lock.acquire()
print("held", flush=True)
sys.stdin.readline()
lock.release()
"""


class holding:
    """Context manager: a real other process holding the lock on `path`."""

    def __init__(self, path):
        self.path = path
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", HOLDER % REPO, self.path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=REPO)
        line = self.proc.stdout.readline().strip()
        if line != "held":
            raise RuntimeError("holder did not start: %r %s"
                               % (line, self.proc.stderr.read()))
        return self.proc

    def __exit__(self, *exc):
        try:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def build_cmd(db, root, cfg):
    return [sys.executable, "-m", "homegraph.cli", "md", "build",
            db, "--root", root]


def run(cmd, cfg, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO,
                          timeout=timeout, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, HOMEGRAPH_CONFIG=cfg))


def counts(db):
    with Store(db) as s:
        return (s.node_count(), s.edge_count())


# -- 1. WAL is in force, not merely requested ------------------------------

def t_wal_is_in_force(tmp):
    """`journal_mode` is the one PRAGMA that can decline.

    The gate reads the mode back out of a second connection rather than
    trusting the attribute the first one set, because an attribute assigned
    from a return value is still an attribute this module wrote.
    """
    db = os.path.join(tmp, "wal.db")
    with Store(db) as s:
        claimed = s.journal_mode
    fresh = sqlite_mode(db)
    check("Store reports the mode it actually got", claimed == fresh,
          "claimed=%s on-disk=%s" % (claimed, fresh))
    check("WAL is in force on a local file", fresh == "wal", fresh)


def sqlite_mode(db):
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()


# -- 2. a second writer is refused, and told who holds it ------------------

def t_second_writer_refuses(tmp, root, cfg):
    db = os.path.join(tmp, "contended.db")
    with holding(db) as holder:
        proc = run(build_cmd(db, root, cfg), cfg)
    check("a second writer exits 2", proc.returncode == 2,
          "exit %d  %s" % (proc.returncode, proc.stderr.strip()[-90:]))
    # The reason, not just the code. Exit 2 is also what a missing config, a
    # missing store and an unknown flag return.
    check("the refusal names the holding pid",
          str(holder.pid) in proc.stderr and "REFUSED" in proc.stderr,
          "pid %d in stderr: %s" % (holder.pid, str(holder.pid) in proc.stderr))
    check("the refusal says waiting is not offered",
          "waiting is not offered" in proc.stderr,
          proc.stderr.strip().splitlines()[-1][:70] if proc.stderr else "")
    check("a refused writer wrote no store",
          not os.path.exists(db) or counts(db) == (0, 0),
          "store absent or empty")


# -- 3. the first writer's result is intact -------------------------------

def t_a_refusal_costs_the_writer_nothing(tmp, root, cfg):
    """A barrier that refused by damaging the store passes every gate above.

    Two free-running CLI builds would be the obvious construction and it is
    the wrong one: on a 54-node fixture they may not overlap at all, and a
    gate whose subject is decided by scheduling is a gate that fails once a
    month and gets deleted. The first run of this checkpoint failed exactly
    that way -- the refusal landed on the process the test called "first".

    So the contention is deterministic: a real other process holds the lock,
    the build is refused, the holder leaves, and the retry has to produce a
    store identical to one that was never contended.
    """
    solo = os.path.join(tmp, "solo.db")
    proc = run(build_cmd(solo, root, cfg), cfg)
    if not check("the solo build exits 0", proc.returncode == 0,
                 "exit %d  %s" % (proc.returncode, proc.stderr[-120:])):
        return
    expected = counts(solo)

    contested = os.path.join(tmp, "contested.db")
    with holding(contested):
        refused = run(build_cmd(contested, root, cfg), cfg)
    check("the build under contention was refused", refused.returncode == 2,
          "exit %d" % refused.returncode)
    check("a refusal leaves no store behind",
          not os.path.exists(contested), contested)

    retry = run(build_cmd(contested, root, cfg), cfg)
    check("the retry after a refusal exits 0", retry.returncode == 0,
          "exit %d  %s" % (retry.returncode, retry.stderr[-120:]))
    got = counts(contested)
    # Counts, not a boolean: a store a refused writer half-wrote holds a
    # plausible-looking number.
    check("the store after a refusal equals an uncontended build",
          got == expected, "solo=%s contested=%s" % (expected, got))


# -- 4. an orphan is cleared, and said so ---------------------------------

def t_orphan_lock_is_cleared(tmp, root, cfg):
    db = os.path.join(tmp, "orphan.db")
    dead = free_pid()
    write_lock(db + ".lock", {"pid": dead, "start": 1, "nonce": "x",
                              "created": "2026-01-01T00:00:00"})
    proc = run(build_cmd(db, root, cfg), cfg)
    check("a build behind an orphan lock succeeds", proc.returncode == 0,
          "exit %d  %s" % (proc.returncode, proc.stderr[-120:]))
    check("clearing an orphan is announced, not silent",
          "stale lock" in proc.stderr and str(dead) in proc.stderr,
          proc.stderr.strip().splitlines()[0][:80] if proc.stderr else "(quiet)")
    check("the lock is gone when the writer is done",
          not os.path.exists(db + ".lock"), db + ".lock")


def t_unparseable_lock_is_not_a_live_writer(tmp, root, cfg):
    """A truncated lock file is a bug, not evidence of a writer.

    Reading it as live would make the store permanently unwritable, which is
    the failure mode that teaches people to delete lock files by reflex.
    """
    db = os.path.join(tmp, "garbage.db")
    with open(db + ".lock", "w") as fh:
        fh.write("{not json")
    proc = run(build_cmd(db, root, cfg), cfg)
    check("an unparseable lock does not block a writer",
          proc.returncode == 0, "exit %d" % proc.returncode)


# -- 5. a reused pid is not the original process --------------------------

def t_reused_pid_is_not_live(tmp, root, cfg):
    """The gate that separates "pid exists" from "the holder is alive".

    The lock names a pid that is genuinely running -- this test's own -- with
    a start time that is not its own. Without the start-time check this reads
    as a live writer and the build is refused forever.
    """
    if not HAVE_PROC:
        check("reused pid is detected (skipped: no /proc)", True, "n/a")
        return
    db = os.path.join(tmp, "reused.db")
    mine = os.getpid()
    real_start = _start_time(mine)
    write_lock(db + ".lock", {"pid": mine, "start": (real_start or 0) + 1,
                              "nonce": "x", "created": "2026-01-01T00:00:00"})
    proc = run(build_cmd(db, root, cfg), cfg)
    check("a live pid with the wrong start time is stale",
          proc.returncode == 0, "exit %d  %s" % (proc.returncode,
                                                 proc.stderr[-100:]))
    # And the converse, or the check above passes for a lock that ignores
    # liveness entirely.
    write_lock(db + ".lock", {"pid": mine, "start": real_start,
                              "nonce": "x", "created": "2026-01-01T00:00:00"})
    proc2 = run(build_cmd(db, root, cfg), cfg)
    check("a live pid with the right start time is refused",
          proc2.returncode == 2, "exit %d" % proc2.returncode)
    os.unlink(db + ".lock")


# -- 6. readers are not blocked -------------------------------------------

def t_reader_is_not_blocked(tmp, root, cfg):
    """WAL exists so `search` answers while `update` runs.

    A barrier taken by readers would be invisible in every gate above and
    would make the MCP server unusable during a build.
    """
    db = os.path.join(tmp, "readable.db")
    proc = run(build_cmd(db, root, cfg), cfg)
    if not check("a store to read exists", proc.returncode == 0,
                 "exit %d" % proc.returncode):
        return
    with holding(db):
        st = run([sys.executable, "-m", "homegraph.cli", "status", db], cfg)
        se = run([sys.executable, "-m", "homegraph.cli", "search", db,
                  "note"], cfg)
    check("status answers while a writer holds the lock",
          st.returncode == 0, "exit %d  %s" % (st.returncode, st.stderr[-80:]))
    check("search answers while a writer holds the lock",
          se.returncode == 0, "exit %d  %s" % (se.returncode, se.stderr[-80:]))


# -- 7. negative control ---------------------------------------------------

def t_single_writer_never_refuses(tmp, root, cfg):
    """If this fails, every refusal gate above is measuring something else."""
    db = os.path.join(tmp, "alone.db")
    codes = []
    for _ in range(3):
        codes.append(run(build_cmd(db, root, cfg), cfg).returncode)
    check("three sequential writers all exit 0", codes == [0, 0, 0],
          "exits %s" % codes)
    check("no lock file survives a clean run",
          not os.path.exists(db + ".lock"), db + ".lock")


# -- 8. the barrier is armed where the user walks --------------------------

MUTATORS = {"upsert_node", "upsert_edge", "delete_node", "rebuild_fts",
            "write_fingerprint", "build_edges", "apply_retention",
            "refresh_datelist", "refresh_all_datelists", "prune", "forget"}
MUTATING_CALLS = {"build", "update", "refresh_mesh"}


def _writes(fn):
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in MUTATORS:
            return f.attr
        if isinstance(f, ast.Attribute) and f.attr in MUTATING_CALLS:
            return f.attr
        if isinstance(f, ast.Name) and f.id in MUTATING_CALLS:
            return f.id
    return None


def _guarded(fn):
    for node in ast.walk(fn):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in ("_writing", "_barrier")):
                return True
    return False


def t_every_writer_takes_the_lock():
    """Derived from the AST, not from a list somebody has to remember.

    The failure this catches is a new `cmd_*` that opens a store and writes to
    it without the barrier -- which is the state the whole package was in
    before CP-11, and which no behavioural gate can see because a lone writer
    behaves identically either way.
    """
    src = open(os.path.join(REPO, "homegraph", "cli.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    writers, unguarded = [], []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("cmd_"):
            continue
        why = _writes(fn)
        if why is None:
            continue
        writers.append((fn.name, why))
        if not _guarded(fn):
            unguarded.append("%s (calls %s)" % (fn.name, why))
    # Two gates. The second is empty-truth insurance: "no unguarded writers"
    # is also true of a file with no writers at all, which is what a rename
    # of the command prefix would produce.
    check("the CLI has writers to guard", len(writers) >= 3,
          "found %s" % [n for n, _ in writers])
    check("every CLI writer is inside the barrier", not unguarded,
          "; ".join(unguarded) or "all guarded")


# -- 9. the lock does not outlive an interrupted writer --------------------

def t_lock_is_released_on_interrupt(tmp):
    """CP-8 says an interrupted update commits nothing. A lock file is
    something."""
    db = os.path.join(tmp, "interrupted.db")
    lock = StoreLock(db)
    try:
        with lock:
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    check("KeyboardInterrupt releases the lock",
          not os.path.exists(db + ".lock"), db + ".lock")

    # A refused writer must not free the holder's lock. This one is guarded
    # by the `held` flag before the nonce is ever compared -- said plainly
    # because the first version of this gate claimed to test the nonce and
    # tested the flag, and the mutation that removed the nonce check walked
    # straight through it.
    a = StoreLock(db)
    a.acquire()
    b = StoreLock(db)
    try:
        b.acquire()
        took = True
    except Locked:
        took = False
    b.release()
    check("a refused writer's release does not free the holder's lock",
          not took and os.path.exists(db + ".lock"),
          "took=%s lock_present=%s" % (took, os.path.exists(db + ".lock")))
    a.release()

    # What the nonce is actually for: our lock was cleared as an orphan while
    # we still believed we held it, and a later writer took the file. Ours is
    # gone; theirs must survive our release. Without the nonce check this
    # hands one writer's store to a third.
    mine = StoreLock(db)
    mine.acquire()
    os.unlink(db + ".lock")
    theirs = StoreLock(db)
    theirs.acquire()
    mine.release()
    check("release does not unlink a lock taken by someone else",
          os.path.exists(db + ".lock")
          and json.load(open(db + ".lock"))["nonce"] == theirs.nonce,
          "present=%s" % os.path.exists(db + ".lock"))
    theirs.release()


# -- helpers ---------------------------------------------------------------

def write_lock(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload))


def free_pid():
    """A pid that is not running. Spawn one and reap it, rather than picking
    a large number and hoping."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def main():
    from tests.fixtures import synthetic as syn
    syn.build_once()
    base = os.path.expanduser("~/.homegraph")
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="cp11-", dir=base)
    cfg = syn.CONFIG
    try:
        t_wal_is_in_force(tmp)
        t_second_writer_refuses(tmp, syn.ROOT, cfg)
        t_a_refusal_costs_the_writer_nothing(tmp, syn.ROOT, cfg)
        t_orphan_lock_is_cleared(tmp, syn.ROOT, cfg)
        t_unparseable_lock_is_not_a_live_writer(tmp, syn.ROOT, cfg)
        t_reused_pid_is_not_live(tmp, syn.ROOT, cfg)
        t_reader_is_not_blocked(tmp, syn.ROOT, cfg)
        t_single_writer_never_refuses(tmp, syn.ROOT, cfg)
        t_every_writer_takes_the_lock()
        t_lock_is_released_on_interrupt(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        syn.use_config(syn.CONFIG)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_cp11():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
