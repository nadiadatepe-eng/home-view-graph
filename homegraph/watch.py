"""Foreground watch: re-run `update` when the corpus changes on disk.

The borrowed idea (measured against colbymchenry/codegraph, 2026-07-23) is
*react to OS filesystem events instead of polling*. What is deliberately NOT
borrowed is codegraph's daemon: homegraph has no long-running services, and a
watch that survives its terminal would be exactly that. So this is opt-in and
non-persistent -- inotify for as long as `homegraph watch` runs, nothing after
Ctrl-C. No autostart, no pidfile, no config entry.

The module is split so the part that decides *whether a change is worth an
update* is pure and testable, and the part that talks to the kernel is thin:

    relevant()   -- the self-trigger guard: an update writes the stores, and
                    an update must not trigger itself. Pure.
    watch_loop() -- debounce/coalesce policy over an injected event source.
                    Pure of inotify; a test drives it with a scripted source.
    Inotify      -- the real event source, ctypes over libc inotify. Linux
                    only, which is all this machine is; unavailable elsewhere
                    is reported, not crashed.

`watch_loop` never writes anything itself. The only writer is the `trigger`
callback, which in production is the same `update` the user would run by hand
-- the watch is a trigger, not a second opinion about what changed. `update`
re-walks the tree and re-derives the diff every time, so a remembered list of
changed paths (a claim about a tree that has since moved) is never built here.
"""
from __future__ import annotations

import ctypes
import os
import select
import struct
from collections.abc import Callable, Iterable

Prune = Callable[[str], bool]


def relevant(path: str, ignore: Iterable[str]) -> bool:
    """True if `path` is a corpus change worth an update.

    False for the stores an update writes -- the store file itself and its
    SQLite `-wal` / `-shm` / `-journal` siblings. Without this guard the first
    update's own writes land back in the watch as fresh events and it updates
    forever. This is the one gate in the module that owns a correctness claim
    rather than an efficiency one.
    """
    p = os.path.abspath(path)
    for db in ignore:
        if p == db or p.startswith(db + "-"):
            return False
    return True


def _any_relevant(batch: list[tuple[str, int]], ignore: Iterable[str]) -> bool:
    return any(relevant(path, ignore) for path, _mask in batch)


class Source:
    """What `watch_loop` needs from an event source.

    `read(timeout)` returns the events available within `timeout` seconds as a
    list of (path, mask); an empty list means the timeout elapsed with nothing.
    `timeout=None` blocks until at least one event arrives. A KeyboardInterrupt
    raised out of `read` is how a stop propagates -- the loop does not catch it.
    """

    def read(self, timeout: float | None) -> list[tuple[str, int]]:
        raise NotImplementedError


def watch_loop(source: Source, trigger: Callable[[], None], *,
               ignore: Iterable[str], debounce: float) -> None:
    """Call `trigger` once per settled burst of relevant filesystem changes.

    A burst begins on the first relevant event and ends once `debounce`
    seconds pass with no further relevant event. Irrelevant events (the store
    writes from `ignore`) neither begin nor extend a burst, which is what stops
    an update from triggering itself: after `trigger` runs and writes the
    stores, those writes arrive as the next batch, fail the relevance guard,
    and are skipped rather than starting a second update.

    Runs until `source.read` raises (KeyboardInterrupt in production). Writes
    nothing itself; `trigger` is the only thing that touches disk.
    """
    ignore = list(ignore)
    while True:
        batch = source.read(None)
        if not _any_relevant(batch, ignore):
            continue
        # Coalesce: keep waiting until a full debounce window is quiet of
        # relevant events, so one save that touches five files is one update.
        while _any_relevant(source.read(debounce), ignore):
            pass
        trigger()


# --------------------------------------------------------------------------
# The real event source: ctypes over libc inotify. Linux only.
# --------------------------------------------------------------------------

IN_MODIFY = 0x00000002
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000

# What we ask the kernel to report. Creation, deletion, moves, and completed
# writes (IN_CLOSE_WRITE, not every IN_MODIFY mid-write) -- enough to know the
# tree moved without a flood of partial-write events. IN_DELETE_SELF /
# IN_MOVE_SELF let us drop a watch whose directory is gone.
_WATCH_MASK = (IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_FROM
               | IN_MOVED_TO | IN_CLOSE_WRITE | IN_DELETE_SELF | IN_MOVE_SELF)

# struct inotify_event: int wd; uint32 mask, cookie, len; then `len` name bytes.
_HDR = struct.Struct("iIII")


class InotifyUnavailable(RuntimeError):
    """inotify could not be initialised -- not Linux, or the fd limit is hit."""


class Inotify(Source):
    def __init__(self) -> None:
        try:
            self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
            self._libc.inotify_init1.argtypes = [ctypes.c_int]
            self._libc.inotify_init1.restype = ctypes.c_int
            self._libc.inotify_add_watch.argtypes = [
                ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            self._libc.inotify_add_watch.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise InotifyUnavailable("no libc inotify on this platform: %s"
                                     % exc) from exc
        self._fd = self._libc.inotify_init1(0)
        if self._fd < 0:
            raise InotifyUnavailable(
                "inotify_init1 failed (errno %d)" % ctypes.get_errno())
        self._wd2path: dict[int, str] = {}
        self._prune: Prune | None = None

    def add_tree(self, root: str, prune: Prune | None = None) -> None:
        """Watch `root` and every directory under it, `prune` permitting.

        A watch is per-directory; recursion is ours to maintain. `prune(dir)`
        returning True excludes that directory and everything under it -- on a
        real home this is what keeps the watch off `.cache`, `.venv`, `.git`
        and the rest, which hold ~50k directories of churn the corpus already
        excludes and would otherwise arm an inotify watch each and fire a
        pointless full update on every write. The predicate is injected rather
        than reimplemented here so the exclusion rule lives in exactly one
        place (the corpus classifier), not two.

        New subdirectories created at runtime are pruned by the same predicate
        (see `read`). Directories that cannot be watched (permissions, a race
        with deletion) are skipped rather than aborting the walk -- an
        unwatchable corner is a gap in coverage, not a reason to refuse the
        whole tree.
        """
        self._prune = prune
        self._walk_add(root, prune)

    def _walk_add(self, root: str, prune: Prune | None) -> None:
        self._add_one(root)
        for dirpath, dirnames, _files in os.walk(root):
            kept = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if prune is not None and prune(full):
                    continue
                self._add_one(full)
                kept.append(d)
            # Mutating dirnames stops os.walk descending into pruned trees, so
            # a pruned directory costs one predicate call, not a walk of all
            # its contents.
            dirnames[:] = kept

    def _add_one(self, path: str) -> None:
        wd = self._libc.inotify_add_watch(
            self._fd, os.fsencode(path), _WATCH_MASK)
        if wd >= 0:
            self._wd2path[wd] = path

    def read(self, timeout: float | None) -> list[tuple[str, int]]:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return []
        buf = os.read(self._fd, 65536)
        events: list[tuple[str, int]] = []
        i = 0
        while i < len(buf):
            wd, mask, _cookie, length = _HDR.unpack_from(buf, i)
            i += _HDR.size
            name = buf[i:i + length].split(b"\0", 1)[0]
            i += length
            base = self._wd2path.get(wd)
            if base is None:
                continue
            path = os.path.join(base, os.fsdecode(name)) if name else base
            if mask & (IN_CREATE | IN_MOVED_TO) and mask & IN_ISDIR:
                # A directory just appeared; watch it before its contents
                # start changing, or the first files created inside it are
                # missed -- unless the same prune rule that shaped the initial
                # walk excludes it.
                if self._prune is None or not self._prune(path):
                    self._walk_add(path, self._prune)
            if mask & (IN_IGNORED | IN_DELETE_SELF):
                self._wd2path.pop(wd, None)
                continue
            events.append((path, mask))
        return events

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1
