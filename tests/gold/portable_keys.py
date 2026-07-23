#!/usr/bin/env python3
"""The answer key for TODO-E1: which node keys carry a root, and what each one
must look like once it does not.

**Written before `portable.py` existed.** Every row was read out of the code
that MAKES these keys -- `m1_build`, `m3_build`, `m4_misc`, `mesh` -- and not
out of the converter that is graded by it. A key derived from the converter
would be a photograph of it, which is the failure `tests/fixtures/synthetic.py`
opens by warning about.

The root in every row is `/root`, chosen because it is short and because a
converter that special-cases home directories would pass a key full of
`/home/...` and fail on a project directory or an external disk. The whole
point of TODO-E is that the root is whatever the user picked.

Columns: (local key, portable key, why this row exists).
"""
from __future__ import annotations

ROOT = "/root"

# The nine forms, one row each. `~` in a portable key is the marker for "this
# was the export root"; it is not a home directory and is never expanded.
KEY_FASIT = [
    ("/root/Documents/report.docx", "~/Documents/report.docx",
     "the ordinary case: a file node's key IS its absolute path"),

    ("/root/Documents/report.docx#3", "~/Documents/report.docx#3",
     "a section: the offset suffix is part of the key and must survive"),

    ("archive:/root/proj/bundle.zip!src/", "archive:~/proj/bundle.zip!src/",
     "an archive entry: the path sits INSIDE the key, between a prefix and a "
     "`!` separator, and the entry name after it may itself contain slashes"),

    ("code::/root/proj/api/handler.js", "code::~/proj/api/handler.js",
     "a mesh code stub: the model prefix is not part of the path"),

    ("m3::/root/notes/plan.md", "m3::~/notes/plan.md",
     "a mesh mirror node: same shape, different prefix"),

    ("m3::/root/notes/plan.md#0", "m3::~/notes/plan.md#0",
     "both at once -- a mirrored section. The two rules must compose rather "
     "than one of them winning"),

    ("author:N. Writer", "author:N. Writer",
     "an author node carries a person's name and no path: unchanged"),

    ("ref:doi:10.1234/abcd-25-00031-5", "ref:doi:10.1234/abcd-25-00031-5",
     "a citation target: unchanged, and note it contains a `/` that is NOT a "
     "path separator"),

    ("wikilink:some-page", "wikilink:some-page",
     "an unresolved wikilink: a page name, never a path"),

    ("app:thunderbird", "app:thunderbird",
     "an owning application: unchanged"),

    ("format:sqlite", "format:sqlite",
     "a detected format: unchanged"),

    ("rollup:home:2026-03", "rollup:home:2026-03",
     "a cold-state rollup: two colons and no path"),
]

# The four that decide whether the converter was written carefully or quickly.
ADVERSARIAL = [
    ("/root/notes/C# and F#.md", "~/notes/C# and F#.md",
     "a `#` in the FILENAME, twice. Splitting on the first `#` to find a "
     "section offset turns this file into a section of a file that does not "
     "exist"),

    ("/root/proj/weird::name.py", "~/proj/weird::name.py",
     "a `::` in the filename. Splitting on the first `::` to find a model "
     "prefix invents the model `/root/proj/weird`"),

    ("/rootless/other.md", None,
     "a path OUTSIDE the root that merely starts with the same characters. "
     "`/rootless` is not under `/root`, and a prefix test on the string "
     "rather than on the path boundary says it is. None means: refuse"),

    ("/elsewhere/file.md", None,
     "a path outside the root entirely. Refused, not silently emitted with "
     "`..` segments that would escape the new root on import"),
]

# Roots that are themselves awkward. (root, local key, portable key).
ROOT_CASES = [
    ("/root/", "/root/Documents/a.md", "~/Documents/a.md",
     "a trailing slash on the root must not produce `~//Documents`"),

    ("/", "/Documents/a.md", "~/Documents/a.md",
     "the filesystem root as the corpus root: every absolute path is inside "
     "it, and the join back must not double the leading slash"),

    ("/root", "/root", "~",
     "the root itself as a node. Degenerate and real: `homegraph init` "
     "accepts any directory, and a file can sit at the top of it"),
]
