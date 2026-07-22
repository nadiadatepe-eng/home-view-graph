#!/usr/bin/env python3
"""CP-4 -- M2, images. The gate is a syscall test.

Everything else in this checkpoint is ordinary. The one check that carries the
model's entire safety argument is that no file under an image root is ever
opened, and it is verified twice: by a Python audit hook inside the build
process, and by strace outside it. Two mechanisms because they fail differently
-- the hook misses anything that bypasses the interpreter, strace misses
nothing but is not always available.

`homegraph init` is held to the same invariant and verified the same two ways.
It has to walk the image directory *before* anything has promised not to read
it, so a scanner that opened files would break the guarantee upstream of the
model that makes it -- and no gate in M2 could see that happen.

Speed and memory are REGRESSION GUARDS, not evidence. The implication does not
run backwards: chunked reading costs one buffer, and sniffing 4 KB of each of a
few hundred files costs neither measurable time nor memory. The harness proves
it -- its own mutation plants a 16-byte read and both limits stay green. Only
the audit hook and strace detect that. `ru_maxrss` is also a high-water mark, so
under `pytest` these limits inherit whatever earlier checkpoints allocated.

Two corpora, as in CP-0. The synthetic one is the default so a fresh clone can
run this anywhere; the real one is opt-in with HOMEGRAPH_REAL_CORPUS=1 and needs
tests/gold/cp4-filenames.tsv, which is not distributed -- it names one person's
artwork. The answer key for the synthetic corpus is DECLARED in
tests/fixtures/synthetic.py, beside the code that plants each name, and nothing
there ever calls the parser: a key computed from the parser would agree with any
parser, including a broken one.

Run:
    python3 tests/test_cp4.py
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.environ.get("HOMEGRAPH_REAL_CORPUS") == "1"
if not REAL:
    # The corpus root and its config have to be known before homegraph is
    # imported, because the strace child process below inherits both through
    # the environment and has to reach the same answer as its parent.
    # m2_build no longer freezes the image root at import time -- it reads the
    # config on call -- but pointing the environment first keeps parent and
    # child honest about which installation they are describing.
    from tests.fixtures.synthetic import CONFIG as SYNTH_CONFIG
    from tests.fixtures.synthetic import ROOT as SYNTH_ROOT
    os.environ.setdefault("HOMEGRAPH_ROOT", SYNTH_ROOT)
    os.environ.setdefault("HOMEGRAPH_CONFIG", SYNTH_CONFIG)

from homegraph.corpus import Classifier                        # noqa: E402
from homegraph.models.m2_build import (ImageBuildReport,       # noqa: E402
                                       build, image_roots,
                                       no_open_guard)
from homegraph.models.m2_images import FilenameParser          # noqa: E402
from homegraph.store import Store                              # noqa: E402

AS_OF = date(2026, 7, 22).isoformat()
TODAY = date(2026, 7, 22)
GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "gold", "cp4-filenames.tsv")

FIELDS = ("date", "kind", "indices", "resolution", "dpi", "copy", "variant")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-42s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# -- the two corpora -------------------------------------------------------

def _real_gold():
    rows = []
    with open(GOLD, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("path\t"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) >= 8:
                rows.append((f[0], dict(zip(FIELDS, f[1:8]))))
    return rows


def _real_spec(name):
    """The real corpus's parameters, or an honest failure. See test_cp2.py."""
    try:
        from tests.gold import real_corpus
    except ImportError as exc:                                  # noqa: BLE001
        raise SystemExit(
            "HOMEGRAPH_REAL_CORPUS=1 needs tests/gold/real_corpus.py, which is "
            "not distributed. See tests/gold/FASIT.md.") from exc
    return dict(getattr(real_corpus, name))


def corpus():
    """(spec). Synthetic unless the real corpus is asked for."""
    if REAL:
        # Measured 0.6s / 3 MB on the real corpus; the limits in that spec sit
        # far above, so they catch a regression rather than the machine's mood.
        spec = _real_spec("CP4")
        spec["name"] = "real"
        spec["gold"] = _real_gold()
        return spec

    from tests.fixtures import synthetic as syn
    syn.build_once()
    gold = [(rel, dict(zip(FIELDS, row[:7])))
            for rel, *row in
            [(r[0],) + tuple(r[1:8]) for r in syn.FILENAME_FASIT]]
    return {
        "name": "synthetic",
        # Also from the config. The fixture DECLARES the role (see
        # synthetic.DECLARED_ROLES); the test reads it back the way the package
        # does, so a config that failed to reach the classifier shows up here.
        "image_root": image_roots(base=syn.ROOT)[0].rstrip("/"),
        "gold": gold,
        "gold_rows": len(syn.FILENAME_FASIT),
        "malformed": sum(1 for r in syn.FILENAME_FASIT if r[2] == "malformed"),
        "ambiguous": (syn.FILENAME_FASIT[12][0], "2019-05-27", "ddmmyy"),
        "images": len(syn.FILENAME_FASIT),
        "collections": dict(syn.IMAGE_COLLECTIONS),
        "skipped_non_image": syn.NON_IMAGE_UNDER_BILDER,
        "series_stem": syn.SERIES_STEM,
        "series_members": set(syn.SERIES_MEMBERS),
        "series_non_member": syn.SERIES_NON_MEMBER,
        "seconds": 2.0, "growth_mb": 100,
    }


def image_paths(spec):
    clf = Classifier()
    out = []
    for dirpath, _, files in os.walk(spec["image_root"]):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                label = clf.classify(p)
            except Exception as exc:                            # noqa: BLE001
                label = "raised:%s" % type(exc).__name__
            if label in ("image", "document"):
                out.append(p)
    return sorted(out)


# -- checks ----------------------------------------------------------------

def t_filename_fasit(spec):
    parser = FilenameParser(today=TODAY)
    gold = spec["gold"]
    root = (spec["image_root"] if REAL
            else os.path.dirname(spec["image_root"]))

    def parse(rel):
        try:
            return parser.parse(os.path.join(root, rel)).as_row()
        except Exception as exc:                                # noqa: BLE001
            return {k: "raised:%s" % type(exc).__name__ for k in FIELDS}

    # Without this, a moved or malformed key yields `0/0 correct` and a green
    # PASS. Every gate below iterates over `gold`, and `all()` over nothing is
    # true, so the row count is its own check rather than a clause inside one.
    check("filename key rows loaded", len(gold) == spec["gold_rows"],
          "%d row(s), expected %d" % (len(gold), spec["gold_rows"]))

    wrong = []
    for rel, want in gold:
        got = parse(rel)
        diff = {k: (want[k], got[k]) for k in FIELDS if want[k] != got[k]}
        if diff:
            wrong.append((os.path.basename(rel), diff))
    check("hand-read filenames", not wrong and gold,
          "%d/%d correct%s" % (len(gold) - len(wrong), len(gold),
                               "" if not wrong else "  %s" % wrong[:2]))

    malformed = [rel for rel, want in gold if want["kind"] == "malformed"]
    check("malformed dates flagged, not raised",
          len(malformed) == spec["malformed"] and spec["malformed"] > 0,
          "%d invalid name(s), all parsed without exception" % len(malformed))

    # The ambiguous one, called out because it is the only row where the
    # obvious reading is wrong and nothing would look broken.
    rel, want_date, want_kind = spec["ambiguous"]
    got = parse(rel)
    check("future-dated YYMMDD falls back to DDMMYY",
          got["date"] == want_date and got["kind"] == want_kind,
          "%s via %s" % (got["date"], got["kind"]))


def t_no_open_in_process(tmp, spec):
    """The audit hook: any open() under an image root aborts the build."""
    paths = image_paths(spec)
    db = os.path.join(tmp, "m2.db")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.time()
    failure = None
    with Store(db, model="m2") as s:
        with no_open_guard(spec["image_root"]) as opened:
            try:
                report = build(s, paths, AS_OF,
                               parser=FilenameParser(today=TODAY))
            except Exception as exc:                            # noqa: BLE001
                # The guard raises ImageOpened the instant something opens an
                # image, and that is the correct behaviour -- but letting it
                # propagate kills the process before any gate can say no, and
                # the mutation harness then reports a "crash kill", the weakest
                # kind of detection there is. Caught, recorded, and failed here.
                failure = "raised:%s" % type(exc).__name__
                report = ImageBuildReport()
        s.rebuild_fts()
    elapsed = time.time() - t0
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    growth_mb = (after - before) / 1024.0

    check("audit hook saw no image opened", not opened and not failure,
          "%d file(s) opened%s"
          % (len(opened), "" if not failure else ", build %s" % failure))
    check("build under %.0fs (regression guard, not proof)" % spec["seconds"],
          elapsed < spec["seconds"],
          "%.2fs for %d images" % (elapsed, report.images))
    check("memory growth under %d MB (regression guard, not proof)"
          % spec["growth_mb"], growth_mb < spec["growth_mb"],
          "%.1f MB; ru_maxrss is a high-water mark, so this is order-dependent"
          % growth_mb)
    check("non-image files under the image root are skipped",
          report.skipped_non_image == spec["skipped_non_image"],
          "%d skipped, expected %d"
          % (report.skipped_non_image, spec["skipped_non_image"]))
    return db, report


def t_no_open_strace(tmp, spec):
    """Independent check: strace the same build from outside the interpreter."""
    if not shutil.which("strace"):
        check("strace confirms no image was read", False,
              "strace not installed -- this gate cannot be skipped silently")
        check("strace actually observed the build", False, "no strace")
        return
    image_root = spec["image_root"]
    script = os.path.join(tmp, "build_once.py")
    with open(script, "w") as fh:
        fh.write(
            "import sys, os\n"
            "sys.path.insert(0, %r)\n"
            "os.environ.setdefault('HOMEGRAPH_ROOT', %r)\n"
            "from datetime import date\n"
            "from homegraph.store import Store\n"
            "from homegraph.models.m2_build import build\n"
            "from homegraph.models.m2_images import FilenameParser\n"
            "from homegraph.corpus import Classifier\n"
            "clf = Classifier()\n"
            "paths = []\n"
            "for dp, _, fs in os.walk(%r):\n"
            "    paths += [os.path.join(dp, f) for f in fs "
            "if clf.classify(os.path.join(dp, f)) == 'image']\n"
            "with Store(%r, model='m2') as s:\n"
            "    build(s, sorted(paths), %r, parser=FilenameParser())\n"
            % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
               os.path.dirname(image_root), image_root,
               os.path.join(tmp, "strace.db"), AS_OF))
    log = os.path.join(tmp, "strace.log")
    subprocess.run(["strace", "-f", "-e", "trace=openat", "-o", log,
                    sys.executable, script],
                   capture_output=True, text=True, timeout=300)
    # Walking the tree has to open directories. That is reading names, which
    # is exactly what this model is allowed to do.
    hits, dir_opens, total = _strace_openat(log, image_root)
    # Without this, an strace that captured nothing at all -- wrong binary,
    # permissions, a silent failure -- would report a clean pass.
    check("strace actually observed the build", total > 100,
          "%d openat() call(s) traced overall" % total)
    check("strace confirms no image was read", not hits,
          "%d file open(s) under the image root, %d directory open(s)%s"
          % (len(hits), dir_opens, "" if not hits else ": " + hits[0][:80]))


def _strace_openat(log, root):
    """(file opens under root, directory opens under root, total openat calls)."""
    hits, dir_opens, total = [], 0, 0
    with open(log, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "openat(" not in line:
                continue
            total += 1
            if root not in line or "ENOENT" in line:
                continue
            if "O_DIRECTORY" in line:
                dir_opens += 1
                continue
            hits.append(line.strip())
    return hits, dir_opens, total


def t_init_never_opens(tmp, spec):
    """`homegraph init` walks the image directory. It must not read it.

    The scan runs before M2 exists, so nothing inside the model can observe it
    breaking the model's promise. Verified the same two ways as the build: an
    audit hook in-process, strace out of it.
    """
    from homegraph.scan import scan

    root = os.path.dirname(spec["image_root"])
    failure = None
    with no_open_guard(spec["image_root"]) as opened:
        try:
            prop = scan(root)
        except Exception as exc:                                # noqa: BLE001
            # Caught, not propagated: a raise here would kill the process
            # before any gate could say no, and the harness would report the
            # weakest kind of detection there is.
            failure = "raised:%s" % type(exc).__name__
            prop = None
    # A scan that saw nothing opens nothing, trivially. The proposal has to
    # actually reach the image directory for the absence to mean anything.
    proposed = list(prop.roles.get("image") or []) if prop else []
    check("init scan reached the image directory",
          bool(proposed) and not failure,
          "proposed image role %s%s"
          % (proposed, "" if not failure else ", scan %s" % failure))
    check("audit hook saw init open no image", not opened and not failure,
          "%d file(s) opened" % len(opened))
    # The scanner is an independent read of the layout; the fixture declared
    # the same thing. Agreement is the check -- a scanner that proposed nothing,
    # or proposed the icon cache, would still have opened no files.
    expected = [os.path.basename(spec["image_root"])]
    check("init proposes the declared image role", proposed == expected,
          "%s, declared %s" % (proposed, expected))

    if not shutil.which("strace"):
        check("strace confirms init read no image", False,
              "strace not installed -- this gate cannot be skipped silently")
        check("strace actually observed init", False, "no strace")
        return
    cfg = os.path.join(tmp, "init.toml")
    log = os.path.join(tmp, "strace-init.log")
    env = dict(os.environ, HOMEGRAPH_CONFIG=cfg)
    subprocess.run(["strace", "-f", "-e", "trace=openat", "-o", log,
                    sys.executable, "-m", "homegraph.cli", "init",
                    "--root", root, "--yes", "--force", "--config", cfg],
                   capture_output=True, text=True, timeout=300, env=env,
                   cwd=os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__))))
    hits, dir_opens, total = _strace_openat(log, spec["image_root"])
    check("strace actually observed init", total > 100,
          "%d openat() call(s) traced overall" % total)
    check("strace confirms init read no image", not hits,
          "%d file open(s) under the image root, %d directory open(s)%s"
          % (len(hits), dir_opens, "" if not hits else ": " + hits[0][:80]))


def t_graph(db, report, spec):
    with Store(db) as s:
        n_images = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='image'").fetchone()["c"]
        check("image count matches the corpus layer",
              n_images == spec["images"],
              "%d image nodes, expected %d" % (n_images, spec["images"]))

        strays = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='image' "
            "AND path NOT LIKE ?",
            (spec["image_root"] + "/%",)).fetchone()["c"]
        check("no image node outside the image root", strays == 0,
              "%d stray" % strays)

        hashes = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='image' "
            "AND content_hash IS NOT NULL").fetchone()["c"]
        check("no content hashes exist", hashes == 0,
              "hashing would mean reading")

        colls = {r["title"] for r in s.db.execute(
            "SELECT title FROM nodes WHERE kind='collection'")}
        expected = set(spec["collections"])
        check("all %d collections become nodes" % len(expected),
              expected <= colls,
              "%d collection node(s); missing %s"
              % (len(colls), sorted(expected - colls) or "none"))

        # Membership counts, declared per collection. A collection node that
        # exists but collects nothing would satisfy the check above.
        wrong = []
        for coll, want in sorted(spec["collections"].items()):
            if want is None:
                continue
            got = s.db.execute(
                "SELECT COUNT(*) c FROM edges e JOIN nodes d ON d.id=e.dst "
                "WHERE e.rel='IN_COLLECTION' AND d.node_key=?",
                ("collection:%s" % coll,)).fetchone()["c"]
            if got != want:
                wrong.append((coll, want, got))
        counted = [c for c, w in spec["collections"].items() if w is not None]
        check("collection member counts match the key",
              not wrong and counted,
              "%d collection(s) checked%s"
              % (len(counted), "" if not wrong else "  wrong=%s" % wrong))


def t_series_gate(db, spec):
    """A series gate that cannot exclude is not a gate."""
    with Store(db) as s:
        members = {os.path.basename(r["node_key"]) for r in s.db.execute(
            "SELECT sN.node_key FROM edges e JOIN nodes sN ON sN.id=e.src "
            "JOIN nodes d ON d.id=e.dst "
            "WHERE e.rel='SERIES_MEMBER' AND d.title=?",
            (spec["series_stem"],))}
        check("the declared series groups as one",
              spec["series_members"] <= members and spec["series_members"],
              "%d member(s) of %s" % (len(members), spec["series_stem"]))
        check("a different date is NOT in that series",
              spec["series_non_member"] not in members,
              "%s correctly excluded" % spec["series_non_member"])


def t_copies(db, report):
    with Store(db) as s:
        n = s.db.execute("SELECT COUNT(*) c FROM edges "
                         "WHERE rel='LIKELY_COPY'").fetchone()["c"]
        check("LIKELY_COPY edges exist", n > 0, "%d edge(s)" % n)
        check("copies are LIKELY, never merged",
              s.db.execute("SELECT COUNT(*) c FROM edges "
                           "WHERE rel='SAME_AS'").fetchone()["c"] == 0,
              "no SAME_AS edge exists without content hashes")


def main():
    spec = corpus()
    print("corpus: %s  (%s)\n" % (spec["name"], spec["image_root"]))
    tmp = tempfile.mkdtemp(prefix="cp4-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        t_filename_fasit(spec)
        db, report = t_no_open_in_process(tmp, spec)
        t_no_open_strace(tmp, spec)
        t_init_never_opens(tmp, spec)
        t_graph(db, report, spec)
        t_series_gate(db, spec)
        t_copies(db, report)
        print("\nbuild report: %s" % report.summary())
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

def test_checkpoint_cp4():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
