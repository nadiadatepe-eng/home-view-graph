#!/usr/bin/env python3
"""Look at a directory and guess what its top-level folders are for.

This runs **once**, during `homegraph init`, and its output is a proposal the
user accepts or edits. It never runs during a build.

That restriction is the whole design. A scanner that kept running would be a
second opinion about what files are, and `corpus.classify()` is the only one
this system is allowed to have -- two classifiers that drift apart produce a
graph whose partition quietly stops holding (DECISIONS.md §1 and §2). So this
module answers exactly one question, "which directories look like they hold
images / documents / notes / code / cache", writes the answer down, and gets
out of the way.

**It reads names and `stat()`, never contents.** The same invariant M2 carries,
for the same reason: `init` walks the image directory before anything has
promised not to open it, so if the scanner read files the guarantee would be
lost before M2 ever ran. CP-4 verifies this over `init` as well as over the
build, with an audit hook and with strace.

The evidence is the **extension mix inside each directory**, not the
directory's name. A name list would only ever recognise the language its author
speaks -- which is the bug this file exists to remove.

Two name-based judgements survive, and both are stated rather than hidden:

  * Directories named in `[cache].dirs` and `[dependencies].dirs` are pruned.
    Those lists come from the same `exclusions.toml` the classifier reads, so
    this is not a second copy of a rule; it is the same rule, read early. Only
    the parts that need no `{root}` substitution are used, because the
    substitution depends on the config this scan is producing.
  * Hidden top-level directories are skipped. `~/.icons` holding 240 SVG files
    is not a photo library, and `[app_state]` already encodes the judgement
    that dotted directories at the top level are machine state. On the fixture
    this is the difference between proposing the image role correctly and
    proposing an unpacked icon theme.
"""
from __future__ import annotations

import collections
import os
import tomllib
from dataclasses import dataclass, field

from . import userconfig

RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")

# category -> the role a directory dominated by that category gets proposed for.
# `misc` deliberately maps to nothing: the junk drawer is the complement of the
# other categories, so "mostly misc" says a directory is unlike everything else,
# which is not a role.
ROLE_OF_CATEGORY = {
    "image": "image",
    "document": "document",
    "markdown": "note",
    "code": "code",
}

# A directory has to be both mostly-one-thing and big enough to mean it. Both
# numbers are thresholds on evidence, and both can reject: a folder holding one
# stray PNG is not the photo library, and a folder that is half code and half
# junk is a folder the user should look at rather than one the tool should
# label. Unassigned is a valid, honest outcome.
MIN_FILES = 3
MIN_SHARE = 0.5


@dataclass
class DirStats:
    name: str
    path: str
    files: int = 0
    pruned: int = 0
    by_category: collections.Counter = field(
        default_factory=collections.Counter)

    @property
    def role(self) -> str | None:
        """The role this directory earns, or None."""
        counted = sum(self.by_category.values())
        if counted < MIN_FILES:
            return None
        category, n = self.by_category.most_common(1)[0]
        if n / counted < MIN_SHARE:
            return None
        return ROLE_OF_CATEGORY.get(category)

    @property
    def mostly_pruned(self) -> bool:
        """Most of what is here sits under a pruned cache or dependency tree.

        Reported, never acted on. An earlier version assigned a `cache` role on
        this and skipped the real one, which meant a directory was named after
        its rubbish rather than its contents: a wiki of 42 markdown files and
        nothing else was proposed as cache because a dependency tree beside it
        outnumbered them. The ratio is a fact about how much a directory
        carries, not about what it is, and it is shown in the `cached` column
        so the reader can weigh it themselves.
        """
        total = self.files + self.pruned
        return total >= MIN_FILES and self.pruned / total >= MIN_SHARE


@dataclass
class Proposal:
    root: str
    dirs: list[DirStats]
    roles: dict[str, list[str]]
    skipped_hidden: list[str]


def _load(name: str) -> dict:
    with open(os.path.join(RULES_DIR, name), "rb") as fh:
        return tomllib.load(fh)


def _extension_map(rules_dir: str | None = None) -> dict[str, str]:
    """extension -> category, straight out of categories.toml.

    Read from the rule file rather than restated here. The list of image
    extensions existing in two places is exactly the shape of defect this
    project keeps finding, and a scanner that disagreed with the classifier
    about what a `.webp` is would propose a photo directory the classifier then
    refused to fill.
    """
    cat = (_load("categories.toml") if rules_dir is None
           else tomllib.load(open(os.path.join(rules_dir,
                                               "categories.toml"), "rb")))
    out: dict[str, str] = {}
    for ext in cat["image"]["extensions"]:
        out[ext] = "image"
    for ext in cat["markdown"]["extensions"]:
        out[ext] = "markdown"
    for ext in cat["document"]["doctypes"]:
        out[ext] = "document"
    for ext in cat["code"]["extensions"]:
        out[ext] = "code"
    return out


def _pruned_dirs() -> set[str]:
    ex = _load("exclusions.toml")
    return set(ex["cache"]["dirs"]) | set(ex["dependencies"]["dirs"])


def _ext_of(basename: str) -> str:
    """Same rule as corpus.ext_of: a leading dot is not a separator."""
    if "." not in basename[1:]:
        return ""
    return basename.rsplit(".", 1)[1].lower()


def scan(root: str, ext_map: dict[str, str] | None = None) -> Proposal:
    """Walk `root` one level down and tally each top-level directory.

    Never opens a file. `os.scandir` yields the type from the directory entry
    itself on Linux, so the common case costs no `stat()` either -- and where
    it does fall back to one, `stat()` is the most this is allowed to do.
    """
    root = os.path.abspath(os.path.expanduser(root)).rstrip("/") or "/"
    ext_map = _extension_map() if ext_map is None else ext_map
    pruned = _pruned_dirs()

    stats: list[DirStats] = []
    hidden: list[str] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return Proposal(root=root, dirs=[], roles={r: [] for r in
                                                   ROLE_OF_CATEGORY.values()},
                        skipped_hidden=[])

    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if entry.name.startswith("."):
            hidden.append(entry.name)
            continue
        stats.append(_tally(entry.name, entry.path, ext_map, pruned))

    roles: dict[str, list[str]] = {r: [] for r in userconfig.ROLES}
    for st in stats:
        role = st.role
        if role:
            roles[role].append(st.name)
    return Proposal(root=root, dirs=stats, roles=roles, skipped_hidden=hidden)


def _tally(name: str, path: str, ext_map: dict[str, str],
           pruned: set[str]) -> DirStats:
    st = DirStats(name=name, path=path)
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        keep = [d for d in dirnames if d not in pruned]
        # Files under a pruned directory are counted before the prune so that
        # "this folder is mostly cache" stays visible. Dropping them silently
        # would make a browser profile look like a small tidy directory.
        st.pruned += sum(
            _count_files(os.path.join(dirpath, d))
            for d in dirnames if d in pruned)
        dirnames[:] = keep
        for fname in filenames:
            st.files += 1
            category = ext_map.get(_ext_of(fname))
            if category:
                st.by_category[category] += 1
    return st


def _count_files(path: str) -> int:
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        total += len(filenames)
    return total


def format_proposal(prop: Proposal) -> str:
    """The table `init` prints. Shows the evidence, not just the verdict."""
    lines = ["root: %s" % prop.root, ""]
    lines.append("%-28s %8s %8s  %s" % ("directory", "files", "cached",
                                        "looks like"))
    for st in prop.dirs:
        counted = sum(st.by_category.values())
        mix = ", ".join("%s %d" % (k, v)
                        for k, v in st.by_category.most_common(3)) or "-"
        # The pruned ratio is a note beside the verdict, not the verdict. A
        # directory that is mostly dependency tree still gets the role its own
        # files earn.
        verdict = st.role or "-"
        note = "  (mostly pruned)" if st.mostly_pruned else ""
        lines.append("%-28s %8d %8d  %-9s (%s of %d classified)%s"
                     % (st.name[:28], st.files, st.pruned, verdict, mix,
                        counted, note))
    if prop.skipped_hidden:
        lines.append("")
        lines.append("skipped %d hidden director%s: %s"
                     % (len(prop.skipped_hidden),
                        "y" if len(prop.skipped_hidden) == 1 else "ies",
                        ", ".join(prop.skipped_hidden[:8])))
    lines.append("")
    lines.append("proposed roles")
    for role in userconfig.ROLES:
        got = prop.roles.get(role) or []
        lines.append("  %-8s %s" % (role, ", ".join(got) or "(none)"))
    if not prop.roles.get("image"):
        lines.append("")
        lines.append("  No image directory found. M2 will be absent and mesh "
                     "will label its answers `partial`.")
    return "\n".join(lines)
