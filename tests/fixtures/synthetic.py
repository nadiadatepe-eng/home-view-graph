#!/usr/bin/env python3
"""Build a synthetic home directory that carries the real corpus's traps.

The checkpoints were written against one person's actual filesystem, which made
them unrunnable by anyone else and unpublishable without leaking 588 589 real
paths. This rebuilds the interesting part: every adversarial case the gold sets
found, planted deliberately, in a tree that generates identically anywhere.

**The keys here are declared, not derived.** Each planted file states what it is
at the moment it is created, and nothing in this file ever calls `classify()`,
`FilenameParser`, `MarkdownExtractor` or `extract()`. That covers all six keys
CP-0 to CP-6 need: the corpus labels (`CASES`), the filename readings
(`FILENAME_FASIT`), the link relations (`LINK_FASIT`), the document metadata
(`DOCUMENT_FASIT`), the collection and corpus sizes, and the cross-model
FIGURE_FOR pairs. Deriving any of them from the code would make the key a
photograph of the implementation rather than a check on it -- the same failure
as writing the key after the rules, and just as invisible.

Every case below exists because a real file produced it. The comments say which,
without naming the file, since naming it is the thing we are avoiding.

Deterministic: fixed content, fixed mtimes, no randomness and no clock reads.
The same call produces byte-identical output on any machine.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import struct
import zipfile
import zlib
from datetime import date

# 2026-07-01 12:00:00 UTC, fixed so mtime-dependent behaviour is reproducible.
# Deliberately INSIDE M4's 90-day rollup window relative to AS_OF: with an older
# stamp every misc file is rolled up, no individual node is ever written, and
# every gate that counts nodes in the junk drawer passes by having nothing to
# count. That is the vacuous-truth shape this project keeps finding.
FIXED_MTIME = 1782907200.0
OLD_MTIME = FIXED_MTIME - 200 * 86400          # past M4's 90-day rollup cutoff

# The date every checkpoint runs "as of". Fixed here rather than in each test,
# because two of the declared answers below depend on it: a YYMMDD date is only
# ambiguous relative to some notion of today, and the rollup cutoff is 90 days
# back from it. A test that read the clock would answer differently in 2027.
AS_OF = date(2026, 7, 22)

# label, subtype, hard, relative path, why
Case = tuple[str, str, bool, str, str]


def _write(root, rel, content, mtime=FIXED_MTIME, binary=False):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if binary else "w"
    with open(path, mode) as fh:
        fh.write(content)
    os.utime(path, (mtime, mtime))
    return path


def _zip_docx(root, rel, title="", creator="", body="Body text."):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0"?><cp:coreProperties '
                   'xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
                   'metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:title>%s</dc:title><dc:creator>%s</dc:creator>'
                   '</cp:coreProperties>' % (title, creator))
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>'
                   '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                   '<w:r><w:t>A Heading</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>%s</w:t></w:r></w:p>'
                   '</w:body></w:document>' % (W, body))
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return path


def _zip_odt(root, rel, pages=3, creator="", heading="Heading"):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    NS_O = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    T = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    M = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
    with zipfile.ZipFile(path, "w") as z:
        # Page count lives in an ATTRIBUTE, not element text -- reading it as
        # text was a real bug the answer key caught.
        z.writestr("meta.xml",
                   '<?xml version="1.0"?><office:document-meta '
                   'xmlns:office="%s" xmlns:meta="%s" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<office:meta><dc:creator>%s</dc:creator>'
                   '<meta:document-statistic meta:page-count="%d"/>'
                   '</office:meta></office:document-meta>' % (NS_O, M, creator, pages))
        z.writestr("content.xml",
                   '<?xml version="1.0"?><office:document-content '
                   'xmlns:office="%s" xmlns:text="%s"><office:body>'
                   '<text:h text:outline-level="1">%s</text:h>'
                   '<text:p>Some prose.</text:p>'
                   '</office:body></office:document-content>' % (NS_O, T, heading))
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return path


def _zip_bundle(root, rel, members):
    """A plain zip holding `members`. Deterministic: fixed dates, no clock.

    Members carry content because an archive of empty files still lists, and
    the point of the entry list is that it is read from the central directory
    without any member being opened.
    """
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name in members:
            info = zipfile.ZipInfo(name, date_time=(2026, 7, 1, 12, 0, 0))
            z.writestr(info, "contents of %s\n" % name)
    os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    return path


def _pdf(root, rel, title=None, author=None, pages=1, text=None,
         encrypted=False, utf16_title=False, doi=None, fonts=True):
    """A small but genuinely parseable PDF.

    Text goes in a Flate-compressed content stream with BT/ET and a Tj, which
    is what the extractor looks for. Not a real PDF writer -- just enough
    structure that the reader's actual code path runs.
    """
    parts = [b"%PDF-1.4\n"]
    for _ in range(pages):
        # With fonts and no text stream a PDF is a scan (needs_ocr); without
        # fonts it is something else entirely (partial). Both branches exist in
        # the extractor, so both are planted.
        parts.append(b"1 0 obj\n<< /Type /Page"
                     + (b" /Font 9 0 R" if fonts else b"")
                     + b" >>\nendobj\n")
    if encrypted:
        parts.append(b"2 0 obj\n<< /Encrypt 3 0 R >>\nendobj\n")
    if text is not None:
        body = ("BT /F1 12 Tf (%s) Tj ET" % text).encode("latin-1")
        stream = zlib.compress(body)
        parts.append(b"4 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n"
                     % len(stream) + stream + b"\nendstream\nendobj\n")
    info = b"5 0 obj\n<< "
    if title is not None:
        if utf16_title:
            info += b"/Title <" + (b"\xfe\xff" + title.encode("utf-16-be")).hex(
            ).encode() + b"> "
        else:
            info += b"/Title (" + title.encode("latin-1") + b") "
    if author:
        info += b"/Author (" + author.encode("latin-1") + b") "
    if doi:
        info += b"/Subject (doi:" + doi.encode("latin-1") + b") "
    info += b">>\nendobj\n"
    parts.append(info)
    if pages > 1:
        parts.append(b"6 0 obj\n<< /Type /Pages /Count %d >>\nendobj\n" % pages)
    else:
        # /Count says 0 while one page exists -- trusting it gave a zero-page
        # document, which the answer key caught.
        parts.append(b"6 0 obj\n<< /Type /Pages /Count 0 >>\nendobj\n")
    parts.append(b"trailer\n<< /Info 5 0 R >>\n%%EOF\n")
    return _write(root, rel, b"".join(parts), binary=True)


def _png(root, rel, size=64):
    """A PNG header and padding. Never opened by M2 -- only stat()ed."""
    return _write(root, rel, b"\x89PNG\r\n\x1a\n" + struct.pack(">I", size)
                  + b"\x00" * size, binary=True)


# -- declared answer keys --------------------------------------------------
#
# Everything below states what the fixture MEANS. None of it is produced by
# running homegraph over the fixture: no classify(), no FilenameParser, no
# extract(), no MarkdownExtractor. A key computed from the thing it grades is a
# photograph of the implementation, and photographs agree with themselves.

# CP-4. Read by hand from the filename grammar, exactly as the real answer key
# was: date, date kind, series indices, resolution, DPI, copy marker, variant.
# The reference date is AS_OF -- one row is only decidable relative to it.
#
#           relative path, date, kind, indices, resolution, dpi, copy, variant,
#           adversarial, why
FILENAME_FASIT = [
    ("Bilder/Art/Experiments-2025/03122025_1.png",
     "2025-12-03", "ddmmyyyy", "1", "-", "-", "n", "-", False,
     "DDMMYYYY plus a series index -- the plain case"),
    ("Bilder/Art/Experiments-2025/03122025_2.png",
     "2025-12-03", "ddmmyyyy", "2", "-", "-", "n", "-", False,
     "same series"),
    ("Bilder/Art/Experiments-2025/03122025_3.png",
     "2025-12-03", "ddmmyyyy", "3", "-", "-", "n", "-", False,
     "same series"),
    ("Bilder/Art/Experiments-2025/05122025_2.png",
     "2025-12-05", "ddmmyyyy", "2", "-", "-", "n", "-", True,
     "a DIFFERENT date -- must NOT join the series above"),
    ("Bilder/Art/Experiments-2025/04122025_copy.png",
     "2025-12-04", "ddmmyyyy", "-", "-", "-", "y", "-", False,
     "_copy marker, no index"),
    ("Bilder/Art/Experiments-2025/161212025_44_3360x2100.png",
     "-", "malformed", "44", "3360x2100", "-", "n", "-", True,
     "9-digit malformed date, an index, and a resolution; flag it and keep "
     "the node rather than raising"),
    ("Bilder/Art/Experiments-2025/161212025_5.png",
     "-", "malformed", "5", "-", "-", "n", "-", True,
     "the same malformed date, different trailing number"),
    ("Bilder/Art/Experiments-2025/161212025_44_3360x2100_copy.png",
     "-", "malformed", "44", "3360x2100", "-", "y", "-", True,
     "malformed date AND a copy marker after a resolution"),
    ("Bilder/Art/qsave_2020-09-24_003.jpeg",
     "2020-09-24", "iso", "3", "-", "-", "n", "-", False,
     "ISO date; the zero-padded index must not stay a string"),
    ("Bilder/Art/Alice_2020-09-08_011_small.jpeg",
     "2020-09-08", "iso", "11", "-", "-", "n", "small", True,
     "index followed by a size variant"),
    ("Bilder/Art/Apophysis-210403-19.jpeg",
     "2021-04-03", "yymmdd", "19", "-", "-", "n", "-", True,
     "hyphen-separated YYMMDD"),
    ("Bilder/Art/26072020_5_ny.jpeg",
     "2020-07-26", "ddmmyyyy", "5", "-", "-", "n", "ny", True,
     "'ny' is a variant marker, not a word in a title"),
    ("Bilder/Art/Series-2021/Statue_270519_25_1.jpeg",
     "2019-05-27", "ddmmyy", "25,1", "-", "-", "n", "-", True,
     "AMBIGUOUS: as YYMMDD this is 2027-05-19, in the future relative to "
     "AS_OF. Only DDMMYY yields a real date"),
    ("Bilder/Art/Series-2021/Bridge_190430_150dpi.jpeg",
     "2019-04-30", "yymmdd", "-", "-", "150", "n", "-", True,
     "150 is DPI, not an index; the date is unambiguous YYMMDD"),
    ("Bilder/Art/Earlier works/Captivity#2.jpeg",
     "-", "-", "2", "-", "-", "n", "-", True,
     "series marked with '#'; no date anywhere in the name"),
    ("Bilder/Art/Earlier works/Lodestar(2018).jpeg",
     "2018", "year", "-", "-", "-", "n", "-", True,
     "bare year inside parentheses"),
    ("Bilder/Art/Earlier works/Stardust-1_desember_2020.jpg",
     "2020-12", "monthname", "1", "-", "-", "n", "-", True,
     "Norwegian month name; the leading -1 is the series index"),
    ("Bilder/Skjermbilder/Skjermbilde fra 2026-07-18 21-26-52.png",
     "2026-07-18", "iso_dt", "-", "-", "-", "n", "-", True,
     "spaces throughout, and a time the parser must not read as a resolution"),
]

# CP-4. The collection directories under Bilder/ and how many images each holds.
# Written out by hand rather than counted from FILENAME_FASIT, so that moving a
# file without updating the key is a failure and not a silent re-measurement.
IMAGE_COLLECTIONS = {
    "Art": 4,
    "Art/Experiments-2025": 8,
    "Art/Series-2021": 2,
    "Art/Earlier works": 3,
    "Skjermbilder": 1,
}

# CP-4. Files under Bilder/ that are NOT images: one .docx and one .odt.
NON_IMAGE_UNDER_BILDER = 2

# CP-4. Names that must group into one series, and the one that must not.
SERIES_STEM = "03122025"
SERIES_MEMBERS = {"03122025_1.png", "03122025_2.png", "03122025_3.png"}
SERIES_NON_MEMBER = "05122025_2.png"

# CP-2. Fifteen link relations, read off the markdown above.
#
#   LINK    src must have a WIKILINKS_TO edge to target
#   NOLINK  src must NOT -- the text contains [[target]], inside a code span,
#           where it is documentation of the syntax rather than a link
#   TAG     src must have a TAGGED edge to target (from frontmatter)
#   BROKEN  target must exist as a node with subtype 'broken'
#
# The NOLINK rows are the point: each is a string a regex sweep extracts
# happily, and each would become a plausible edge nobody would notice was wrong.
LINK_FASIT = [
    ("LINK", "wiki/wiki/concepts/trails.md", "bush",
     "piped alias [[bush|Vannevar Bush]]"),
    ("LINK", "wiki/wiki/concepts/trails.md", "bookkeeping",
     "plain link inside a parenthetical"),
    ("LINK", "wiki/wiki/concepts/trails.md", "viewer",
     "plain link; closes a cycle with the viewer row below"),
    ("LINK", "wiki/wiki/concepts/trails.md", "summaries-note",
     "ambiguous target: two files carry this name"),
    ("NOLINK", "wiki/wiki/concepts/trails.md", "wikilinks",
     "`[[wikilinks]]` is in an inline code span -- it names the syntax"),
    ("NOLINK", "wiki/wiki/concepts/trails.md", "gamma",
     "inside a fenced block"),
    ("LINK", "wiki/wiki/entities/viewer.md", "trails",
     "piped alias [[trails|the trails]]; closes the cycle"),
    ("LINK", "docs/adding.mdx", "bush",
     ".mdx, not .md -- the extension list has to cover it"),
    ("LINK", "wiki/wiki/entities/graphify.md", "bush",
     "piped alias immediately followed by a possessive 's"),
    ("LINK", "wiki/wiki/entities/graphify.md", "trails",
     "the link sits between two real code spans on one line"),
    ("LINK", "wiki/wiki/entities/graphify.md", "bookkeeping",
     "inside a parenthetical after a code span on the same line"),
    ("NOLINK", "wiki/index.md", "page-name",
     "inside a code span in a convention note"),
    ("NOLINK", "wiki/CLAUDE.md", "source-summary-page",
     "inside a code span in an instruction"),
    ("TAG", "wiki/wiki/concepts/trails.md", "links",
     "frontmatter tag; collides by name with nothing, but is a tag and not a "
     "wikilink"),
    ("BROKEN", "notes/memory/plan.md", "never-written",
     "no file has this name; a deliberate marker, not an error"),
]

# CP-2. The ambiguous target, and which copy must win from which file.
AMBIGUOUS_TARGET = "summaries-note"
AMBIGUOUS_LINKER = "wiki/wiki/concepts/trails.md"
AMBIGUOUS_WINNER = "wiki/wiki/summaries/summaries-note.md"
AMBIGUOUS_LOSER = "wiki/raw/summaries-note.md"

# CP-2. Names that appear inside wiki/ ONLY within code spans. None of them may
# ever be the source end of an edge from a file under wiki/.
CODE_SPAN_ARTEFACTS = ("wikilinks", "gamma", "page-name", "source-summary-page")

# CP-3. Five documents, with what each one's own metadata says.
#
#   author  as recorded in the file; "-" when absent
#   pages   page count; "-" when the format records none
#   cites   one reference the document must yield; "-" when it has none
#   title   prefix the extracted title must start with; "-" to skip
DOCUMENT_FASIT = [
    ("Documents/paper.pdf", "pdf", "C. Researcher", "8",
     "10.1234/abcd-25-00031-5", "On Self-Organisation",
     "the DOI is glued to the next word with no delimiter; a greedy pattern "
     "swallows it"),
    ("Documents/certificate.pdf", "pdf", "-", "1", "-", "Page Title",
     "UTF-16BE title with a BOM, no author, and /Count reports 0 while one "
     "page exists"),
    ("Documents/paper.tex", "tex", "N. Writer", "-", "key1", "Graded Access",
     "the LaTeX author carries a line break and an affiliation that is not "
     "part of the name"),
    ("Documents/review.odt", "odt", "-", "3", "-", "-",
     "ODF stores its page count in a meta:document-statistic attribute, not "
     "element text; no author was ever set"),
    ("Documents/report.docx", "docx", "A. Author", "-", "-",
     "Quarterly Report", "an ordinary docx, as the control"),
]

# CP-3. Every REFERENCES_FILE edge M1 must draw, and nothing else. All three
# targets are DOCUMENTS: an edge inside one model can only reach that model's
# own nodes, and a document naming a note is the mesh's MENTIONS_FILE, not
# this. The three anchors prose uses are all exercised -- root-relative,
# sibling-relative, and one that resolves to neither.
REFERENCES_FILE_FASIT = frozenset({
    ("Documents/paper.tex", "Documents/review.odt"),      # root-relative
    ("Documents/report.docx", "Documents/review.odt"),    # root-relative
    ("Documents/report.docx", "Documents/paper.tex"),     # sibling-relative
})
# Named in paper.tex, never written. A graph that resolves this to the nearest
# existing .pdf is worse than one with a gap.
REFERENCES_FILE_PHANTOM = "Documents/appendix-b.pdf"
# A URL whose tail looks exactly like a path. It appears in paper.tex, and no
# part of it may become a mention -- checked against the extractor directly,
# because the resolver would refuse this one anyway and a gate that passes for
# the wrong reason is the shape this project keeps finding.
REFERENCES_FILE_URL = "https://example.org/Documents/review.odt"

# CP-5. What ARCHIVE_CONTAINS must say, one level deep. Read off the zip that
# is written below, by hand: three top-level names out of four members, with
# `src/` standing for the two files inside it.
ARCHIVE_FASIT = {
    "proj/bundle.zip": ("README", "notes.md", "src/"),
}
# A PK header and nothing behind it. It must be COUNTED as unlistable, not
# skipped: an archive that cannot be opened and an archive with no members
# both produce zero edges, and only one of them is a defect.
ARCHIVE_UNLISTABLE = ("proj/corrupt.zip",)
# Gzip: an archive by magic number, with no member list to read without
# decompressing it. Zero entries, and not an error either.
ARCHIVE_NOT_LISTED = ("proj/logs.gz",)
# A VALID zip that must not be listed anyway: somebody else's build output.
# It has real members, so an implementation that skipped it by accident --
# because it could not open it, say -- would not satisfy the gate; the point
# is that the contents are readable and deliberately not read.
ARCHIVE_BY_POLICY = ("proj/langpack-nb.xpi",)

# CP-6. Every CITES_CODE edge, with the method each must carry. `code` is a
# corpus category with no store, so these point at inventory stubs.
CITES_CODE_FASIT = frozenset({
    ("notes/dev/code-map.md", "proj/api/v1/[rpc].ts", "mention"),
    ("notes/dev/code-map.md", "proj/tests/suite.test.mjs", "basename"),
    ("notes/dev/code-map.md", "proj/api/live_runner.js", "basename"),
})
# Named nowhere. Its name occurs in the note only INSIDE `live_runner.js`, and
# containment is not naming: measured on the real corpus, 89 of 1 253 basename
# edges rested on exactly this shape (`runner.py` inside `live_runner.py`).
CITES_CODE_GLUED = "proj/api/runner.js"
# Searched for by name in CP-6. A stub carries the basename and the path and
# no contents, so this is a name, not a term from inside the file -- which is
# the whole of what `mesh search` can promise about code.
CITES_CODE_FINDABLE = "suite.test.mjs"
# Written in the same note, and deliberately NOT also named by its full path:
# a file named both ways draws its edge from the path, and the uniqueness
# condition below would then be untestable -- dropping it changes nothing,
# which is what the mutation harness reported the first time this was written.
# Two files are called handler.js, so the bare name names neither.
CITES_CODE_AMBIGUOUS = "handler.js"
# Also written there, and never created.
CITES_CODE_PHANTOM = "nowhere.js"

# CP-3. Which documents can yield body text at all, and which cannot -- with
# the reason each empty one must report. `all()` over an empty list is True, so
# "every empty document says why" needs a non-empty declared set to mean
# anything.
DOCUMENTS_WITH_TEXT = (
    "Documents/report.docx",
    "Documents/review.odt",
    "Documents/Colon:Name.docx",
    "Documents/paper.pdf",
    "Documents/paper.tex",
    "Bilder/Art/Gallery Notes.docx",
    "Bilder/Art/Earlier works/Exhibition notes.odt",
)
DOCUMENTS_EMPTY = {
    "Documents/certificate.pdf": "partial",     # a title, no content stream
    "Documents/scanned.pdf": "needs_ocr",       # fonts, no text layer
    "Documents/locked.pdf": "encrypted",        # never decrypted
    "Documents/broken.docx": "corrupt",         # not really a zip
}
# The only document that must fail extraction outright.
DOCUMENTS_CORRUPT = ("Documents/broken.docx",)

# CP-5. Three files over the 100 MB cap. Sparse: they occupy no disk and are
# never read past the header, which is the property under test.
LARGE_FILE_BYTES = 101 * 1024 * 1024
LARGE_FILES = (".appstate/large/blob-a.bin",
               ".appstate/large/blob-b.bin",
               ".appstate/large/blob-c.bin")

# CP-5. Everything that lands in M4's junk drawer, counted out by hand: the
# eight declared `misc` cases, the forty cold application-state files, and the
# three large blobs. A corpus that silently grows -- an exclusion layer switched
# off -- otherwise reads as a bigger, greener build.
MISC_TOTAL = 8 + 4 + 40 + 3   # declared misc, the four archives, cold, large

# CP-6. Every (note, image) pair FIGURE_FOR must produce, and nothing else.
# `03122025_9.png` is named in experiments.md and does not exist; a graph that
# invents a nearest match is worse than one with a gap.
FIGURE_FOR_PAIRS = frozenset({
    ("notes/art/series-analysis.md", "Bilder/Art/Series-2021/Statue_270519_25_1.jpeg"),
    ("notes/art/series-analysis.md", "Bilder/Art/Series-2021/Bridge_190430_150dpi.jpeg"),
    ("notes/art/series-analysis.md", "Bilder/Art/Earlier works/Captivity#2.jpeg"),
    ("notes/art/series-analysis.md", "Bilder/Art/Earlier works/Lodestar(2018).jpeg"),
    ("notes/art/series-analysis.md",
     "Bilder/Art/Earlier works/Stardust-1_desember_2020.jpg"),
    ("notes/art/series-analysis.md",
     "Bilder/Art/Experiments-2025/03122025_1.png"),
    ("notes/art/series-analysis.md",
     "Bilder/Art/Experiments-2025/03122025_2.png"),
    ("notes/art/experiments.md", "Bilder/Art/Experiments-2025/05122025_2.png"),
    ("notes/art/experiments.md", "Bilder/Art/26072020_5_ny.jpeg"),
    ("notes/art/experiments.md", "Bilder/Art/qsave_2020-09-24_003.jpeg"),
})
FIGURE_FOR_PHANTOM = ("notes/art/experiments.md", "03122025_9.png")

# The installation config this corpus is described by. DECLARED, like every
# other key here: `homegraph init` would propose something from the extension
# mix on disk, and CP-7 checks the proposal against these lines. A fixture that
# obtained its config by running the scanner would agree with any scanner.
#
# The directory names are Norwegian on purpose. Nothing in the package knows
# them; they arrive through the config, which is the claim under test. The
# English variant below renames them and asserts the partition is unchanged.
DECLARED_ROLES = {
    "image": ["Bilder"],
    "document": ["Documents"],
    "note": ["docs", "graph-export", "notes", "wiki"],
    "code": [],
    "cache": [],
}

# Renaming applied by build_english(). Path components only, so a file whose
# NAME is Norwegian stays Norwegian -- filenames are data, and the point is
# that no directory name is baked into a rule.
# Only the directories a user's config names. Adding `.icons` here was tried
# and reverted: the shipped [app_state] layer names `.icons` on purpose, the
# way it names `.cache`, so renaming it does not produce the same corpus under
# another layout -- it produces a different corpus. CP-7's claim is that the
# ROLE mechanism imposes no layout, not that every shipped rule is free of
# directory names. What the experiment showed is recorded in test_cp7.py's
# `_decisions`, because the result is worth keeping even though the rename is
# not: the two corpora agreed on all 857 labels while 240 paths were decided
# by different layers.
ENGLISH_DIRS = {"Bilder": "Pictures", "Skjermbilder": "Screenshots"}


def _config_for(root):
    """Where this corpus's config file goes: beside the root, not inside it.

    Inside would put a TOML file into the corpus, where it would be classified,
    counted, and quietly change every declared total.
    """
    return root.rstrip("/") + ".config.toml"


def write_config(root, roles=None):
    """Write the config that describes `root`. Returns its path."""
    from homegraph import userconfig
    return userconfig.write(_config_for(root), root,
                            dict(roles or DECLARED_ROLES))


def build(root, clean=True):
    """Create the corpus. Returns (root, cases) with cases as the answer key."""
    if clean and os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)
    cases: list[Case] = []

    def case(label, subtype, hard, rel, why):
        cases.append((label, subtype, hard, rel, why))

    # -- markdown: the link traps ------------------------------------------
    #
    # The wiki is nested one level deeper than it looks -- `wiki/wiki/` inside
    # `wiki/` -- because that is what makes the ambiguous-target case decidable.
    # With a flat layout the two copies of `summaries-note` are equidistant from
    # the linking file and "nearest wins" degenerates into alphabetical order,
    # which is the bug the rule exists to prevent.
    _write(root, "wiki/wiki/concepts/trails.md",
           "---\ntype: concept\ntags: [memex, links]\n---\n\n"
           "# Trails\n\n"
           "From [[bush|Vannevar Bush]]'s idea. The wiki's `[[wikilinks]]` are\n"
           "the trails themselves ([[bookkeeping]]).\n\n"
           "```\nfenced [[gamma]] block\n```\n\n"
           "See also [[viewer]] and [[summaries-note]].\n")
    case("markdown", "note", True, "wiki/wiki/concepts/trails.md",
         "piped alias, an inline code span that is NOT a link, and a fenced "
         "block that is NOT a link")
    _write(root, "wiki/wiki/entities/bush.md",
           "# Bush\n\nLinks to [[trails]] and to [[memex]].\n")
    case("markdown", "note", False, "wiki/wiki/entities/bush.md", "link target")
    # Reachable from two different pages at the same distance. That is what
    # makes a forgetful traversal visible: the same node arrives twice in one
    # frontier, and without a visited set its edges are reported twice.
    _write(root, "wiki/wiki/concepts/memex.md",
           "# Memex\n\nBack to [[trails]].\n")
    case("markdown", "note", True, "wiki/wiki/concepts/memex.md",
         "two pages link here from the same distance; a traversal without a "
         "visited set reports its edges twice")
    _write(root, "wiki/wiki/concepts/bookkeeping.md", "# Bookkeeping\n")
    case("markdown", "note", False, "wiki/wiki/concepts/bookkeeping.md",
         "link target")
    _write(root, "wiki/wiki/entities/viewer.md",
           "# Viewer\n\nBack to [[trails|the trails]] -- closes a cycle,\n"
           "and sideways to [[memex]].\n")
    case("markdown", "note", True, "wiki/wiki/entities/viewer.md",
         "A -> B -> A cycle; traversal must terminate")
    # Two real code spans and a real link on the same line, plus a piped alias
    # immediately followed by a possessive 's.
    _write(root, "wiki/wiki/entities/graphify.md",
           "# Graphify\n\n"
           "Built on [[bush|Bush]]'s idea. Run `graphify --build` and then\n"
           "[[trails]] is rebuilt by `graphify --serve` ([[bookkeeping]]).\n")
    case("markdown", "note", True, "wiki/wiki/entities/graphify.md",
         "a link sitting between two code spans on one line, and a piped "
         "alias followed by a possessive")

    # Same page name in two directories: nearest common prefix must win.
    _write(root, "wiki/wiki/summaries/summaries-note.md", "# Curated\n")
    case("markdown", "note", True, "wiki/wiki/summaries/summaries-note.md",
         "ambiguous target: the curated copy, nearer to the linking file")
    _write(root, "wiki/raw/summaries-note.md", "# Raw source\n")
    case("markdown", "note", True, "wiki/raw/summaries-note.md",
         "ambiguous target: the raw copy, which sorts first alphabetically "
         "and is the wrong answer")

    # Two files whose only `[[...]]` is documentation of the syntax.
    _write(root, "wiki/index.md",
           "# Index\n\nEvery page is addressed by its `[[page-name]]`, with no\n"
           "path and no extension.\n")
    case("markdown", "note", True, "wiki/index.md",
         "the only [[target]] in the file sits in a code span; a regex sweep "
         "invents an edge here")
    _write(root, "wiki/CLAUDE.md",
           "# Instructions\n\nWhen you summarise a source, write the result to\n"
           "the `[[source-summary-page]]` for that source.\n")
    case("markdown", "note", True, "wiki/CLAUDE.md",
         "an instruction file whose [[target]] is syntax documentation")

    _write(root, "notes/memory/plan.md",
           "# Plan\n\nSee [[never-written]] -- a deliberate marker.\n")
    case("markdown", "memory", True, "notes/memory/plan.md",
         "broken wikilink used on purpose to mark something worth writing")
    _write(root, "docs/adding.mdx", "# Endpoints\n\nA [[bush]] reference.\n")
    case("markdown", "note", True, "docs/adding.mdx",
         ".mdx, not .md -- the extension list has to cover it")
    _write(root, "proj/README.md", "# Project\n")
    case("markdown", "readme", False, "proj/README.md", "readme subtype")
    _write(root, "graph-export/GRAPH_REPORT.md",
           "# Report\n\n" + "".join("- [[_COMMUNITY_Cluster %d]]\n" % i
                                    for i in range(12)))
    case("markdown", "generated", True, "graph-export/GRAPH_REPORT.md",
         "machine-written; its 12 unresolved links must not drown the one "
         "human marker")
    _write(root, "notes/bad-frontmatter.md",
           "---\nname: ok\nthis line has no colon at all\ntags: [x]\n---\n\n# T\n")
    case("markdown", "note", True, "notes/bad-frontmatter.md",
         "malformed frontmatter must be logged and survived, not raised")

    # -- documents ---------------------------------------------------------
    _zip_docx(root, "Documents/report.docx", title="Quarterly Report",
              creator="A. Author",
              body="Body text. Supersedes Documents/review.odt, and the "
                   "method is unchanged from paper.tex.")
    case("document", "docx", False, "Documents/report.docx",
         "ordinary docx; names one document by root-relative path and one by "
         "bare sibling name -- both must become REFERENCES_FILE")
    _zip_odt(root, "Documents/review.odt", pages=3, creator="")
    case("document", "odt", True, "Documents/review.odt",
         "ODF page count is an attribute, not element text; no author set")
    _zip_docx(root, "Documents/Colon:Name.docx", title="Colon Name")
    case("document", "docx", True, "Documents/Colon:Name.docx",
         "colon in the basename")
    _pdf(root, "Documents/paper.pdf", title="On Self-Organisation",
         author="C. Researcher", pages=8, doi="10.1234/abcd-25-00031-5",
         text="On Self-Organisation. See doi 10.1234/abcd-25-00031-5Next Part")
    case("document", "pdf", True, "Documents/paper.pdf",
         "the DOI is followed immediately by more text with no delimiter")
    _pdf(root, "Documents/certificate.pdf", title="Page Title",
         utf16_title=True, pages=1, fonts=False)
    case("document", "pdf", True, "Documents/certificate.pdf",
         "UTF-16BE title with a BOM, and /Count reports 0 while a page exists")
    _pdf(root, "Documents/scanned.pdf", title="Scan", pages=1)
    case("document", "pdf", True, "Documents/scanned.pdf",
         "fonts but no text layer -- must report needs_ocr, not empty")
    _pdf(root, "Documents/locked.pdf", title="Locked", encrypted=True)
    case("document", "pdf", True, "Documents/locked.pdf",
         "encrypted: flagged, never decrypted, never a traceback")
    _write(root, "Documents/paper.tex",
           "\\title{Graded Access\\\\ and the Gap}\n"
           "\\author{N. Writer\\\\ \\small Independent}\n"
           "\\begin{document}\\section{Intro}\\cite{key1,key2}\n"
           "The measurements are tabulated in Documents/review.odt.\n"
           "Derivations were promised in Documents/appendix-b.pdf and never "
           "written.\n"
           "A preprint sits at " + REFERENCES_FILE_URL + " -- a URL, not a "
           "path.\n"
           "\\end{document}\n")
    case("document", "tex", True, "Documents/paper.tex",
         "LaTeX author carries a line break and an affiliation that is not "
         "part of the name; also names one document that exists, one that "
         "never did, and a URL whose tail looks like a path")
    _write(root, "Documents/broken.docx", b"PK\x03\x04not really a zip",
           binary=True)
    case("document", "docx", True, "Documents/broken.docx",
         "corrupt: becomes an error node, the build continues")

    # -- images: the filename grammar --------------------------------------
    #
    # Every row of FILENAME_FASIT (below) is planted here. The expected parse is
    # declared there, next to the name, and is never obtained by calling the
    # parser -- see the module docstring.
    for rel, *_ in FILENAME_FASIT:
        _png(root, rel)
    for rel, _d, _k, _i, _r, _dpi, _c, _v, hard, why in FILENAME_FASIT:
        case("image", "-", hard, rel, why)

    _zip_docx(root, "Bilder/Art/Gallery Notes.docx", title="Gallery Notes")
    case("document", "docx", True, "Bilder/Art/Gallery Notes.docx",
         "a DOCUMENT inside Bilder/. Being under Bilder/ is necessary for "
         "image, never sufficient")
    _zip_odt(root, "Bilder/Art/Earlier works/Exhibition notes.odt",
             pages=2, creator="")
    case("document", "odt", True, "Bilder/Art/Earlier works/Exhibition notes.odt",
         "a second non-image under Bilder/, in a collection directory: the "
         "skip must be by extension, not by which folder it is in")
    _png(root, "Bilder/.thumbnails/normal/preview.png")
    case("EXCLUDED", "cache", True, "Bilder/.thumbnails/normal/preview.png",
         "a thumbnail cache INSIDE the image root: being under Bilder/ is "
         "necessary for image and never sufficient, and layer 3 is the only "
         "thing that catches this one")
    _png(root, "proj/e2e/snapshots/layer-z5.png")
    case("EXCLUDED", "image-outside-root", True, "proj/e2e/snapshots/layer-z5.png",
         "a PNG inside the user's own source tree; only the image boundary "
         "catches it")

    # -- notes that name artwork by filename -------------------------------
    #
    # M5's FIGURE_FOR needs prose that names an image file, and the negative
    # case needs prose that names one which does not exist.
    _write(root, "notes/art/series-analysis.md",
           "# Series, 2021\n\n"
           "The sequence reads best in order: Statue_270519_25_1.jpeg opens it,\n"
           "Bridge_190430_150dpi.jpeg closes it. Earlier work worth rereading\n"
           "alongside: Captivity#2.jpeg, Lodestar(2018).jpeg and\n"
           "Stardust-1_desember_2020.jpg. The two studies 03122025_1.png and\n"
           "03122025_2.png came out of the same afternoon.\n")
    case("markdown", "note", True, "notes/art/series-analysis.md",
         "names seven image files in prose; every one must become a FIGURE_FOR "
         "edge and no more")
    _write(root, "notes/art/experiments.md",
           "# Experiments\n\n"
           "05122025_2.png and 26072020_5_ny.jpeg are keepers;\n"
           "qsave_2020-09-24_003.jpeg is not. I meant to make 03122025_9.png\n"
           "but never did.\n")
    case("markdown", "note", True, "notes/art/experiments.md",
         "names three real images and one that was never made -- the phantom "
         "must produce no edge at all")

    # -- code --------------------------------------------------------------
    _write(root, "proj/api/handler.js", "export const x = 1;\n")
    case("code", "-", False, "proj/api/handler.js", "own source")
    _write(root, "proj/web/handler.js", "export const y = 2;\n")
    _write(root, "proj/api/live_runner.js", "export const run = 3;\n")
    _write(root, "proj/api/runner.js", "export const r = 4;\n")
    case("code", "-", True, "proj/web/handler.js",
         "a SECOND handler.js: the bare name now names neither, which is what "
         "CITES_CODE's uniqueness condition has to notice")
    case("code", "-", True, "proj/api/live_runner.js",
         "the note names THIS file; its name contains runner.js, which is a "
         "different file entirely")
    case("code", "-", True, "proj/api/runner.js",
         "named nowhere in the corpus -- its name occurs only inside "
         "live_runner.js, and a substring is not a mention")
    _write(root, "proj/api/v1/[rpc].ts", "export default 1;\n")
    case("code", "-", True, "proj/api/v1/[rpc].ts",
         "square brackets break naive glob matching")
    _write(root, "proj/tests/suite.test.mjs", "test('x', () => {});\n")
    case("code", "-", True, "proj/tests/suite.test.mjs",
         "tests are code, not excluded")

    # -- prose that names source files -------------------------------------
    #
    # M5's CITES_CODE needs a note that names code three ways: by full path,
    # by a unique bare name, and by a name two files share. No markdown link
    # syntax anywhere in it -- these are mentions in prose, and a link would
    # make M3 resolve them as MENTIONS_PATH before the mesh ever saw them.
    _write(root, "notes/dev/code-map.md",
           "# Request path\n\n"
           "It starts in proj/api/v1/[rpc].ts and is covered by\n"
           "suite.test.mjs. Saying handler.js on its own is no longer enough\n"
           "since the web tree grew one too. nowhere.js was never written.\n"
           "The loop itself lives in live_runner.js.\n")
    case("markdown", "note", True, "notes/dev/code-map.md",
         "names code by full path, by unique basename, by an ambiguous "
         "basename and by a name that does not exist")

    # -- archives ----------------------------------------------------------
    _zip_bundle(root, "proj/bundle.zip",
                ("README", "notes.md", "src/main.py", "src/util.py"))
    case("misc", "unknown", True, "proj/bundle.zip",
         "a real zip: four members, three top-level names -- ARCHIVE_CONTAINS "
         "is one level, so src/ is one entry and not two")
    _write(root, "proj/corrupt.zip", b"PK\x03\x04 and then nothing at all",
           binary=True)
    case("misc", "unknown", True, "proj/corrupt.zip",
         "the zip magic number with no central directory behind it: counted "
         "as unlistable, never a traceback")
    _zip_bundle(root, "proj/langpack-nb.xpi",
                ("manifest.json", "chrome/nb/locale.properties"))
    case("misc", "unknown", True, "proj/langpack-nb.xpi",
         "a browser extension: a perfectly listable zip whose contents are "
         "somebody else's build output, so ARCHIVE_CONTAINS declines it by "
         "policy and says it did")
    _write(root, "proj/logs.gz", b"\x1f\x8b\x08\x00" + b"\x00" * 16,
           binary=True)
    case("misc", "unknown", True, "proj/logs.gz",
         "gzip: an archive with no member list to read without decompressing "
         "it, so no entries and no error either")

    # -- misc --------------------------------------------------------------
    _write(root, "HORMA", "ASCII prose with no extension at all.\n")
    case("misc", "unknown", True, "HORMA",
         "prose with no extension; not one of M1's doctypes, so it lands in "
         "the junk drawer")
    _write(root, "global.yml", "")
    case("misc", "unknown", True, "global.yml",
         "zero bytes -- empty files must classify, not divide by zero")
    _write(root, "notes.txt", "plain text\n")
    case("misc", "unknown", False, "notes.txt", "plain text")
    _write(root, ".bashrc", "export PS1='$ '\n")
    case("misc", "unknown", False, ".bashrc",
         "top-level dotfile config; layer 2 covers .config/ etc, not this")
    _write(root, ".bash_history.bak-20260620", "ls -la\n")
    case("misc", "unknown", True, ".bash_history.bak-20260620",
         "layer 3 globs *.bak; a dated backup suffix slips straight through")
    _write(root, "proj/data.sqlite", b"", binary=True)
    conn = sqlite3.connect(os.path.join(root, "proj/data.sqlite"))
    conn.execute("CREATE TABLE creds (id INTEGER, token TEXT)")
    conn.execute("INSERT INTO creds VALUES (1, 'ZZROWSECRETZZ')")
    conn.commit()
    conn.close()
    os.utime(os.path.join(root, "proj/data.sqlite"), (FIXED_MTIME, FIXED_MTIME))
    case("misc", "unknown", True, "proj/data.sqlite",
         "SQLite: the schema may be indexed, the rows never")
    _write(root, "proj/nameless", b"SQLite format 3\x00" + b"\x00" * 32,
           binary=True)
    case("misc", "unknown", True, "proj/nameless",
         "no extension; only the magic number identifies it")
    _write(root, "proj/actually_a_database.json",
           b"SQLite format 3\x00" + b"\x00" * 32, binary=True)
    case("misc", "unknown", True, "proj/actually_a_database.json",
         "the extension lies; magic must win")

    # -- excluded ----------------------------------------------------------
    _write(root, "proj/node_modules/dep/readme.md", "# Dep\n")
    case("EXCLUDED", "dependency", False, "proj/node_modules/dep/readme.md",
         "layer 1")
    _write(root, "proj/.venv/lib/site-packages/pkg/doc.pdf", "x")
    case("EXCLUDED", "dependency", True,
         "proj/.venv/lib/site-packages/pkg/doc.pdf",
         "a .pdf inside .venv -- a document extension in a dependency tree")
    _write(root, ".cache/tool/notes.md", "# Cached\n")
    case("EXCLUDED", "cache", False, ".cache/tool/notes.md", "layer 3")
    _write(root, "deep/a/b/c/d/.cache/buried.md", "# Buried\n")
    case("EXCLUDED", "cache", True, "deep/a/b/c/d/.cache/buried.md",
         "cache at depth 5 -- the rule must match at any level")
    _write(root, "deep/a/b/tmp/also.pdf", "x")
    case("EXCLUDED", "cache", True, "deep/a/b/tmp/also.pdf",
         "tmp at depth, holding a document extension")
    _write(root, ".config/app/state.json", "{}")
    case("EXCLUDED", "app-state", False, ".config/app/state.json", "layer 2")
    _write(root, ".env", "API_TOKEN=ZZSECRETZZ\n")
    case("EXCLUDED", "redacted", True, ".env", "layer 5, by name")
    _write(root, ".ssh/id_rsa", "-----BEGIN OPENSSH PRIVATE KEY-----\n")
    case("EXCLUDED", "redacted", True, ".ssh/id_rsa", "layer 5, by glob")
    _write(root, "app/credentials.json", '{"token": "ZZSECRETZZ"}')
    case("EXCLUDED", "redacted", True, "app/credentials.json",
         "a secret that looks like ordinary JSON")
    _write(root, "environment.md", "# Not a secret\n")
    case("markdown", "note", True, "environment.md",
         "a lookalike: 'environment' must not trip the .env rule")

    # A cloned third-party repository: a real .git with a foreign origin.
    _write(root, "Documents/handbook/.git/config",
           "[remote \"origin\"]\n\turl = https://github.com/someone-else/handbook.git\n")
    _write(root, "Documents/handbook/materials/deep/data.pdf", "x")
    case("EXCLUDED", "vendored-repo", True,
         "Documents/handbook/materials/deep/data.pdf",
         "layer 4: a cloned repo sitting inside a document directory")
    _write(root, "Documents/handbook/README.md", "# Handbook\n")
    case("markdown", "readme", True, "Documents/handbook/README.md",
         "the README is why the repo was cloned -- kept by the top-level "
         "exception")

    # A symlink. Following it would double-count and can escape the root.
    link = os.path.join(root, "icons/link.svg")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if not os.path.lexists(link):
        os.symlink(os.path.join(root, "Bilder/Art/03122025_1.png"), link)
    case("EXCLUDED", "symlink", True, "icons/link.svg",
         "a symlink, and an image outside Bilder/ -- two reasons")

    # And one the symlink layer is the ONLY reason to exclude. The case above
    # says "two reasons" and means it: switch [symlinks] off and `link.svg` is
    # still excluded, by the image boundary, so the corpus count does not move
    # and the layer's contribution is invisible. Every other exclusion layer
    # uniquely owns files; this one owned none, which is why the per-layer
    # control in CP-0 could not have proven the symlink policy does anything.
    #
    # A .md link inside notes/ has no second reason: markdown is included, the
    # directory is included, and the only thing standing between it and a
    # `markdown` label is [symlinks].
    solo = os.path.join(root, "notes/mirror-of-plan.md")
    os.makedirs(os.path.dirname(solo), exist_ok=True)
    if not os.path.lexists(solo):
        os.symlink(os.path.join(root, "notes/memory/plan.md"), solo)
    case("EXCLUDED", "symlink", True, "notes/mirror-of-plan.md",
         "a symlink and nothing else -- included directory, included "
         "extension, no second layer behind it")

    # -- the noise layer ---------------------------------------------------
    #
    # Not padding. Without it the negative control has nothing to unmask: on a
    # real home directory switching the exclusion rules off moves the image
    # count from 135 to 52 661, and a fixture with twenty images cannot show
    # that the rules are load-bearing at all. The ratios below are scaled-down
    # versions of what was actually measured -- an unpacked icon theme (240
    # files), a dependency tree (320), a browser cache (120), application
    # state (60) and cold state for the rollup (40).
    for i in range(240):
        _png(root, ".icons/Theme/scalable/icon-%03d.svg" % i, size=8)
    # 160 packages, two files each. Distinct paths, not the same twenty
    # rewritten: with only 40 files here the dependency tree stopped dominating
    # the corpus, and CP-0's negative control for layer 1 no longer moved the
    # noise threshold -- it was caught by the gold set instead, which is a
    # different claim.
    for i in range(160):
        _write(root, "proj/node_modules/pkg%03d/index.js" % i,
               "module.exports = %d;\n" % i)
        _write(root, "proj/node_modules/pkg%03d/readme.md" % i,
               "# pkg%03d\n" % i)
    for i in range(120):
        _write(root, ".cache/browser/Cache_Data/%04x" % i, "x" * 20)
    for i in range(60):
        _write(root, ".config/app/state-%03d.json" % i, "{}")

    # Cold application state, for M4's rollup.
    for i in range(40):
        _write(root, ".appstate/cold/f%03d.dat" % i, "x" * (10 + i),
               mtime=OLD_MTIME)

    # Three files over M4's 100 MB cap. Sparse -- created with truncate, so
    # they cost no disk and cannot be read into memory by accident; if the size
    # cap is ever removed, the build reads 300 MB of zeroes and says so.
    for rel in LARGE_FILES:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.truncate(LARGE_FILE_BYTES)
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))

    write_config(root)
    return root, cases


def _rename(rel, mapping):
    """Apply a directory renaming to a relative path, component by component."""
    return "/".join(mapping.get(part, part) for part in rel.split("/"))


def build_english(root, clean=True, mapping=None):
    """The same corpus with English directory names, at a different root.

    This is the experiment the configurable layout exists for. Same rule files,
    same classifier, same declared labels -- only the directory names and the
    `image` role differ. If the partition comes out identical, no layout is
    imposed. If it does not, one is, and no amount of `{root}` substitution
    hides that.

    The labels are NOT re-derived. Each declared case keeps the label it was
    born with; only its path is rewritten by the same mapping that renames the
    directories on disk. Nothing here calls `classify()`.
    """
    mapping = ENGLISH_DIRS if mapping is None else mapping
    _, cases = build(root, clean=clean)

    # Deepest first, so renaming a parent cannot invalidate a child's path.
    targets = []
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
            if name in mapping:
                targets.append(os.path.join(dirpath, name))
    for old in sorted(targets, key=lambda p: p.count(os.sep), reverse=True):
        new = os.path.join(os.path.dirname(old),
                           mapping[os.path.basename(old)])
        os.rename(old, new)

    # A symlink's target still names the old directory. Left as-is it would
    # merely be dangling, which it already is -- but a dangling link that names
    # a directory this corpus no longer has is a confusing artefact, and the
    # symlink cases must keep testing the symlink layer and nothing else.
    #
    # Every symlink, not one named path: the corpus has two now, and a rewriter
    # that hardcodes one of them silently stops covering the next one added.
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            link = os.path.join(dirpath, name)
            if not os.path.islink(link):
                continue
            target = os.readlink(link)
            os.unlink(link)
            os.symlink(os.path.join(root, _rename(
                os.path.relpath(target, root), mapping)), link)

    renamed = [(label, sub, hard, _rename(rel, mapping), why)
               for label, sub, hard, rel, why in cases]
    roles = {name: [_rename(d, mapping) for d in dirs]
             for name, dirs in DECLARED_ROLES.items()}
    write_config(root, roles)
    return root, renamed, roles


# CP-8. How corpus A becomes corpus B, DECLARED, one entry per axis of
# `incremental.diff`. All five have to be non-empty: a mutation that ignores
# `removed` cannot be killed by a corpus where nothing was removed, and an
# equivalence gate over two identical corpora is the emptiest gate in this
# project -- it passes for any update path, including one that does nothing.
#
# `touched` is the subtle one: same bytes, new mtime, which is what a formatter
# or a sync tool leaves behind. It must cost a stat() and no reparse.
EVOLUTION = {
    "added": [
        "notes/late-arrival.md",
        # A page that finally exists. `notes/memory/plan.md` has linked to
        # [[never-written]] since corpus A, deliberately, as a marker; adding
        # the page turns that broken link into a resolved one WITHOUT plan.md
        # changing a byte. Nothing a per-file diff can see.
        "notes/never-written.md",
        "Bilder/Art/Experiments-2025/06122025_1.png",
    ],
    "changed": [
        # Loses two wikilinks and a heading, so section nodes and edges have to
        # disappear -- an upsert alone would leave both behind, looking exactly
        # like a section that still exists.
        "wiki/wiki/concepts/trails.md",
        # A different size, which is all M2 can see: it does not hash, because
        # hashing means opening.
        "Bilder/Art/qsave_2020-09-24_003.jpeg",
    ],
    "touched": [
        "wiki/index.md",
    ],
    "removed": [
        # A .mdx, so the extension list is exercised on the way out too.
        "docs/adding.mdx",
        # A link TARGET, not a linker. Two other files point at [[bush]] and
        # neither of them changes; after this their links are broken, and a
        # full rebuild writes a `wikilink:bush` node they must also get.
        "wiki/wiki/entities/bush.md",
        "Bilder/Art/Earlier works/Captivity#2.jpeg",
    ],
}

# CP-8. The files that must be rebuilt even though nothing about them changed,
# because the corpus around them did. Declared, so that an expansion which
# quietly does nothing is a failure rather than a smaller number.
#
#   graphify.md linked to [[bush]], which has just been deleted
#   plan.md     linked to [[never-written]], which has just been created
#
# trails.md also links to [[bush]] and adding.mdx also linked to it, but both
# are already in `changed`/`removed`, so neither counts as a neighbour.
EVOLUTION_NEIGHBOURS = [
    "wiki/wiki/entities/graphify.md",
    "notes/memory/plan.md",
]
EVOLVED_MTIME = FIXED_MTIME + 86400


def evolve(root):
    """Turn corpus A into corpus B, in place. Returns the declared changes.

    Nothing here consults the store or the classifier. What changed is stated,
    not measured -- a fixture that asked the code under test what it had done
    would agree with any answer.
    """
    _write(root, "notes/late-arrival.md",
           "# Late arrival\n\nLinks to [[trails]] and to [[never-written]].\n",
           mtime=EVOLVED_MTIME)
    _write(root, "notes/never-written.md",
           "# Never written\n\nWritten at last.\n", mtime=EVOLVED_MTIME)
    _png(root, "Bilder/Art/Experiments-2025/06122025_1.png")
    os.utime(os.path.join(root, "Bilder/Art/Experiments-2025/06122025_1.png"),
             (EVOLVED_MTIME, EVOLVED_MTIME))

    # Rewritten: shorter, one heading fewer, two links fewer.
    _write(root, "wiki/wiki/concepts/trails.md",
           "---\ntype: concept\ntags: [memex]\n---\n\n"
           "# Trails\n\nOnly [[bush]] survives this edit.\n",
           mtime=EVOLVED_MTIME)
    _write(root, "Bilder/Art/qsave_2020-09-24_003.jpeg",
           b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, mtime=EVOLVED_MTIME,
           binary=True)

    # Same bytes, new mtime. Read back and written unchanged rather than
    # retyped here, so "identical content" cannot drift out of sync with the
    # original if that ever gets edited.
    touched = os.path.join(root, "wiki/index.md")
    with open(touched, "rb") as fh:
        same = fh.read()
    with open(touched, "wb") as fh:
        fh.write(same)
    os.utime(touched, (EVOLVED_MTIME, EVOLVED_MTIME))

    for rel in EVOLUTION["removed"]:
        os.remove(os.path.join(root, rel))
    return {k: list(v) for k, v in EVOLUTION.items()}


def inventory(root):
    """(path, is_symlink) for every non-directory, like the real scan."""
    rows = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            p = os.path.join(dirpath, name)
            rows.append((p, os.path.islink(p)))
        for name in list(dirnames):
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                dirnames.remove(name)
                rows.append((p, True))
    return sorted(rows)


ROOT = os.path.join(os.path.expanduser("~"), ".homegraph", "synthetic-corpus")
ROOT_EN = os.path.join(os.path.expanduser("~"), ".homegraph",
                       "synthetic-corpus-en")
CONFIG = _config_for(ROOT)
_BUILT = {"done": False}
CASES: list[Case] = []


def use_config(path):
    """Point this process, and anything it spawns, at a config file.

    An environment variable rather than an argument threaded through every
    call, because the checkpoints also drive homegraph as a subprocess -- CP-4
    runs the image build under strace -- and a child process has to arrive at
    the same answer as its parent or the two are not testing the same thing.
    """
    os.environ["HOMEGRAPH_CONFIG"] = path
    return path


def build_once(root=None):
    """Build once per process and cache the declared key in CASES."""
    global CASES
    target = root or ROOT
    if not _BUILT["done"] or root:
        _, CASES[:] = build(target)
        _BUILT["done"] = True
    use_config(_config_for(target))
    return target, CASES


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    target = args[0] if args else ROOT
    if "--english" in sys.argv:
        _, cs, roles = build_english(target)
        print("english layout: %s" % roles)
    else:
        _, cs = build(target)
    print("%s: %d files, %d declared cases (%d adversarial)"
          % (target, len(inventory(target)), len(cs),
             sum(1 for c in cs if c[2])))
    print("config: %s" % _config_for(target))
