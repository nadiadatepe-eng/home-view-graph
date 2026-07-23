#!/usr/bin/env python3
"""CP-0 -- the checkpoint for the corpus layer.

Each check below is written so it can fail. That is not a figure of speech: the
gold set scoring 60/60 on the first run proves almost nothing, because the
rules were written with the answer key in hand. What proves something is the
partition holding across 588 589 real paths, and the negative control showing
that removing the rules changes the answer by three orders of magnitude.

Run:
    PYTHONPATH=~/homegraph python3 tests/test_cp0.py <inventory.tsv>
"""
import collections
import inspect
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.corpus import ALL_LABELS, Classifier, EXCLUDED  # noqa: E402

# Two corpora. The synthetic one is the default so a fresh clone can run this
# on any machine; the real one is opt-in with HOMEGRAPH_REAL_CORPUS=1 and needs
# the inventory snapshot, which is not distributed -- it is 74 MB of one
# person's actual filenames.
#
# Thresholds differ per corpus and are declared per corpus. The noise floor is
# 70% on a real home directory, where dependency trees dominate; on a fixture
# built by hand it would be a statement about how much junk the fixture author
# chose to plant, which is not a fact about anything.
REAL = os.environ.get("HOMEGRAPH_REAL_CORPUS") == "1"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-34s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def read_inventory(path):
    rows = []
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                ftype, size, mtime, p = raw.decode(
                    "utf-8", "surrogateescape").rstrip("\n").split("\t", 3)
            except ValueError:
                continue
            rows.append((p, ftype == "l"))
    return rows


def cp0_partition(clf, rows):
    """Every file gets exactly one label, and it is a known one.

    classify() returning a single value makes 'exactly one' true by
    construction, so what this actually tests is the part that can break:
    that no path raises, none comes back unlabelled, and none is lost.
    """
    counts = collections.Counter()
    errors = []
    for p, is_link in rows:
        try:
            label = clf.classify(p, is_symlink=is_link)
        except Exception as exc:                      # noqa: BLE001
            errors.append((p, repr(exc)))
            continue
        if label not in ALL_LABELS:
            errors.append((p, "unknown label %r" % label))
            continue
        counts[label] += 1

    check("partition: no errors", not errors,
          "%d failures%s" % (len(errors),
                             "" if not errors else "  e.g. %s" % (errors[0],)))
    check("partition: all accounted for", sum(counts.values()) == len(rows),
          "%d of %d" % (sum(counts.values()), len(rows)))
    return counts


def cp0_noise(counts, total, spec):
    frac = counts[EXCLUDED] / total
    check("noise threshold >= %d%%" % (spec['noise_floor'] * 100),
          frac >= spec['noise_floor'],
          "%.1f%% excluded (%d of %d)" % (frac * 100, counts[EXCLUDED], total))


def cp0_image_gate(clf, rows, counts, spec):
    image_roots = spec["image_roots"]
    def safe(path, is_link):
        try:
            return clf.classify(path, is_symlink=is_link)
        except Exception:                                       # noqa: BLE001
            return "raised"

    strays = [p for p, is_link in rows
              if safe(p, is_link) == "image"
              and not any(p.startswith(r) for r in image_roots)]
    # `any()` over no roots is False, so with the image role emptied EVERY
    # image would be a stray -- except that with no roots nothing is labelled
    # `image` in the first place. The count gate below is what sees that case,
    # which is why the two are separate checks and not one.
    check("image gate: none outside the image root", not strays,
          "%d stray, roots=%s" % (len(strays), list(image_roots)))
    # Was `check(..., True, ...)` -- a print statement counted as a gate. The
    # plan's `count(image) == 102` was removed rather than remeasured, so this
    # pins the measured baseline: the files under the image root minus the two
    # documents that live there, derived independently in FASIT.md rather than
    # copied from the classifier.
    check("image count matches the independent baseline",
          counts["image"] == spec["images"],
          "count(image) = %d, expected %d (%s)"
          % (counts["image"], spec["images"], spec["images_why"]))


def cp0_cache_gate(clf):
    """Planted cache/tmp files must be excluded at any depth."""
    # NOT the system temp dir: "tmp" is itself a cache directory name, so a
    # tree under /tmp is excluded before any planted file is reached. That is
    # the rule working, but it makes the test meaningless. Also keeps every
    # write inside ~/.homegraph/, which CP-FINAL requires.
    scratch = os.path.expanduser("~/.homegraph/cp0-scratch")
    os.makedirs(scratch, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="cache-gate-", dir=scratch)
    try:
        planted = [
            ("foo/.cache/x.md", EXCLUDED),
            ("bar/tmp/y.pdf", EXCLUDED),
            ("a/b/c/d/e/.cache/deep.md", EXCLUDED),
            ("a/b/c/d/e/f/g/temp/deeper.png", EXCLUDED),
            ("keep/real.md", "markdown"),
        ]
        bad = []
        for rel, expected in planted:
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            open(full, "w").close()
            try:
                got = clf.classify(full)
            except Exception as exc:                            # noqa: BLE001
                got = "raised:%s" % type(exc).__name__
            if got != expected:
                bad.append((rel, expected, got))
        check("cache gate at any depth", not bad, "%d wrong %s" % (len(bad), bad))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cp0_negative_control(rows, baseline, spec):
    """Turn the exclusion layers off. If the rules do anything, image count
    must explode from ~100 to well past 50 000. A negative control that does
    not move means the rules were never load-bearing."""
    scratch = os.path.expanduser("~/.homegraph/cp0-scratch")
    os.makedirs(scratch, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="null-rules-", dir=scratch)
    try:
        src = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "homegraph", "rules")
        shutil.copy(os.path.join(src, "categories.toml"), tmp)
        with open(os.path.join(tmp, "exclusions.toml"), "w") as fh:
            fh.write("""
[secrets]
names = []
globs = []
[allow]
paths = []
globs = []
[cache]
dirs = []
globs = []
[dependencies]
dirs = []
[app_state]
prefixes = []
prefixes_scoped = []
home_files = []
[vendored_repos]
enabled = false
own_owners = []
no_origin_is_own = true
keep_toplevel_globs = []
[symlinks]
exclude = false
[image_boundary]
roots = ["/"]
""")
        null = Classifier(rules_dir=tmp)
        def safe_null(path, is_link):
            try:
                return null.classify(path, is_symlink=is_link)
            except Exception:                                   # noqa: BLE001
                return "raised"

        without = collections.Counter(
            safe_null(p, is_link) for p, is_link in rows)
        # Previously this counted `image` alone, so the markdown, document,
        # code and misc rules were never shown to be load-bearing at all. Each
        # category must move by orders of magnitude when the layers come off.
        moved = {}
        for label, factor in spec["control_factors"].items():
            moved[label] = (baseline[label], without[label],
                            without[label] >= baseline[label] * factor)
        check("negative control: every category's rules matter",
              all(ok for _, _, ok in moved.values()),
              "  ".join("%s %d->%d" % (k, a, b) for k, (a, b, _) in
                        moved.items()))
        check("negative control: almost nothing is excluded without rules",
              without[EXCLUDED] < len(rows) * 0.02,
              "%d of %d still excluded (%.2f%%)"
              % (without[EXCLUDED], len(rows),
                 without[EXCLUDED] / len(rows) * 100))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _classify_all(clf, rows):
    """Label counts, with every call wrapped so a raising mutation cannot kill
    the process before a gate says no."""
    def one(path, is_link):
        try:
            return clf.classify(path, is_symlink=is_link)
        except Exception:                                       # noqa: BLE001
            return "raised"
    return collections.Counter(one(p, is_link) for p, is_link in rows)


# Turning a layer off, one layer at a time. These reach past the TOML into the
# loaded attributes on purpose: the alternative is serialising seven variant
# rule files, and what is under test here is the rule engine's layering, not
# the loader. The loader has its own gates -- `{image_roots}` expansion and the
# role lookup are both mutated in mutate_cp0.py.
LAYERS_OFF = {
    "secrets": lambda c: (setattr(c, "_secret_names", set()),
                          setattr(c, "_secret_globs", [])),
    "cache": lambda c: (setattr(c, "_cache_dirs", set()),
                        setattr(c, "_cache_globs", [])),
    "dependencies": lambda c: setattr(c, "_dep_dirs", set()),
    "app_state": lambda c: (setattr(c, "_state_prefixes", ()),
                            setattr(c, "_state_scoped", ()),
                            setattr(c, "_home_files", set())),
    "vendored_repos": lambda c: setattr(c, "_vendored_roots", []),
    "symlinks": lambda c: setattr(c, "_exclude_symlinks", False),
    # Not an empty tuple: empty means "no image corpus on this machine" and
    # excludes every image, which would make the layer look MORE load-bearing
    # when switched off. "/" is the boundary genuinely disabled.
    "image_boundary": lambda c: setattr(c, "_image_roots", ("/",)),
}


def cp0_exclusion_report(rows, baseline, spec, home):
    """The report has to name what the rules did, and admit when it cut a list.

    Separate from `cp0_layer_independence`, which asks whether a layer earns
    its keep. This asks whether the *reporting path* can still see it. Those
    fail differently: a layer can own three hundred files and be missing from
    every line the user reads, and `build` said "99.09% excluded" for months
    without anyone being able to check the 99.09%.

    `truncated` is the check that matters. A capped list that does not say it
    was capped reads as full coverage -- the same shape as an `all()` over an
    empty list reading as agreement.
    """
    from homegraph.corpus import EXCLUSION_LAYERS, ExclusionReport

    clf = Classifier(home=home)
    clf.vendored_roots
    report = ExclusionReport(home)
    for path, is_link in rows:
        report.record(path, clf.explain(path, is_symlink=is_link))

    # 1. The report agrees with the census. Two tallies of one walk that
    #    disagree mean one of them is answering about a different corpus.
    check("the report's exclusion count matches the census",
          report.count == baseline[EXCLUDED],
          "report=%d census=%d" % (report.count, baseline[EXCLUDED]))

    # 2. Every layer is named. A layer at zero here is either a rule this
    #    corpus cannot reach or a layer the report forgot; both are findings,
    #    and neither is visible from a percentage.
    silent = [layer for layer in EXCLUSION_LAYERS
              if not report.by_layer.get(layer)]
    check("every exclusion layer is named in the report", not silent,
          "  ".join("%s %d" % (k, report.by_layer.get(k, 0))
                    for k in EXCLUSION_LAYERS)
          + ("" if not silent else "  SILENT: %s" % silent))

    # 3. The cap announces itself. Two directions, because "truncated is
    #    always False" passes the first and "always True" passes nothing --
    #    while "always True" would pass a one-sided gate that only checked
    #    the capped case.
    tight = ExclusionReport(home, cap=2)
    for path, is_link in rows:
        tight.record(path, clf.explain(path, is_symlink=is_link))
    check("a capped list reports itself as truncated",
          tight.truncated and len(tight.top()) == 2
          and tight.dirs_total > 2,
          "cap=2 shown=%d of %d truncated=%s"
          % (len(tight.top()), tight.dirs_total, tight.truncated))

    loose = ExclusionReport(home, cap=None)
    for path, is_link in rows:
        loose.record(path, clf.explain(path, is_symlink=is_link))
    check("an uncapped list does not claim truncation",
          not loose.truncated and len(loose.top()) == loose.dirs_total,
          "shown=%d of %d truncated=%s"
          % (len(loose.top()), loose.dirs_total, loose.truncated))

    # 4. The negative control for the cap: a cap wider than the tally must
    #    not report truncation either. Without this, `truncated` could be
    #    "did anyone set a cap" rather than "did the cap bite".
    wide = ExclusionReport(home, cap=loose.dirs_total + 10)
    for path, is_link in rows:
        wide.record(path, clf.explain(path, is_symlink=is_link))
    check("a cap wider than the tally does not report truncation",
          not wide.truncated, "cap=%d dirs=%d truncated=%s"
          % (wide.cap, wide.dirs_total, wide.truncated))


def cp0_layer_independence(rows, baseline, spec, home):
    """Every exclusion layer must uniquely own at least one file.

    The all-layers-off control answers "do the rules do anything", which is a
    question about the pile. It cannot answer "does THIS layer do anything",
    and the difference is not academic: a layer whose files are all caught by
    some other layer as well is dead weight that every gate reports as green.
    Deleting it would change no output, so no test would notice -- and neither
    would a reviewer, because the layer is plainly written and plainly correct.

    This is the duplicated-invariant failure seen from the other side. There
    the same rule lived in two places and the control could not move; here a
    layer contributes nothing the rest of the stack was not already doing.

    Measured, not assumed: the first run of this gate found [symlinks] owning
    zero files. The corpus's only symlink was `icons/link.svg`, an image
    outside the image root, so the boundary excluded it either way -- and the
    fixture's own comment claimed that case tested "the symlink layer and
    nothing else". A second symlink with no second reason fixed the fixture,
    not the rules.
    """
    owned = {}
    for layer, disable in LAYERS_OFF.items():
        clf = Classifier(home=home)
        clf.vendored_roots            # discover before anyone empties the list
        disable(clf)
        without = _classify_all(clf, rows)
        owned[layer] = baseline[EXCLUDED] - without[EXCLUDED]

    floor = spec["layer_floor"]
    thin = sorted(k for k, v in owned.items() if v < floor.get(k, 1))
    check("every exclusion layer uniquely owns files", not thin,
          "  ".join("%s %d" % (k, v) for k, v in sorted(owned.items()))
          + ("" if not thin else "  BELOW FLOOR: %s" % thin))
    # The instrument, checked against itself. Everything above rests on the
    # claim that LAYERS_OFF really disables the layer it names; a lambda that
    # missed an attribute -- `_secret_names` cleared but `_secret_globs` left
    # standing -- would under-report that layer's ownership and the gate would
    # read as green. Applying all seven has to land where the TOML-driven null
    # control lands, near zero. It also catches an exclusion hardcoded into
    # explain() that belongs to no layer and that nothing can switch off.
    every = Classifier(home=home)
    every.vendored_roots
    for disable in LAYERS_OFF.values():
        disable(every)
    none_left = _classify_all(every, rows)
    check("switching off every layer excludes almost nothing",
          none_left[EXCLUDED] < len(rows) * 0.02,
          "%d of %d still excluded (%.2f%%)"
          % (none_left[EXCLUDED], len(rows),
             none_left[EXCLUDED] / len(rows) * 100))
    return owned


def cp0_one_place_only(clf):
    """The Bilder/ boundary must live in exactly one layer.

    The negative control cannot catch a re-duplication: the null config sets
    `image_boundary.root = "/"`, and a duplicate copy in the category step reads
    that same knob, so both are disabled together and the image count still
    explodes. Mutation testing found this -- the mutation that reintroduces the
    original bug survived the control that was written to prevent it.

    So the check is structural instead of behavioural. It is the weaker kind of
    test, and it is the only kind that can see this.
    """
    cat = clf.cat.get("image", {})
    src = inspect.getsource(Classifier._categorize)
    check("the image boundary exists in exactly one layer",
          "required_root" not in cat and "roots" not in cat
          and "_image_root" not in src
          and "image_boundary" in clf.ex,
          "categories.toml has no root key; _categorize does not consult one")


def cp0_secrets(clf):
    """The corpus layer owns the secrets rule, so it is tested here.

    Neither the gold set nor any CP-0 check exercised `[secrets]` -- switching
    the whole layer off left this checkpoint green. CP-5 tests it, but against
    its own planted corpus, so the rule had no guard at the layer that defines
    it. Names only; nothing is created or read.
    """
    # Invented paths under a root that exists on no machine. Nothing is
    # created and nothing is read -- the rule is about the name -- so the
    # directory not existing is the point rather than a limitation.
    fake = "/nonexistent-corpus"
    cases = [(fake + "/proj/.env", EXCLUDED),
             (fake + "/proj/.env.production", EXCLUDED),
             (fake + "/.ssh/id_rsa", EXCLUDED),
             (fake + "/certs/server.pem", EXCLUDED),
             (fake + "/app/credentials.json", EXCLUDED),
             (fake + "/.codex/auth.json", EXCLUDED),
             (fake + "/proj/environment.md", "markdown")]
    def safe(path):
        try:
            return clf.classify(path)
        except Exception as exc:                                # noqa: BLE001
            return "raised:%s" % type(exc).__name__

    wrong = [(p, want, safe(p)) for p, want in cases if safe(p) != want]
    check("secret filenames are excluded, lookalikes are not", not wrong,
          "%d/%d correct%s" % (len(cases) - len(wrong), len(cases),
                               "" if not wrong else "  %s" % wrong[:2]))
    def subtype(path):
        try:
            return clf.explain(path).subtype
        except Exception:                                       # noqa: BLE001
            return "raised"

    redacted = [p for p, want in cases if want == EXCLUDED
                and subtype(p) != "redacted"]
    check("excluded secrets are counted as redacted", not redacted,
          "%d not marked redacted" % len(redacted))


def cp0_idempotent(clf, rows):
    sample = rows[::37]  # deterministic stride, ~16k paths
    def safe(path, k):
        try:
            return clf.classify(path, is_symlink=k)
        except Exception as exc:                                # noqa: BLE001
            return "raised:%s" % type(exc).__name__

    a = [safe(p, k) for p, k in sample]
    b = [safe(p, k) for p, k in sample]
    check("idempotent", a == b, "%d paths, two passes" % len(sample))


DEFAULT_INVENTORY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "gold", "inventory-2026-07-22.tsv")


GOLD_SET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "gold", "gold-set.tsv")


def load_gold_rows():
    """(label, hard, path, why) for whichever corpus is in play.

    For the synthetic corpus the rows come from the generator, which DECLARES
    what it planted and never calls classify(). A key derived from the thing it
    grades is a photograph, not a check.
    """
    if REAL:
        rows = []
        with open(GOLD_SET, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#") or line.startswith("label\t"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 5:
                    rows.append((parts[0], parts[2] == "y", parts[3], parts[4]))
        return rows, 60, 0.95
    from tests.fixtures.synthetic import CASES, ROOT
    return ([(label, hard, os.path.join(ROOT, rel), why)
             for label, _sub, hard, rel, why in CASES],
            len(CASES), 1.0)


def cp0_gold_set(clf):
    """Score the 60 hand-labelled files.

    This lived only in `tests/gold/score_gold.py`, a standalone script pytest
    never collected -- so the flagship answer key, 60 files and 24 adversarial
    cases, was not guarding anything. A rule change that reclassified `.face`
    or dropped `.mdx` would have turned nothing red in any of the seven
    checkpoints.
    """
    gold, expected_rows, threshold = load_gold_rows()
    rows, wrong, hard_wrong, hard = [], [], [], 0
    for label, is_hard, path, _why in gold:
        rows.append(path)
        hard += is_hard
        try:
            got = clf.classify(path)
        except Exception as exc:                                # noqa: BLE001
            # A classifier that raises has failed the row, not the suite. This
            # scorer used to propagate, so a mutation that made classify()
            # raise killed the process before any gate could say no -- the
            # mutation harness reported it as "detected by a crash", which is
            # the weakest kind of detection there is.
            got = "raised:%s" % type(exc).__name__
        if got != label:
            wrong.append((path, label, got))
            if is_hard:
                hard_wrong.append(path)

    check("gold set: all %d rows loaded" % expected_rows,
          len(rows) == expected_rows, "%d row(s)" % len(rows))
    check("gold set: >= %d%% correct" % (threshold * 100),
          rows and (len(rows) - len(wrong)) / len(rows) >= threshold,
          "%d/%d = %.1f%%%s"
          % (len(rows) - len(wrong), len(rows),
             (len(rows) - len(wrong)) / len(rows) * 100 if rows else 0,
             "" if not wrong else "  %s" % wrong[:2]))
    # Reported separately so a run that scrapes past 95% by acing the easy 36
    # and failing the adversarial cases is visible rather than green.
    check("gold set: adversarial cases", not hard_wrong,
          "%d/%d hard cases correct" % (hard - len(hard_wrong), hard))


def _real_spec(name):
    """The real corpus's parameters, or an honest failure. See test_cp2.py."""
    try:
        from tests.gold import real_corpus
    except ImportError as exc:                                  # noqa: BLE001
        raise SystemExit(
            "HOMEGRAPH_REAL_CORPUS=1 needs tests/gold/real_corpus.py, which is "
            "not distributed. See tests/gold/FASIT.md.") from exc
    return dict(getattr(real_corpus, name))


def corpus(inv=None):
    """(rows, classifier, spec). Synthetic unless the real one is asked for."""
    if REAL:
        # The real corpus needs $HOMEGRAPH_CONFIG pointing at a config that
        # describes the machine. Nothing about that machine is written down
        # here: a checkpoint that hardcoded one person's directory names is
        # exactly what the configurable layout removed.
        rows = read_inventory(inv or DEFAULT_INVENTORY)
        clf = Classifier()
        spec = {"name": "real", "image_roots": clf.config.role_dirs("image"),
                "home": None,
                "noise_floor": 0.70,
                "control_factors": {"image": 300, "markdown": 8,
                                    "code": 80, "misc": 100},
                # One file is the weakest floor that still means something:
                # "this layer decides something no other layer would". A real
                # home directory will be far above it for most layers, and
                # real_corpus.py can raise them -- but a default of 1 keeps the
                # gate from silently passing on a corpus that never declared
                # any floor at all.
                "layer_floor": {k: 1 for k in LAYERS_OFF}}
        spec.update(_real_spec("CP0"))
        return rows, clf, spec

    from tests.fixtures.synthetic import CASES, ROOT, build_once, inventory
    build_once()
    rows = inventory(ROOT)
    declared = sum(1 for label, *_ in CASES if label == "image")
    clf = Classifier(home=ROOT)
    spec = {"name": "synthetic", "image_roots": clf.config.role_dirs("image", base=ROOT),
            "images": declared,
            "images_why": "declared by the fixture, not counted from classify",
            "home": ROOT,
            "noise_floor": 0.70,
            # Measured 3 / 124 / 322 / 61 / 1 / 1 / 1, halved and floored at
            # one. The three that measure 1 are structural: the fixture plants
            # exactly one vendored repo, one image outside the boundary, and
            # one symlink with no second reason. Raising those floors would be
            # a claim about how many the fixture author planted.
            "layer_floor": {"secrets": 1, "cache": 60, "dependencies": 160,
                            "app_state": 30, "vendored_repos": 1,
                            "symlinks": 1, "image_boundary": 1},
            # Measured 14.4x / 2.8x / 7.7x / 4.9x; the thresholds sit at
            # roughly half so the gate is about the rules rather than about
            # the fixture author's exact file counts.
            "control_factors": {"image": 7, "markdown": 2,
                                "code": 4, "misc": 2.5}}
    return rows, clf, spec


def main(inv=None):
    # Taken as a parameter, not read from sys.argv. Reading argv inside main()
    # meant that under pytest the runner's own arguments became the inventory
    # path: first `-q`, then `tests/`. A function that reads global process
    # state cannot be called by anything but the command line.
    rows, clf, spec = corpus(inv)
    print("corpus: %s  (%d files)\n" % (spec["name"], len(rows)))
    print("vendored repos found: %d" % len(clf.vendored_roots))
    for r in clf.vendored_roots:
        print("   %s" % r)
    print()

    counts = cp0_partition(clf, rows)
    cp0_noise(counts, len(rows), spec)
    cp0_image_gate(clf, rows, counts, spec)
    cp0_cache_gate(clf)
    cp0_negative_control(rows, counts, spec)
    cp0_layer_independence(rows, counts, spec, spec["home"])
    cp0_exclusion_report(rows, counts, spec, spec["home"])
    cp0_idempotent(clf, rows)
    cp0_one_place_only(clf)
    cp0_secrets(clf)
    cp0_gold_set(clf)

    print("\ncensus:")
    for label in sorted(counts, key=lambda k: -counts[k]):
        print("  %-9s %8d  %5.2f%%"
              % (label, counts[label], counts[label] / len(rows) * 100))

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

def test_checkpoint_cp0():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sys.exit(main(args[0] if args else None))
