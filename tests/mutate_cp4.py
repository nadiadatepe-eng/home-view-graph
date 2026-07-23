#!/usr/bin/env python3
"""Mutation test for CP-4.

The first mutation is the one that matters. Every other check here confirms
that the build does not read image files -- but a test asserting an absence
passes just as happily when the detector is broken as when the behaviour is
correct. So mutation one makes the build actually open an image, and both
detectors have to notice.

If that mutation ever survives, the strace gate and the audit hook are
decoration and M2 has no safety argument at all.

Run:
    python3 tests/mutate_cp4.py
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
    # -- what M2 builds out of names alone --------------------------------
    ("collections are counted but never become nodes",
     "homegraph/models/m2_build.py",
     '        store.upsert_node("collection:%s" % coll, kind="collection",\n'
     '                          subtype="collection", title=coll, body=coll,\n'
     "                          as_of=as_of)",
     "        pass  # mutated: collections exist only in the report",
     "collections become nodes"),

    ("every image lands in one collection",
     "homegraph/models/m2_build.py",
     "        coll = collection_of(path, roots)\n"
     "        report.collections[coll] += 1",
     '        coll = "all"  # mutated: one bucket for everything\n'
     "        report.collections[coll] += 1",
     "collection member counts match the key"),

    # A series that admits any date stops being a series: the whole claim is
    # that the same stem on a DIFFERENT date is a different group.
    ("every image is folded into a single series",
     "homegraph/models/m2_build.py",
     '            key = "series:%s/%s" % (coll, info.series_stem)\n'
     "            report.series[key] += 1",
     '            key = "series:any"  # mutated: one series for everything\n'
     "            report.series[key] += 1",
     "the declared series groups as one"),

    ("likely copies are merged into one node instead of linked",
     "homegraph/models/m2_build.py",
     "    _link_copies(store, infos, as_of, report)",
     "    pass  # mutated: no LIKELY_COPY edge is ever written",
     "LIKELY_COPY edges exist"),

    # The invariant M2 is named for. `content_hash=None` is not an oversight:
    # a hash means the file was read, and M2 must never read one.
    ("M2 starts hashing the images it indexes",
     "homegraph/models/m2_build.py",
     "            size=st[\"size\"], mtime=st[\"mtime\"], content_hash=None, as_of=as_of)",
     "            size=st[\"size\"], mtime=st[\"mtime\"],  # mutated: reads the file\n"
     "            content_hash=__import__('hashlib').sha256(\n"
     "                open(path, 'rb').read()).hexdigest(), as_of=as_of)",
     "audit hook saw no image opened"),

    ("a malformed date raises instead of being flagged",
     "homegraph/models/m2_images.py",
     "        m = self.re_malformed.search(work)\n"
     "        if m:\n"
     "            info.malformed_date = True",
     "        m = self.re_malformed.search(work)\n"
     "        if m:  # mutated: an unparseable name is now fatal\n"
     "            raise ValueError('malformed date in %s' % work)",
     "malformed dates flagged, not raised"),

    ("build actually reads an image",
     "homegraph/models/m2_build.py",
     "        info = parser.parse(path)\n        st = stat_only(path)",
     "        info = parser.parse(path)\n"
     "        with open(path, 'rb') as _fh:  # mutated: the forbidden act\n"
     "            _fh.read(16)\n"
     "        st = stat_only(path)",
     "strace confirms no image was read"),

    ("malformed 9-digit dates parsed as real ones",
     "homegraph/rules/filenames.toml",
     "malformed = '(?<![\\d])(\\d{9,})(?![\\d])'",
     "malformed = '(?!x)x'  # mutated: never matches",
     "hand-read filenames"),

    ("YYMMDD/DDMMYY fallback disabled",
     "homegraph/rules/filenames.toml",
     "yymmdd_vs_ddmmyy = true",
     "yymmdd_vs_ddmmyy = false  # mutated: future dates accepted",
     "future-dated YYMMDD falls back to DDMMYY"),

    ("DPI markers read as series indices",
     "homegraph/models/m2_images.py",
     "        m = self.re_dpi.search(work)\n        if m:\n"
     "            info.dpi = int(m.group(1))\n"
     "            work = self._blank(work, m.span())",
     "        pass  # mutated: dpi left in place for the index regex",
     "hand-read filenames"),

    ("series stem taken from the blanked text",
     "homegraph/models/m2_images.py",
     '        info.series_stem = (stem[:first.start()].strip(" _-#")\n'
     '                            if first else stem.strip(" _-#"))',
     '        info.series_stem = (work[:first.start()].strip(" _-#")\n'
     '                            if first else work.strip(" _-#"))',
     "the declared series groups as one"),

    ("blanking deletes instead of padding",
     "homegraph/models/m2_images.py",
     '        return text[:span[0]] + " " * (span[1] - span[0]) + text[span[1]:]',
     "        return text[:span[0]] + text[span[1]:]  # mutated: offsets shift",
     # Padding exists so that the series stem, sliced from the ORIGINAL stem,
     # still lines up after tokens are consumed. Deleting instead of padding
     # shifts those offsets, so the series gate is the gate that sees it.
     "the declared series groups as one"),

    ("non-image files under the image root become image nodes",
     "homegraph/models/m2_build.py",
     "        if os.path.splitext(path)[1].lower() not in IMAGE_EXT:",
     "        if False:  # mutated: everything under the image root is an image",
     "image count matches the corpus layer"),

    # `init` walks the image directory before M2 exists to promise anything.
    # If the scanner reads a file, no gate inside M2 can see it -- these two
    # make the init verification real rather than decorative.
    ("the init scan reads the files it tallies",
     "homegraph/scan.py",
     "            category = ext_map.get(_ext_of(fname))",
     "            with open(os.path.join(dirpath, fname), 'rb') as _fh:\n"
     "                _fh.read(16)  # mutated: the forbidden act\n"
     "            category = ext_map.get(_ext_of(fname))",
     "strace confirms init read no image"),

    ("the init scan proposes nothing at all",
     "homegraph/scan.py",
     "        role = st.role\n        if role in roles:\n            roles[role].append(st.name)",
     "        pass  # mutated: no role is ever proposed",
     "init scan reached the image directory"),

    ("content hashes computed after all",
     "homegraph/models/m2_build.py",
     "            size=st[\"size\"], mtime=st[\"mtime\"], content_hash=None, as_of=as_of)",
     "            size=st[\"size\"], mtime=st[\"mtime\"],\n"
     "            content_hash='deadbeef', as_of=as_of)",
     "no content hashes exist"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp4.py")],
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
        tree = tempfile.mkdtemp(prefix="mut4-",
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
