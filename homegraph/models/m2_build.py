#!/usr/bin/env python3
"""Build the image graph from names and stat() alone.

Node identity is the path, not a content hash, because a hash requires reading.
That choice propagates: M5 cannot merge two identical images in two folders, so
they stay two nodes joined by LIKELY_COPY. Written down here and in the mesh
model, because a year from now it will look like a deduplication bug.

`no_open_guard()` is the enforcement. Python's audit hook sees every `open()`
the interpreter performs, including ones inside libraries, so an import that
starts reading images fails the build rather than silently weakening the
guarantee. CP-4 runs strace over the same build as an independent check.

**This module reads where the image corpus is; it does not decide it.** The
directories come from `~/.homegraph/config.toml`, the same list that
`[image_boundary]` in exclusions.toml expands to, and the paths handed to
`build()` have already been through `corpus.classify()`. Re-testing the
boundary here would be a second copy of it, which is the mistake that froze
CP-0's negative control at 135 (DECISIONS.md §2) and is exactly the mistake
that becomes easy to make again once the root is configurable. What this file
does with the roots is *name* things -- which collection a path belongs to --
and naming is not enforcing.
"""
from __future__ import annotations

import collections
import contextlib
import os
import sys

from .. import userconfig
from ..temporal import record_observation, refresh_datelist
from .m2_images import FilenameParser, stat_only


def image_roots(config=None, base=None):
    """The configured image directories, absolute, with a trailing separator.

    Resolved on call, not at import: a module-level constant would freeze the
    root at import time, which is what forced the tests to set an environment
    variable before their first import and would make a config change invisible
    until the process restarted.
    """
    cfg = config if config is not None else userconfig.load()
    return list(cfg.role_dirs("image", base=base))


class ImageOpened(RuntimeError):
    """Raised the instant anything opens a file under an image root."""


@contextlib.contextmanager
def no_open_guard(roots=None):
    """Fail loudly if any code path opens a file under any of `roots`.

    An in-process tripwire rather than a code review: the invariant survives
    a future contributor importing Pillow far more reliably than a comment
    saying not to.

    Accepts one directory or several. With no roots at all the guard watches
    nothing and can never fire -- true, and vacuous, so callers that mean "no
    image corpus" should not be running M2 in the first place.
    """
    if roots is None:
        roots = image_roots()
    if isinstance(roots, (str, bytes, os.PathLike)):
        roots = [roots]
    watched = [os.path.abspath(os.fspath(r)) for r in roots]
    opened = []

    def hook(event, args):
        if event != "open":
            return
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            path = os.fspath(target)
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            full = os.path.abspath(path)
            if any(full.startswith(r) for r in watched):
                opened.append(path)
                raise ImageOpened("M2 opened %s -- the one thing it must "
                                  "never do" % path)

    sys.addaudithook(hook)
    try:
        yield opened
    finally:
        pass  # audit hooks cannot be removed; the closure just stops mattering


class ImageBuildReport:
    def __init__(self):
        self.images = 0
        self.skipped_non_image = 0
        self.collections = collections.Counter()
        self.series = collections.Counter()
        self.malformed_dates = []
        self.edges = collections.Counter()
        self.resolutions = collections.Counter()
        self.copies = 0

    def summary(self):
        return {
            "images": self.images,
            "skipped_non_image": self.skipped_non_image,
            "collections": len(self.collections),
            "series": len([s for s, n in self.series.items() if n > 1]),
            "malformed_dates": len(self.malformed_dates),
            "copies": self.copies,
            "edges": dict(self.edges),
        }


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif",
             ".tiff", ".heic", ".heif", ".svg", ".ico", ".avif"}


def collection_of(path, roots):
    """Which collection a path belongs to: its directory relative to its root.

    Naming, not enforcement -- see the module docstring. A path that matches no
    root still gets a name (its parent directory) rather than an exception,
    because refusing here would be this file second-guessing the classifier
    that already accepted the path.
    """
    parent = os.path.dirname(os.path.abspath(path))
    for root in roots:
        root = os.path.abspath(root)
        if parent == root or parent.startswith(root.rstrip("/") + os.sep):
            rel = os.path.relpath(parent, root)
            return os.path.basename(root.rstrip("/")) if rel == "." else rel
    return os.path.basename(parent)


def build(store, paths, as_of, parser=None, report=None, roots=None):
    parser = parser or FilenameParser()
    report = report or ImageBuildReport()
    roots = image_roots() if roots is None else (
        [roots] if isinstance(roots, str) else list(roots))
    infos = []

    for path in paths:
        if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
            # A `.docx` can live inside the image directory and is still a
            # document. Being under the image root is necessary, never
            # sufficient.
            report.skipped_non_image += 1
            continue
        info = parser.parse(path)
        st = stat_only(path)
        infos.append((info, st))

        nid = store.upsert_node(
            path, kind="image", subtype="image", path=path,
            title=os.path.basename(path),
            # Body is the searchable surface: filename, directory and
            # collection. That is the entire text this model will ever have.
            body=" ".join([os.path.basename(path), info.stem,
                           os.path.basename(os.path.dirname(path))]),
            size=st["size"], mtime=st["mtime"], content_hash=None, as_of=as_of)
        record_observation(store, nid, as_of, size=st["size"])
        refresh_datelist(store, nid, as_of)
        report.images += 1
        if info.malformed_date:
            report.malformed_dates.append(path)
        if info.copy:
            report.copies += 1

        coll = collection_of(path, roots)
        report.collections[coll] += 1
        store.upsert_node("collection:%s" % coll, kind="collection",
                          subtype="collection", title=coll, body=coll,
                          as_of=as_of)
        if info.date:
            store.upsert_node("date:%s" % info.date, kind="datebucket",
                              subtype="date", title=info.date, body=info.date,
                              as_of=as_of)
        if info.resolution:
            store.upsert_node("resolution:%s" % info.resolution,
                              kind="resolution", subtype="resolution",
                              title=info.resolution, body=info.resolution,
                              as_of=as_of)
            report.resolutions[info.resolution] += 1
        if info.indices and info.series_stem:
            key = "series:%s/%s" % (coll, info.series_stem)
            report.series[key] += 1
            store.upsert_node(key, kind="series", subtype="series",
                              title=info.series_stem, body=info.series_stem,
                              as_of=as_of)

    for info, _ in infos:
        path = info.path
        coll = collection_of(path, roots)
        store.upsert_edge(path, "collection:%s" % coll, "IN_COLLECTION", as_of)
        report.edges["IN_COLLECTION"] += 1
        if info.date:
            store.upsert_edge(path, "date:%s" % info.date, "NAMED_DATE", as_of)
            report.edges["NAMED_DATE"] += 1
        if info.resolution:
            store.upsert_edge(path, "resolution:%s" % info.resolution,
                              "SAME_RESOLUTION", as_of)
            report.edges["SAME_RESOLUTION"] += 1
        if info.indices and info.series_stem:
            key = "series:%s/%s" % (coll, info.series_stem)
            if report.series[key] > 1:
                store.upsert_edge(path, key, "SERIES_MEMBER", as_of)
                report.edges["SERIES_MEMBER"] += 1

    _link_copies(store, infos, as_of, report)
    store.commit()
    return report


def _link_copies(store, infos, as_of, report):
    """LIKELY_COPY, from names alone.

    Two routes: an explicit `_copy` marker, and the same basename appearing in
    two directories. Both are guesses -- the relation is named LIKELY_COPY and
    not SAME_AS for exactly that reason. Without reading bytes there is no way
    to do better, and pretending otherwise would be the dishonest option.
    """
    by_base = collections.defaultdict(list)
    for info, _ in infos:
        by_base[os.path.basename(info.path)].append(info.path)
    for paths in by_base.values():
        if len(paths) > 1:
            for a, b in zip(sorted(paths), sorted(paths)[1:]):
                store.upsert_edge(a, b, "LIKELY_COPY", as_of)
                report.edges["LIKELY_COPY"] += 1

    by_stripped = collections.defaultdict(list)
    for info, _ in infos:
        if info.copy:
            stripped = info.stem
            for marker in ("_copy", "-copy", "_kopi", " copy", "_copy2"):
                stripped = stripped.replace(marker, "")
            by_stripped[stripped.strip()].append(info.path)
    for info, _ in infos:
        if not info.copy and info.stem.strip() in by_stripped:
            for other in by_stripped[info.stem.strip()]:
                store.upsert_edge(info.path, other, "LIKELY_COPY", as_of)
                report.edges["LIKELY_COPY"] += 1
