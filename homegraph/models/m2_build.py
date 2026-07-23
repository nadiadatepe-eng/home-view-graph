#!/usr/bin/env python3
"""Build the image graph from names and stat() alone.

Node identity is the path, not a content hash, because a hash requires reading.
That choice propagates: M5 cannot merge two identical images in two folders, so
they stay two nodes joined by LIKELY_COPY. Written down here and in the mesh
model, because a year from now it will look like a deduplication bug.

`no_open_guard()` is a VERIFICATION tool, not the enforcement. It used to be
described as the enforcement, and it never was: it is installed by CP-4 and by
nothing in this package, so what keeps a real `homegraph` run from opening an
image is that no line here opens one. An external review named it, correctly,
as a mechanism that owned nothing -- the same shape as an exclusion layer whose
files another layer already caught.

It is not armed in `build()` on purpose. A Python audit hook cannot be removed
once installed, so arming it per build would leave a permanent process-global
tripwire behind in anything long-running -- the MCP server most of all -- where
it would eventually raise on an `open()` that has nothing to do with M2. A
verification tool belongs in the process that verifies.

What CP-4 does with it is real: it runs the actual builder under the hook AND
under strace, and mutation-tests both by making the build open an image. The
guarantee rests on those two independent observations of the real code path,
not on a guard that ships armed.

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
import functools
import os
import sys
import tomllib

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
        # Audit hooks cannot be removed. The comment here used to say "the
        # closure just stops mattering", and it does not: the hook stays
        # installed for the life of the process and keeps raising ImageOpened
        # for ANY open under `watched`, long after this block exits. In a test
        # process that is the intent. It is also precisely why this is not
        # armed by `build()` -- see the module docstring.
        pass


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


@functools.lru_cache(maxsize=1)
def image_extensions(rules_dir=None):
    """`{'.png', '.jpg', ...}` read out of categories.toml, with the dot.

    This used to be a hand-written set beside `build()`, which made this file
    hold a second opinion about what an image is -- in the module whose own
    docstring warns against exactly that, and beside a `scan.py` that already
    did it correctly by reading the rule file.

    The two sets happened to be identical, so nothing was wrong today. The
    failure it invited is silent and one-sided: add an extension to
    categories.toml alone and `classify()` labels the file `image`,
    `corpus_paths(label="image")` hands it to M2, and M2 drops it into
    `skipped_non_image` -- a counter whose comment documents the `.docx` case,
    so the loss reads as intended behaviour in the report.

    Naming versus enforcing still holds: the BOUNDARY (which directories hold
    images) stays in `[image_boundary]` and is not re-tested here. What this
    reads is the category, which is the one thing this module has to agree
    with the classifier about in order to be handed the right files at all.
    """
    from ..corpus import RULES_DIR
    path = os.path.join(rules_dir or RULES_DIR, "categories.toml")
    with open(path, "rb") as fh:
        return frozenset("." + e for e in tomllib.load(fh)["image"]["extensions"])


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
        if os.path.splitext(path)[1].lower() not in image_extensions():
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
