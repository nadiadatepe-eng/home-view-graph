#!/usr/bin/env python3
"""Node keys without a root. The one place a path is made portable.

A node key IS the path in this system -- `upsert_node(path, ...)` -- so making
a graph portable is rewriting the identity of every file node without losing a
single edge. That is the whole of TODO-E1, and it lives here alone: two copies
of "what is root-relative here" is the failure this package has now found four
times, most recently as three hand-written copies of an extension list.

The portable form marks the export root with `~`. It is **not** a home
directory and is never expanded: the root is whatever the user pointed
`homegraph init` at -- a home directory, one project, or a disk full of them --
and a converter that assumed `$HOME` would work on exactly one of those three.

Keys come in three shapes, and only the first two carry anything:

    /abs/path[#offset]          a file, or a section of one
    <prefix>:<...>/abs/path...  a path wrapped in a prefix: `archive:` puts it
                                before a `!`, `m3::`/`code::` after a `::`
    <prefix>:<name>             no path at all: author, ref, wikilink, app,
                                format, rollup

**A path outside the root is refused, never emitted.** The tempting fallback is
`../../elsewhere/file.md`, which imports into a directory the user did not
choose and did not name -- an artifact that writes outside its own root is a
different problem from a graph that is missing a node.
"""
from __future__ import annotations

import os

MARKER = "~"

# The two wrappers, and where the path sits inside each. Read off the code that
# writes the keys -- `m4_misc._archive_contains` and `mesh._mirror_code` /
# `Mesh.build_edges` -- rather than inferred from the data, so a new wrapper
# has to be added here deliberately instead of being silently mangled.
#
# `archive:` is a PREFIX with the path immediately after it, ending at the
# first `!`. `::` is a SEPARATOR after a model name, with the path running to
# the end. They are handled separately because they nest: `m3::/p/f.md#0` is a
# model prefix around a path around an offset.
ARCHIVE_PREFIX = "archive:"
MODEL_SEP = "::"


class OutsideRoot(ValueError):
    """A path that is not under the export root. Refused, not rewritten."""


def _under(path: str, root: str) -> str:
    """`path` relative to `root`, or raise. Compares path components.

    `/rootless/x` starts with the characters of `/root` and is not inside it.
    A string prefix test says otherwise, which is how an export ends up
    carrying a neighbour directory's files under a root that never held them.
    """
    root = os.path.normpath(root)
    norm = os.path.normpath(path)
    if norm == root:
        return ""
    prefix = root if root.endswith(os.sep) else root + os.sep
    if not norm.startswith(prefix):
        raise OutsideRoot("%s is not under %s" % (path, root))
    return norm[len(prefix):]


def path_to_portable(path: str, root: str) -> str:
    rel = _under(path, root)
    return MARKER if not rel else "%s/%s" % (MARKER, rel)


def path_to_local(portable: str, root: str) -> str:
    if portable == MARKER:
        return os.path.normpath(root)
    if not portable.startswith(MARKER + "/"):
        raise OutsideRoot("%r does not start with the root marker" % portable)
    return os.path.normpath(os.path.join(root, portable[len(MARKER) + 1:]))


def _split_wrapper(key: str):
    """(head, path_part, tail) -- the path inside a key, and what wraps it.

    Returns `(None, None, None)` when the key carries no path at all.

    **The order of the tests is the load-bearing part**, and it is the only
    thing protecting a filename that contains `::`:
    `/root/proj/weird::name.py` is a real filename, and splitting on the first
    `::` would invent the model `/root/proj/weird`. The absolute-path branch
    returns first, so it never gets the chance.
    """
    if key.startswith(ARCHIVE_PREFIX):
        rest = key[len(ARCHIVE_PREFIX):]
        inner, sep, entry = rest.partition("!")
        if inner.startswith("/"):
            return ARCHIVE_PREFIX, inner, (sep + entry) if sep else ""
        return None, None, None
    if key.startswith("/"):
        return "", key, ""
    # `m3::/path`. **The branch ORDER is what protects a filename containing
    # `::`**, not a test here: `/root/proj/weird::name.py` starts with `/` and
    # was already returned above. A `"/" not in head` guard was written here
    # first and was unreachable -- the exact shape DECISIONS.md section 20 is
    # about, added by the same hand that documents it. The invariant lives in
    # the ordering, in one place, where a mutation can reach it.
    head, sep, rest = key.partition(MODEL_SEP)
    if sep and rest.startswith("/"):
        return head + MODEL_SEP, rest, ""
    return None, None, None


def to_portable(key: str, root: str) -> str:
    """A node key with the export root replaced by `~`.

    Raises `OutsideRoot` for a path the root does not contain. Keys with no
    path -- `author:`, `ref:`, `wikilink:`, `app:`, `format:`, `rollup:` --
    come back unchanged, including the ones whose value contains a `/`
    (`ref:doi:10.1234/abcd`), which is why the decision is made by shape and
    not by looking for a slash.
    """
    head, path, tail = _split_wrapper(key)
    if path is None:
        return key
    return "%s%s%s" % (head, path_to_portable(path, root), tail)


def to_local(key: str, root: str) -> str:
    """The inverse. `to_local(to_portable(k, r), r) == k` for every form."""
    head, path, tail = _split_portable(key)
    if path is None:
        return key
    return "%s%s%s" % (head, path_to_local(path, root), tail)


def _split_portable(key: str):
    """`_split_wrapper` for the portable side, where paths start with `~`."""
    if key.startswith(ARCHIVE_PREFIX):
        rest = key[len(ARCHIVE_PREFIX):]
        inner, sep, entry = rest.partition("!")
        if inner.startswith(MARKER):
            return ARCHIVE_PREFIX, inner, (sep + entry) if sep else ""
        return None, None, None
    if key.startswith(MARKER):
        return "", key, ""
    head, sep, rest = key.partition(MODEL_SEP)
    if sep and rest.startswith(MARKER):
        return head + MODEL_SEP, rest, ""
    return None, None, None
