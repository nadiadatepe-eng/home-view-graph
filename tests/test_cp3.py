#!/usr/bin/env python3
"""CP-3 -- M1, documents.

The per-type gate is the one that matters. A single overall success rate looks
fine while one format fails every single time: 41 of 42 documents extracting
cleanly is a 97.6% success rate and could still mean every PDF is empty.

The degradation gate is not a simulation here. None of the six libraries the
plan named is installed, so every check below runs against stdlib extractors,
and "what happens when a dependency is missing" is simply the state of the
machine.

Two corpora, as in CP-0. The synthetic one is the default; the real one is
opt-in with HOMEGRAPH_REAL_CORPUS=1 and needs the inventory snapshot and
tests/gold/cp3-documents.tsv, neither of which is distributed. For the synthetic
corpus the five documents' metadata is DECLARED in tests/fixtures/synthetic.py
at the point each file is written -- the title, author and page count are put
INTO the file there, and read back out here by the extractor. Nothing in the
fixture calls extract().

Run:
    python3 tests/test_cp3.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.environ.get("HOMEGRAPH_REAL_CORPUS") == "1"
if not REAL:
    from tests.fixtures.synthetic import ROOT as SYNTH_ROOT
    os.environ.setdefault("HOMEGRAPH_ROOT", SYNTH_ROOT)

from homegraph.corpus import Classifier, known_extensions     # noqa: E402
from homegraph.models.m1_build import DocBuildReport, build   # noqa: E402
from homegraph.models.m1_extractors import (                  # noqa: E402
    extract, file_mentions)
from homegraph.store import Store                             # noqa: E402

AS_OF = date(2026, 7, 22).isoformat()
GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "gold", "cp3-documents.tsv")
INVENTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gold", "inventory-2026-07-22.tsv")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-44s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def safe_extract(path):
    """extract() must not raise. If it does, that is a failed row, not a dead
    suite -- a mutation that makes it raise would otherwise be scored as
    'detected by a crash', which is the weakest detection there is."""
    try:
        return extract(path)
    except Exception as exc:                                    # noqa: BLE001
        return {"doctype": "raised:%s" % type(exc).__name__,
                "status": "raised", "text": "", "sections": [],
                "metadata": {}, "outbound_refs": [], "problems": [repr(exc)]}


# -- the two corpora -------------------------------------------------------

def _real_gold(home):
    rows = []
    with open(GOLD, encoding="utf-8") as fh:
        for line in fh:
            if (line.startswith("#") or line.startswith("path\t")
                    or not line.strip()):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) >= 6:
                # The key stores paths relative to the corpus root.
                rows.append((os.path.join(home, f[0]),) + tuple(f[1:6]))
    return rows


def _paths_from_inventory():
    clf = Classifier()
    out = []
    with open(INVENTORY, "rb") as fh:
        for raw in fh:
            ftype, _, _, p = raw.decode(
                "utf-8", "surrogateescape").rstrip("\n").split("\t", 3)
            try:
                label = clf.classify(p, is_symlink=ftype == "l")
            except Exception:                                   # noqa: BLE001
                continue
            if label == "document":
                out.append(p)
    # Two numbers: the size gate is about what the RULES classified, which is
    # what the declared baseline counts, while the build gets only the files
    # that still exist. The snapshot is older than the filesystem, and a
    # deleted file is not a classification error.
    return len(out), [p for p in out if os.path.exists(p)]


def _paths_from_walk(root):
    clf = Classifier()
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                label = clf.classify(p)
            except Exception:                                   # noqa: BLE001
                continue
            if label == "document":
                out.append(p)
    return len(out), sorted(out)


def _real_spec(name):
    """The real corpus's parameters, or an honest failure.

    They live in tests/gold/real_corpus.py, which is not distributed for the
    same reason the four answer keys beside it are not: every value names a
    real directory or document. Missing file, clear message -- never a silent
    fall back to the synthetic numbers, which would report a real-corpus run
    that never happened.
    """
    try:
        from tests.gold import real_corpus
    except ImportError as exc:                                  # noqa: BLE001
        raise SystemExit(
            "HOMEGRAPH_REAL_CORPUS=1 needs tests/gold/real_corpus.py, which is "
            "not distributed. See tests/gold/FASIT.md.") from exc
    return dict(getattr(real_corpus, name))


def corpus():
    if REAL:
        spec = _real_spec("CP3")
        spec["name"] = "real"
        spec["gold"] = _real_gold(spec["home"])
        return _paths_from_inventory(), spec

    from tests.fixtures import synthetic as syn
    syn.build_once()
    root = syn.ROOT
    spec = {
        "name": "synthetic", "home": root,
        "gold": [(os.path.join(root, rel),) + tuple(rest)
                 for rel, *rest in
                 [(r[0], r[1], r[2], r[3], r[4], r[5])
                  for r in syn.DOCUMENT_FASIT]],
        "gold_rows": len(syn.DOCUMENT_FASIT),
        "files": sum(1 for label, *_ in syn.CASES if label == "document"),
        "corrupt": len(syn.DOCUMENTS_CORRUPT),
        "with_text": {os.path.join(root, r) for r in syn.DOCUMENTS_WITH_TEXT},
        "text_ratio": None,
        "empty": {os.path.join(root, r): st
                  for r, st in syn.DOCUMENTS_EMPTY.items()},
        "doctypes": 4,
        "references": {(os.path.join(root, a), os.path.join(root, b))
                       for a, b in syn.REFERENCES_FILE_FASIT},
        "ref_phantom": os.path.join(root, syn.REFERENCES_FILE_PHANTOM),
        "ref_url": syn.REFERENCES_FILE_URL,
    }
    return _paths_from_walk(root), spec


# -- checks ----------------------------------------------------------------

def t_fasit(spec):
    gold = spec["gold"]
    check("document key rows loaded", len(gold) == spec["gold_rows"],
          "%d row(s), expected %d" % (len(gold), spec["gold_rows"]))

    wrong = []
    for path, doctype, author, pages, cites, title in gold:
        r = safe_extract(path)
        md = r["metadata"]
        got = {
            "doctype": r["doctype"],
            "author": (md.get("author") or "-").strip(),
            "pages": str(md.get("pages", "-")),
            "cites": (cites if cites == "-" or any(
                x["value"] == cites for x in r["outbound_refs"])
                else "MISSING"),
            "title": ("OK" if title == "-"
                      or (md.get("title") or "").startswith(title)
                      else repr((md.get("title") or "")[:40])),
        }
        want = {"doctype": doctype, "author": author, "pages": pages,
                "cites": cites, "title": "OK"}
        diff = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
        if diff:
            wrong.append((os.path.basename(path), diff))
    check("hand-read documents", not wrong and gold,
          "%d/%d correct%s"
          % (len(gold) - len(wrong), len(gold),
             "" if not wrong else "  %s" % wrong[:2]))


def t_build(tmp, paths, classified, spec):
    db = os.path.join(tmp, "m1.db")
    failure = None
    with Store(db, model="m1") as s:
        try:
            report = build(s, paths, AS_OF)
        except Exception as exc:                                # noqa: BLE001
            failure = "raised:%s" % type(exc).__name__
            report = DocBuildReport()
        s.rebuild_fts()

    with Store(db) as s:
        stored = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='document'"
        ).fetchone()["c"]
    # A corpus that silently grows -- an exclusion layer switched off -- looks
    # like a bigger, greener build. The size is declared, not measured.
    check("the document corpus is the declared size",
          classified == spec["files"],
          "%d file(s) classified document, expected %d (%d still on disk)"
          % (classified, spec["files"], len(paths)))
    check("every classified document is a node in the graph",
          not failure and paths and report.documents == len(paths) == stored,
          "%d passed in, %d processed, %d document nodes stored%s"
          % (len(paths), report.documents, stored,
             "" if not failure else "  build %s" % failure))

    bad = report.status["corrupt"] + report.status["missing_extractor"]
    check("only the declared corrupt files fail",
          bad == spec["corrupt"],
          "%d unreadable of %d, expected %d"
          % (bad, report.documents, spec["corrupt"]))

    # The gate a single overall number would hide.
    bad_types = [dt for dt in report.by_doctype
                 if report.status_by_doctype[dt]["ok"] == 0]
    check("every doctype has at least one clean extraction",
          not bad_types and len(report.by_doctype) >= spec["doctypes"],
          "types=%s  failing=%s" % (dict(report.by_doctype), bad_types))

    empty_paths = {p for p, _ in report.empty_text}
    if spec["with_text"] is not None:
        missing = sorted(spec["with_text"] & empty_paths)
        check("text recovered from every document the key says has it",
              not missing and spec["with_text"],
              "%d document(s) declared readable, %d came back empty%s"
              % (len(spec["with_text"]), len(missing),
                 "" if not missing else ": %s"
                 % [os.path.basename(p) for p in missing[:3]]))
    else:
        with_text = report.documents - len(report.empty_text)
        check("text recovered for >= %d%%" % (spec["text_ratio"] * 100),
              report.documents
              and with_text / report.documents >= spec["text_ratio"],
              "%d of %d (%.0f%%)" % (with_text, report.documents,
                                     with_text / max(report.documents, 1) * 100))

    # `all()` over an empty list is True. If the PDF reader ever starts
    # returning "" with status ok -- the exact failure DECISIONS.md §5 exists
    # to prevent -- empty_text goes to zero and this passes vacuously. The
    # count clause is what CP-5's large-file gate got after mutation testing;
    # the same shape needed the same fix here.
    if spec["empty"] is not None:
        got = {p: st for p, st in report.empty_text}
        check("every empty document says why, and says the declared why",
              len(got) >= 1 and got == spec["empty"],
              "%d empty; mismatches %s"
              % (len(got), {os.path.basename(p): (spec["empty"].get(p), st)
                            for p, st in got.items()
                            if spec["empty"].get(p) != st} or "none"))
    else:
        check("every empty document says why",
              len(report.empty_text) >= 1
              and all(st != "ok" for _, st in report.empty_text),
              "%d empty, each flagged: %s"
              % (len(report.empty_text),
                 sorted({st for _, st in report.empty_text})))
    return db, report


def t_graph(db, report):
    with Store(db) as s:
        authors = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='author'").fetchone()["c"]
        check("author nodes exist", authors > 0, "%d author(s)" % authors)
        check("CITES edges exist", report.edges.get("CITES", 0) > 0,
              "%d CITES" % report.edges.get("CITES", 0))
        # Not COUNT(DISTINCT subtype). The column carries `doctype/status` for
        # anything that did not extract cleanly, so four distinct STRINGS is a
        # bar that `document`, `document/corrupt`, `document/needs_ocr` and
        # `document/encrypted` clear on their own -- one doctype wearing four
        # status suffixes. A mutation that flattened every doctype to the word
        # `document` left this gate green. What makes the column filterable is
        # the doctype half, so that is what is compared, and against the set
        # the build reports rather than against a number.
        stored = {(r["subtype"] or "").split("/")[0] for r in s.db.execute(
            "SELECT subtype FROM nodes WHERE kind='document'")}
        expected = set(report.by_doctype)
        check("doctype is filterable in the store",
              len(stored) >= 4 and stored == expected,
              "stored %s  expected %s"
              % (sorted(stored), sorted(expected)))
        page = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='section' "
            "AND body LIKE '%offset%'").fetchone()["c"]
        check("section offsets are preserved", page > 0,
              "%d section(s) carry an offset" % page)


def t_references(db, report, spec):
    """REFERENCES_FILE: the declared set, and nothing the corpus did not say.

    Three gates, and the order matters. The extractor is checked first and on
    its own, because it is the half that can be wrong without the graph ever
    showing it: a resolver that refuses everything and an extractor that finds
    nothing both produce zero edges.
    """
    # Two calls, because the two rules need decoys the other one would eat.
    #
    # First, against the extension set the BUILD uses. `N.R` and `M.C` are
    # initials out of a reference list -- PDF extraction drops the space after
    # the first period -- and `.r` and `.c` are real source extensions, so the
    # extension filter cannot tell them from files and the stem length is the
    # only thing that can. Both were produced by the real corpus, which is
    # where this rule came from.
    found = file_mentions(
        "The data are in Documents/review.odt.\n"
        "After N.R and M.C, whose initials lost their space in extraction.\n"
        "A preprint sits at %s -- a URL, not a path.\n" % spec["ref_url"],
        known_extensions())
    check("a path in prose is a mention, a URL and an initial are not",
          found == ["Documents/review.odt"],
          "found %s" % (found,))

    # Second, against a narrow set, so the decoys are path-SHAPED tokens whose
    # suffix the rules do not name. Without this the extension filter could be
    # deleted outright and the check above stayed green.
    narrow = file_mentions(
        "The data are in Documents/review.odt.\n"
        "Rebuilt with make.sh; see figure 2.4b for the residuals.\n",
        frozenset({".odt", ".pdf", ".tex"}))
    check("only the suffixes the rules name count as files",
          narrow == ["Documents/review.odt"],
          "found %s" % (narrow,))

    declared = spec.get("references")
    if declared is None:
        return
    with Store(db) as s:
        got = {(r["src_key"], r["dst_key"])
               for r in s.edges_as_of(AS_OF, rel="REFERENCES_FILE")}
    check("REFERENCES_FILE is exactly the declared set",
          bool(declared) and got == declared,
          "%d edge(s), %d declared; extra %s; missing %s"
          % (len(got), len(declared),
             sorted(os.path.basename(a) + "->" + os.path.basename(b)
                    for a, b in got - declared) or "none",
             sorted(os.path.basename(a) + "->" + os.path.basename(b)
                    for a, b in declared - got) or "none"))

    # The negative half. A named file that was never written must produce no
    # edge AND no node -- resolving it to the nearest existing document would
    # satisfy the gate above and be a fabrication.
    with Store(db) as s:
        phantom_node = s.node_id(spec["ref_phantom"])
    phantom_edges = [1 for _, dst in got if dst == spec["ref_phantom"]]
    check("a document naming a file that does not exist gets no edge",
          not phantom_node and not phantom_edges
          and report.unresolved_refs >= 1,
          "node=%s edges=%d unresolved=%d"
          % (phantom_node, len(phantom_edges), report.unresolved_refs))


def t_damaged(tmp):
    """Encrypted, corrupt and unknown files must degrade, never raise."""
    corrupt = os.path.join(tmp, "broken.docx")
    with open(corrupt, "wb") as fh:
        fh.write(b"PK\x03\x04this is not really a zip")
    r = safe_extract(corrupt)
    check("corrupt file becomes an error node", r["status"] == "corrupt",
          "status=%s, %d problem(s)" % (r["status"], len(r["problems"])))

    truncated = os.path.join(tmp, "half.pdf")
    with open(truncated, "wb") as fh:
        fh.write(b"%PDF-1.4\n1 0 obj\n<< /Encrypt 2 0 R /Type /Page >>\n")
    r = safe_extract(truncated)
    check("encrypted PDF is flagged, not decrypted",
          r["status"] == "encrypted", "status=%s" % r["status"])

    unknown = os.path.join(tmp, "thing.xyz")
    open(unknown, "w").close()
    r = safe_extract(unknown)
    check("unknown type degrades to missing_extractor",
          r["status"] == "missing_extractor", "status=%s" % r["status"])

    # A valid ZIP whose metadata XML is malformed. The inner handlers do not
    # cover _ooxml_core, so this is the case that reaches extract()'s outer
    # catch-all -- and until this test existed, removing that catch-all changed
    # nothing anywhere in the suite.
    weird = os.path.join(tmp, "weird.docx")
    with zipfile.ZipFile(weird, "w") as z:
        z.writestr("docProps/core.xml", "<not><closed>")
        z.writestr("word/document.xml", "<w:document/>")
    r = safe_extract(weird)
    check("unexpected exceptions are caught, not raised",
          r["status"] == "corrupt" and r["problems"],
          "status=%s  %s" % (r["status"], (r["problems"] or [""])[0][:44]))

    scanned = os.path.join(tmp, "scan.pdf")
    with open(scanned, "wb") as fh:
        fh.write(b"%PDF-1.4\n/Type /Page\n/Font 1 0 R\nstream\nnothing\n"
                 b"endstream\n")
    r = safe_extract(scanned)
    check("PDF without a text layer says needs_ocr",
          r["status"] == "needs_ocr", "status=%s" % r["status"])


def t_degradation(tmp):
    """One broken file of a type must not affect the others.

    The plan phrased this as uninstalling python-pptx. Nothing is installed to
    uninstall, so the equivalent is a file of one type that cannot be read: the
    others of its type, and every other type, must come through untouched.
    """
    good = os.path.join(tmp, "good.odt")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("content.xml",
                   '<?xml version="1.0"?><office:document-content '
                   'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:'
                   'office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:'
                   'xmlns:text:1.0"><office:body><text:h text:outline-level="1"'
                   '>Heading</text:h><text:p>Body text</text:p>'
                   '</office:body></office:document-content>')
    bad = os.path.join(tmp, "bad.odt")
    with open(bad, "wb") as fh:
        fh.write(b"not a zip at all")

    db = os.path.join(tmp, "degrade.db")
    failure = None
    with Store(db, model="m1") as s:
        try:
            report = build(s, [good, bad], AS_OF)
        except Exception as exc:                                # noqa: BLE001
            failure = "raised:%s" % type(exc).__name__
            report = DocBuildReport()
    check("a broken file does not take down its neighbours",
          not failure and report.documents == 2
          and report.status["corrupt"] == 1 and report.status["ok"] == 1,
          "ok=%d corrupt=%d%s" % (report.status["ok"],
                                  report.status["corrupt"],
                                  "" if not failure else "  " + failure))
    with Store(db) as s:
        node = s.get_node(bad)
        check("the unreadable file is still a node, with its reason",
              node is not None and "corrupt" in (node["subtype"] or ""),
              "subtype=%s" % (node["subtype"] if node else None))


def main():
    (classified, paths), spec = corpus()
    print("corpus: %s  (%d documents classified, %d on disk)\n"
          % (spec["name"], classified, len(paths)))
    tmp = tempfile.mkdtemp(prefix="cp3-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        t_fasit(spec)
        db, report = t_build(tmp, paths, classified, spec)
        t_graph(db, report)
        t_references(db, report, spec)
        t_damaged(tmp)
        t_degradation(tmp)
        print("\nbuild report: %s" % report.summary())
        if report.empty_text:
            print("no text recovered from:")
            for p, st in report.empty_text:
                print("   %-16s %s" % (st, os.path.basename(p)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter --------------------------------------------------------
#
# The checks above are written as a script: `t_*` helpers driven by `main()`,
# which prints a readable report and returns an exit code. pytest collects
# `test_*` functions, so without this it collected the file, found nothing, and
# reported success -- a runner that verifies nothing while looking green.
#
# One test per checkpoint rather than one per check, because the phases share
# built state: the corpus is built once and then queried repeatedly, and
# splitting that across independent tests would rebuild it each time.

def test_checkpoint_cp3():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
