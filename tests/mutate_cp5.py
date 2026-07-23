#!/usr/bin/env python3
"""Mutation test for CP-5.

M4 is the complement of every other model, so its failures are absences: a file
that quietly stopped being counted, a secret that quietly got indexed, a rollup
that quietly lost its arithmetic. None of those raise. Each mutation below
creates one of them on purpose.
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
    ("an extension's build output is listed like the user's own filing",
     "homegraph/models/m4_misc.py",
     "    if os.path.splitext(path)[1].lower() in UNLISTED_ARCHIVES:\n"
     "        report.unlisted_by_policy.append(path)\n"
     "        return",
     "    pass  # mutated: .xpi contents come back into the graph",
     "a declined archive draws no edges and is reported as declined"),

    ("declining an archive is silent",
     "homegraph/models/m4_misc.py",
     "        report.unlisted_by_policy.append(path)",
     "        pass  # mutated: skipped on purpose looks like never seen",
     "a declined archive draws no edges and is reported as declined"),

    # -- ARCHIVE_CONTAINS -------------------------------------------------
    ("an archive lists every member, not one level",
     "homegraph/models/m4_misc.py",
     '                entry = head + ("/" if sep else "")',
     '                entry = name  # mutated: every member becomes an entry',
     "ARCHIVE_CONTAINS is exactly the declared listing"),

    ("an unlistable archive reports as an empty one",
     "homegraph/models/m4_misc.py",
     "        return None\n    return names",
     "        return []  # mutated: broken and empty become one answer\n"
     "    return names",
     "an empty archive and an unlistable one differ"),

    ("the archive that could not be opened is not counted",
     "homegraph/models/m4_misc.py",
     '        report.unlistable_archives.append((path, "not listable"))',
     "        pass  # mutated: the failure is swallowed",
     "an unlistable archive is counted, a gzip is not an error"),

    ("entries are listed but never linked to their archive",
     "homegraph/models/m4_misc.py",
     '        store.upsert_edge(path, key, "ARCHIVE_CONTAINS", as_of, method="exact")',
     "        pass  # mutated: the entry node floats free",
     "ARCHIVE_CONTAINS is exactly the declared listing"),

    ("an archive entry claims a path on disk",
     "homegraph/models/m4_misc.py",
     '        store.upsert_node(key, kind="archive_entry", subtype="entry",\n'
     "                          path=None, title=entry,",
     '        store.upsert_node(key, kind="archive_entry", subtype="entry",\n'
     "                          path=path, title=entry,  # mutated: a path no stat can confirm",
     "an archive entry carries no filesystem path"),

    # -- the barrier M4 exists behind -------------------------------------
    #
    # M4 reaches the widest part of the disk, so the claim that it stores
    # filenames and schema and never contents is the one with real
    # consequences. It had no mutation against it.
    ("M4 starts storing file contents as the body",
     "homegraph/models/m4_misc.py",
     "        body = os.path.basename(path)",
     "        body = os.path.basename(path)\n"
     "        try:  # mutated: contents indexed\n"
     "            body += ' ' + open(path, 'rb').read(4096).decode(\n"
     "                'utf-8', 'replace')\n"
     "        except OSError:\n"
     "            pass",
     "no secret body reaches the FTS index even when indexed"),

    ("sqlite rows are read along with the schema",
     "homegraph/models/m4_misc.py",
     '                body += " | tables: " + ", ".join(tables[:40])',
     '                body += " | tables: " + ", ".join(tables[:40])\n'
     "                import sqlite3 as _s3  # mutated: rows indexed too\n"
     "                _c = _s3.connect(path)\n"
     "                for _t in tables[:5]:\n"
     "                    body += ' ' + repr(_c.execute(\n"
     "                        'SELECT * FROM \"%s\" LIMIT 5' % _t).fetchall())\n"
     "                _c.close()",
     "sqlite row contents never reach the index"),

    ("the sqlite schema is read and then dropped",
     "homegraph/models/m4_misc.py",
     "            tables = sqlite_schema(path)",
     "            tables = []  # mutated: schema never reaches the index",
     "the schema did reach the index"),

    # -- typing --------------------------------------------------------
    ("content sniffing gives way to the extension",
     "homegraph/models/m4_misc.py",
     "def sniff(path, size=None):",
     "def sniff(path, size=None):\n"
     "    import os as _os  # mutated: extension is the answer again\n"
     "    return _os.path.splitext(path)[1].lstrip('.').lower() or 'data'",
     "magic numbers beat missing extensions"),

    # -- rollup accounting ------------------------------------------------
    ("rolled-up bytes are counted but not summed",
     "homegraph/models/m4_misc.py",
     '            slot["bytes"] += st.st_size',
     '            slot["bytes"] += 0  # mutated: byte totals go to zero',
     "rollup byte totals reconcile"),

    ("warm files are rolled up along with the cold ones",
     "homegraph/models/m4_misc.py",
     "            report.rolled_up_files += 1\n            continue",
     "            report.rolled_up_files += 1\n"
     "            continue\n"
     "        if True:  # mutated: nothing keeps an individual node\n"
     "            rollup[(app, '0000-00')]['count'] += 1\n"
     "            continue",
     "warm files keep individual nodes"),

    ("every row gets a label, and some get two",
     "homegraph/corpus.py",
     "    def classify(self, path: str, is_symlink: bool | None = None) -> str:\n"
     "        return self.explain(path, is_symlink=is_symlink).label",
     "    def classify(self, path: str, is_symlink: bool | None = None) -> str:\n"
     "        d = self.explain(path, is_symlink=is_symlink)\n"
     "        return d.label if d.label != 'misc' else 'misc '  # mutated",
     "content-based typing, not extension"),

    ("extension beats magic number",
     "homegraph/models/m4_misc.py",
     "    for magic, subtype, detected in MAGIC:\n"
     "        if head.startswith(magic):\n"
     "            return subtype, detected\n"
     "    if ext in EXT_HINTS:\n"
     "        return EXT_HINTS[ext]",
     "    if ext in EXT_HINTS:\n"
     "        return EXT_HINTS[ext]\n"
     "    for magic, subtype, detected in MAGIC:\n"
     "        if head.startswith(magic):\n"
     "            return subtype, detected",
     "magic beats a lying extension"),

    ("sqlite rows indexed alongside the schema",
     "homegraph/models/m4_misc.py",
     '                "SELECT name FROM sqlite_master WHERE type=\'table\' "\n'
     '                "ORDER BY name LIMIT 200").fetchall()\n'
     "            return [r[0] for r in rows]",
     '                "SELECT name FROM sqlite_master WHERE type=\'table\' "\n'
     '                "ORDER BY name LIMIT 200").fetchall()\n'
     "            out = [r[0] for r in rows]\n"
     "            for t in list(out):\n"
     "                try:\n"
     "                    for row in conn.execute('SELECT * FROM \"%s\" LIMIT 5' % t):\n"
     "                        out += [str(v) for v in row]\n"
     "                except Exception:\n"
     "                    pass\n"
     "            return out",
     "sqlite row contents never reach the index"),

    ("secrets layer switched off",
     "homegraph/rules/exclusions.toml",
     'names = [\n  ".env", ".netrc", ".pgpass", ".npmrc", ".git-credentials",',
     'names = [  # mutated: secrets no longer named\n  ".__never__",',
     "all 5 planted secrets are EXCLUDED"),

    ("rollup forgets to count its files",
     "homegraph/models/m4_misc.py",
     '            slot["count"] += 1',
     '            pass  # mutated: rollup loses its arithmetic',
     "rollup counts reconcile with the raw files"),

    ("rollup never triggers",
     "homegraph/models/m4_misc.py",
     "ROLLUP_AFTER_DAYS = 90",
     "ROLLUP_AFTER_DAYS = 999999  # mutated: everything stays individual",
     "rollup reduces node count"),

    ("rolled-up files stop being accounted for",
     "homegraph/models/m4_misc.py",
     "            report.rolled_up_files += 1",
     "            pass  # mutated: files vanish between the counts",
     "every M4 file is accounted for"),

    ("large files read in full",
     "homegraph/models/m4_misc.py",
     "        if st.st_size > LARGE_FILE:\n"
     '            subtype, detected = "binary", "large"',
     "        if False:  # mutated: no size cap\n"
     '            subtype, detected = "binary", "large"',
     "large files are metadata only"),

    ("images stop being a category and fall through to M4",
     "homegraph/rules/categories.toml",
     'extensions = [\n  "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif",\n  "svg", "ico", "avif",\n]',
     'extensions = []  # mutated: nothing is an image, so images land in misc',
     "no image file reached M4"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp5.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        # A mutation that makes the suite die before reaching its assertions
        # is DETECTED, but not by any gate. Kept separate from a real kill:
        # counting it as one made the `expected` field decorative, and an
        # injected mutation that only broke an import was reported as killed.
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut5-",
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
                # Red, but not the gate that was supposed to catch it. Still a
                # kill; the attribution is wrong and worth seeing.
                print("misattrib %-44s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                # Detected only because the process died. No gate said no.
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
