#!/usr/bin/env python3
"""CP-9 -- provenance on edges. One claim, and it is about honesty.

    an edge that was inferred says so, in the row and in every answer
    that hands it back.

Before migration v2 the ambiguity was counted and then lost: `m3_build`
tallied `report.ambiguous_targets` and wrote an edge indistinguishable from
one the text stated outright. The aggregate was honest; the individual fact
was not. Anyone querying the store -- or reading a backlink -- got a guess
that looked like a fact.

The gates are built so the obvious cheat fails:

  * **Both directions of the marker.** "Ambiguous links are marked" passes for
    an implementation that marks everything. "Unambiguous links are unmarked"
    passes for one that marks nothing. Neither alone says anything, so the
    negative control counts the *unmarked* edges too.
  * **Per method, not in aggregate.** One gate over "some low-confidence edge
    exists somewhere" is satisfied by one of the four mechanisms working. Each
    is checked where it is produced.
  * **The answer, not just the column.** A `confidence` nobody surfaces is the
    decoration this borrowed the idea from. The read paths are driven and
    their output is read.
  * **The migration is total.** Existing edges become `exact`/1.0 and none is
    lost -- checked by counting before and after on a store built at v1.

Run:
    python3 tests/test_cp9.py
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import cli                                          # noqa: E402
from homegraph.models.m3_build import backlinks                    # noqa: E402
from homegraph.store import (EDGE_METHODS, MIGRATIONS, Store,      # noqa: E402
                             provenance_note)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-56s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def edges(db, rel=None):
    with Store(db) as s:
        sql = ("SELECT s.node_key src, d.node_key dst, e.rel, e.method, "
               "e.confidence FROM edges e JOIN nodes s ON s.id=e.src "
               "JOIN nodes d ON d.id=e.dst")
        args = []
        if rel:
            sql += " WHERE e.rel = ?"
            args.append(rel)
        return s.db.execute(sql, args).fetchall()


# -- 1. the table is a table, not a scale ----------------------------------

def t_methods_are_a_closed_set():
    """Five named methods with fixed values. The number orders; it does not
    estimate."""
    check("exact is the only method worth 1.0",
          [m for m, c in EDGE_METHODS.items() if c >= 1.0] == ["exact"],
          "%s" % {m: c for m, c in EDGE_METHODS.items() if c >= 1.0})
    check("every other method is strictly below 1.0",
          all(0.0 < c < 1.0 for m, c in EDGE_METHODS.items() if m != "exact"),
          " ".join("%s=%.1f" % kv for kv in sorted(EDGE_METHODS.items())))

    # An unknown method must be refused where it is written, not stored and
    # discovered later as a NULL confidence.
    tmp = tempfile.mkdtemp(prefix="cp9-", dir=os.path.expanduser("~/.homegraph"))
    try:
        with Store(os.path.join(tmp, "m.db")) as s:
            s.upsert_node("/a", kind="file", path="/a")
            s.upsert_node("/b", kind="file", path="/b")
            try:
                s.upsert_edge("/a", "/b", "R", method="guesswork")
                refused = False
            except ValueError:
                refused = True
            check("an unknown method is refused", refused, "ValueError")
            # And `method` has no default: omitting it is a TypeError, which
            # is a stronger guarantee than a test, because there is no green
            # run in which someone forgot.
            try:
                s.upsert_edge("/a", "/b", "R")                # type: ignore
                omitted = False
            except TypeError:
                omitted = True
            check("method cannot be omitted", omitted, "TypeError")

            # Re-asserting an edge updates its provenance, in both
            # directions. A link that was unambiguous and now collides with
            # a new file is genuinely less certain than it was; keeping the
            # old 1.0 because it is higher would freeze a claim the corpus
            # stopped supporting. Both directions, because "always keep the
            # first" and "always keep the highest" each pass a one-sided gate.
            s.upsert_edge("/a", "/b", "L", as_of="2026-01-01", method="exact")
            s.upsert_edge("/a", "/b", "L", as_of="2026-01-02",
                          method="cohort")
            down = s.db.execute("SELECT method, confidence, first_seen FROM "
                                "edges WHERE rel='L'").fetchone()
            check("re-asserting downgrades when the evidence weakened",
                  down["method"] == "cohort" and down["confidence"] == 0.4
                  and down["first_seen"] == "2026-01-01",
                  "method=%s conf=%.1f first_seen=%s"
                  % (down["method"], down["confidence"], down["first_seen"]))
            s.upsert_edge("/a", "/b", "L", as_of="2026-01-03", method="exact")
            up = s.db.execute("SELECT method, confidence FROM edges "
                              "WHERE rel='L'").fetchone()
            check("re-asserting upgrades when the ambiguity is gone",
                  up["method"] == "exact" and up["confidence"] == 1.0,
                  "method=%s conf=%.1f" % (up["method"], up["confidence"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -- 2. the ambiguous wikilink, both directions ----------------------------

def t_ambiguous_wikilink_is_marked(db, syn):
    """The defect this checkpoint exists for.

    `wiki/raw/summaries-note.md` and `wiki/wiki/summaries/summaries-note.md`
    both answer to `[[summaries-note]]`; the linker is in `wiki/wiki/`, so the
    nearest common prefix picks the second. That is a rule resolving a real
    ambiguity, and until v2 the resulting row said nothing about it.
    """
    rows = edges(db, "WIKILINKS_TO")
    linker = os.path.join(syn.ROOT, syn.AMBIGUOUS_LINKER)
    winner = os.path.join(syn.ROOT, syn.AMBIGUOUS_WINNER)

    marked = [r for r in rows if r["src"] == linker and r["dst"] == winner]
    check("the ambiguous link resolved to the nearest prefix",
          len(marked) == 1, "%d edge(s) %s -> %s"
          % (len(marked), os.path.basename(linker), os.path.basename(winner)))
    if marked:
        r = marked[0]
        check("the ambiguous link is marked path_prefix",
              r["method"] == "path_prefix" and r["confidence"] < 1.0,
              "method=%s confidence=%.1f" % (r["method"], r["confidence"]))

    # The negative control, and it is the half that makes the gate mean
    # something: an implementation that marks every wikilink passes the check
    # above and fails this one.
    #
    # Restricted to RESOLVED links. Counting the broken-link stubs too made
    # this gate pass against a build that marked every resolved link, because
    # the thirteen stubs kept `exact` non-empty -- the mutation harness caught
    # it, and the fix is to compare within the population the rule applies to.
    resolved = [r for r in rows if not r["dst"].startswith("wikilink:")]
    plain = [r for r in resolved if r["method"] == "exact"]
    lowconf = [r for r in resolved if r["method"] != "exact"]
    check("unambiguous wikilinks are NOT marked",
          len(plain) > 0 and len(lowconf) < len(resolved),
          "%d exact, %d derived, %d resolved (%d stubs excluded)"
          % (len(plain), len(lowconf), len(resolved),
             len(rows) - len(resolved)))
    # And the count is pinned, so "mark everything below N" cannot drift in
    # unnoticed as the fixture grows.
    check("exactly the planted collisions are marked",
          len(lowconf) == 1, "%d marked: %s"
          % (len(lowconf), [os.path.basename(r["dst"]) for r in lowconf]))


# -- 3. each mechanism, where it is produced -------------------------------

def t_each_method_is_reachable(stores):
    """One gate over "some derived edge exists" is satisfied by one mechanism
    working and three being dead. Each is checked where it is written.

    Its first run found two: `mention` and `cohort` were produced by nothing.
    Neither was broken -- the shared fixture is a single-day corpus whose
    path mentions resolve to files no model holds, so `MENTIONS_PATH` never
    fired and `_temporal_cohort` never saw the two observation days it
    requires. That is a fixture that cannot see a rule, the same finding
    CP-0 made about `[symlinks]`, and the answer is the same: plant a case
    that reaches it rather than soften the gate.
    """
    produced: dict[str, set[str]] = {}
    for db in stores:
        for row in edges(db):
            produced.setdefault(row["method"], set()).add(row["rel"])

    for method in sorted(EDGE_METHODS):
        rels = produced.get(method)
        check("method %-12s is produced by a build" % method, bool(rels),
              ", ".join(sorted(rels)) if rels else "NOTHING PRODUCES IT")


# -- 4. the answer says so, not only the column ----------------------------

def t_provenance_note_is_both_ways():
    exact = [{"method": "exact", "confidence": 1.0}]
    mixed = [{"method": "exact", "confidence": 1.0},
             {"method": "cohort", "confidence": 0.4}]
    check("all-exact rows produce no note", provenance_note(exact) is None,
          repr(provenance_note(exact)))
    note = provenance_note(mixed)
    check("a derived row produces a note naming the method",
          note is not None and "cohort" in note, repr(note))
    check("the note does not name methods that were not used",
          note is not None and "basename" not in note, repr(note))


def t_backlinks_surface_the_note(db, syn):
    """The read path, driven through the command a user runs.

    Checking `backlinks()` alone would pass with the note computed and never
    printed -- the shape CP-2 already had to fix once for `--as-of`.
    """
    winner = os.path.join(syn.ROOT, syn.AMBIGUOUS_WINNER)
    with Store(db) as s:
        sources, note = backlinks(s, winner)
    check("backlinks to the contested note returns the note",
          bool(sources) and note is not None and "path_prefix" in note,
          "%d source(s), note=%r" % (len(sources), note))

    out = io.StringIO()
    old, sys.stdout = sys.stdout, out
    try:
        cli.main(["md", "backlinks", db, winner])
    except SystemExit:
        pass
    finally:
        sys.stdout = old
    printed = out.getvalue()
    check("the CLI prints the warning, not just computes it",
          "PARTIAL" in printed and "path_prefix" in printed,
          printed.strip().splitlines()[-1][:70] if printed else "(silent)")

    # Negative control on the same path: a target with no contested inbound
    # link must print no warning. Without this, "always warn" passes.
    quiet = os.path.join(syn.ROOT, syn.AMBIGUOUS_LOSER)
    out = io.StringIO()
    old, sys.stdout = sys.stdout, out
    try:
        cli.main(["md", "backlinks", db, quiet])
    except SystemExit:
        pass
    finally:
        sys.stdout = old
    check("an uncontested target prints no warning",
          "PARTIAL" not in out.getvalue(),
          out.getvalue().strip().splitlines()[-1][:60] if out.getvalue() else "")


# -- 5. the migration is total ---------------------------------------------

def t_migration_preserves_every_edge(tmp):
    """A v1 store with edges, migrated. Nothing lost, nothing invented."""
    db = os.path.join(tmp, "v1.db")
    # Build a v1 store by hand: apply migration 1 only, so this is genuinely
    # a database that predates the column rather than one made by today's
    # code and relabelled.
    conn = sqlite3.connect(db)
    conn.executescript(MIGRATIONS[0][2])
    conn.executescript("""CREATE TABLE schema_version (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, note TEXT);
        INSERT INTO schema_version VALUES (1, '2026-01-01', 'initial');""")
    for i in range(5):
        conn.execute("INSERT INTO nodes(node_key, kind, first_seen, last_seen)"
                     " VALUES (?,?,?,?)", ("/n%d" % i, "file",
                                           "2026-01-01", "2026-01-01"))
    for i in range(4):
        conn.execute("INSERT INTO edges(src, dst, rel, first_seen, last_seen)"
                     " VALUES (?,?,?,?,?)",
                     (i + 1, i + 2, "R%d" % i, "2026-01-01", "2026-01-02"))
    conn.commit()
    before = conn.execute(
        "SELECT src, dst, rel, first_seen, last_seen FROM edges "
        "ORDER BY id").fetchall()
    conn.close()

    with Store(db) as s:                       # opening runs the migration
        after = s.db.execute(
            "SELECT src, dst, rel, first_seen, last_seen, method, confidence "
            "FROM edges ORDER BY id").fetchall()
        version = s.version

    check("the migration ran", version == len(MIGRATIONS),
          "version %d" % version)
    check("every v1 edge survived, unchanged in its own columns",
          [tuple(r) for r in before] == [tuple(r)[:5] for r in after],
          "%d before, %d after" % (len(before), len(after)))
    check("migrated edges default to exact/1.0",
          all(r["method"] == "exact" and r["confidence"] == 1.0
              for r in after),
          "%s" % sorted({(r["method"], r["confidence"]) for r in after}))


def main():
    from tests.fixtures import synthetic as syn
    syn.build_once()
    tmp = tempfile.mkdtemp(prefix="cp9-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        t_methods_are_a_closed_set()
        t_provenance_note_is_both_ways()
        t_migration_preserves_every_edge(tmp)

        db = os.path.join(tmp, "m3.db")
        mesh_db = os.path.join(tmp, "mesh.db")
        _build(db, mesh_db, syn)
        t_ambiguous_wikilink_is_marked(db, syn)
        t_backlinks_surface_the_note(db, syn)
        # The shared corpus reaches three of the five methods. The other two
        # need a corpus with a resolvable path mention and two observation
        # days; planted here rather than added to the shared fixture, whose
        # declared totals every other checkpoint measures against.
        extra = _reach_corpus(tmp, syn)
        t_each_method_is_reachable([db, mesh_db] + extra)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        syn.use_config(syn.CONFIG)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def _build(db, mesh_db, syn):
    """m3 + m2 + mesh over the synthetic corpus, through the builders."""
    from datetime import date

    from homegraph.corpus import Classifier
    from homegraph.mesh import Mesh
    from homegraph.models.m2_build import build as m2_build
    from homegraph.models.m3_build import build as m3_build
    from homegraph.models.m3_build import rules_from_config

    as_of = date.today().isoformat()
    clf = Classifier()
    md, img = [], []
    for dirpath, dirnames, filenames in os.walk(syn.ROOT, followlinks=False):
        for name in filenames:
            p = os.path.join(dirpath, name)
            label = clf.classify(p)
            if label == "markdown":
                md.append(p)
            elif label == "image":
                img.append(p)
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                dirnames.remove(name)

    m2 = os.path.join(os.path.dirname(db), "m2.db")
    with Store(db, model="m3") as s:
        m3_build(s, sorted(md), as_of, rules=rules_from_config(clf.config))
        s.rebuild_fts()
    with Store(m2, model="m2") as s:
        m2_build(s, sorted(img), as_of)
        s.rebuild_fts()
    with Mesh({"m2": m2, "m3": db}, mesh_db=mesh_db) as mesh:
        mesh.build_edges(as_of)


def _reach_corpus(tmp, syn):
    """A corpus that reaches `mention` and `cohort`. Returns store paths.

    `mention`: one note names another note's absolute path, and the target is
    a node in the same store -- `MENTIONS_PATH` only fires when it resolves.
    `cohort`: two models, both built on the same two dates, so the identical
    32-day masks that `_temporal_cohort` groups on actually exist. A one-day
    build has one bit set and `min_days=2` rejects it, which is why this is a
    two-date build and not a bigger one.
    """
    from datetime import date, timedelta

    from homegraph.mesh import Mesh
    from homegraph.models.m3_build import build as m3_build
    from homegraph.models.m4_misc import build as m4_build

    root = os.path.join(tmp, "reach")
    os.makedirs(os.path.join(root, "notes"), exist_ok=True)
    target = os.path.join(root, "notes", "target.md")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("# Target\n\nplain note\n")
    with open(os.path.join(root, "notes", "mentioner.md"), "w",
              encoding="utf-8") as fh:
        fh.write("# Mentioner\n\nsee %s for the rest\n" % target)
    with open(os.path.join(root, "notes", "data.sqlite"), "wb") as fh:
        fh.write(b"SQLite format 3\x00" + b"\x00" * 64)

    cfg = syn.write_config(root, roles={"image": []})
    old_cfg = os.environ.get("HOMEGRAPH_CONFIG")
    old_root = os.environ.get("HOMEGRAPH_ROOT")
    syn.use_config(cfg)
    # `HOMEGRAPH_ROOT` too, and not only the config: `_pathish(home_root())`
    # decides what counts as a path mention, so a corpus at another root
    # produces none. CP-2/3/4/6 `setdefault` this at import time, which made
    # this gate pass on its own and fail under pytest -- a result that
    # depended on which other checkpoints had been imported first.
    os.environ["HOMEGRAPH_ROOT"] = root
    try:
        md = sorted(os.path.join(root, "notes", n)
                    for n in os.listdir(os.path.join(root, "notes"))
                    if n.endswith(".md"))
        misc = [os.path.join(root, "notes", "data.sqlite")]
        m3db = os.path.join(tmp, "reach-m3.db")
        m4db = os.path.join(tmp, "reach-m4.db")
        meshdb = os.path.join(tmp, "reach-mesh.db")
        today = date.today()
        for day in (today - timedelta(days=1), today):
            with Store(m3db, model="m3") as s:
                m3_build(s, md, day.isoformat())
            with Store(m4db, model="m4") as s:
                m4_build(s, misc, day.isoformat())
        with Mesh({"m3": m3db, "m4": m4db}, mesh_db=meshdb) as mesh:
            mesh.build_edges(today.isoformat())
        return [m3db, m4db, meshdb]
    finally:
        if old_cfg:
            syn.use_config(old_cfg)
        if old_root is None:
            os.environ.pop("HOMEGRAPH_ROOT", None)
        else:
            os.environ["HOMEGRAPH_ROOT"] = old_root


def test_checkpoint_cp9():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
