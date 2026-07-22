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
ROLES = ("image", "document", "note", "code")

# `cache` was here and is gone. Nothing ever read it: exclusion is decided by
# `[cache]` and `[dependencies]` in rules/exclusions.toml, which `corpus.py`
# loads directly. A role the user fills in and no code consults is exactly the
# "line the user believes" this module refuses to allow for a typo, and it was
# worse than a typo -- `init` proposed it, so the config arrived pre-filled
# with a lie. Named on removal rather than dropped, so an existing config says
# what happened instead of failing as an unrecognised key.
RETIRED_ROLES = {
    "cache": "exclusion is decided by rules/exclusions.toml, not by a role; "
             "delete the line",
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


def write(path: str, root: str, roles: dict[str, list[str]],
          own_owners: tuple[str, ...] = (),
          generated_dirs: tuple[str, ...] = ()) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(root, roles, own_owners, generated_dirs))
    return path
