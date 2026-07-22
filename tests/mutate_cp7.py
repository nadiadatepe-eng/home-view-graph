#!/usr/bin/env python3
"""Mutation test for CP-7 -- the config layer.

CP-7's gates are the easiest in this project to write vacuously. "The commands
refuse without a config" passes if they refuse for any reason at all, including
crashing. "Renaming the directories changes no label" passes if nothing is ever
labelled. "The written config matches the proposal" passes when both are empty.
Every one of those is the shape that has taken fourteen gates here already.

So each mutation below breaks one specific link in the chain from the config
file to a label, and names the gate that must notice.

Run:
    python3 tests/mutate_cp7.py
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
    # -- the config must reach the extractor ----------------------------
    #
    # The original defect: the key was read from the file and never handed to
    # the builder. What made it survive was that the checkpoint called
    # `build(..., rules=...)` itself, so it tested a code path no user takes.
    ("the config stops reaching the markdown extractor",
     "homegraph/models/m3_build.py",
     '    return {"generated_markers": tuple(getattr(cfg, "generated_dirs", ()) or ())}',
     '    return {}  # mutated: config read, then dropped',
     "rules_from_config carries the directory into the build"),

    # -- config writes are atomic ---------------------------------------
    ("the config is truncated in place again",
     "homegraph/userconfig.py",
     '    tmp = "%s.tmp-%d" % (path, os.getpid())',
     '    tmp = path  # mutated: write straight over the original',
     "the previous config survives byte-for-byte"),

    ("a failed write leaves its scratch file behind",
     "homegraph/userconfig.py",
     '        if os.path.exists(tmp):\n            os.remove(tmp)',
     '        pass  # mutated: leave the partial file',
     "no scratch file is left in the config directory"),

    # -- thresholds -----------------------------------------------------
    ("the share is measured against known extensions only",
     "homegraph/scan.py",
     "        if n / max(self.files, counted) <= MIN_SHARE:",
     "        if n / counted <= MIN_SHARE:  # mutated",
     "a handful of known files among many unknown earns nothing"),

    ("an exact tie is settled by insertion order again",
     "homegraph/scan.py",
     "        if n / max(self.files, counted) <= MIN_SHARE:",
     "        if n / max(self.files, counted) < MIN_SHARE:  # mutated",
     "an exact tie earns nothing rather than insertion order"),

    # -- retired roles are not proposed ---------------------------------
    ("the scanner proposes roles the config does not carry",
     "homegraph/scan.py",
     "        if role in roles:\n            roles[role].append(st.name)",
     "        if role:\n            roles.setdefault(role, []).append(st.name)"
     "  # mutated",
     "a role nothing reads is not proposed"),

    # -- --yes must not imply --force -----------------------------------
    #
    # The original coupling, restored. It shipped, and it meant a scripted
    # `init --yes` replaced a config it had no way to reconstruct.
    ("--yes counts as permission to overwrite again",
     "homegraph/cli.py",
     '    if os.path.exists(path) and not args.force:',
     '    if os.path.exists(path) and not (args.force or args.yes):  # mutated',
     "`init --yes` refuses an existing config"),

    ("the refusal stops leaving the file alone",
     "homegraph/cli.py",
     '    if os.path.exists(path) and not args.force:\n'
     '        print(',
     '    if os.path.exists(path) and not args.force:\n'
     '        open(path, "w").close()  # mutated: truncates before refusing\n'
     '        print(',
     "`--yes` left the file byte-identical"),

    ("--force stops being able to replace anything",
     "homegraph/cli.py",
     '    if os.path.exists(path) and not args.force:',
     '    if os.path.exists(path):  # mutated: nothing may replace a config',
     "`--force` still replaces it"),

    # -- the pruned ratio must not decide the role ----------------------
    #
    # This is the bug the gate was written for, restored exactly: assign the
    # retired `cache` role on the ratio and skip the real one. It shipped, and
    # `init` on a real home directory called a 42-file wiki `cache`.
    ("the pruned ratio decides the role again",
     "homegraph/scan.py",
     '        counted = sum(self.by_category.values())',
     '        if self.mostly_pruned:  # mutated\n'
     '            return None\n'
     '        counted = sum(self.by_category.values())',
     "its own files decide the verdict"),

    ("the pruned ratio stops being reported",
     "homegraph/scan.py",
     '        total = self.files + self.pruned\n'
     '        return total >= MIN_FILES and self.pruned / total >= MIN_SHARE',
     '        return False  # mutated\n'
     '        total = self.files + self.pruned\n'
     '        return total >= MIN_FILES and self.pruned / total >= MIN_SHARE',
     "the pruned ratio is still reported"),

    # -- a retired role must be named, not accepted ----------------------
    #
    # Deleting the retired branch does not make the config load: `cache` then
    # falls through to the unknown-role net, which still refuses it. So the
    # gate this kills is the *message*, not the refusal. Two mechanisms, one
    # specialising the other -- worth stating, because a mutation attributed to
    # the wrong gate is how a checkpoint claims coverage it does not have.
    ("a retired role loses its explanation and reads as a typo",
     "homegraph/userconfig.py",
     '    retired = sorted(set(roles) & set(RETIRED_ROLES))',
     '    retired = []  # mutated: nothing is retired any more',
     "the message names the role and says what replaced it"),

    ("the retirement is announced without saying what replaced it",
     "homegraph/userconfig.py",
     '    "cache": "exclusion is decided by rules/exclusions.toml, not by a '
     'role; "\n             "delete the line",',
     '    "cache": "",  # mutated: named, but not explained',
     "the message names the role and says what replaced it"),

    # -- refusal --------------------------------------------------------
    ("a missing config is silently defaulted instead of refused",
     "homegraph/userconfig.py",
     '    except FileNotFoundError as exc:\n'
     '        raise ConfigMissing(',
     '    except FileNotFoundError as exc:\n'
     '        return UserConfig(path=path, root=os.path.expanduser("~"),\n'
     '                          roles={"image": ("Pictures",)})  # mutated\n'
     '        raise ConfigMissing(',
     "load() refuses when there is no config"),

    ("a config with no root gets one invented for it",
     "homegraph/userconfig.py",
     '    root = raw.get("root")\n'
     '    if not isinstance(root, str) or not root.strip():\n'
     '        raise ConfigMissing(',
     '    root = raw.get("root") or "~"  # mutated: no root is fine now\n'
     '    if False:\n'
     '        raise ConfigMissing(',
     "a config with no root is refused, not defaulted"),

    ("unparseable TOML is reported as a missing config",
     "homegraph/userconfig.py",
     "    except (OSError, tomllib.TOMLDecodeError) as exc:\n"
     '        # A corrupt config is not a missing one',
     "    except (OSError, tomllib.TOMLDecodeError) as exc:\n"
     '        raise ConfigMissing("run init") from exc  # mutated\n'
     '        # A corrupt config is not a missing one',
     "unparseable TOML is not reported as a missing config"),

    ("the refusal loses its instruction",
     "homegraph/cli.py",
     '        print("%s\\n\\nhomegraph does not assume where anything lives. '
     'Run:\\n"\n'
     '              "    homegraph init --root <any directory>\\n"\n'
     '              "and edit the file it writes if the proposal is wrong."\n'
     '              % exc, file=sys.stderr)',
     '        print("%s" % exc, file=sys.stderr)  # mutated: no instruction',
     "the refusal names the command to run"),

    # -- init writes what it proposed -----------------------------------
    ("init writes a config that does not match what it showed",
     "homegraph/cli.py",
     "    userconfig.write(path, root, roles)",
     "    userconfig.write(path, root, {})  # mutated: proposal discarded",
     "the written config matches the proposal"),

    ("init records the wrong root",
     "homegraph/userconfig.py",
     '        \'root = "%s"\' % root,',
     '        \'root = "%s"\' % os.path.expanduser("~"),  # mutated',
     "init recorded the root it was given"),

    ("init accepts unattended without being asked to",
     "homegraph/cli.py",
     '            except EOFError:',
     '            except EOFError:\n'
     '                break  # mutated: silently accept the proposal',
     "init without --yes and without a terminal refuses"),

    # -- the scan -------------------------------------------------------
    ("the scan stops skipping hidden directories",
     "homegraph/scan.py",
     '        if entry.name.startswith("."):\n'
     '            hidden.append(entry.name)\n'
     '            continue',
     '        if False:  # mutated: an icon theme is now a photo library\n'
     '            hidden.append(entry.name)\n'
     '            continue',
     "init proposes the English image directory"),

    ("the scan proposes on any majority, however thin",
     "homegraph/scan.py",
     "        if n / max(self.files, counted) <= MIN_SHARE:\n            return None",
     "        if False:  # mutated: a plurality of one is enough\n"
     "            return None",
     "a thin majority earns nothing"),

    ("the scan proposes from too little evidence",
     "homegraph/scan.py",
     "        if counted < MIN_FILES:\n            return None",
     "        if counted < 1:  # mutated: two files are a photo library\n"
     "            return None",
     "too few files earns nothing"),

    # -- the config reaching a label ------------------------------------
    ("the image role is read but never used",
     "homegraph/corpus.py",
     '        lists = {"{image_roots}": list(\n'
     '            self.config.role_dirs("image", base=self.home))}',
     '        lists = {"{image_roots}": ["/"]}  # mutated: everything is an '
     'image root',
     "emptying the image role removes every image"),

    ("images with no image root fall into the junk drawer",
     "homegraph/corpus.py",
     "        if ext in self._image_exts and not any(\n"
     "                p.startswith(r) for r in self._image_roots):",
     "        if ext in self._image_exts and self._image_roots and not any(\n"
     "                p.startswith(r) for r in self._image_roots):  # mutated",
     "images with no root are excluded, never demoted to misc"),

    ("a role that is not the configured one is used anyway",
     "homegraph/userconfig.py",
     '        for entry in self.roles.get(name, ()):',
     '        for entry in (self.roles.get(name, ())\n'
     '                      or ("Bilder", "Pictures")):  # mutated: fallback',
     "emptying the image role removes every image"),

    # -- machine-specific markers ---------------------------------------
    ("the config replaces the shipped generated marker instead of adding to it",
     "homegraph/models/m3_markdown.py",
     '    for marker in tuple(GENERATED_MARKERS) + tuple(\n'
     '            rules.get("generated_markers", ())):',
     '    for marker in tuple(rules.get("generated_markers",\n'
     '                                  GENERATED_MARKERS)):  # mutated',
     "configuring a directory does not disable the shipped marker"),

    ("a machine-specific directory name is shipped in the package again",
     "homegraph/models/m3_markdown.py",
     'GENERATED_MARKERS = ("GRAPH_REPORT.md",)',
     'GENERATED_MARKERS = ("GRAPH_REPORT.md", "/graph-export/")  # mutated',
     "an unconfigured generated directory is an ordinary note"),

    # -- the scanner must not become a second classifier -----------------
    ("a build starts consulting the scanner",
     "homegraph/models/m2_build.py",
     "from .. import userconfig",
     "from .. import scan  # mutated: a second opinion about the layout\n"
     "from .. import userconfig",
     "only `init` imports the scanner"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp7.py")],
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
        tree = tempfile.mkdtemp(prefix="mut7-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-52s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-52s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-52s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-52s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-52s unclassified" % name)
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
