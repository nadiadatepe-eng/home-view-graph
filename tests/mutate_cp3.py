#!/usr/bin/env python3
"""Mutation test for CP-3.

Every extractor here was written against a corpus with no libraries installed,
so most of the logic is boundary handling: where a DOI stops, which byte
encoding a title uses, whether a page count can be trusted. Those are exactly
the places that fail quietly -- a title of mojibake and a document of zero
pages both look like data rather than like errors.

Run:
    python3 tests/mutate_cp4.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    ("an author's initials are read as a source file",
     "homegraph/models/m1_extractors.py",
     "        if len(stem) < MIN_STEM or ext not in extensions or token in seen:",
     "        if ext not in extensions or token in seen:"
     "  # mutated: N.R is a file again",
     "a path in prose is a mention, a URL and an initial are not"),

    # -- REFERENCES_FILE: the M1 half of "a document names a file" ---------
    #
    # These gates are new, and new gates are exactly where the empty ones have
    # been found. Four needles: the extraction, the URL exclusion, the
    # resolution, and the refusal to invent.
    ("path mentions are collected and then never linked",
     "homegraph/models/m1_build.py",
     '                store.upsert_edge(path, candidate, "REFERENCES_FILE", as_of,\n'
     '                                  method="mention")',
     '                pass  # mutated: the mention resolves and no edge is drawn',
     "REFERENCES_FILE is exactly the declared set"),

    ("a URL tail counts as a file mention",
     "homegraph/models/m1_extractors.py",
     'r"(?<![\\w@/.-])((?:~/|\\.{1,2}/|/)?(?:[\\w.+-]+/)*[\\w.+-]+\\.[A-Za-z0-9]{1,8})"',
     'r"((?:~/|\\.{1,2}/|/)?(?:[\\w.+-]+/)*[\\w.+-]+\\.[A-Za-z0-9]{1,8})"'
     '  # mutated: no left boundary',
     "a path in prose is a mention, a URL and an initial are not"),

    ("any suffix counts, not only the ones the rules name",
     "homegraph/models/m1_extractors.py",
     "        if len(stem) < MIN_STEM or ext not in extensions or token in seen:",
     "        if len(stem) < MIN_STEM or token in seen:"
     "  # mutated: every dotted word is a file",
     "only the suffixes the rules name count as files"),

    ("an unresolvable mention is resolved to something anyway",
     "homegraph/models/m1_build.py",
     "        else:\n            report.unresolved_refs += 1",
     "        else:\n            pass  # mutated: nothing counts the misses",
     "a document naming a file that does not exist gets no edge"),

    ("only the document's own directory is tried",
     "homegraph/models/m1_build.py",
     "    return [os.path.normpath(os.path.join(os.path.dirname(src), token)),\n"
     "            os.path.normpath(os.path.join(root, token))]",
     "    return [os.path.normpath(os.path.join(os.path.dirname(src), token))]"
     "  # mutated: root-relative prose no longer resolves",
     "REFERENCES_FILE is exactly the declared set"),

    # -- degradation: the claims CP-3 exists to make ---------------------
    #
    # Coverage before this batch was 3 of 21 checks. Everything about how a
    # damaged file behaves -- the whole reason the extractors are stdlib and
    # defensive -- rested on gates no mutation had ever tried to break.
    ("a corrupt archive reports success instead of corrupt",
     "homegraph/models/m1_extractors.py",
     '    except (zipfile.BadZipFile, OSError) as exc:\n'
     '        result["status"] = "corrupt"',
     '    except (zipfile.BadZipFile, OSError) as exc:\n'
     '        result["status"] = "ok"  # mutated: unreadable reads as fine',
     "corrupt file becomes an error node"),

    ("an encrypted PDF is treated as an ordinary one",
     "homegraph/models/m1_extractors.py",
     '    if b"/Encrypt" in data:',
     '    if False:  # mutated: encryption no longer noticed',
     "encrypted PDF is flagged, not decrypted"),

    # The junk-drawer failure: a type nobody wrote an extractor for comes back
    # as an empty document that succeeded, and the graph gains a node with no
    # text and no reason for having none.
    ("an unknown extension yields an empty success",
     "homegraph/models/m1_extractors.py",
     '        r = blank_result("unknown", "missing_extractor")',
     '        r = blank_result("unknown")  # mutated: status ok',
     "unknown type degrades to missing_extractor"),

    ("one bad file aborts the whole batch",
     "homegraph/models/m1_build.py",
     "    for path in paths:\n        data = extract(path)\n"
     "        try:\n            st = os.stat(path)",
     "    for path in paths:\n        data = extract(path)\n"
     "        if data['status'] == 'corrupt':  # mutated: give up on the batch\n"
     "            break\n"
     "        try:\n            st = os.stat(path)",
     "a broken file does not take down its neighbours"),

    # Silently dropping what cannot be read is the tempting fix and the wrong
    # one: the file disappears from the graph entirely, so nothing downstream
    # can report that it was ever there.
    ("unreadable files are skipped instead of stored with a reason",
     "homegraph/models/m1_build.py",
     "        meta = data[\"metadata\"]\n",
     "        if data[\"status\"] == \"corrupt\":  # mutated: skip, do not record\n"
     "            continue\n"
     "        meta = data[\"metadata\"]\n",
     "the unreadable file is still a node, with its reason"),

    # -- the graph M1 produces -------------------------------------------
    ("author metadata is read and then dropped",
     "homegraph/models/m1_build.py",
     '        author = (meta.get("author") or "").strip()\n'
     "        if author:\n            report.authors.add(author)",
     '        author = ""  # mutated: authors never become nodes\n'
     "        if author:\n            report.authors.add(author)",
     "author nodes exist"),

    ("citations are collected but never linked",
     "homegraph/models/m1_build.py",
     '            if store.node_id(key):\n'
     '                store.upsert_edge(path, key, "CITES", as_of, method="exact")',
     '            if False:  # mutated: no CITES edge is ever written\n'
     '                store.upsert_edge(path, key, "CITES", as_of, method="exact")',
     "CITES edges exist"),

    ("every section is stored without its offset",
     "homegraph/models/m1_build.py",
     '                body="%s (offset %d)" % (sec["title"], sec.get("offset", 0)),',
     '                body=sec["title"],  # mutated: offset dropped',
     "section offsets are preserved"),

    # One subtype for everything: the store still has every document, and
    # `--doctype pdf` quietly returns nothing.
    #
    # Aimed at the UPDATE rather than at the upsert. The first version of this
    # mutation changed `subtype=` in upsert_node and survived, because the
    # UPDATE below rewrites the column unconditionally three lines later --
    # two statements set the same field, and only the second one decides. The
    # status half is left intact so exactly one gate goes red.
    ("doctype is flattened to one value in the store",
     "homegraph/models/m1_build.py",
     '            ("%s/%s" % (data["doctype"], data["status"])\n'
     '             if data["status"] != "ok" else data["doctype"], nid))',
     '            ("document/%s" % data["status"]  # mutated: one doctype\n'
     '             if data["status"] != "ok" else "document", nid))',
     "doctype is filterable in the store"),

    # -- the size and completeness of the corpus --------------------------
    ("half the documents never reach the graph",
     "homegraph/models/m1_build.py",
     "    for path in paths:\n        data = extract(path)\n"
     "        try:\n            st = os.stat(path)\n        except OSError:\n"
     "            continue",
     "    for i__, path in enumerate(paths):  # mutated: every other file\n"
     "        data = extract(path)\n"
     "        if i__ % 2:\n            continue\n"
     "        try:\n            st = os.stat(path)\n        except OSError:\n"
     "            continue",
     "every classified document is a node in the graph"),

    ("a doctype stops being classified as a document",
     "homegraph/rules/categories.toml",
     'odt = "odt"',
     '# odt = "odt"  # mutated: odt is no longer a document',
     "the document corpus is the declared size"),

    # An empty document with status `ok` is the exact shape DECISIONS.md
    # section 5 was written to prevent: no text, no error, nothing to look at.
    ("empty text is recorded without the reason it is empty",
     "homegraph/models/m1_build.py",
     '        if not data["text"].strip():\n'
     '            report.empty_text.append((path, data["status"]))',
     '        if not data["text"].strip():  # mutated: reason replaced by ok\n'
     '            report.empty_text.append((path, "ok"))',
     "every empty document says why, and says the declared why"),

    ("ODF documents come back with no text at all",
     "homegraph/models/m1_extractors.py",
     "def extract_odf(path):",
     "def extract_odf(path):\n"
     "    if os.path.exists(path):  # mutated: odt yields nothing\n"
     '        return blank_result("odt")',
     "text recovered from every document the key says has it"),

    ("DOI run-on not trimmed",
     "homegraph/models/m1_extractors.py",
     'DOI_RUNON = re.compile(r"(?<=[0-9])\\.?(?=[A-Z])")',
     'DOI_RUNON = re.compile(r"(?!x)x")  # mutated: never matches',
     "hand-read documents"),

    ("UTF-16 PDF strings not decoded",
     "homegraph/models/m1_extractors.py",
     '    if raw.startswith(b"\\xfe\\xff"):\n'
     '        return raw[2:].decode("utf-16-be", "replace").strip()',
     '    pass  # mutated: BOM ignored, title becomes mojibake',
     "hand-read documents"),

    ("page count trusts /Count",
     "homegraph/models/m1_extractors.py",
     "    pages = max(by_object, declared)",
     "    pages = declared  # mutated: the certificate reports 0 pages",
     "hand-read documents"),

    ("ODF page count read as element text",
     "homegraph/models/m1_extractors.py",
     '        pages = stat.get(ODF_META + "page-count")',
     '        pages = stat.text  # mutated: it is an attribute, not text',
     "hand-read documents"),

    ("LaTeX author keeps its affiliation",
     "homegraph/models/m1_extractors.py",
     '        result["metadata"]["author"] = _tex_clean(\n'
     '            re.split(r"\\\\\\\\", m.group(1))[0])',
     '        result["metadata"]["author"] = _tex_clean(m.group(1))',
     "hand-read documents"),

    ("extract() no longer catches exceptions",
     "homegraph/models/m1_extractors.py",
     "    except Exception as exc:                                    # noqa: BLE001",
     "    except ZeroDivisionError as exc:  # mutated: real errors escape",
     # The corrupt .docx is caught by the inner zipfile handler and never
     # reaches the outer catch-all. The case that does reach it is the valid
     # ZIP with malformed core.xml, so that is the gate that must say no.
     "unexpected exceptions are caught, not raised"),

    ("scanned PDFs report success instead of needs_ocr",
     "homegraph/models/m1_extractors.py",
     '        result["status"] = "needs_ocr" if b"/Font" in data else "partial"',
     '        pass  # mutated: an empty scan looks like an empty document',
     "PDF without a text layer says needs_ocr"),

    ("one doctype silently yields nothing",
     "homegraph/models/m1_extractors.py",
     "def extract_pdf(path):\n    result = blank_result(\"pdf\")",
     "def extract_pdf(path):\n    return blank_result(\"pdf\")  # mutated\n"
     "    result = blank_result(\"pdf\")",
     "hand-read documents"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp3.py", prefix="mut3-", timeout=300))
