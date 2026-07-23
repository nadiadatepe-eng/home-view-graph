#!/usr/bin/env python3
"""One writer per store. It refuses; it does not queue.

Before this module, `Store.__init__` opened SQLite with `foreign_keys = ON`
and nothing else -- no journal mode, no busy timeout, no write barrier. Two
concurrent `homegraph update` runs against one store were undefined, and
`update` is a single transaction, which makes the damage from a collision
larger rather than smaller.

**Refusing is the design, not a shortcut.** A queue would make a second writer
wait for a first one that may be rebuilding a 588k-file corpus, and the second
writer's caller -- a cron line, a shell loop -- has no way to know it is
waiting rather than working. Exit 2 with the holder's pid is a fact the caller
can act on. This is the intent borrowed from codebase-memory-mcp's admission
barrier; the daemon it hangs off is not borrowed, because homegraph has no
long-running service to coordinate (the MCP server is stdio and read-only,
and there is no watcher).

**A pid is not enough to decide a lock is stale.** Pids are reused. A lock file
left behind by a crash whose number has since been handed to a browser tab
would read as live forever, and the store would be unwritable until someone
deleted the file by hand. So the lock records the holder's boot-relative start
time as well, and a pid that is alive with a *different* start time is a
different process wearing a dead one's number.

**The residual race is closed by verifying after writing, not by locking
harder.** Two processes can both find a stale lock and both unlink it; only one
`O_CREAT|O_EXCL` succeeds, but the loser may have removed the winner's fresh
file. So the writer re-reads the lock it believes it took and checks its own
nonce is the one on disk. Whoever's nonce survives owns the lock; the other
refuses. Without that read-back the window is small, silent, and exactly the
thing this module exists to prevent.
"""
from __future__ import annotations

import json
import os
import secrets
from typing import Any, Callable

# Liveness is decided from /proc when it exists. Elsewhere -- macOS, BSD --
# only `kill(pid, 0)` is available, which cannot tell a reused pid from the
# original. The lock still works there; it is one guarantee weaker, and
# `stale_check` on the refusal says so rather than implying otherwise.
HAVE_PROC = os.path.isdir("/proc/self")


class Locked(RuntimeError):
    """Another live writer holds this store."""

    def __init__(self, path: str, holder: dict[str, Any]) -> None:
        self.path = path
        self.holder = holder
        pid = holder.get("pid", "?")
        since = holder.get("created", "?")
        extra = ""
        if not HAVE_PROC:
            extra = " (no /proc: a reused pid cannot be told from the original)"
        super().__init__(
            "store is being written by pid %s since %s%s" % (pid, since, extra))


def _start_time(pid: int) -> int | None:
    """Boot-relative start time of `pid`, or None if it is not running.

    `comm` is field 2 of /proc/pid/stat and may contain spaces and
    parentheses, so the fixed-width tail is everything after the LAST ')'.
    That tail begins at field 3, which puts starttime (field 22) at index 19.
    Splitting the whole line on whitespace instead is the classic way to read
    the wrong number for any process whose name has a space in it.
    """
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            tail = fh.read().rsplit(b")", 1)[-1].split()
        return int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


def _liveness(holder: dict[str, Any]) -> tuple[bool, str]:
    """(is the holder still running, why we say so).

    The reason is returned rather than reconstructed by the caller because
    the three ways a lock goes stale are not interchangeable, and a message
    that says "no such process" about a pid that is plainly running sends the
    reader looking for the wrong bug. That is the message this returned
    before the CP-11 run that caught it.
    """
    try:
        pid = int(holder["pid"])
    except (KeyError, TypeError, ValueError):
        # A lock file we cannot parse is not evidence of a live writer. It is
        # evidence of a bug or a truncated write, and treating it as live
        # would make the store permanently unwritable.
        return False, "unreadable lock file"
    if pid <= 0:
        return False, "lock file names pid %s" % pid
    if not HAVE_PROC:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False, "no such process"
        except PermissionError:
            # Running, owned by someone else. Still a live process.
            return True, "running"
        return True, "running"
    now = _start_time(pid)
    if now is None:
        return False, "no such process"
    recorded = holder.get("start")
    if recorded is None:
        # Written by a build without /proc. The pid is alive; we cannot prove
        # it is the same process, and guessing "stale" here would let a second
        # writer in. Err toward refusing.
        return True, "running (start time was not recorded)"
    if now == recorded:
        return True, "running"
    return False, ("pid %d is running but started later than the lock: the "
                   "number was reused" % pid)


def _read(path: str) -> dict[str, Any]:
    """The lock file as a dict; `{}` when there is nothing usable there.

    It returns `{}` rather than `None` on purpose. With `None`, callers had to
    write `holder is not None and live`, which put the "is there a live
    holder" decision in two places -- the None check for a file that will not
    parse, `_liveness` for one that parses without a pid. CP-11's mutation of
    the second branch survived because the first one answered first, so the
    gate that claimed to test unparseable locks tested nothing. One decision,
    in `_liveness`, and every path reaches it.
    """
    try:
        with open(path, "rb") as fh:
            loaded = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class StoreLock:
    """Context manager holding the write barrier for one store path.

    Readers do not take this. `status`, `search`, `explain` and the MCP server
    answer while a writer holds the lock -- WAL exists so they can.
    """

    def __init__(self, store_path: str, fingerprint: str | None = None,
                 on_stale: Callable[[str], None] | None = None) -> None:
        self.store_path = str(store_path)
        self.path = self.store_path + ".lock"
        self.fingerprint = fingerprint
        # Called with a message when an orphan is cleared. Clearing a lock is
        # a thing the user should be told about, not a thing that happens.
        self.on_stale = on_stale
        self.nonce = secrets.token_hex(8)
        self.held = False

    # -- acquisition ------------------------------------------------------

    def _payload(self) -> bytes:
        from datetime import datetime
        pid = os.getpid()
        return json.dumps({
            "pid": pid,
            "start": _start_time(pid) if HAVE_PROC else None,
            "created": datetime.now().isoformat(timespec="seconds"),
            "nonce": self.nonce,
            "fingerprint": self.fingerprint,
            "store": self.store_path,
        }).encode("utf-8")

    def _create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        try:
            os.write(fd, self._payload())
            os.fsync(fd)
        finally:
            os.close(fd)
        return True

    def acquire(self) -> "StoreLock":
        # Two attempts, not a loop: one to take a free lock, one more after
        # clearing an orphan. An unbounded retry against a live writer is the
        # queue this module refuses to be.
        for attempt in (0, 1):
            if self._create():
                # Read back. If another process cleared our file as an orphan
                # and wrote its own, the nonce on disk is not ours and we lost.
                held = _read(self.path)
                if held.get("nonce") == self.nonce:
                    self.held = True
                    return self
                raise Locked(self.store_path, held)

            holder = _read(self.path)
            live, why = _liveness(holder)
            if live:
                raise Locked(self.store_path, holder)
            if attempt == 1:
                # Cleared once already and it is back: someone is racing us
                # for it and won. Refuse rather than clear a second time.
                raise Locked(self.store_path, holder)
            if self.on_stale is not None:
                self.on_stale("cleared a stale lock left by pid %s (%s)"
                              % (holder.get("pid", "?"), why))
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
        raise AssertionError("unreachable")

    # -- release ----------------------------------------------------------

    def release(self) -> None:
        """Drop the lock, but only if it is still ours.

        Unlinking unconditionally would delete a lock a later writer took
        after ours was cleared as an orphan, which turns one stuck run into
        two concurrent ones.
        """
        if not self.held:
            return
        self.held = False
        holder = _read(self.path)
        if holder.get("nonce") != self.nonce:
            # Not ours any more: ours was cleared as an orphan and a later
            # writer took the file. Unlinking here would hand that writer's
            # store to a third one.
            return
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> "StoreLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        # Releases on KeyboardInterrupt too, which CP-8's interrupt gate
        # depends on: an interrupted update must leave nothing behind, and a
        # lock file is something.
        self.release()
