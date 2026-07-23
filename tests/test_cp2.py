#!/usr/bin/env python3
"""CP-2 -- M3, markdown.

The known-answer set carries most of the weight in its NOLINK rows: each names a
`[[target]]` that a regex sweep happily turns into an edge, and each sits inside
a code span where it is documentation of the syntax rather than a link.

The backlinks check uses grep as an independent oracle, per the plan. That is
sound only because grep and the extractor disagree on purpose about code spans
-- so the check runs against a link with no code spans in play, and the code
span behaviour is asserted separately by the NOLINK rows.

Two corpora, as in CP-0. The synthetic one is the default so a fresh clone can
run this anywhere; the real one is opt-in with HOMEGRAPH_REAL_CORPUS=1 and needs
the inventory snapshot and tests/gold/cp2-links.tsv, neither of which is
distributed. For the synthetic corpus the fifteen relations are DECLARED in
tests/fixtures/synthetic.py, beside the text that carries them, and nothing
there ever runs the extractor.

Run:
    python3 tests/test_cp2.py
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.environ.get("HOMEGRAPH_REAL_CORPUS") == "1"
if not REAL:
    from tests.fixtures.synthetic import ROOT as SYNTH_ROOT
    os.environ.setdefault("HOMEGRAPH_ROOT", SYNTH_ROOT)

from homegraph import cli                                        # noqa: E402
from homegraph.corpus import Classifier                          # noqa: E402
from homegraph.models.m3_build import (BuildReport,              # noqa: E402
                                       backlinks, broken_links,
                                       build, neighbours)
from homegraph.models.m3_markdown import MarkdownExtractor       # noqa: E402
from homegraph.search import hybrid_search                       # noqa: E402
from homegraph.store import Store                                # noqa: E402

AS_OF = date(2026, 7, 22).isoformat()
GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "gold", "cp2-links.tsv")
INVENTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gold", "inventory-2026-07-22.tsv")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-44s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# -- the two corpora -------------------------------------------------------

def _real_gold(home):
    rows = []
    with open(GOLD, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("kind\t"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                rows.append((parts[0], os.path.join(home, parts[1]), parts[2]))
    return rows


def _paths_from_inventory(home):
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
            if label == "markdown":
                out.append(p)
    # Two numbers, deliberately. The size gate is about what the RULES
    # classified, which is what the declared baseline counts; the build gets
    # only the files that still exist, because the snapshot is older than the
    # filesystem and a deleted file is not a classification error.
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
            if label == "markdown":
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
    """(paths, spec). Synthetic unless the real corpus is asked for."""
    if REAL:
        spec = _real_spec("CP2")
        home = spec["home"]
        spec["name"] = "real"
        spec["gold"] = _real_gold(home)
        return _paths_from_inventory(home), spec

    from tests.fixtures import synthetic as syn
    syn.build_once()
    root = syn.ROOT
    spec = {
        "name": "synthetic", "home": root,
        "gold": [(kind, os.path.join(root, rel), target)
                 for kind, rel, target, _why in syn.LINK_FASIT],
        "gold_rows": len(syn.LINK_FASIT),
        # Declared by the fixture: every planted markdown file, counted from
        # the answer key rather than from what the classifier happens to
        # return. Without it a corpus that silently triples -- an exclusion
        # layer switched off -- reads as a bigger, greener build.
        "files": sum(1 for label, *_ in syn.CASES if label == "markdown"),
        "artefacts": syn.CODE_SPAN_ARTEFACTS,
        "artefact_scope": os.path.join(root, "wiki"),
        # Measured 22 WIKILINKS_TO edges; the gate sits at half, so it is
        # about links existing at all and not about the fixture's exact size.
        "min_wikilink_edges": 11,
        "seconds": 90,
        "ambiguous": (syn.AMBIGUOUS_TARGET,
                      os.path.join(root, syn.AMBIGUOUS_LINKER),
                      os.path.join(root, syn.AMBIGUOUS_WINNER),
                      os.path.join(root, syn.AMBIGUOUS_LOSER)),
        "grep_root": os.path.join(root, "wiki"),
        "cycle": (os.path.join(root, "wiki/wiki/concepts/trails.md"),
                  os.path.join(root, "wiki/wiki/entities/viewer.md")),
    }
    return _paths_from_walk(root), spec


# -- extraction unit checks, no database ----------------------------------

def t_code_spans():
    ex = MarkdownExtractor()

    def extract(text):
        try:
            return ex.extract("/tmp/x.md", text=text)
        except Exception as exc:                                # noqa: BLE001
            return {"wikilinks": ["raised:%s" % type(exc).__name__],
                    "tags": [], "frontmatter_problems": []}

    # The tilde fence is not decoration. RE_INLINE_CODE is DOTALL and matches
    # a run of backticks not followed by another backtick, which is exactly
    # what a ``` fence opens with -- so inline blanking alone already swallows
    # every backtick fence, and deleting RE_FENCE changed nothing any check
    # could see. `~~~` is the one fence only RE_FENCE can blank, and it is
    # what makes the two passes independently load-bearing.
    text = ("---\ntags: [a, b]\n---\n\n"
            "# Title\n\n"
            "A real link to [[alpha]] and a documented one: `[[beta]]`.\n\n"
            "```\nfenced [[gamma]] block\n```\n\n"
            "~~~\ntilde-fenced [[epsilon]] block\n~~~\n\n"
            "See [[delta|an alias]] too.\n")
    d = extract(text)
    check("inline code span is not a link", "beta" not in d["wikilinks"],
          "wikilinks=%s" % d["wikilinks"])
    check("fenced block is not a link", "gamma" not in d["wikilinks"], "")
    check("a tilde fence is not a link either",
          "epsilon" not in d["wikilinks"], "wikilinks=%s" % d["wikilinks"])
    check("real links survive", d["wikilinks"] == ["alpha", "delta"],
          str(d["wikilinks"]))

    # Same length, not removal. Every link offset downstream of a code span
    # depends on it, and the link list is identical either way -- so no gate
    # above can see the difference. DECISIONS.md section 7 states the rule;
    # this is what holds it.
    from homegraph.models.m3_markdown import blank_code
    src = "a `code` b\n\n```\nfenced\n```\n\n~~~\ntilde\n~~~\n[[alpha]]\n"
    blanked = blank_code(src)
    check("blanking preserves every offset",
          len(blanked) == len(src)
          and blanked.index("[[alpha]]") == src.index("[[alpha]]"),
          "%d chars in, %d out" % (len(src), len(blanked)))
    check("piped alias resolves to the target", "delta" in d["wikilinks"], "")
    check("frontmatter list parses", d["tags"] == ["a", "b"], str(d["tags"]))

    # The line without a colon is the point: the previous version of this
    # check used `not valid: [`, which parses fine, so the tolerant path was
    # never exercised and a parser that raised would have passed.
    bad = "---\nname: ok\nthis line has no colon at all\ntags: [x]\n---\n\n# T\n"
    broken = extract(bad)
    check("malformed frontmatter does not raise",
          broken["frontmatter_problems"] and broken["tags"] == ["x"],
          "%d problem(s) reported, parsing continued"
          % len(broken["frontmatter_problems"]))


# -- build over the corpus -------------------------------------------------

def t_build(tmp, paths, classified, spec):
    db = os.path.join(tmp, "m3.db")
    t0 = time.time()
    failure = None
    with Store(db, model="m3") as s:
        try:
            report = build(s, paths, AS_OF)
        except Exception as exc:                                # noqa: BLE001
            # A build that dies takes every gate below with it and the mutation
            # harness scores it as a crash kill. Recorded and failed instead.
            failure = "raised:%s" % type(exc).__name__
            report = BuildReport()
        s.rebuild_fts()
    elapsed = time.time() - t0

    # `report.files == len(paths)` compares the builder's count to its own
    # input -- true whenever nothing was dropped, which is worth knowing but is
    # not the coverage claim the name makes. The store is queried independently
    # so the assertion is about what ended up in the graph.
    with Store(db) as s:
        stored = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE kind='file'").fetchone()["c"]
    check("every classified markdown file is a node in the graph",
          not failure and paths and report.files == len(paths) == stored,
          "%d passed in, %d built, %d file nodes stored; %d sections, %.1fs%s"
          % (len(paths), report.files, stored, report.sections, elapsed,
             "" if not failure else "  build %s" % failure))
    check("the markdown corpus is the declared size",
          classified == spec["files"],
          "%d file(s) classified markdown, expected %d (%d still on disk)"
          % (classified, spec["files"], len(paths)))
    check("full build under %ds" % spec["seconds"], elapsed < spec["seconds"],
          "%.1fs for %d files" % (elapsed, report.files))
    check("wikilink edges exist",
          report.edges.get("WIKILINKS_TO", 0) > spec["min_wikilink_edges"],
          "%d WIKILINKS_TO, floor %d"
          % (report.edges.get("WIKILINKS_TO", 0), spec["min_wikilink_edges"]))
    hand_written = sum(n for st, n in report.broken_by_subtype.items()
                       if st != "generated")
    check("broken links counted separately, and > 0",
          sum(report.broken_links.values()) > 0,
          "%d distinct targets, %d occurrences (%d from hand-written files)"
          % (len(report.broken_links), sum(report.broken_links.values()),
             hand_written))
    check("generated markdown is separated from notes",
          report.subtypes.get("generated", 0) > 0
          and report.broken_by_subtype.get("generated", 0)
          > report.broken_by_subtype.get("note", 0),
          "generated files=%d contributing %d broken links vs %d from notes"
          % (report.subtypes.get("generated", 0),
             report.broken_by_subtype.get("generated", 0),
             report.broken_by_subtype.get("note", 0)))
    # Was `check(..., True, ...)`, and doubly empty: the real corpus reports
    # zero unparsable files, so there was nothing to observe even had it not
    # been hardcoded. The claim worth making is that the build completed over
    # every file regardless of how many had bad frontmatter.
    check("frontmatter problems are logged, not fatal",
          report.files == len(paths)
          and isinstance(report.frontmatter_problems, list),
          "%d file(s) with unparsable frontmatter, %d/%d still built"
          % (len(report.frontmatter_problems), report.files, len(paths)))
    return db, report


def t_known_answers(db, spec):
    gold = spec["gold"]
    with Store(db) as s:
        def outbound(src, rel):
            try:
                return {r["node_key"] for r in s.db.execute(
                    "SELECT d.node_key FROM edges e "
                    "JOIN nodes sN ON sN.id=e.src JOIN nodes d ON d.id=e.dst "
                    "WHERE sN.node_key=? AND e.rel=?", (src, rel))}
            except Exception as exc:                            # noqa: BLE001
                return {"raised:%s" % type(exc).__name__}

        wrong = []
        for kind, src, target in gold:
            if kind in ("LINK", "NOLINK"):
                links = outbound(src, "WIKILINKS_TO")
                names = {os.path.splitext(os.path.basename(k))[0]
                         for k in links}
                names |= {k.split(":", 1)[1] for k in links
                          if k.startswith("wikilink:")}
                if (target in names) != (kind == "LINK"):
                    wrong.append((kind, os.path.basename(src), target))
            elif kind == "TAG":
                if "tag:%s" % target not in outbound(src, "TAGGED"):
                    wrong.append((kind, os.path.basename(src), target))
            elif kind == "BROKEN":
                # The row names WHICH file holds the unresolved link. Checking
                # only that the name exists somewhere in the corpus threw that
                # away and would pass if a completely different note carried it.
                try:
                    sources = backlinks(s, "wikilink:%s" % target)[0]
                    names = broken_links(s)
                except Exception as exc:                        # noqa: BLE001
                    sources, names = [], ["raised:%s" % type(exc).__name__]
                if target not in names or src not in sources:
                    wrong.append((kind, os.path.basename(src), target))
        # Without the row count, a moved or malformed key yields `0/0 correct`
        # and a green PASS.
        check("known-answer rows loaded", len(gold) == spec["gold_rows"],
              "%d row(s), expected %d" % (len(gold), spec["gold_rows"]))
        check("known-answer relations", not wrong and gold,
              "%d/%d correct%s"
              % (len(gold) - len(wrong), len(gold),
                 "" if not wrong else "  %s" % wrong[:3]))


def t_backlinks(db, spec):
    """grep as an independent oracle.

    The target exists twice in the wiki, so the comparison unions the backlinks
    of both candidates. That is deliberate: it tests extraction against grep
    without also baking in whichever candidate resolution picked, which is
    asserted separately below.
    """
    target, linker, winner, loser = spec["ambiguous"]
    with Store(db) as s:
        ours = set()
        for c in (winner, loser):
            try:
                ours |= {os.path.abspath(p) for p in backlinks(s, c)[0]}
            except Exception:                                   # noqa: BLE001
                ours.add("raised")
        proc = subprocess.run(
            ["grep", "-rl", "--include=*.md", "-E",
             r"\[\[%s(\||\]\])" % target, spec["grep_root"]],
            capture_output=True, text=True)
        theirs = {line for line in proc.stdout.split("\n") if line}
        check("backlinks match grep", ours == theirs and theirs,
              "ours=%d grep=%d diff=%s" % (len(ours), len(theirs),
                                           sorted(ours ^ theirs)[:2]))
        # The known-answer rows compare basenames, so they cannot tell the two
        # copies apart. Resolution needs its own assertion or "nearest wins" is
        # untested.
        try:
            picked = [r["node_key"] for r in s.db.execute(
                "SELECT d.node_key FROM edges e JOIN nodes sN ON sN.id=e.src "
                "JOIN nodes d ON d.id=e.dst WHERE sN.node_key=? "
                "AND e.rel='WIKILINKS_TO' AND d.node_key LIKE ?",
                (linker, "%%/%s.md" % target))]
        except Exception as exc:                                # noqa: BLE001
            picked = ["raised:%s" % type(exc).__name__]
        check("ambiguous target resolves to the nearest file",
              picked == [winner],
              "picked %s" % [os.path.relpath(p, spec["home"]) for p in picked])

        check("backlinks are derived, not stored",
              not s.db.execute("SELECT name FROM sqlite_master WHERE "
                               "type='table' AND name LIKE '%backlink%'"
                               ).fetchall(), "no backlink table exists")


def t_backlinks_time_travel(tmp):
    """Backlinks as they stood on a date, through the command a user runs.

    `Store.edges_as_of` holds the whole of time travel -- one predicate, in one
    place -- and until `md backlinks --as-of` existed nothing outside a test
    called it. The schema carried `first_seen` and `last_seen` on every edge,
    store.py's docstring named "which links did this note have last week" as
    the reason, and there was no way to ask.

    So this gate runs the CLI path, not the helper. A check that called
    `backlinks(..., as_of=)` directly would pass just as happily with the
    argument unwired from argparse, which is the shape DECISIONS.md section 21
    is about: the mechanism works, and the product never reaches it.
    """
    db = os.path.join(tmp, "history.db")
    target = "/notes/target.md"
    with Store(db, model="m3") as s:
        for key in (target, "/notes/early.md", "/notes/late.md"):
            s.upsert_node(key, kind="file", path=key, as_of="2026-01-01")
        # early.md linked on both days; late.md only on the second.
        s.upsert_edge("/notes/early.md", target, "WIKILINKS_TO", "2026-01-01", method="exact")
        s.upsert_edge("/notes/early.md", target, "WIKILINKS_TO", "2026-01-05", method="exact")
        s.upsert_edge("/notes/late.md", target, "WIKILINKS_TO", "2026-01-05", method="exact")
        s.commit()

    def run(*extra):
        # SystemExit is caught, not allowed to propagate. argparse raises it
        # for an unknown flag, so a `--as-of` that was never wired up would
        # otherwise kill the checkpoint before any gate spoke -- and the
        # mutation harness scores that as detected-only-by-a-crash, the
        # weakest signal there is. The same for a broken SQL predicate, which
        # surfaces as an exception rather than a wrong answer.
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(["md", "backlinks", db, target, *extra])
        except SystemExit as exc:
            return "exit:%s" % exc.code, out.getvalue()
        except Exception as exc:                                # noqa: BLE001
            return "raised:%s" % type(exc).__name__, out.getvalue()
        return code, out.getvalue()

    code_now, now = run()
    code_then, then = run("--as-of", "2026-01-01")

    check("md backlinks runs and finds both links today",
          code_now == 0 and "early.md" in now and "late.md" in now,
          "exit %s, %d line(s)" % (code_now, len(now.splitlines())))
    # The one that matters: a link that did not exist yet must be absent, and
    # `--as-of` must not simply return everything with a date printed on it.
    check("--as-of hides a link that did not exist yet",
          code_then == 0 and "early.md" in then and "late.md" not in then,
          "exit %s: %s" % (code_then, then.replace("\n", " ")[:70]))
    check("the date is echoed with the answer",
          "2026-01-01" in then, then.splitlines()[0] if then else "(no output)")


def t_subtype_gate(tmp):
    """The filter must return zero for a term that only a hidden file has."""
    db = os.path.join(tmp, "sub.db")
    with Store(db, model="m3") as s:
        s.upsert_node("/notes/real.md", kind="file", subtype="note",
                      title="note", body="ordinary content here")
        s.upsert_node("/logs/session.md", kind="file", subtype="transcript",
                      title="log", body="zzsentinelterm appears only here")
        s.rebuild_fts()

        def search(**kw):
            try:
                return hybrid_search(s, "zzsentinelterm", **kw)
            except Exception as exc:                            # noqa: BLE001
                raise AssertionError from exc

        hidden = search()
        check("subtype gate says no", len(hidden) == 0,
              "%d hits without --all" % len(hidden))
        shown = search(include_all=True)
        check("--all reveals it", len(shown) == 1,
              "%d hits with include_all" % len(shown))
        check("the hiding is announced",
              any("hiding subtype" in w for w in hidden.warnings), "")


def t_cycle(db, spec):
    a, b = spec["cycle"]
    with Store(db) as s:
        def near(key, depth):
            try:
                return neighbours(s, key, depth=depth)
            except Exception as exc:                            # noqa: BLE001
                return [("raised:%s" % type(exc).__name__, key)]

        fwd = {t for _, t in near(a, 1)}
        back = {t for _, t in near(b, 1)}
        check("A -> B -> A cycle exists in the corpus",
              b in fwd and a in back, "verified both directions")
        t0 = time.time()
        edges = near(a, 6)
        # Time alone does not test cycle handling: a traversal that revisits
        # nodes still finishes on a graph this small, it just does the work
        # repeatedly. Each edge must be reported exactly once.
        check("traversal terminates through the cycle",
              time.time() - t0 < 5 and edges and len(edges) == len(set(edges)),
              "%d edges, %d unique, %.2fs"
              % (len(edges), len(set(edges)), time.time() - t0))


def t_broken_are_nodes(db, spec):
    with Store(db) as s:
        try:
            names = broken_links(s)
        except Exception as exc:                                # noqa: BLE001
            names = []
            print("   broken_links raised: %r" % exc)
        check("broken links are nodes, not dropped", len(names) > 0,
              "%d broken target node(s)" % len(names))

        # Scoped to the wiki. Each of these names appears in the wiki ONLY
        # inside a code span, so none may be the source end of an edge from
        # there -- while elsewhere in the corpus the same string could legally
        # be a link, which is why the claim is scoped rather than global.
        leaked = set()
        for name in spec["artefacts"]:
            try:
                sources = backlinks(s, "wikilink:%s" % name)[0]
            except Exception:                                   # noqa: BLE001
                sources = ["raised"]
            for src in sources:
                if src.startswith(spec["artefact_scope"]):
                    leaked.add((os.path.basename(src), name))
        check("no code-span artefacts from wiki/",
              not leaked and spec["artefacts"],
              "%d artefact name(s) checked, %d leaked %s"
              % (len(spec["artefacts"]), len(leaked), sorted(leaked)[:3]))


def main():
    (classified, paths), spec = corpus()
    print("corpus: %s  (%d markdown files classified, %d on disk)\n"
          % (spec["name"], classified, len(paths)))
    tmp = tempfile.mkdtemp(prefix="cp2-", dir=os.path.expanduser("~/.homegraph"))
    try:
        t_code_spans()
        db, report = t_build(tmp, paths, classified, spec)
        t_known_answers(db, spec)
        t_backlinks(db, spec)
        t_backlinks_time_travel(tmp)
        t_subtype_gate(tmp)
        t_cycle(db, spec)
        t_broken_are_nodes(db, spec)
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

def test_checkpoint_cp2():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
