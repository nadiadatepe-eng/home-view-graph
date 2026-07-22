#!/usr/bin/env python3
"""CP-5 -- M4, and the cross-validation that proves the partition held.

The most important check here is not about M4 at all. It requires every row of
the corpus to carry exactly one label, and every file M4 was handed to be
accounted for afterwards -- either as its own node or inside a rollup. A gap in
M1-M3 does not raise an error anywhere; it silently becomes junk in M4. This is
the arithmetic that catches it.

The secrets gate runs on planted fakes, never on a real home directory. A test
that proves secrets are excluded by searching your actual `.ssh` directory is a
test that has already read your actual `.ssh` directory.

One threshold from the plan is not usable and is replaced rather than quietly
dropped: it expected M4 to fall from ~87 000 nodes to under 15 000 through
rollup. The measured real corpus is 1 686 files, because the exclusion layers
now remove the application state those 87 000 were. The rollup still has to
prove it reduces and that its sums reconcile -- see `t_rollup`.

Two corpora, as in CP-0: synthetic by default, the real one with
HOMEGRAPH_REAL_CORPUS=1 and the undistributed inventory snapshot.

Run:
    python3 tests/test_cp5.py
"""
from __future__ import annotations

import collections
import os
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.environ.get("HOMEGRAPH_REAL_CORPUS") == "1"
if not REAL:
    from tests.fixtures.synthetic import ROOT as SYNTH_ROOT
    os.environ.setdefault("HOMEGRAPH_ROOT", SYNTH_ROOT)

from homegraph.corpus import Classifier                       # noqa: E402
from homegraph.models.m4_misc import (MiscBuildReport,        # noqa: E402
                                      build, sniff, sqlite_schema)
from homegraph.search import fts_search                       # noqa: E402
from homegraph.store import Store                             # noqa: E402

AS_OF = date(2026, 7, 22).isoformat()
INVENTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gold", "inventory-2026-07-22.tsv")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def classify_all(spec):
    """(by_label, row_count). The row count is taken independently of the
    labelling, so the partition check has something external to compare to."""
    clf = Classifier()
    by_label = collections.defaultdict(list)
    rows = 0

    def label(path, is_link):
        try:
            return clf.classify(path, is_symlink=is_link)
        except Exception as exc:                                # noqa: BLE001
            return "raised:%s" % type(exc).__name__

    if REAL:
        with open(INVENTORY, "rb") as fh:
            for raw in fh:
                ftype, _, _, p = raw.decode(
                    "utf-8", "surrogateescape").rstrip("\n").split("\t", 3)
                rows += 1
                by_label[label(p, ftype == "l")].append(p)
    else:
        from tests.fixtures.synthetic import inventory
        for p, is_link in inventory(spec["home"]):
            rows += 1
            by_label[label(p, is_link)].append(p)
    return by_label, rows


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
        # `large_exact` is False there: the real corpus's large files come and
        # go between snapshots, so the count is a floor. The fixture's three
        # are planted, so there it is an equality.
        spec = _real_spec("CP5")
        spec["name"] = "real"
        return spec
    from tests.fixtures import synthetic as syn
    syn.build_once()
    return {"name": "synthetic", "home": syn.ROOT,
            "files": syn.MISC_TOTAL,
            "large": len(syn.LARGE_FILES), "large_exact": True,
            "seconds": 900,
            "unknown_fraction": 0.25}


def t_build(tmp, by_label, spec):
    # The size gate counts what the RULES classified; the build gets only the
    # files that still exist. On a snapshot older than the filesystem those two
    # differ, and a deleted file is not a classification error.
    classified = len(by_label["misc"])
    paths = [p for p in by_label["misc"] if os.path.exists(p)]
    db = os.path.join(tmp, "m4.db")
    t0 = time.time()
    failure = None
    with Store(db, model="m4") as s:
        try:
            report = build(s, paths, AS_OF)
        except Exception as exc:                                # noqa: BLE001
            failure = "raised:%s" % type(exc).__name__
            report = MiscBuildReport()
        s.rebuild_fts()
    elapsed = time.time() - t0
    check("the misc corpus is the declared size",
          classified == spec["files"],
          "%d file(s) classified misc, expected %d (%d still on disk)"
          % (classified, spec["files"], len(paths)))
    check("M4 builds in under %d min" % (spec["seconds"] // 60),
          elapsed < spec["seconds"] and not failure,
          "%.1fs for %d files%s" % (elapsed, report.files,
                                    "" if not failure else "  " + failure))
    check("content-based typing, not extension",
          report.files
          and report.detected["unknown"]
          < report.files * spec["unknown_fraction"],
          "%d of %d unresolved; subtypes=%s"
          % (report.detected["unknown"], report.files, dict(report.subtypes)))
    # `all()` over an empty list is True, so without the first clause this
    # check passed cleanly when the size cap was removed entirely and
    # large_files came back empty. Vacuous truth is how an absent gate looks.
    n_large = len(report.large_files)
    check("large files are metadata only",
          spec["large"] >= 1
          and (n_large == spec["large"] if spec["large_exact"]
               else n_large >= spec["large"])
          and all(sz > 100 * 1024 * 1024 for _, sz in report.large_files)
          and report.detected["large"] == len(report.large_files),
          "%d file(s) over 100 MB (%s %d), %d typed 'large' without being read"
          % (n_large, "exactly" if spec["large_exact"] else "at least",
             spec["large"], report.detected["large"]))
    return db, report, paths


def t_cross_validation(db, by_label, report, inventory_rows, spec):
    """Partition arithmetic against an independently counted total.

    The previous version of this check summed one dictionary and compared it to
    the same dictionary summed again -- true for any labelling whatsoever,
    including random noise. It was described in this module's docstring as the
    arithmetic that catches a model silently losing files, and it caught
    nothing. The real cross-model version lives in CP-6, which is where all four
    stores actually exist; what belongs here is the weaker but genuine claim:
    classify() emitted exactly one label per inventory row, no more and no less.
    """
    parts = {k: len(v) for k, v in by_label.items() if k != "EXCLUDED"}
    excluded = len(by_label["EXCLUDED"])
    check("every inventory row got exactly one label",
          inventory_rows and sum(parts.values()) + excluded == inventory_rows,
          "%d categorised + %d excluded == %d rows  %s"
          % (sum(parts.values()), excluded, inventory_rows, parts))

    with Store(db) as s:
        # Every M4 file is represented, either as its own node or inside a
        # rollup. Losing files to a rollup that does not account for them is
        # exactly the silent shrinkage this checks for.
        accounted = report.individual_nodes + report.rolled_up_files
        check("every M4 file is accounted for",
              report.files and accounted == report.files,
              "%d individual + %d rolled up = %d of %d"
              % (report.individual_nodes, report.rolled_up_files, accounted,
                 report.files))

        home = spec["home"]
        strays = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='file' AND path IS NOT "
            "NULL AND (path LIKE '%/node_modules/%' OR path LIKE '%/.venv/%' "
            "OR path LIKE ?)", (home + "/.cache/%",)).fetchone()["c"]
        check("no EXCLUDED file reached M4", strays == 0, "%d stray" % strays)

        images = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='file' AND ("
            "path LIKE '%.png' OR path LIKE '%.jpg' OR path LIKE '%.jpeg')"
        ).fetchone()["c"]
        check("no image file reached M4", images == 0,
              "%d image(s) in the junk drawer" % images)


def t_rollup(tmp):
    """Rollup must reduce, and its sums must reconcile with the raw counts.

    Built on its own tree so the ratio is controlled: 400 cold files across 4
    apps, 20 warm ones. On the corpus proper the effect is smaller, because the
    exclusion layers already removed most application state -- that is reported
    by the build, not asserted here.
    """
    corpus_dir = os.path.join(tmp, "rollup")
    old = (date.fromisoformat(AS_OF) - timedelta(days=200))
    old_ts = time.mktime(old.timetuple())
    expected_bytes = collections.Counter()
    for app in ("appA", "appB", "appC", "appD"):
        d = os.path.join(corpus_dir, ".%s" % app, "state")
        os.makedirs(d)
        for i in range(100):
            p = os.path.join(d, "f%03d.dat" % i)
            with open(p, "wb") as fh:
                fh.write(b"x" * (100 + i))
            os.utime(p, (old_ts, old_ts))
            expected_bytes[app] += 100 + i
    warm = os.path.join(corpus_dir, "live")
    os.makedirs(warm)
    for i in range(20):
        with open(os.path.join(warm, "w%02d.json" % i), "w") as fh:
            fh.write("{}")

    paths = []
    for dirpath, _, files in os.walk(corpus_dir):
        paths += [os.path.join(dirpath, f) for f in files]

    db = os.path.join(tmp, "rollup.db")
    failure = None
    with Store(db, model="m4") as s:
        try:
            rep = build(s, sorted(paths), AS_OF)
        except Exception as exc:                                # noqa: BLE001
            failure = "raised:%s" % type(exc).__name__
            rep = MiscBuildReport()
    check("rollup reduces node count",
          rep.files and not failure
          and rep.individual_nodes + rep.rollup_nodes < rep.files / 4,
          "%d files -> %d nodes (%d individual + %d rollup)%s"
          % (rep.files, rep.individual_nodes + rep.rollup_nodes,
             rep.individual_nodes, rep.rollup_nodes,
             "" if not failure else "  " + failure))
    check("warm files keep individual nodes", rep.individual_nodes == 20,
          "%d recent file(s) not rolled up" % rep.individual_nodes)

    with Store(db) as s:
        rows = s.db.execute(
            "SELECT title, size, body FROM nodes WHERE kind='rollup'").fetchall()
        try:
            got_count = sum(int(r["body"].split(": ")[1].split(" files")[0])
                            for r in rows)
        except (IndexError, ValueError):
            got_count = -1
        got_bytes = sum(r["size"] for r in rows)
        check("rollup counts reconcile with the raw files",
              rows and got_count == 400, "%d files summarised" % got_count)
        check("rollup byte totals reconcile",
              rows and got_bytes == sum(expected_bytes.values()),
              "%d == %d bytes" % (got_bytes, sum(expected_bytes.values())))


def t_secrets_gate(tmp):
    """Planted fakes, on a tree built for this check. Never a real home."""
    corpus_dir = os.path.join(tmp, "secretcorpus")
    os.makedirs(os.path.join(corpus_dir, ".ssh"))
    planted = {
        ".ssh/id_rsa": "-----BEGIN OPENSSH PRIVATE KEY-----\nZZSECRETZZ\n",
        ".env": "API_TOKEN=ZZSECRETZZ\n",
        "credentials.json": '{"token": "ZZSECRETZZ"}',
        "server.pem": "-----BEGIN CERTIFICATE-----\nZZSECRETZZ\n",
        ".netrc": "machine example.com password ZZSECRETZZ\n",
    }
    for rel, content in planted.items():
        p = os.path.join(corpus_dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(content)
    with open(os.path.join(corpus_dir, "ordinary.json"), "w") as fh:
        fh.write('{"note": "ZZORDINARYZZ"}')

    clf = Classifier()

    def label(path):
        try:
            return clf.classify(path)
        except Exception as exc:                                # noqa: BLE001
            return "raised:%s" % type(exc).__name__

    verdicts = {rel: label(os.path.join(corpus_dir, rel)) for rel in planted}
    check("all 5 planted secrets are EXCLUDED",
          verdicts and all(v == "EXCLUDED" for v in verdicts.values()),
          str({k: v for k, v in verdicts.items() if v != "EXCLUDED"} or "all"))

    # Every planted secret is fed to build() ON PURPOSE. The earlier version
    # filtered them out first -- so nothing indexable was ever handed over, and
    # "no secret reaches the index" restated the gate above it instead of
    # testing a second, independent barrier.
    everything = [os.path.join(corpus_dir, rel) for rel in planted]
    everything.append(os.path.join(corpus_dir, "ordinary.json"))
    db = os.path.join(tmp, "secrets.db")
    with Store(db, model="m4") as s:
        try:
            build(s, everything, AS_OF)
        except Exception as exc:                                # noqa: BLE001
            print("   M4 build raised: %r" % exc)
        s.rebuild_fts()
        leaked = fts_search(s, "ZZSECRETZZ", hidden_subtypes=())
        control = fts_search(s, "ZZORDINARYZZ", hidden_subtypes=())
        check("no secret body reaches the FTS index even when indexed",
              not leaked,
              "%d hit(s); M4 indexes filenames and schema, never contents"
              % len(leaked))
        # A real positive control. `len(control) >= 0` was always true and the
        # `and` meant `control` was never read at all, so an index that could
        # not find ANY body text reported the same clean result as one that
        # correctly withheld the secrets. Measured: it could not, and said so.
        planted_name = fts_search(s, "credentials", hidden_subtypes=())
        check("the index can find what it does index",
              len(planted_name) >= 1 and s.fts_count() == s.node_count() > 0,
              "%d FTS row(s) == %d nodes; filename term found %d time(s), "
              "body term %d (M4 stores no bodies, so 0 is correct)"
              % (s.fts_count(), s.node_count(), len(planted_name),
                 len(control)))


def t_sqlite_safety(tmp):
    db_path = os.path.join(tmp, "sample.sqlite")
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE creds (id INTEGER, token TEXT)")
    conn.execute("INSERT INTO creds VALUES (1, 'ZZROWSECRETZZ')")
    conn.commit()
    conn.close()

    try:
        tables = sqlite_schema(db_path)
    except Exception as exc:                                    # noqa: BLE001
        tables = ["raised:%s" % type(exc).__name__]
    check("sqlite yields table names", tables == ["creds"], str(tables))

    store_db = os.path.join(tmp, "m4sql.db")
    with Store(store_db, model="m4") as s:
        try:
            build(s, [db_path], AS_OF)
        except Exception as exc:                                # noqa: BLE001
            print("   M4 build raised: %r" % exc)
        s.rebuild_fts()
        rows = fts_search(s, "ZZROWSECRETZZ", hidden_subtypes=())
        check("sqlite row contents never reach the index", not rows,
              "%d hit(s); schema is indexed, data is not" % len(rows))
        check("the schema did reach the index",
              len(fts_search(s, "creds", hidden_subtypes=())) == 1,
              "table name searchable")


def t_typing(tmp):
    def sniffed(path):
        try:
            return sniff(path)
        except Exception as exc:                                # noqa: BLE001
            return ("raised:%s" % type(exc).__name__, "raised")

    # A file whose extension lies. Magic must win, or every mislabelled file
    # in the corpus is typed by its name. The earlier version of this test used
    # extensionless files only, so precedence was never exercised at all.
    liar = os.path.join(tmp, "actually_a_database.json")
    with open(liar, "wb") as fh:
        fh.write(b"SQLite format 3\x00" + b"\x00" * 32)
    check("magic beats a lying extension",
          sniffed(liar) == ("database", "sqlite"),
          "got %s for a .json file that is really SQLite" % (sniffed(liar),))

    cases = [(b"SQLite format 3\x00", "database", "sqlite"),
             (b"\x7fELF\x02\x01", "binary", "elf"),
             (b"PK\x03\x04\x14", "archive", "zip"),
             (b"\x89PNG\r\n", "media", "png"),
             (b"plain readable text here", "text", "text"),
             (b"\x00\xff\x00\xff\x00\xfe\x01\x02", "binary", "unknown")]
    wrong = []
    for i, (head, subtype, detected) in enumerate(cases):
        p = os.path.join(tmp, "sniff%d" % i)   # no extension, on purpose
        with open(p, "wb") as fh:
            fh.write(head)
        got = sniffed(p)
        if got != (subtype, detected):
            wrong.append((head[:8], (subtype, detected), got))
    check("magic numbers beat missing extensions", not wrong and cases,
          "%d/%d correct%s" % (len(cases) - len(wrong), len(cases),
                               "" if not wrong else " %s" % wrong[:2]))


def main():
    spec = corpus()
    tmp = tempfile.mkdtemp(prefix="cp5-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        by_label, inventory_rows = classify_all(spec)
        print("corpus: %s  (%d files)\n" % (spec["name"], inventory_rows))
        db, report, paths = t_build(tmp, by_label, spec)
        t_cross_validation(db, by_label, report, inventory_rows, spec)
        t_rollup(tmp)
        t_secrets_gate(tmp)
        t_sqlite_safety(tmp)
        t_typing(tmp)
        print("\nbuild report: %s" % report.summary())
        print("largest files (metadata only):")
        for p, sz in sorted(report.large_files, key=lambda x: -x[1])[:4]:
            print("   %7.1f MB  %s" % (sz / 1e6, os.path.relpath(p,
                                                                spec["home"])))
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

def test_checkpoint_cp5():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
