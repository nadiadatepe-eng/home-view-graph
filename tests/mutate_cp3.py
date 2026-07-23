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
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 300

MUTATIONS = [
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


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp3.py")],
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
        tree = tempfile.mkdtemp(prefix="mut3-",
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
