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
import sys

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

    # The third copy of "what is an image". The sets agreed, so the duplicate
    # was invisible; what it invited is one-sided -- categories.toml grows an
    # extension, the classifier sends the file to M2, and M2 drops it into a
    # counter whose comment makes the loss read as intended.
    ("m2 goes back to its own hand-written extension list",
     "homegraph/models/m2_build.py",
     '        return frozenset("." + e for e in tomllib.load(fh)["image"]["extensions"])',
     '        tomllib.load(fh)  # mutated: read, then ignored\n'
     '    return frozenset({".png", ".jpg", ".gif"})',
     "image count matches the corpus layer"),

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
     "        if os.path.splitext(path)[1].lower() not in image_extensions():",
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp4.py", prefix="mut4-", timeout=300))
