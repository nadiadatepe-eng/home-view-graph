#!/usr/bin/env python3
"""The single source of truth for which category a file belongs to.

Every model calls `classify()`. There is no second opinion anywhere in
homegraph, because two classifiers that drift apart produce a graph whose
partition silently stops holding.

Two properties the rest of the system leans on:

  * The categories are mutually exclusive and exhaustive. `misc` is the
    complement of the other four, which makes a gap in them invisible -- it
    turns into junk in M4 rather than an error. CP-0's partition proof and
    CP-5's cross-validation are the only things that catch that.

  * Classification is a decision about a *path*. Nothing here opens a file.
    That keeps it cheap across 588 589 paths and it is what makes M2's
    no-open invariant enforceable at all: if the classifier read image files,
    the guarantee would be lost before M2 ever ran.

The one unavoidable filesystem touch is layer 4, which has to read `.git/config`
to tell a cloned third-party repository from the user's own. That happens once
per repository, is cached, and never touches the classified file itself.

Where the image corpus lives is **not** decided here and not shipped in the
rules. It comes from `~/.homegraph/config.toml` via userconfig.py; without that
file a Classifier refuses to be built rather than guessing a directory name
that would be wrong on most machines. See userconfig.py for why.

Symlinks need an `lstat` to detect. Pass `is_symlink=` when you already know
(the inventory records it) and no stat happens at all.
"""
from __future__ import annotations

import fnmatch
import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from . import userconfig
from .config import ENV_VAR, home_root, substitute
from typing import Any

RULES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules")

EXCLUDED = "EXCLUDED"
CATEGORIES = ("markdown", "document", "image", "code", "misc")
ALL_LABELS = CATEGORIES + (EXCLUDED,)


# The seven layers that can exclude a file. Named here so a report can say
# which one owned a decision, and so CP-0 can check that each of them shows
# up in a run rather than being a rule nobody's corpus ever reaches.
EXCLUSION_LAYERS = ("secrets", "L3_cache", "L1_dependencies", "L2_app_state",
                    "L4_vendored_repo", "symlinks", "image_boundary")

# How many directories a report names before it says it stopped.
TOP_DIRS = 20


class ExclusionReport:
    """What a walk left out, and honestly enough to audit.

    `build` and `update` used to report exclusion as a percentage. With
    588,589 files in and 5,333 out, "99.09% excluded" is 99.09% nobody can
    check. `census` did better and named directories, but capped the list at
    twenty and said nothing about the cap -- a silent truncation reads as
    full coverage, which is the pattern this package keeps finding in its own
    gates.

    So: the count, the layer that owned each exclusion, the directories, and
    `truncated` when the printed list is shorter than the tally. The flag is
    the point; the list is a convenience.

    Directories are attributed to the first two components below the root,
    which is the granularity at which a surprise is actionable -- `.cache/pip`
    tells you something, `.cache` alone does not.

    One class, fed by `record()` from each walk. The walks legitimately
    differ (`census` wants every decision, `corpus_paths` wants one label),
    but the truncation rule must not: two copies of "did we cut the list" is
    how one of them ends up always saying no.
    """

    # `cap=None` means no cap -- what `census --all` passes to see the tail.
    # The three uses below all test `is not None` first, so None was always
    # supported; only the annotation said otherwise.
    def __init__(self, root: str, cap: int | None = TOP_DIRS) -> None:
        self.root = root
        self.cap = cap
        self.count = 0
        self.seen = 0
        self.by_layer: dict[str, int] = {}
        self.dirs: dict[str, int] = {}

    def record(self, path: str, decision: "Decision") -> "Decision":
        """Tally one classified path. Returns the decision, so a caller can
        write `if report.record(p, d).label == label:` without a second call
        to `explain()`."""
        self.seen += 1
        if decision.label != EXCLUDED:
            return decision
        self.count += 1
        self.by_layer[decision.layer] = self.by_layer.get(decision.layer, 0) + 1
        try:
            rel = os.path.relpath(path, self.root)
        except ValueError:                       # different drive on Windows
            rel = path
        key = "/".join(rel.split(os.sep)[:2])
        self.dirs[key] = self.dirs.get(key, 0) + 1
        return decision

    @property
    def dirs_total(self) -> int:
        return len(self.dirs)

    @property
    def truncated(self) -> bool:
        return self.cap is not None and len(self.dirs) > self.cap

    def top(self) -> list[tuple[str, int]]:
        ranked = sorted(self.dirs.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked if self.cap is None else ranked[:self.cap]

    def summary(self) -> dict[str, Any]:
        return {
            "seen": self.seen,
            "excluded": self.count,
            "by_layer": dict(sorted(self.by_layer.items())),
            "dirs": self.top(),
            "dirs_total": self.dirs_total,
            "truncated": self.truncated,
        }

    def line(self) -> str:
        """One line for a command that prints rows, not JSON."""
        layers = ", ".join("%s %d" % (k, v)
                           for k, v in sorted(self.by_layer.items())) or "none"
        tail = ""
        # Tested against `self.cap` rather than `self.truncated`, which says the
        # same thing but through a property -- neither checker can carry the
        # None-ness back through it, and the `%d` below needs an int.
        if self.cap is not None and len(self.dirs) > self.cap:
            tail = " (naming %d of %d directories)" % (self.cap, len(self.dirs))
        return "%d of %d excluded by [%s]%s" % (self.count, self.seen,
                                                layers, tail)


@dataclass(frozen=True)
class Decision:
    """Why a path got its label. This is what `corpus explain` prints."""
    label: str          # one of ALL_LABELS
    subtype: str        # doctype, exclusion reason, or "-"
    layer: str          # which rule layer decided
    detail: str         # the specific rule that matched

    def __str__(self) -> str:
        return "%s (%s) via %s: %s" % (
            self.label, self.subtype, self.layer, self.detail)


def _load(rules_dir: str, name: str) -> dict[str, Any]:
    with open(os.path.join(rules_dir, name), "rb") as fh:
        return tomllib.load(fh)


def ext_of(basename: str) -> str:
    """Extension, lowercased, without the dot. '' if there is none.

    A leading dot is not a separator: `.bashrc` has no extension, and neither
    does `.face`, which is a JPEG. Extension-based image detection therefore
    cannot see it -- that is why it is caught by name in [app_state].
    """
    if "." not in basename[1:]:
        return ""
    return basename.rsplit(".", 1)[1].lower()


def _match_any(name: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


class Classifier:
    def __init__(self, rules_dir: str = RULES_DIR,
                 home: str | None = None,
                 config: userconfig.UserConfig | None = None) -> None:
        # An alternate rules_dir is how CP-0's negative control turns the
        # exclusion layers off: if the rules are truly doing the work, image
        # count must jump from ~100 to >50 000 when they are removed.
        self.rules_dir = rules_dir
        # The installation config. Loaded rather than defaulted: it carries
        # the image root, and a guessed image root fails silently -- the
        # boundary matches nothing, every image is excluded, and M2 builds an
        # empty model while reporting success. ConfigMissing propagates; the
        # CLI turns it into an honest exit 2.
        self.config = config if config is not None else userconfig.load()
        # Resolved once, then substituted into every rule that names a path.
        # Passing it down rather than reading a module global means two
        # Classifiers over two roots can coexist -- which the tests need, and
        # which a hardcoded constant made impossible.
        self.home = home_root(
            home or os.environ.get(ENV_VAR) or self.config.root)
        # Role directories follow whichever root won above, so one config can
        # describe a tree that was generated somewhere else.
        lists = {"{image_roots}": list(
            self.config.role_dirs("image", base=self.home))}
        self.ex = substitute(_load(rules_dir, "exclusions.toml"), self.home,
                             lists)
        self.cat = substitute(_load(rules_dir, "categories.toml"), self.home,
                              lists)

        self._secret_names = set(self.ex["secrets"]["names"])
        self._secret_globs = self.ex["secrets"]["globs"]
        self._allow_paths = set(self.ex["allow"]["paths"])
        self._allow_globs = self.ex["allow"]["globs"]
        self._cache_dirs = set(self.ex["cache"]["dirs"])
        self._cache_globs = self.ex["cache"]["globs"]
        self._dep_dirs = set(self.ex["dependencies"]["dirs"])
        self._state_prefixes = tuple(self.ex["app_state"]["prefixes"])
        self._state_scoped = tuple(self.ex["app_state"]["prefixes_scoped"])
        self._home_files = set(self.ex["app_state"]["home_files"])
        self._exclude_symlinks = self.ex["symlinks"]["exclude"]
        # A tuple, possibly empty. Empty means there is no image corpus on this
        # machine, so every image-extension file is outside the boundary and is
        # excluded -- M2 ends up absent, which mesh reports as `partial`. That
        # is a real state and not an error, and it is the state a user gets by
        # leaving the `image` role empty.
        self._image_roots = tuple(
            str(r).rstrip("/") + "/"
            for r in self.ex["image_boundary"]["roots"])

        vr = self.ex["vendored_repos"]
        self._vendored_enabled = vr.get("enabled", True)
        # The rule file ships this empty; the account names come from the
        # user's config. A shipped default would be one person's identity baked
        # into everyone's installation. With none listed, `no_origin_is_own`
        # still keeps purely local repositories, and remote ones are treated as
        # vendored until you say otherwise.
        self._own_owners = {o.lower() for o in
                            tuple(vr.get("own_owners", ()))
                            + tuple(self.config.own_owners)}
        self._no_origin_is_own = vr["no_origin_is_own"]
        self._keep_toplevel = vr["keep_toplevel_globs"]
        self._vendored_roots: list[str] | None = None  # on first use

        self._image_exts = set(self.cat["image"]["extensions"])
        self._md_exts = set(self.cat["markdown"]["extensions"])
        self._doctypes = dict(self.cat["document"]["doctypes"])
        self._code_exts = set(self.cat["code"]["extensions"])
        self._misc_subtype = self.cat["misc"]["default_subtype"]

    # -- layer 4 ----------------------------------------------------------

    def _origin_owner(self, repo_root: str) -> str | None:
        """Owner from .git/config's origin URL, or None if there is no origin.

        Reads the config file directly rather than shelling out to git: this
        runs once per repository, but a subprocess per repository on a tree
        this size is still an avoidable cost, and git may not be installed.
        """
        cfg = os.path.join(repo_root, ".git", "config")
        try:
            with open(cfg, encoding="utf-8", errors="replace") as fh:
                in_origin = False
                for line in fh:
                    s = line.strip()
                    if s.startswith("["):
                        in_origin = s.replace(" ", "").startswith(
                            '[remote"origin"]')
                        continue
                    if in_origin and s.startswith("url"):
                        url = s.split("=", 1)[1].strip()
                        if url.startswith("git@") and ":" in url:
                            url = url.split(":", 1)[1]
                        else:
                            for scheme in ("https://", "http://", "ssh://"):
                                if url.startswith(scheme):
                                    url = url[len(scheme):]
                                    url = url.split("/", 1)[-1]
                                    break
                        parts = [p for p in url.split("/") if p]
                        return parts[0] if parts else None
        except OSError:
            return None
        return None

    def _discover_vendored(self) -> list[str]:
        """Find repositories under $HOME whose origin is someone else's.

        Prunes dependency and cache directories on the way down, so the walk
        does not descend into the ~200k files of node_modules to find a `.git`
        that would be excluded by layer 1 anyway.
        """
        if not self._vendored_enabled:
            return []
        vendored: list[str] = []
        for dirpath, dirnames, _ in os.walk(self.home, topdown=True):
            if ".git" in dirnames:
                owner = self._origin_owner(dirpath)
                own = (self._no_origin_is_own if owner is None
                       else owner.lower() in self._own_owners)
                if not own:
                    vendored.append(dirpath.rstrip("/") + "/")
            dirnames[:] = [d for d in dirnames
                           if d not in self._dep_dirs
                           and d not in self._cache_dirs]
        # Longest first: a repo nested inside another must win.
        vendored.sort(key=len, reverse=True)
        return vendored

    @property
    def vendored_roots(self) -> list[str]:
        if self._vendored_roots is None:
            self._vendored_roots = self._discover_vendored()
        return self._vendored_roots

    # -- main entry point -------------------------------------------------

    def explain(self, path: str, is_symlink: bool | None = None) -> Decision:
        p = os.path.normpath(os.path.abspath(path))
        base = os.path.basename(p)
        parent = os.path.dirname(p)
        parts = p.split(os.sep)[1:]
        ext = ext_of(base)

        # Layer 5 first, and not overridable by the allowlist. A secret that an
        # allowlist entry can resurrect is not protected by anything.
        if base in self._secret_names or _match_any(base, self._secret_globs):
            return Decision(EXCLUDED, "redacted", "secrets", base)

        if p in self._allow_paths or _match_any(p, self._allow_globs):
            return self._categorize(p, base, ext, allowed=True)

        # Layer 3 -- broadest and cheapest, and matches at any depth.
        for part in parts[:-1]:
            if part in self._cache_dirs:
                return Decision(EXCLUDED, "cache", "L3_cache", "dir %s" % part)
        if _match_any(base, self._cache_globs):
            return Decision(EXCLUDED, "cache", "L3_cache", "file %s" % base)

        # Layer 1
        for part in parts[:-1]:
            if part in self._dep_dirs:
                return Decision(EXCLUDED, "dependency", "L1_dependencies",
                                "dir %s" % part)

        # Layer 2
        for pref in self._state_prefixes + self._state_scoped:
            if p.startswith(pref):
                return Decision(EXCLUDED, "app-state", "L2_app_state", pref)
        if parent == self.home and base in self._home_files:
            return Decision(EXCLUDED, "app-state", "L2_app_state",
                            "home file %s" % base)

        # Layer 4
        for root in self.vendored_roots:
            if p.startswith(root):
                if (parent.rstrip("/") + "/" == root
                        and _match_any(base, self._keep_toplevel)):
                    break  # the README is why the repo was cloned
                return Decision(EXCLUDED, "vendored-repo", "L4_vendored_repo",
                                root)

        if self._exclude_symlinks:
            if is_symlink is None:
                try:
                    is_symlink = os.path.islink(p)
                except OSError:
                    is_symlink = False
            if is_symlink:
                return Decision(EXCLUDED, "symlink", "symlinks", p)

        # Decision 2: images outside the image root(s) are excluded, never
        # demoted to M4. With no roots configured `any()` is False for every
        # path, so every image is excluded -- the intended reading, and the
        # one case where a vacuous quantifier is the honest answer rather than
        # a gate that cannot say no.
        if ext in self._image_exts and not any(
                p.startswith(r) for r in self._image_roots):
            return Decision(EXCLUDED, "image-outside-root", "image_boundary",
                            "%s outside %s" % (ext, list(self._image_roots)))

        return self._categorize(p, base, ext, allowed=False)

    def _categorize(self, p: str, base: str, ext: str,
                    allowed: bool) -> Decision:
        layer = "allow" if allowed else "category"
        # No image-root check here on purpose -- see categories.toml. Images
        # outside the image root never reach this point; [image_boundary]
        # excluded them, and it is the only place that boundary is enforced.
        if ext in self._image_exts:
            return Decision("image", "-", layer, "image ext")
        if ext in self._md_exts:
            return Decision("markdown", "note", layer, ".%s" % ext)
        if ext in self._doctypes:
            return Decision("document", self._doctypes[ext], layer, ".%s" % ext)
        if ext in self._code_exts:
            return Decision("code", "-", layer, ".%s" % ext)
        # The complement. Everything the four above declined lands here, which
        # is why M4 inherits every classification mistake in the system.
        return Decision("misc", self._misc_subtype, layer,
                        "complement (ext=%r)" % ext)

    def classify(self, path: str, is_symlink: bool | None = None) -> str:
        return self.explain(path, is_symlink=is_symlink).label

    def known_extensions(self) -> frozenset[str]:
        """This classifier's extensions -- see `known_extensions()` below."""
        return _extensions_from(self.cat)


@lru_cache(maxsize=1)
def _default() -> Classifier:
    return Classifier()


def _extensions_from(cat: dict[str, Any]) -> frozenset[str]:
    exts = set(cat["image"]["extensions"]) | set(cat["markdown"]["extensions"])
    exts |= set(cat["document"]["doctypes"]) | set(cat["code"]["extensions"])
    return frozenset("." + str(e).lower() for e in exts)


@lru_cache(maxsize=4)
def known_extensions(rules_dir: str = RULES_DIR) -> frozenset[str]:
    """Every extension categories.toml names, lowercased, with the dot.

    Read from the rules rather than written out again. Three hand-written
    copies of an extension list have already been found in this package --
    `m2_build.IMAGE_EXT`, a shorter one in `m3_markdown`, and the pair
    `image_extensions()` replaced -- and the shortest copy was the one that
    filed `.heic` embeds as ordinary links.

    `misc` contributes nothing: it is the complement and names no extensions,
    so "known" here means "some category claims this suffix", not "the system
    can read it".

    Takes a rules directory and not a Classifier, because the answer does not
    depend on the user's config -- roles and roots do, extensions do not -- and
    requiring a config here would make `m1_build` refuse to run in a process
    that has none, for a question no config can answer.
    """
    return _extensions_from(_load(rules_dir, "categories.toml"))


def classify(path: str, is_symlink: bool | None = None) -> str:
    """Label a path as one of ALL_LABELS. Never opens the file."""
    return _default().classify(path, is_symlink=is_symlink)


def explain(path: str, is_symlink: bool | None = None) -> Decision:
    """Same decision as classify(), plus which rule made it and why."""
    return _default().explain(path, is_symlink=is_symlink)
