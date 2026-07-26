#!/usr/bin/env python3
"""M1 extractors: one per format family, all returning the same shape.

The plan named six libraries -- python-docx, python-pptx, openpyxl, pypdf,
odfpy, PyMuPDF -- and listed "six dependencies, one missing" as a medium risk.
On this machine all six are missing, so the degradation path is not a scenario
to simulate: it is the default. That pushed the design somewhere better.

docx, pptx, xlsx and odt are ZIP archives of XML. `zipfile` and `xml.etree`
read them natively, so 30 of the 42 documents need nothing installed at all.
PDF is the one genuinely hard format; it gets a stdlib reader for metadata,
page count, encryption and text-layer detection, and hands body text to pypdf
if that ever appears. A PDF with no text layer reports `needs_ocr` rather than
an empty string, because an empty string is indistinguishable from a document
that genuinely says nothing.

Every extractor returns:

    {text, sections, metadata, outbound_refs, doctype, status, problems}

`status` is one of ok / partial / missing_extractor / encrypted / corrupt.
Nothing here raises: a single unreadable file must not end a build over 42.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
import zipfile

DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
ARXIV = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
URL = re.compile(r"https?://[^\s<>\"')\]}]+")

# A DOI runs up to the first character that cannot appear in one -- and in a
# PDF almost nothing is such a character. Two ways it overruns:
#
#   raw bytes:      `10.1038/s44260-025-00031-5)/Author(Carlos Gershenson)`
#   extracted text: `10.1016/j.jbc.2022.101866ClaraOrtega-deSanLuis1`
#
# The second has no delimiter at all, because PDF text carries no spaces. The
# cut is therefore a heuristic and is labelled one: publisher DOI suffixes are
# lowercase, digits, dots and hyphens, so an UPPERCASE letter arriving right
# after a digit is where the DOI stopped and the next word began. Prefixes like
# 10.1103/PhysRevLett survive, since their capitals follow letters.
DOI_TRAIL = re.compile(r"[).,;:'\"]+$")
DOI_RUNON = re.compile(r"(?<=[0-9])\.?(?=[A-Z])")


# `xml.etree` is vulnerable to entity-expansion attacks -- a 1 KB file can
# expand to gigabytes and take the build down. That is not hypothetical here:
# these documents come from the user's download directory, i.e. from the
# internet.
#
# The usual advice is to add `defusedxml`. This project deliberately carries no
# extraction dependencies, and the attack needs a DOCTYPE or ENTITY declaration
# to work, so the guard rejects those outright. A legitimate .docx or .odt has
# neither. Found by bandit (B314), not by reading the code.
DTD = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


class UnsafeXML(ValueError):
    pass


def safe_fromstring(data):
    """Parse XML, refusing anything that declares a DTD or entities."""
    if DTD.search(data[:65536]):
        raise UnsafeXML("XML declares a DOCTYPE or ENTITY; refusing to expand")
    return ET.fromstring(data)


def blank_result(doctype, status="ok"):
    return {"doctype": doctype, "status": status, "text": "", "sections": [],
            "metadata": {}, "outbound_refs": [], "problems": []}


# A path-shaped token in prose: an optional anchor (`~/`, `./`, `../`, `/`),
# any number of directory components, and a name ending in a suffix. Which
# suffixes count is NOT decided here -- `file_mentions` takes the set from
# `corpus.known_extensions()`, so this module owns no extension list of its
# own. See DECISIONS.md section 26.
#
# The leading lookbehind is what keeps URLs out, and it does it structurally
# rather than by pattern-matching `http`: every position inside
# `https://host/a/paper.pdf` is preceded by `/` or `.`, so no match can start
# there at all. A mailto: address is excluded the same way, by `@`.
#
# Spaces are not path characters here. `Gallery Notes.docx` is therefore not
# found, and that is the deliberate half of the trade: admitting spaces makes
# every sentence ending in a filename a candidate, and the false edges land in
# a graph where a wrong relation is invisible once it is a row.
FILEREF = re.compile(
    r"(?<![\w@/.-])((?:~/|\.{1,2}/|/)?(?:[\w.+-]+/)*[\w.+-]+\.[A-Za-z0-9]{1,8})"
    r"(?![\w/])")


# A name of one character before the dot is an initial, not a file. Measured,
# not guessed: the first run over the real corpus produced `N.R`, `M.C`, `H.R`,
# `J.H`, `D.H`, `A.C` and `L.H` -- author initials out of PDF reference lists,
# where extraction drops the space after the first period. `.r` and `.c` are
# genuine source extensions, so the extension filter cannot tell them apart and
# the length of the stem is the only thing that can. Nothing resolved, so no
# false edge was drawn; on a corpus holding a file actually called `N.R` one
# would have been, and it would have looked like a citation.
MIN_STEM = 2


def file_mentions(text, extensions, limit=40):
    """Path-shaped tokens in `text` whose suffix is in `extensions`.

    Pure: it takes text and a set, touches no filesystem and resolves nothing.
    Whether a mention points at a file that exists is `m1_build`'s question,
    and keeping the two apart is what lets the negative case -- a document
    naming a file that was never written -- be tested without a corpus.
    """
    out, seen = [], set()
    for m in FILEREF.finditer(text or ""):
        token = m.group(1)
        stem, ext = os.path.splitext(os.path.basename(token))
        ext = ext.lower()
        if len(stem) < MIN_STEM or ext not in extensions or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _clean_refs(text, limit=200):
    refs = []
    for m in DOI.finditer(text or ""):
        value = m.group(0)
        # Cut at the next PDF dictionary key if one got glued on.
        value = re.split(r"\)/|\(", value)[0]
        cut = DOI_RUNON.search(value)
        if cut:
            value = value[:cut.start()]
        value = DOI_TRAIL.sub("", value)
        if len(value) > 7:
            refs.append(("doi", value))
    for m in ARXIV.finditer(text or ""):
        refs.append(("arxiv", m.group(1)))
    for m in URL.finditer(text or ""):
        refs.append(("url", m.group(0).rstrip(".,);")))
    seen, out = set(), []
    for kind, value in refs:
        if (kind, value) not in seen:
            seen.add((kind, value))
            out.append({"kind": kind, "value": value})
        if len(out) >= limit:
            break
    return out


# -- OOXML: docx / pptx / xlsx --------------------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCTERMS = "{http://purl.org/dc/terms/}"
SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

MAX_CELLS = 2000  # spreadsheets contribute headings and text, not datasets


def extract_ooxml(path):
    ext = os.path.splitext(path)[1].lower()
    doctype = {"docx": "docx", "doc": "docx", "pptx": "pptx", "ppt": "pptx",
               "xlsx": "xlsx", "xls": "xlsx"}.get(ext.lstrip("."), "docx")
    result = blank_result(doctype)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result["status"] = "corrupt"
        result["problems"].append(repr(exc))
        return result

    with zf:
        names = set(zf.namelist())
        if "docProps/core.xml" in names:
            result["metadata"].update(_ooxml_core(zf))
        try:
            if doctype == "docx" and "word/document.xml" in names:
                _docx_body(zf, result)
            elif doctype == "pptx":
                _pptx_body(zf, names, result)
            elif doctype == "xlsx":
                _xlsx_body(zf, names, result)
            else:
                result["status"] = "partial"
        except (ET.ParseError, UnsafeXML) as exc:
            result["status"] = "partial"
            result["problems"].append(repr(exc))

    result["outbound_refs"] = _clean_refs(result["text"])
    return result


def _ooxml_core(zf):
    meta = {}
    root = safe_fromstring(zf.read("docProps/core.xml"))
    for tag, key in ((DC + "creator", "author"), (DC + "title", "title"),
                     (DC + "subject", "subject"),
                     (CP + "lastModifiedBy", "last_modified_by"),
                     (DCTERMS + "created", "created"),
                     (DCTERMS + "modified", "modified")):
        el = root.find(tag)
        text = (el.text or "").strip() if el is not None else ""
        if text:
            meta[key] = text
    return meta


def _docx_body(zf, result):
    root = safe_fromstring(zf.read("word/document.xml"))
    lines, sections, offset = [], [], 0
    for para in root.iter(W + "p"):
        style = para.find("./%spPr/%spStyle" % (W, W))
        name = style.get(W + "val") if style is not None else None
        text = "".join(t.text or "" for t in para.iter(W + "t"))
        if not text.strip():
            continue
        if name and name.lower().startswith("heading"):
            level = "".join(c for c in name if c.isdigit()) or "1"
            sections.append({"level": int(level), "title": text.strip(),
                             "offset": offset})
        lines.append(text)
        offset += len(text) + 1
    result["text"] = "\n".join(lines)
    result["sections"] = sections


def _pptx_body(zf, names, result):
    slides = sorted(n for n in names
                    if re.match(r"ppt/slides/slide\d+\.xml$", n))
    lines, sections, offset = [], [], 0
    for i, name in enumerate(slides, start=1):
        root = safe_fromstring(zf.read(name))
        texts = [t.text for t in root.iter(A + "t") if t.text]
        title = texts[0].strip() if texts else "Slide %d" % i
        # One slide is one section: it is the unit a person points at.
        sections.append({"level": 1, "title": title, "offset": offset,
                         "slide": i})
        body = "\n".join(texts)
        lines.append(body)
        offset += len(body) + 1
    result["text"] = "\n".join(lines)
    result["sections"] = sections
    result["metadata"]["slides"] = len(slides)


def _xlsx_body(zf, names, result):
    shared = []
    if "xl/sharedStrings.xml" in names:
        root = safe_fromstring(zf.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(SS + "t"))
                  for si in root.iter(SS + "si")]
    sheets = sorted(n for n in names
                    if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    lines, sections, cells = [], [], 0
    for i, name in enumerate(sheets, start=1):
        sections.append({"level": 1, "title": "Sheet %d" % i,
                         "offset": len("\n".join(lines)), "sheet": i})
        root = safe_fromstring(zf.read(name))
        for c in root.iter(SS + "c"):
            if cells >= MAX_CELLS:
                result["status"] = "partial"
                result["problems"].append(
                    "cell cap %d reached; a spreadsheet is not a document"
                    % MAX_CELLS)
                break
            v = c.find(SS + "v")
            if v is None or not v.text:
                continue
            value = (shared[int(v.text)] if c.get("t") == "s"
                     and v.text.isdigit() and int(v.text) < len(shared)
                     else v.text)
            if value and not value.replace(".", "", 1).lstrip("-").isdigit():
                lines.append(value)
            cells += 1
    result["text"] = "\n".join(lines)
    result["sections"] = sections
    result["metadata"]["sheets"] = len(sheets)


# -- ODF: odt / odp / ods --------------------------------------------------

ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
ODF_META = "{urn:oasis:names:tc:opendocument:xmlns:meta:1.0}"
ODF_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def extract_odf(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    doctype = {"odt": "odt", "rtf": "odt", "odp": "pptx", "ods": "xlsx"}.get(
        ext, "odt")
    result = blank_result(doctype)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result["status"] = "corrupt"
        result["problems"].append(repr(exc))
        return result

    with zf:
        names = set(zf.namelist())
        if "meta.xml" in names:
            try:
                result["metadata"].update(_odf_meta(zf))
            except (ET.ParseError, UnsafeXML) as exc:
                result["problems"].append(repr(exc))
        if "content.xml" not in names:
            result["status"] = "partial"
            return result
        try:
            root = safe_fromstring(zf.read("content.xml"))
        except (ET.ParseError, UnsafeXML) as exc:
            result["status"] = "corrupt"
            result["problems"].append(repr(exc))
            return result

        lines, sections, offset = [], [], 0
        body = root.find(ODF_OFFICE + "body")
        for el in (body.iter() if body is not None else root.iter()):
            if el.tag == ODF_TEXT + "h":
                text = "".join(el.itertext()).strip()
                if text:
                    sections.append({
                        "level": int(el.get(ODF_TEXT + "outline-level", "1")),
                        "title": text, "offset": offset})
                    lines.append(text)
                    offset += len(text) + 1
            elif el.tag == ODF_TEXT + "p":
                text = "".join(el.itertext()).strip()
                if text:
                    lines.append(text)
                    offset += len(text) + 1
        result["text"] = "\n".join(lines)
        result["sections"] = sections
    result["outbound_refs"] = _clean_refs(result["text"])
    return result


def _odf_meta(zf):
    meta = {}
    root = safe_fromstring(zf.read("meta.xml"))
    for tag, key in ((DC + "creator", "author"), (DC + "title", "title"),
                     (DC + "date", "modified"),
                     (ODF_META + "initial-creator", "initial_author")):
        el = root.find(".//" + tag)
        text = (el.text or "").strip() if el is not None else ""
        if text:
            meta[key] = text
    # Page count is an ATTRIBUTE on meta:document-statistic, not element text.
    stat = root.find(".//" + ODF_META + "document-statistic")
    if stat is not None:
        pages = stat.get(ODF_META + "page-count")
        if pages and pages.isdigit():
            meta["pages"] = int(pages)
    return meta


# -- PDF -------------------------------------------------------------------

PDF_INFO = re.compile(rb"/(Title|Author|Creator|Producer|Subject)\s*"
                      rb"(?:\((?P<lit>(?:[^()\\]|\\.)*)\)|<(?P<hex>[0-9A-Fa-f\s]+)>)")


def _pdf_string(raw):
    """Decode a PDF text string. UTF-16BE when it carries a BOM, else latin-1.

    One certificate PDF in the survey corpus stored its title as UTF-16BE;
    read as bytes it looks like `P\\x00a\\x00g\\x00e`, which is not a title
    anyone would recognise in a search result. `Documents/certificate.pdf` in
    the synthetic corpus is that case, planted.
    """
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace").strip()
    return raw.decode("latin-1", "replace").replace("\\(", "(").replace(
        "\\)", ")").strip()


def extract_pdf(path):
    result = blank_result("pdf")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        result["status"] = "corrupt"
        result["problems"].append(repr(exc))
        return result

    if not data.startswith(b"%PDF"):
        result["status"] = "corrupt"
        result["problems"].append("no %PDF header")
        return result

    if b"/Encrypt" in data:
        # Flagged, not attempted. A traceback here would take down the build.
        result["status"] = "encrypted"
        result["problems"].append("/Encrypt present; not decrypted")

    for m in PDF_INFO.finditer(data):
        key = m.group(1).decode().lower()
        raw = m.group("lit")
        if raw is None:
            try:
                raw = bytes.fromhex(m.group("hex").decode().replace(" ", ""))
            except ValueError:
                continue
        value = _pdf_string(raw)
        if value and key not in result["metadata"]:
            result["metadata"][key if key != "title" else "title"] = value
    if "author" in result["metadata"]:
        result["metadata"]["author"] = result["metadata"]["author"].strip()

    # Counting /Type /Page objects rather than trusting /Count: one file here
    # reports /Count 0 and has a page.
    by_object = len(re.findall(rb"/Type\s*/Page[^s]", data))
    declared = 0
    m = re.search(rb"/Count\s+(\d+)", data)
    if m:
        declared = int(m.group(1))
    pages = max(by_object, declared)
    if pages:
        result["metadata"]["pages"] = pages

    text = _pdf_text(data)
    if text.strip():
        result["text"] = text
    elif result["status"] == "ok":
        # A PDF with fonts but no extractable text is a scan. Saying so beats
        # returning "" and letting it look like an empty document.
        result["status"] = "needs_ocr" if b"/Font" in data else "partial"
        result["problems"].append(
            "no text recovered; %s" % ("scanned or unsupported encoding"
                                       if b"/Font" in data else "no font refs"))

    haystack = result["text"] or data.decode("latin-1", "replace")
    result["outbound_refs"] = _clean_refs(haystack)
    return result


def _pdf_text(data):
    """Best-effort body text from uncompressed and Flate content streams.

    Deliberately partial. Proper PDF text extraction means glyph positioning
    and font encoding tables; this recovers the text-showing operators, which
    is enough for search and citation mining on well-behaved PDFs and returns
    nothing at all on the rest. `needs_ocr` covers the difference, honestly.
    """
    import zlib
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        # `Tj` can occur by coincidence inside an image or font stream, and
        # decoding one of those emits a page of mojibake that then dominates
        # the document's searchable text. A real content stream has a BT/ET
        # text block and is overwhelmingly printable.
        if b"BT" not in raw:
            continue
        sample = raw[:4096]
        printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
        if sample and printable / len(sample) < 0.7:
            continue
        # A TJ array interleaves strings with kerning adjustments. Large
        # negative adjustments are word spaces; without restoring them the
        # whole page comes out as one unbroken token, which ruins both search
        # and every attempt to find where a DOI ends.
        for t in re.finditer(rb"\((?:[^()\\]|\\.)*\)|(-?\d+(?:\.\d+)?)(?=\s*[\]])",
                             raw):
            if t.group(1) is not None:
                if abs(float(t.group(1))) > 100:
                    chunks.append(" ")
                continue
            piece = t.group(0)[1:-1]
            chunks.append(piece.decode("latin-1", "replace"))
        chunks.append(" ")
        if sum(len(c) for c in chunks) > 400000:
            break
    text = "".join(chunks)
    text = re.sub(r"\\[0-7]{1,3}", " ", text)
    text = text.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
    return re.sub(r"\s{3,}", "  ", text)


# -- LaTeX -----------------------------------------------------------------

TEX_TITLE = re.compile(r"\\title\{(.+?)\}\s*(?:\n|%|\\)", re.DOTALL)
TEX_AUTHOR = re.compile(r"\\author\{(.+?)\}\s*(?:\n|%|\\date)", re.DOTALL)
TEX_SECTION = re.compile(r"\\(sub)*section\*?\{([^}]*)\}")
TEX_CITE = re.compile(r"\\cite[tp]?\*?(?:\[[^\]]*\])*\{([^}]*)\}")


def extract_tex(path):
    result = blank_result("tex")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        result["status"] = "corrupt"
        result["problems"].append(repr(exc))
        return result

    result["text"] = text
    m = TEX_TITLE.search(text)
    if m:
        result["metadata"]["title"] = _tex_clean(m.group(1))
    m = TEX_AUTHOR.search(text)
    if m:
        # `\author{N. Writer\\ \small Independent Researcher --- ...}`
        # The affiliation after `\\` is not part of the name.
        result["metadata"]["author"] = _tex_clean(
            re.split(r"\\\\", m.group(1))[0])
    result["sections"] = [
        {"level": 1 + len(sub or "") // 3, "title": _tex_clean(title),
         "offset": m.start()}
        for m in TEX_SECTION.finditer(text)
        for sub, title in [(m.group(1), m.group(2))]]
    refs = _clean_refs(text)
    for m in TEX_CITE.finditer(text):
        for key in m.group(1).split(","):
            if key.strip():
                refs.append({"kind": "citekey", "value": key.strip()})
    result["outbound_refs"] = refs
    return result


def _tex_clean(value):
    value = re.sub(r"\\[a-zA-Z]+\s*", " ", value)
    value = value.replace("{", "").replace("}", "").replace("--", "-")
    return re.sub(r"\s+", " ", value).strip()


EXTRACTORS = {
    ".docx": extract_ooxml, ".doc": extract_ooxml, ".pptx": extract_ooxml,
    ".ppt": extract_ooxml, ".xlsx": extract_ooxml, ".xls": extract_ooxml,
    ".odt": extract_odf, ".odp": extract_odf, ".ods": extract_odf,
    ".rtf": extract_odf, ".pdf": extract_pdf, ".tex": extract_tex,
}


def extract(path):
    """Dispatch by extension. Unknown types degrade, they do not raise."""
    fn = EXTRACTORS.get(os.path.splitext(path)[1].lower())
    if fn is None:
        r = blank_result("unknown", "missing_extractor")
        r["problems"].append("no extractor for %s" % os.path.splitext(path)[1])
        return r
    try:
        return fn(path)
    except Exception as exc:                                    # noqa: BLE001
        # The last line of defence. One malformed file out of 42 must not cost
        # the other 41 -- and the reason has to survive into the graph.
        r = blank_result(os.path.splitext(path)[1].lstrip("."), "corrupt")
        r["problems"].append(repr(exc))
        return r
