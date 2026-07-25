#!/usr/bin/env python3
"""Mutation test for CP-I3 -- the Obsidian vault writer.

Every mechanism `obsidian.py` claims is neutralised here, and each must
redden a gate that NAMES it. Two of these mutations are not hypothetical:
mutation 2 and mutation 6 are the two defects the gates actually caught while
this checkpoint was being written, kept as mutations so a later refactor
cannot quietly reintroduce either.

Run:
    python3 tests/mutate_i3.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 300

MUTATIONS = [
    # 1. The writer decides for itself what a level carries, instead of asking
    # the one function that owns the answer. This is the whole premise of the
    # module: a second exporter would leak differently from the first.
    ("the writer skips redaction and prints the raw row",
     "homegraph/obsidian.py",
     "                row = redact(row, redaction)",
     "                pass  # mutated: no redaction",
     "and the file's text is nowhere in the vault"),

    # 2. FOUND BY THE GATE. Only node rows are redacted, so at `shape` the
    # edges still carry readable keys -- which no longer match the hashed node
    # names, so every link silently disappears from a vault that still counts
    # itself complete.
    ("only node rows are redacted, so edges keep readable keys",
     "homegraph/obsidian.py",
     "                row = redact(row, redaction)\n"
     '                if row["t"] == "node":',
     '                if row["t"] == "node":\n'
     "                    row = redact(row, redaction)  # mutated: nodes only",
     "shape: every link lands on a note that exists"),

    # 3. The naming pass ignores the level, so at `shape` the map is built
    # from unhashed keys and the writing pass looks up a key that is not
    # there. This was the first defect the checkpoint found.
    ("the naming pass ignores the redaction level",
     "homegraph/obsidian.py",
     '    return [redact({"node_key": to_portable(r["node_key"], root)},\n'
     '                   level)["node_key"]',
     '    return [to_portable(r["node_key"], root)  # mutated: level ignored',
     "shape: every link lands on a note that exists"),

    # 4. Collisions are detected case-sensitively. Both files survive on ext4,
    # so nothing looks wrong here -- the vault is simply broken on any
    # case-insensitive filesystem, which is where a lot of vaults live.
    ("collisions are detected case-sensitively",
     "homegraph/obsidian.py",
     "        groups.setdefault(_candidate(key).casefold(), []).append(key)",
     "        groups.setdefault(_candidate(key), []).append(key)  # mutated",
     "and no two names collide even case-folded"),

    # 5. Colliding names are never disambiguated, so one note overwrites
    # another and a node vanishes from a vault whose report still counts it.
    ("colliding names are never disambiguated",
     "homegraph/obsidian.py",
     "        if len(groups[base.casefold()]) > 1:",
     "        if False:  # mutated: no disambiguation",
     "every node got its own file on disk"),

    # 6. FOUND BY THE GATE. The containment check is bound to the vault rather
    # than to the model directory, so a single `..` climbs out of `m3/` into
    # the vault root -- still inside the vault, and still the wrong place.
    ("containment is checked against the vault, not the model directory",
     "homegraph/obsidian.py",
     "    folder = os.path.realpath(folder)\n"
     "    resolved = os.path.realpath(target)\n"
     "    return resolved.startswith(folder + os.sep)",
     "    folder = os.path.realpath(os.path.dirname(folder))  # mutated\n"
     "    resolved = os.path.realpath(target)\n"
     "    return resolved.startswith(folder + os.sep)",
     "a name that escapes its model directory is refused at the write"),

    # 7. The containment check is gone entirely.
    ("nothing checks where the note actually lands",
     "homegraph/obsidian.py",
     "            if not _inside(folder, target):",
     "            if False:  # mutated: guard removed",
     "a name that escapes its model directory is refused at the write"),

    # 8. A populated vault is written into without being asked. The mistake
    # this guards is pointing the command at a vault of hand-written notes.
    ("a vault that already holds notes is written into anyway",
     "homegraph/obsidian.py",
     "    if os.path.isdir(vault) and os.listdir(vault) and not force:",
     "    if False:  # mutated: no guard",
     "a vault that already holds files is refused"),

    # 9. Every relation becomes a clickable link, including the 1674
    # `SAME_FORMAT` edges that mean "these two files share a magic number".
    ("every relation becomes a wikilink",
     "homegraph/obsidian.py",
     "        heading = LINK_RELATIONS.get(rel)",
     '        heading = LINK_RELATIONS.get(rel, "Related")  # mutated',
     "a non-link relation does not become a clickable link"),

    # 10. Frontmatter values are emitted unquoted, so a title containing a
    # newline or a colon writes a second key -- or ends the block early, at
    # which point the rest renders as body text.
    ("frontmatter values are emitted unquoted",
     "homegraph/obsidian.py",
     "    return '\"%s\"' % text",
     "    return text  # mutated: unquoted",
     "and every value is quoted, so none can be reread as syntax"),

    # 10b. The quotes stay but the escaping goes, so a title carrying a real
    # newline ends its own line and the rest of the block becomes body text.
    # Kept separate from 10 because the mutation run showed they are two
    # mechanisms: removing the quotes alone left the newline gate green.
    ("frontmatter values are quoted but not escaped",
     "homegraph/obsidian.py",
     '    text = text.replace("\\n", "\\\\n").replace("\\r", "\\\\r")'
     '.replace("\\t", "\\\\t")',
     "    pass  # mutated: no escaping",
     "a title containing a newline does not become two YAML lines"),

    # 11. Forbidden characters are left in the name, so a `#` turns
    # `[[a#b]]` into a link to a SECTION of `a` and a `/` invents a directory.
    ("forbidden characters are left in note names",
     "homegraph/obsidian.py",
     '    stem = "".join(" " if ch in FORBIDDEN else ch for ch in stem)',
     "    stem = stem  # mutated: nothing replaced",
     "and none carries a character the name may not hold"),

    # 12. Names are never cut, so a long key produces a filename the
    # filesystem refuses -- on the measured corpus there are keys well past
    # 255 bytes.
    ("note names are never truncated",
     "homegraph/obsidian.py",
     '    return _truncate_bytes(stem, MAX_STEM_BYTES) or "untitled"',
     '    return stem or "untitled"  # mutated: no truncation',
     "no note name exceeds what the filesystem accepts"),

    # 13. A missing store is not named. `Store()` CREATES the file it cannot
    # find, so without this check the export succeeds against an empty
    # database and reports zero notes as though that were the answer.
    ("a missing store is opened rather than refused",
     "homegraph/obsidian.py",
     '            raise ExportError("no store for %s at %s" % (model, path))',
     "            pass  # mutated: missing store accepted",
     "a missing store is refused by name, not by traceback"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_i3.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="muti3-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__", ".git"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-56s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>" for r in red)
            gate_red = [r for r in red
                        if not r.startswith("<crash>") and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-56s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-56s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-56s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-56s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-56s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d by a different gate, %d crash-only, "
          "%d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
