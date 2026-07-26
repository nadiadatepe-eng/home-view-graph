#!/usr/bin/env python3
"""Mutation test for CP-0 -- the checkpoint that had none.

Every other checkpoint got a mutation harness; the corpus layer did not, and it
is the one everything else rests on. An adversarial audit put the consequence
plainly: the empty gates it found all sat among the checks no mutation targeted,
because a check that cannot fail cannot have a mutation written against it. The
two defects co-locate by construction, which makes an unmutated checkpoint the
first place to look rather than the last.

CP-0's negative control was validated once by hand -- 135 → 52 661 -- and never
again. That is a demonstration, not a repeatable proof. These mutations make it
one.

Run:
    python3 tests/mutate_cp0.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    ("classify returns a label nobody defined",
     "homegraph/corpus.py",
     '        return Decision("misc", self._misc_subtype, layer,\n'
     '                        "complement (ext=%r)" % ext)',
     '        return Decision("mystery", self._misc_subtype, layer,\n'
     '                        "complement (ext=%r)" % ext)  # mutated',
     "partition: no errors"),

    ("classify raises on some paths",
     "homegraph/corpus.py",
     "    def explain(self, path: str, is_symlink: bool | None = None) -> Decision:\n"
     "        p = os.path.normpath(os.path.abspath(path))",
     "    def explain(self, path: str, is_symlink: bool | None = None) -> Decision:\n"
     # Depth 8, not 12: the synthetic corpus's deepest path is ten components,
     # so a 12-deep trigger fired only inside the cache gate's own scratch
     # directory and the partition proof never saw it.
     "        if path.count('/') > 8:  # mutated: deep paths blow up\n"
     "            raise ValueError('too deep')\n"
     "        p = os.path.normpath(os.path.abspath(path))",
     "partition: no errors"),

    ("cache and dependency layers switched off",
     "homegraph/rules/exclusions.toml",
     'dirs = [\n  "node_modules", ".venv", "venv", "site-packages", "dist-packages",',
     'dirs = [  # mutated: dependency trees no longer excluded\n  "__never__",',
     "noise threshold"),

    ("cache directories match only at the top level",
     "homegraph/corpus.py",
     "        for part in parts[:-1]:\n"
     "            if part in self._cache_dirs:\n"
     '                return Decision(EXCLUDED, "cache", "L3_cache", "dir %s" % part)',
     "        if parts and parts[0] in self._cache_dirs:  # mutated: depth 1 only\n"
     '            return Decision(EXCLUDED, "cache", "L3_cache", "top-level")',
     "cache gate at any depth"),

    ("images outside the image root demoted to misc instead of excluded",
     "homegraph/corpus.py",
     "        if ext in self._image_exts and not any(\n"
     "                p.startswith(r) for r in self._image_roots):\n"
     '            return Decision(EXCLUDED, "image-outside-root", "image_boundary",',
     "        if False:  # mutated: the boundary no longer excludes\n"
     '            return Decision(EXCLUDED, "image-outside-root", "image_boundary",',
     "image gate: none outside the image root"),

    ("image extensions lose png",
     "homegraph/rules/categories.toml",
     '  "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif",',
     '  "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif",',
     "image count matches the independent baseline"),

    # The one the audit called out by name: the control was demonstrated once
    # and never re-run. Re-introducing the duplicated invariant must make it
    # fail, or it is a story rather than a gate.
    ("the image boundary duplicated into the category step again",
     "homegraph/corpus.py",
     "        if ext in self._image_exts:\n"
     '            return Decision("image", "-", layer, "image ext")',
     "        if ext in self._image_exts and any(\n"
     "                p.startswith(r) for r in self._image_roots):  # mutated\n"
     '            return Decision("image", "-", layer, "image ext")',
     "the image boundary exists in exactly one layer"),

    # The config is the only place the image directory is named. Emptying the
    # role must move the image count to zero -- if it does not, something else
    # is still deciding where images live, which is the duplicated invariant
    # coming back through a new door.
    ("the image role emptied in the user config",
     "homegraph/userconfig.py",
     '    roles = {}\n    for name, value in roles_raw.items():',
     '    roles = {}\n    roles_raw = dict(roles_raw, image=[])  # mutated\n'
     '    for name, value in roles_raw.items():',
     "image count matches the independent baseline"),

    # `{image_roots}` is what carries the config into the rules. A substitution
    # that silently no-ops would leave the literal placeholder as a path prefix
    # that matches nothing -- every image excluded, and no error anywhere.
    ("the list placeholder stops expanding",
     "homegraph/config.py",
     "        if lists and value in lists:\n            return list(lists[value])",
     "        if False:  # mutated: {image_roots} stays a literal\n"
     "            return list(lists[value])",
     "image count matches the independent baseline"),

    # The per-layer control's own reason for existing. Switching [symlinks]
    # off is invisible to every count-based gate -- the corpus stays the same
    # size, the partition still holds -- because the layer's files land in
    # other categories or are caught by another layer. Only "does this layer
    # uniquely own anything" can see it, and before the fixture gained a
    # symlink with no second reason, not even that could.
    ("the symlink layer switched off",
     "homegraph/rules/exclusions.toml",
     "exclude = true",
     "exclude = false  # mutated: symlinks are ordinary files",
     "every exclusion layer uniquely owns files"),

    # The duplicated invariant, arriving through a different door than the
    # image boundary did: layer 3 grows to cover everything layer 1 covered.
    # Both still work, every count is unchanged, and layer 1 is now dead code
    # that no output depends on.
    ("the dependency directories duplicated into the cache layer",
     "homegraph/rules/exclusions.toml",
     'dirs = [\n  ".cache", "Cache", "cache", "caches", "Cache_Data", "CacheStorage",',
     'dirs = [  # mutated: layer 3 swallows layer 1\n'
     '  "node_modules", ".venv", "venv", "site-packages", "dist-packages",\n'
     '  ".git", "vendor", "bower_components", ".bundle", "Pods",\n'
     '  ".yarn", ".pnpm-store", "eggs", ".eggs",\n'
     '  ".cache", "Cache", "cache", "caches", "Cache_Data", "CacheStorage",',
     "every exclusion layer uniquely owns files"),

    # An exclusion that belongs to no layer. Every knob in the rule files can
    # be turned off and this one path stays excluded, so the null control can
    # never reach zero -- which is the only gate that looks.
    ("an exclusion hardcoded into explain(), owned by no layer",
     "homegraph/corpus.py",
     "        if p in self._allow_paths or _match_any(p, self._allow_globs):",
     "        if '/.icons/' in p:  # mutated: hardcoded, no layer owns it\n"
     '            return Decision(EXCLUDED, "cache", "L3_cache", "hardcoded")\n'
     "        if p in self._allow_paths or _match_any(p, self._allow_globs):",
     "switching off every layer excludes almost nothing"),

    ("secrets layer switched off",
     "homegraph/rules/exclusions.toml",
     'names = [\n  ".env", ".netrc", ".pgpass", ".npmrc", ".git-credentials",',
     'names = [  # mutated: secrets no longer named\n  ".__never__",',
     "secret filenames are excluded"),

    # -- authored in, derived out ---------------------------------------
    #
    # Both of these were real states on 2026-07-25 and neither failed anything.
    # The first is the shape that matters most: an allow-list entry does not
    # break when the content moves, it just stops matching, and the content
    # leaves the corpus in silence.
    ("the memory notes' new home dropped from the allow-list",
     "homegraph/rules/exclusions.toml",
     '  "{root}/.claude/memory/*",\n',
     '',   # mutated: the notes moved here and nothing names them any more
     "authored notes under .claude survive the wholesale exclusion"),

    ("the package's own vault export back inside the corpus",
     "homegraph/rules/exclusions.toml",
     '  "{root}/homegraph-vault/",\n',
     '',   # mutated: a build after an export ingests the export
     "the package's own export is not corpus"),

    # -- the exclusion report -------------------------------------------
    #
    # A silent truncation reads as full coverage. These are the mutations
    # that separate "the list is short" from "the list was cut".
    ("a capped list never admits it was cut",
     "homegraph/corpus.py",
     "        return self.cap is not None and len(self.dirs) > self.cap",
     "        return False  # mutated: never truncated",
     "a capped list reports itself as truncated"),

    ("truncation is reported whether or not the cap bit",
     "homegraph/corpus.py",
     "        return self.cap is not None and len(self.dirs) > self.cap",
     "        return self.cap is not None  # mutated: a cap set is a cap hit",
     "a cap wider than the tally does not report truncation"),

    ("the cap is announced but not applied",
     "homegraph/corpus.py",
     "        return ranked if self.cap is None else ranked[:self.cap]",
     "        return ranked  # mutated: cap ignored",
     "a capped list reports itself as truncated"),

    # A layer that owns files and is missing from every line the user reads.
    # No behavioural gate sees this: the classification is unchanged.
    ("one layer stops being named in the report",
     "homegraph/corpus.py",
     "        self.by_layer[decision.layer] = self.by_layer.get(decision.layer, 0) + 1",
     "        if decision.layer != \"symlinks\":  # mutated: one layer goes quiet\n"
     "            self.by_layer[decision.layer] = "
     "self.by_layer.get(decision.layer, 0) + 1",
     "every exclusion layer is named in the report"),

    ("the report counts files the census did not exclude",
     "homegraph/corpus.py",
     "        if decision.label != EXCLUDED:\n            return decision",
     "        if False:  # mutated: everything counts as excluded\n"
     "            return decision",
     "the report's exclusion count matches the census"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp0.py", prefix="mut0-", timeout=600))
