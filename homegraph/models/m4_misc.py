#!/usr/bin/env python3
"""M4 -- everything else. Defined as the complement, and dangerous for it.

M4 inherits every classification mistake in the system. A gap in M1-M3 does not
raise; it quietly becomes junk here, and a junk drawer nobody searches looks
identical to a junk drawer that is correctly empty. CP-5's cross-validation is
the only thing that distinguishes them.

Type detection is by content, not extension: 250 of these 1 686 files have no
extension at all. The sniffing reads a 512-byte header and nothing more --
enough for a magic number, far short of loading a 751 MB CUDA library.

Two rules that exist for safety rather than tidiness:

  * **SQLite files give up their schema, never their rows.** A browser profile
    or a session database will happily hand over credentials if you SELECT from
    it. Table names are informative; contents are not worth the risk.
  * **An archive gives up its table of contents, never its contents.**
    `archive_entries()` is the one place in this module that reads past the
    512-byte header, and it is worth stating plainly rather than leaving in
    the small print: listing a zip reads its central directory, which lives at
    the END of the file. No member is opened, nothing is decompressed, and
    nothing is written outside the store -- so a zip bomb is a list of names
    here, not an unpacked filesystem.
  * **Files over 100 MB become metadata nodes.** Nothing is read past the
    header -- and "the header" is the literal bound: `sniff()` reads at most
    512 bytes, of any file, at any size. The first pass skips `sniff` entirely
    for large files; the second pass calls it on every path, large ones
    included, to draw SAME_FORMAT edges. So a 4 GB disk image is opened and
    512 bytes are read. That is within the promise but not within the picture
    the promise paints, which is why it is written down rather than left to
    the reader to discover in the second loop.

And the rollup, borrowed from the handbook's reduced fact tables: anything that
has not changed in 90 days is aggregated to (app, month, count, bytes, dominant
type) instead of one node per file. Tens of thousands of nodes nobody will ever
search are not a knowledge graph, they are ballast.

"Application state" is how this was phrased, and it is narrower than what the
code does: `owning_app()` returns "home" for everything outside a dot-directory,
so cold files anywhere in the corpus roll up under that name. The age is the
criterion; the app is only the bucket.
"""
from __future__ import annotations

import collections
import functools
import os
import re
import sqlite3
import time
import zipfile
from datetime import date, timedelta

from ..config import home_root
from ..temporal import record_observation, refresh_datelist

HEADER_BYTES = 512
LARGE_FILE = 100 * 1024 * 1024
ROLLUP_AFTER_DAYS = 90
# One level, and a bounded one. A source tarball has tens of thousands of
# members and one top-level directory; enumerating all of them would bury the
# fact worth keeping, which is what the archive is.
MAX_ARCHIVE_ENTRIES = 200
# Above this, the table of contents is not read at all. The cost of listing is
# a seek to the end of the file rather than a full read, so the limit is
# generous -- but a multi-gigabyte archive on a spinning disk is still a stall,
# and a build that stalls gets killed and reports nothing.
ARCHIVE_MAX_BYTES = 512 * 1024 * 1024
# Zip containers whose contents are somebody else's build output, not the
# user's filing. A Thunderbird language pack is a zip by magic number and by
# every technical measure, and listing it is correct and useless: on the real
# corpus 12 of 78 ARCHIVE_CONTAINS edges came from `.xpi`, naming `chrome/`
# and `manifest.json` in extensions nobody filed there by hand.
#
# **A policy list, not a fourth copy of an extension list.** Nothing else in
# the package decides anything by these names, so there is no second opinion
# to drift from -- which is the distinction that matters in a package that has
# already had to delete three duplicated extension lists. The exclusion is
# counted rather than silent: an archive that was skipped on purpose and one
# that could not be opened are different facts.
UNLISTED_ARCHIVES = frozenset({".xpi"})

# Magic numbers, checked in order. Deliberately small: this is a triage step,
# not a format library. Anything unrecognised is `unknown`, which is an honest
# answer and a countable one.
MAGIC = [
    (b"SQLite format 3\x00", "database", "sqlite"),
    (b"\x7fELF", "binary", "elf"),
    (b"PK\x03\x04", "archive", "zip"),
    (b"\x1f\x8b", "archive", "gzip"),
    (b"BZh", "archive", "bzip2"),
    (b"\xfd7zXZ\x00", "archive", "xz"),
    (b"7z\xbc\xaf\x27\x1c", "archive", "7z"),
    (b"%PDF", "document", "pdf"),
    (b"\x89PNG", "media", "png"),
    (b"\xff\xd8\xff", "media", "jpeg"),
    (b"GIF8", "media", "gif"),
    (b"RIFF", "media", "riff"),
    (b"OggS", "media", "ogg"),
    (b"ID3", "media", "mp3"),
    (b"\x00\x01\x00\x00\x00", "media", "truetype"),
    (b"OTTO", "media", "opentype"),
    (b"wOFF", "media", "woff"),
    (b"\xca\xfe\xba\xbe", "binary", "java-class"),
    (b"!<arch>", "archive", "ar"),
]

EXT_HINTS = {
    ".json": ("config", "json"), ".jsonl": ("log", "jsonl"),
    ".yaml": ("config", "yaml"), ".yml": ("config", "yaml"),
    ".toml": ("config", "toml"), ".ini": ("config", "ini"),
    ".conf": ("config", "conf"), ".cfg": ("config", "conf"),
    ".proto": ("config", "protobuf"), ".log": ("log", "log"),
    ".txt": ("text", "text"), ".csv": ("text", "csv"),
    ".html": ("text", "html"), ".css": ("text", "css"),
    ".db": ("database", "sqlite"), ".sqlite": ("database", "sqlite"),
    ".sqlite3": ("database", "sqlite"),
}

# Built against the resolved root rather than a literal, and cached so the
# regexes are compiled once per root instead of once per file.
@functools.lru_cache(maxsize=8)
def _app_roots(root):
    esc = re.escape(root)
    return (re.compile(esc + r"/\.local/lib/([^/]+)/"),
            re.compile(esc + r"/\.([a-zA-Z0-9_-]+)/"),
            re.compile(esc + r"/([a-zA-Z0-9_-]+)/"))


def owning_app(path, root=None):
    for pattern in _app_roots(home_root(root)):
        m = pattern.match(path)
        if m:
            return m.group(1)
    return "home"


def sniff(path, size=None):
    """(subtype, detected_type). Reads at most HEADER_BYTES."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEADER_BYTES)
    except OSError:
        return "unknown", "unreadable"

    for magic, subtype, detected in MAGIC:
        if head.startswith(magic):
            return subtype, detected
    if ext in EXT_HINTS:
        return EXT_HINTS[ext]
    if not head:
        return "unknown", "empty"
    printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13))
    if printable / len(head) > 0.9:
        return "text", "text"
    return "binary", "unknown"


def sqlite_schema(path):
    """Table names only. Never a row.

    Opened read-only through a URI so a build cannot modify a live database it
    happens to walk past.
    """
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=1)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name LIMIT 200").fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def archive_entries(path, limit=MAX_ARCHIVE_ENTRIES):
    """Top-level names inside a zip, or None if it cannot be listed.

    `None` and `[]` are different answers and both are kept: an empty zip is a
    fact, an unlistable one is a defect, and collapsing them would let a run
    where every archive is corrupt report the same shape as a run where every
    archive is empty.

    Only zip. Gzip and xz carry no member list at all -- a `.tar.gz` would have
    to be decompressed to be enumerated, which is the read this module refuses
    to do -- so they are archives with no contents to state, and they get no
    edges rather than guessed ones. That limit is narrow and deliberate; the
    alternative is streaming a compressed tar through memory to learn some
    filenames.

    Encrypted members still list: a zip encrypts contents, not its directory.
    Nothing here decrypts anything, because nothing here opens a member.
    """
    names = []
    seen = set()
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                # The first component, with a trailing slash when it is a
                # directory -- "one level" has to distinguish `docs/` holding
                # forty files from a file that happens to be called `docs`.
                head, sep, _ = name.lstrip("/").partition("/")
                if not head:
                    continue
                entry = head + ("/" if sep else "")
                if entry in seen:
                    continue
                seen.add(entry)
                names.append(entry)
                if len(names) >= limit:
                    break
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        # RuntimeError and ValueError are here for the same reason the rest of
        # this module catches broadly: a malformed archive in a download
        # directory must not take down a build over 1 686 other files.
        return None
    return names


class MiscBuildReport:
    def __init__(self):
        self.files = 0
        self.individual_nodes = 0
        self.rolled_up_files = 0
        self.rollup_nodes = 0
        self.subtypes = collections.Counter()
        self.detected = collections.Counter()
        self.apps = collections.Counter()
        self.large_files = []
        self.sqlite_files = 0
        self.archives = 0
        self.archive_entries = 0
        self.unlistable_archives = []
        self.unlisted_by_policy = []
        self.edges = collections.Counter()

    def summary(self):
        return {
            "files": self.files,
            "individual_nodes": self.individual_nodes,
            "rolled_up_files": self.rolled_up_files,
            "rollup_nodes": self.rollup_nodes,
            "total_nodes": self.individual_nodes + self.rollup_nodes,
            "subtypes": dict(self.subtypes),
            "apps": len(self.apps),
            "large_files": len(self.large_files),
            "sqlite_files": self.sqlite_files,
            "archives": self.archives,
            "archive_entries": self.archive_entries,
            "unlistable_archives": len(self.unlistable_archives),
            "unlisted_by_policy": len(self.unlisted_by_policy),
            "edges": dict(self.edges),
        }


def build(store, paths, as_of, report=None, rollup_after=ROLLUP_AFTER_DAYS):
    report = report or MiscBuildReport()
    as_of_date = date.fromisoformat(as_of)
    cutoff = time.mktime(
        (as_of_date - timedelta(days=rollup_after)).timetuple())
    rollup = collections.defaultdict(
        lambda: {"count": 0, "bytes": 0, "types": collections.Counter()})

    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        report.files += 1
        app = owning_app(path)
        report.apps[app] += 1

        if st.st_size > LARGE_FILE:
            subtype, detected = "binary", "large"
            report.large_files.append((path, st.st_size))
        else:
            subtype, detected = sniff(path, st.st_size)
        report.subtypes[subtype] += 1
        report.detected[detected] += 1

        if st.st_mtime < cutoff:
            # Cold application state: aggregated, not enumerated.
            month = date.fromtimestamp(st.st_mtime).strftime("%Y-%m")
            slot = rollup[(app, month)]
            slot["count"] += 1
            slot["bytes"] += st.st_size
            slot["types"][detected] += 1
            report.rolled_up_files += 1
            continue

        body = os.path.basename(path)
        # No size conjunct: this branch is the `else` of `st.st_size >
        # LARGE_FILE` above, so the limit is already settled. Repeating it
        # here read as a second, independent guard and was neither.
        if detected == "sqlite":
            tables = sqlite_schema(path)
            report.sqlite_files += 1
            if tables:
                body += " | tables: " + ", ".join(tables[:40])

        nid = store.upsert_node(
            path, kind="file", subtype=subtype, path=path,
            title=os.path.basename(path), body=body,
            size=st.st_size, mtime=st.st_mtime, as_of=as_of)
        record_observation(store, nid, as_of, size=st.st_size)
        refresh_datelist(store, nid, as_of)
        report.individual_nodes += 1

        store.upsert_node("app:%s" % app, kind="app", subtype="app",
                          title=app, body=app, as_of=as_of)
        store.upsert_node("format:%s" % detected, kind="format",
                          subtype="format", title=detected, body=detected,
                          as_of=as_of)

    for (app, month), slot in sorted(rollup.items()):
        dominant = slot["types"].most_common(1)[0][0]
        key = "rollup:%s:%s" % (app, month)
        store.upsert_node(
            key, kind="rollup", subtype=dominant, path=None,
            title="%s %s" % (app, month),
            body="%s %s: %d files, %d bytes, mostly %s"
                 % (app, month, slot["count"], slot["bytes"], dominant),
            size=slot["bytes"], as_of=as_of)
        store.upsert_node("app:%s" % app, kind="app", subtype="app",
                          title=app, body=app, as_of=as_of)
        report.rollup_nodes += 1

    for path in paths:
        if store.node_id(path) is None:
            continue
        app = owning_app(path)
        store.upsert_edge(path, "app:%s" % app, "BELONGS_TO_APP", as_of, method="exact")
        report.edges["BELONGS_TO_APP"] += 1
        detected = None
        try:
            detected = sniff(path)[1]
        except OSError:
            pass
        if detected and store.node_id("format:%s" % detected):
            store.upsert_edge(path, "format:%s" % detected, "SAME_FORMAT",
                              as_of, method="exact")
            report.edges["SAME_FORMAT"] += 1
        if detected == "zip":
            _archive_contains(store, path, as_of, report)

    for (app, month) in sorted(rollup):
        store.upsert_edge("rollup:%s:%s" % (app, month), "app:%s" % app,
                          "BELONGS_TO_APP", as_of, method="exact")
        report.edges["BELONGS_TO_APP"] += 1

    return report


def _archive_contains(store, path, as_of, report):
    """ARCHIVE_CONTAINS: an archive and the top-level names it declares.

    `method="exact"` (1.0), and that is the whole reason this relation is
    allowed to exist at all: the archive's own directory states the names, so
    nothing is inferred. Every other candidate relation here -- what is INSIDE
    those members, which project a tarball came from -- would be a guess, and
    the confidence scale exists so that guesses are marked rather than
    avoided. This one is not a guess.

    An entry is a node, not a string on the archive. It has no `path`: nothing
    at `bundle.zip/notes.md` exists on the filesystem, and giving it one would
    put a path into the graph that no `stat()` can confirm and that the mesh
    would happily mirror as a file.

    A rolled-up archive gets nothing, silently. Cold files have no individual
    node, so there is no endpoint to attach to -- the same reason SAME_FORMAT
    skips them, and visible in the report as archives < zip files.
    """
    if os.path.splitext(path)[1].lower() in UNLISTED_ARCHIVES:
        report.unlisted_by_policy.append(path)
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size > ARCHIVE_MAX_BYTES:
        report.unlistable_archives.append((path, "over %d bytes"
                                           % ARCHIVE_MAX_BYTES))
        return
    entries = archive_entries(path)
    if entries is None:
        report.unlistable_archives.append((path, "not listable"))
        return
    report.archives += 1
    for entry in entries:
        key = "archive:%s!%s" % (path, entry)
        store.upsert_node(key, kind="archive_entry", subtype="entry",
                          path=None, title=entry,
                          body="%s in %s" % (entry, os.path.basename(path)),
                          as_of=as_of)
        store.upsert_edge(path, key, "ARCHIVE_CONTAINS", as_of, method="exact")
        report.edges["ARCHIVE_CONTAINS"] += 1
        report.archive_entries += 1
