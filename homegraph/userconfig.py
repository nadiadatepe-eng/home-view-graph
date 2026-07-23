#!/usr/bin/env python3
"""What this installation was told about the machine it runs on.

`config.py` answers "where is the root". This file answers the harder question:
**what is under it**. Nothing in the package may assume an answer.

The first version hardcoded one directory name for the image corpus. On a
machine whose desktop language is not the author's, that directory does not
exist -- and the failure is silent: the boundary matches nothing, every image
is excluded, and M2 builds a graph with zero image nodes while reporting
success. A tool that quietly produces an empty model on most of the machines it
runs on is worse than one that refuses.

So the layout is **declared, once, by the user**, in `~/.homegraph/config.toml`:

    root = "/srv/corpus"          # any directory; your home directory by default

    [roles]
    image    = ["Pictures"]
    document = ["Documents"]
    note     = ["notes", "wiki"]
    code     = ["src"]

`homegraph init` proposes that file by looking at what is actually on disk;
nothing here writes it without being asked to. Role entries are relative to
`root` unless they are absolute, which keeps a config portable between two
copies of the same tree -- the checkpoints rely on that to run the same rules
over a fixture built anywhere.

Without the file the commands refuse with exit 2. That is deliberate and it is
the same idiom `build`/`update`/`embed` already use: an honest refusal beats a
no-op that exits 0 and looks like it worked.

**The roles are data, not a second classifier.** They say where to look. What a
file *is* remains `corpus.classify()`'s decision and nothing here duplicates
it -- see DECISIONS.md §2 for what that duplication cost the first time.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

CONFIG_ENV = "HOMEGRAPH_CONFIG"
DEFAULT_CONFIG = os.path.join("~", ".homegraph", "config.toml")

# Roles the package knows what to do with. `image` is the only one that is
# load-bearing today -- it supplies `{image_roots}` to the rule files. The rest
# are recorded because `init` can see them and a later model will want them;
# they are listed here rather than invented per caller so that a typo in the
# config is a visible unknown role rather than a silently ignored line.
ROLES = ("image",)

# Four roles were here and are gone. Every one of them was a line the user
# filled in and no code consulted.
#
# `cache` went first: exclusion is decided by `[cache]` and `[dependencies]` in
# rules/exclusions.toml, which `corpus.py` loads directly. The other three were
# kept afterwards on the grounds that they were "reserved for a later model"
# and documented as such -- which turned out to be the same defect wearing a
# label. External review put it plainly: with `note = []` a markdown file is
# still built, because the CLI classifies the whole root and only `image` is
# ever handed to the rules. Documenting that a line does nothing does not stop
# the reader believing it does.
#
# `image` stays because it is load-bearing: it supplies `{image_roots}` to the
# rule files, and emptying it demonstrably moves the image count to zero. When
# a model needs one of the others, it comes back with a consumer attached.
#
# Named on removal rather than dropped, so a config written by an older `init`
# explains itself instead of failing as an unrecognised key.
_RESERVED = ("declared but never consulted -- the models classify the whole "
             "root, and only `image` reaches the rules; delete the line")
RETIRED_ROLES = {
    "cache": "exclusion is decided by rules/exclusions.toml, not by a role; "
             "delete the line",
    "document": _RESERVED,
    "note": _RESERVED,
    "code": _RESERVED,
}


class ConfigMissing(RuntimeError):
    """No config file. Raised rather than guessed at."""


@dataclass(frozen=True)
class UserConfig:
    path: str
    root: str
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # GitHub accounts whose repositories are the user's own work. Empty by
    # default and NOT shipped in the rule file: a default here would bake one
    # person's identity into everyone's installation. See exclusions.toml
    # [vendored_repos] for why emptiness inverts rather than disables the layer.
    own_owners: tuple[str, ...] = ()
    # Directory fragments whose markdown is machine-written. The name of the
    # tool that produced them is a fact about one machine, so it lives here and
    # not in models/m3_markdown.py.
    generated_dirs: tuple[str, ...] = ()

    def role_dirs(self, name: str, base: str | None = None) -> tuple[str, ...]:
        """Absolute directories for a role, each with a trailing separator.

        `base` overrides the configured root, which is what lets one config
        describe a fixture that was generated somewhere else. Relative entries
        follow the base; absolute entries are left alone, because a user who
        writes an absolute path meant it.
        """
        root = (base or self.root).rstrip("/") or "/"
        out = []
        for entry in self.roles.get(name, ()):
            p = entry if os.path.isabs(entry) else os.path.join(root, entry)
            out.append(os.path.abspath(p).rstrip("/") + "/")
        return tuple(out)


def config_path(explicit: str | None = None) -> str:
    """First match wins: an argument, $HOMEGRAPH_CONFIG, the default location."""
    raw = explicit or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG
    return os.path.abspath(os.path.expanduser(raw))


def exists(explicit: str | None = None) -> bool:
    return os.path.isfile(config_path(explicit))


def load(explicit: str | None = None) -> UserConfig:
    path = config_path(explicit)
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigMissing(
            "no homegraph config at %s -- run `homegraph init` to create one"
            % path) from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # A corrupt config is not a missing one, and pretending otherwise would
        # send the user to `init`, which would overwrite the file they meant to
        # fix. Different failure, different message.
        raise RuntimeError("homegraph config at %s is unreadable: %s"
                           % (path, exc)) from exc

    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ConfigMissing(
            "%s has no `root` -- run `homegraph init` to rewrite it" % path)
    root = os.path.abspath(os.path.expanduser(root)).rstrip("/") or "/"

    roles_raw = raw.get("roles", {})
    if not isinstance(roles_raw, dict):
        raise RuntimeError("%s: [roles] must be a table" % path)
    roles = {}
    for name, value in roles_raw.items():
        if not isinstance(value, list) or not all(
                isinstance(v, str) for v in value):
            raise RuntimeError(
                "%s: roles.%s must be a list of strings" % (path, name))
        roles[name] = tuple(value)
    retired = sorted(set(roles) & set(RETIRED_ROLES))
    if retired:
        # Told what happened, not just that it is wrong. A config written by an
        # older `init` is not a user error.
        raise RuntimeError(
            "%s: role(s) %s were removed -- %s"
            % (path, ", ".join(retired),
               "; ".join("%s: %s" % (r, RETIRED_ROLES[r]) for r in retired)))
    unknown = sorted(set(roles) - set(ROLES))
    if unknown:
        # Named, not ignored. A role nobody reads is a line the user believes
        # is doing something.
        raise RuntimeError("%s: unknown role(s) %s; known roles are %s"
                           % (path, ", ".join(unknown), ", ".join(ROLES)))

    owners = tuple(raw.get("vendored_repos", {}).get("own_owners", ()))
    generated = tuple(raw.get("markdown", {}).get("generated_dirs", ()))
    return UserConfig(path=path, root=root, roles=roles,
                      own_owners=owners, generated_dirs=generated)


def render(root: str, roles: dict[str, list[str]],
           own_owners: tuple[str, ...] = (),
           generated_dirs: tuple[str, ...] = ()) -> str:
    """The config file as text. Written by `init`, editable by hand afterwards.

    Every key is emitted even when empty, so the file documents what can be set
    rather than what happened to be found. A user who has no `Pictures`
    directory should see an empty `image` role and know that M2 will be absent,
    not wonder whether the key exists.
    """
    def _list(values):
        return "[%s]" % ", ".join('"%s"' % v.replace('"', '\\"')
                                  for v in values)

    lines = [
        "# homegraph installation config.",
        "#",
        "# Written by `homegraph init`, and yours to edit afterwards -- it is",
        "# read on every run, never rewritten behind your back.",
        "#",
        "# Role entries are directories relative to `root`, or absolute paths.",
        "# An empty role means homegraph has nothing of that kind: the matching",
        "# model is simply absent, and `mesh` labels its answers `partial` and",
        "# names it. That is the honest outcome, not an error.",
        "",
        'root = "%s"' % root,
        "",
        "[roles]",
    ]
    for name in ROLES:
        lines.append("%-8s = %s" % (name, _list(roles.get(name, []))))
    lines += [
        "",
        "[vendored_repos]",
        "# GitHub accounts that are yours. A repository whose origin belongs to",
        "# anyone else is treated as a vendored dependency and excluded.",
        "# Emptying this does not disable that layer, it INVERTS it: with no",
        "# owners listed, every repository with a remote becomes someone",
        "# else's, including your own published work.",
        "#",
        "# THIS LINE IS YOURS ALONE. It names accounts, so it cannot ship with",
        "# the package and cannot be inherited from whoever set up the machine",
        "# before you: copying someone else's config here silently excludes",
        "# your repositories and keeps theirs. `init` cannot fill it in --",
        "# owning a clone is not owning the account that published it, and",
        "# guessing that from a remote URL is a mistake this project has",
        "# already made once. Put your own account names here, and nothing",
        "# you merely cloned.",
        "own_owners = %s" % _list(own_owners),
        "",
        "[markdown]",
        "# Directory fragments whose markdown is machine-written -- graph",
        "# reports, generated wikis. Files matching these get the `generated`",
        "# subtype so their unresolved links do not drown the handful a person",
        "# wrote on purpose.",
        "generated_dirs = %s" % _list(generated_dirs),
        "",
    ]
    return "\n".join(lines)


def _fsync_dir(path: str) -> None:
    """Flush a directory's own metadata, so a rename into it survives a crash.

    Errors are swallowed, and that is the deliberate reading rather than the
    lazy one. By the time this is called `os.replace` has already returned: the
    config file exists, is complete, and is the one the next run will read.
    Raising here would report a failed write for a write that succeeded, and
    the caller's cleanup would then look for a scratch file that has already
    been renamed away. Durability is the thing being attempted; correctness of
    the return value is the thing being protected.

    Opening a directory read-only is POSIX; on Windows it fails outright, which
    is why the open is inside the guard rather than only the fsync.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write(path: str, root: str, roles: dict[str, list[str]],
          own_owners: tuple[str, ...] = (),
          generated_dirs: tuple[str, ...] = ()) -> str:
    # Written to a sibling and renamed, never truncated in place. `open(path,
    # "w")` destroys the old file before the new one exists, and a process that
    # dies partway leaves *valid* TOML: `root = "/h"` with no [roles] section
    # parses cleanly, every role comes back empty, and the next `update` finds
    # no images and deletes the image corpus as a successful run. A config that
    # is half-written has to be no config at all.
    #
    # Same directory so the rename stays on one filesystem, where it is atomic;
    # /tmp could be a different mount and os.replace would fall back to a copy.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(render(root, roles, own_owners, generated_dirs))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # The bytes were fsync'd above; the rename was not. Atomic and durable
        # are different properties: `os.replace` guarantees no reader ever sees
        # a half-written config, and guarantees nothing at all about which of
        # the two names survives a power cut. The directory entry can still be
        # in the page cache with every byte of the new file safely on disk, and
        # the machine comes back with the OLD config and a NEW store built from
        # it -- a mismatch `update` reads as a config change and refuses on,
        # which is the good outcome. The bad one is a first-ever config that
        # vanishes while the store it produced remains.
        _fsync_dir(os.path.dirname(path) or ".")
    except BaseException:
        # BaseException, not Exception: KeyboardInterrupt is the realistic way
        # this is interrupted, and leaving the scratch file behind would make
        # the next run's directory listing lie about what configs exist.
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path
