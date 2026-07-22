#!/usr/bin/env python3
"""Where "home" is.

The first version of this package hardcoded one author's home directory in 66
places, which made it a program that worked on exactly one computer. That is
fine for a tool you wrote for yourself and fatal the moment anyone else clones
it.

Resolution order, first match wins:

    1. an explicit argument passed to Classifier(home=...)
    2. $HOMEGRAPH_ROOT
    3. `root` from ~/.homegraph/config.toml (see userconfig.py)
    4. the current user's home directory

The rule files write `{root}` rather than a literal path, and it is substituted
when they load. That keeps the rules readable -- `{root}/.cache/` still says
what it means -- without pinning them to one machine.

**Directory names under the root get the same treatment, and it took longer to
learn.** A rule that reads `{root}/Pictures/` is still a rule about one
machine: on a system whose desktop language differs, that directory does not
exist, the boundary matches nothing, and the image model builds zero nodes
while reporting success. So a rule may also write a *list placeholder* --
`roots = "{image_roots}"` -- which `substitute()` replaces with a list supplied
by the user's config. A placeholder standing alone becomes a list; one embedded
in a longer string is left alone, because splicing a list into text has no
meaning.
"""
from __future__ import annotations

import os
from typing import Any

ENV_VAR = "HOMEGRAPH_ROOT"


def home_root(explicit: str | None = None) -> str:
    """The directory homegraph treats as the corpus root, without a trailing /."""
    root = explicit or os.environ.get(ENV_VAR) or os.path.expanduser("~")
    return os.path.abspath(os.path.expanduser(root)).rstrip("/") or "/"


def substitute(value: Any, root: str,
               lists: dict[str, list[str]] | None = None) -> Any:
    """Replace `{root}` and any list placeholders throughout a loaded TOML tree.

    Walks lists and dicts because the rule files put paths in both. Anything
    that is not a string is returned untouched, so numbers and booleans pass
    through without a special case.

    A string that is *exactly* a key of `lists` is replaced by that list. This
    is how `[image_boundary].roots = "{image_roots}"` becomes the directories
    the user actually has. Only a standalone placeholder expands: a rule file
    that writes a literal list -- as CP-0's null config does, to switch the
    boundary off -- passes straight through, so turning a rule off and
    parameterising it stay independent operations.
    """
    if isinstance(value, str):
        if lists and value in lists:
            return list(lists[value])
        return value.replace("{root}", root)
    if isinstance(value, list):
        return [substitute(v, root, lists) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, root, lists) for k, v in value.items()}
    return value
