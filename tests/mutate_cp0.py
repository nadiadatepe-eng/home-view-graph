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
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 600

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

    ("secrets layer switched off",
     "homegraph/rules/exclusions.toml",
     'names = [\n  ".env", ".netrc", ".pgpass", ".npmrc", ".git-credentials",',
     'names = [  # mutated: secrets no longer named\n  ".__never__",',
     "secret filenames are excluded"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp0.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut0-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-44s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-44s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-44s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-44s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-44s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-44s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
