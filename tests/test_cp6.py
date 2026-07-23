#!/usr/bin/env python3
"""CP-6 -- M5, the mesh.

Builds all four models over the corpus, then federates them.

Two checks carry most of the weight. The degradation gate takes a model away
mid-query and requires the answer to come back labelled `partial` and naming
what was missing -- a federated search that silently drops a model returns
fewer results and looks exactly like a smaller corpus. And the negative
path-matching gate requires that a reference to a file that does not exist
produces no edge whatsoever, because a graph that invents plausible edges is
worse than one with gaps.

One correction worth recording. An early probe reported that no markdown file
anywhere names an image filename, which would have left FIGURE_FOR -- the
plan's load-bearing link for the Art project -- with nothing to connect. The
probe was too narrow: it checked one image directory against four note
directories and missed a whole tree. The relation does fire on real data, and
the synthetic corpus carries the same shape: two notes that name artwork by
filename, and one filename that was never made.

Two corpora, as in CP-0: synthetic by default, the real one with
HOMEGRAPH_REAL_CORPUS=1 and the undistributed inventory snapshot.

Run:
    python3 tests/test_cp6.py
"""
from __future__ import annotations

import json
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

from homegraph.corpus import Classifier                        # noqa: E402
from homegraph.mesh import Mesh, ModelUnavailable              # noqa: E402
from homegraph.models import m1_build, m2_build, m3_build, m4_misc  # noqa: E402
from homegraph.store import Store                              # noqa: E402
from homegraph.temporal import refresh_all_datelists           # noqa: E402

AS_OF = date(2026, 7, 22).isoformat()
LAST_WEEK = (date(2026, 7, 22) - timedelta(days=7)).isoformat()
INVENTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gold", "inventory-2026-07-22.tsv")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# -- the two corpora -------------------------------------------------------

def classify_all(spec):
    clf = Classifier()
    out = {}

    def add(p, is_link):
        try:
            label = clf.classify(p, is_symlink=is_link)
        except Exception:                                       # noqa: BLE001
            return
        if label != "EXCLUDED" and os.path.exists(p):
            out.setdefault(label, []).append(p)

    if REAL:
        with open(INVENTORY, "rb") as fh:
            for raw in fh:
                ftype, _, _, p = raw.decode(
                    "utf-8", "surrogateescape").rstrip("\n").split("\t", 3)
                add(p, ftype == "l")
    else:
        from tests.fixtures.synthetic import inventory
        for p, is_link in inventory(spec["home"]):
            add(p, is_link)
    return out


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
        # `figure_for` is None there: no declared pair list exists for the real
        # corpus, so the gate falls back to a measured floor.
        spec = _real_spec("CP6")
        spec["name"] = "real"
        return spec
    from tests.fixtures import synthetic as syn
    syn.build_once()
    root = syn.ROOT
    return {
        "name": "synthetic", "home": root,
        # `query2` is the one asked while m2 is taken away, so it has to be a
        # term the OTHER models can answer -- "art" only exists in image paths,
        # and a query nobody can answer makes "the rest still answered" true
        # for the wrong reason.
        "query": "trails", "query2": "report",
        "figure_for": {(os.path.join(root, note), os.path.join(root, img))
                       for note, img in syn.FIGURE_FOR_PAIRS},
        "min_figure_for": len(syn.FIGURE_FOR_PAIRS),
        # Measured 130 nodes in the rendered graph; the floor sits at half so
        # the gate is about the graph existing, not about the fixture's size.
        "min_graph_nodes": 65,
        "descriptive_queries": ["trails", "bush", "memex", "graphify", "art",
                                "wiki", "report", "paper", "note", "search"],
    }


def build_models(tmp, by_label, spec):
    paths = {}
    t0 = time.time()
    errors = []

    handled = {}

    def run(name, builder, label, counted):
        db = os.path.join(tmp, "%s.db" % name)
        with Store(db, model=name) as s:
            try:
                report = builder(s, by_label.get(label, []), AS_OF)
                handled[name] = getattr(report, counted)
            except Exception as exc:                            # noqa: BLE001
                errors.append("%s: %r" % (name, exc))
                handled[name] = 0
            refresh_all_datelists(s, AS_OF)
            s.rebuild_fts()
        paths[name] = db

    run("m3", m3_build.build, "markdown", "files")
    run("m1", m1_build.build, "document", "documents")
    run("m2", m2_build.build, "image", "images")
    run("m4", m4_misc.build, "misc", "files")

    # `len(paths) == 4` was a claim about this function's own literal
    # structure: the dict gets four keys unconditionally above. A build that
    # fails raises, and the check is never reached. What it must assert is that
    # each store actually contains something.
    counts = {}
    for name, dbpath in paths.items():
        with Store(dbpath) as s:
            counts[name] = s.node_count()
    check("all four models build and are non-empty",
          len(paths) == 4 and all(n > 0 for n in counts.values()) and not errors,
          "%.1fs total  %s%s" % (time.time() - t0, counts,
                                 "" if not errors else "  " + errors[0][:60]))

    # The cross-model arithmetic CP-5's docstring promises and cannot perform,
    # because only here do all four stores exist at once. Every non-excluded
    # file must be handled by exactly one model -- except `code`, which no
    # model claims yet, and which is therefore named rather than absorbed. A
    # model that silently drops files does not raise anywhere; this is the only
    # place the loss shows up.
    total = sum(len(v) for v in by_label.values())
    unmodelled = len(by_label.get("code", []))
    check("every non-excluded file is handled by exactly one model",
          total and sum(handled.values()) + unmodelled == total,
          "%s + %d code = %d of %d non-excluded"
          % (handled, unmodelled, sum(handled.values()) + unmodelled, total))
    return paths


def t_federated_beats_single(paths, spec):
    """Recall across model boundaries, measured with planted sentinels.

    Runs LAST: it mutates the stores by appending a sentinel term, so every
    other check must have taken its measurements already.
    """
    # Provenance is not evidence. An audit showed both of the earlier gates
    # -- "top-10 spans several models" and "no single model supplies it" --
    # pass identically on RANDOMLY SHUFFLED rankings, because the models
    # partition the corpus: every hit gets exactly one RRF contribution, so
    # ordering by score is ordering by rank, and the fused list is forced to
    # interleave. They measured a property of the fusion algorithm.
    #
    # What federation is actually for is recall across boundaries. So: plant a
    # sentinel term in one file per model, then ask for it. Mesh must find all
    # four; no single model can find more than its own one.
    sentinel = "zzcrossmodelsentinelzz"
    planted = {}
    for name, dbpath in paths.items():
        with Store(dbpath) as s:
            row = s.db.execute(
                "SELECT node_key, body FROM nodes WHERE kind IN "
                "('file','document','image') ORDER BY node_key LIMIT 1"
            ).fetchone()
            if row is None:
                continue
            s.db.execute("UPDATE nodes SET body = COALESCE(body,'') || ? "
                         "WHERE node_key = ?", (" " + sentinel, row["node_key"]))
            s.rebuild_fts()
            planted[name] = row["node_key"]

    with Mesh(paths) as mesh:
        found = mesh.search(sentinel, limit=20)
        models_hit = {h["model"] for h in found.hits}
    per_model = {}
    for name, dbpath in paths.items():
        with Mesh({name: dbpath}) as single:
            per_model[name] = len(single.search(sentinel, limit=20))

    check("federation finds every planted cross-model item",
          len(planted) == len(paths)
          and models_hit == set(paths) and len(found) == len(paths),
          "%d hit(s) from %s" % (len(found), sorted(models_hit)))
    check("no single model finds more than its own",
          per_model and max(per_model.values()) == 1
          and len(found) > max(per_model.values()),
          "mesh %d vs best single %d  %s"
          % (len(found), max(per_model.values() or [0]), per_model))

    # Kept as a descriptive measurement, no longer sold as a gate.
    with Mesh(paths) as mesh:
        shares = {m: [] for m in paths}
        for q in spec["descriptive_queries"]:
            res = mesh.search(q, limit=10)
            if not res.hits:
                continue
            for m in paths:
                shares[m].append(
                    sum(1 for h in res.hits if h["model"] == m) / len(res.hits))
    avg = {m: (sum(v) / len(v) if v else 0) for m, v in shares.items()}
    print("   top-10 share per model (descriptive, not a gate): %s"
          % {m: "%.0f%%" % (v * 100) for m, v in avg.items()})


def t_degradation(tmp, paths, spec):
    """Take a model away mid-query. The answer must say so."""
    broken = dict(paths)
    broken["m2"] = os.path.join(tmp, "does-not-exist.db")
    with Mesh(broken) as mesh:
        res = mesh.search(spec["query2"])
        check("missing model yields a partial result", res.partial,
              "status=%s" % res.status)
        check("the missing model is named", "m2" in res.models_missing,
              "missing=%s" % res.models_missing)
        check("the warning is unmissable",
              any("PARTIAL RESULT" in w for w in res.warnings),
              (res.warnings or [""])[0][:60])
        check("the other models still answered",
              len(res.models_queried) == 3 and res.hits,
              "%d model(s), %d hit(s)" % (len(res.models_queried),
                                          len(res.hits)))

    corrupt = os.path.join(tmp, "corrupt.db")
    with open(corrupt, "wb") as fh:
        fh.write(b"this is not a database at all" * 100)
    broken["m2"] = corrupt
    with Mesh(broken) as mesh:
        try:
            res = mesh.search(spec["query2"])
            ok = res.partial and "m2" in res.models_missing
        except Exception as exc:                                # noqa: BLE001
            ok = False
            res = exc
        check("a corrupt model does not take down the rest", ok, str(res)[:70])

    with Mesh({"m9": os.path.join(tmp, "nope.db")}) as mesh:
        try:
            mesh.store("m9")
            raised = False
        except ModelUnavailable:
            raised = True
        check("an unavailable model raises rather than returning empty",
              raised, "ModelUnavailable")


def t_complete_is_labelled(paths, spec):
    with Mesh(paths) as mesh:
        res = mesh.search(spec["query"])
        check("a complete result is labelled complete",
              not res.partial and res.status == "complete" and res.hits,
              "%d model(s) answered, %d hit(s)"
              % (len(res.models_queried), len(res.hits)))


def t_rrf_ranking():
    """A constructed case where raw score ordering is wrong and RRF is right."""
    # m1's index produces large-magnitude BM25 values; m3's produces small
    # ones. Sorting the union by raw score puts BOTH of m1's results above
    # m3's, purely because of index scale. RRF puts m3's rank-1 result above
    # m1's rank-2 result, which is the correct reading of two rankings.
    rankings = {
        "m1": [{"node_key": "X", "title": "X", "score": -99.0, "path": "/x"},
               {"node_key": "Y", "title": "Y", "score": -98.0, "path": "/y"}],
        "m3": [{"node_key": "Z", "title": "Z", "score": -0.5, "path": "/z"}],
    }
    for model, rows in rankings.items():
        for r in rows:
            r["model"] = model
    try:
        fused = [h["node_key"] for h in Mesh._rrf(rankings, 10)]
    except Exception as exc:                                    # noqa: BLE001
        fused = ["raised:%s" % type(exc).__name__]
    raw = [r["node_key"] for r in sorted(
        (r for rows in rankings.values() for r in rows),
        key=lambda r: r["score"])]
    check("RRF disagrees with raw score, correctly",
          set("XYZ") <= set(fused)
          and fused.index("Z") < fused.index("Y")
          and raw.index("Y") < raw.index("Z"),
          "raw=%s  rrf=%s" % (raw, fused))

    # And consensus must accumulate: the same document found by two models
    # outranks one found by a single model at the same rank.
    # S is only SECOND in both models; L is first in its own. Correct identity
    # gives S 2/62 against L's 1/61 and S wins. Key by model instead and S
    # splits into two entries of 1/62, so L wins -- which is how the earlier
    # version of this case passed against exactly that bug: both orderings put
    # S first when S was ranked first, and the tie-break hid the difference.
    both = {"m1": [{"node_key": "P", "path": "/p", "model": "m1"},
                   {"node_key": "S", "path": "/shared", "model": "m1"}],
            "m3": [{"node_key": "Q", "path": "/q", "model": "m3"},
                   {"node_key": "S", "path": "/shared", "model": "m3"}],
            "m4": [{"node_key": "L", "path": "/lonely", "model": "m4"}]}
    try:
        order = [h["node_key"] for h in Mesh._rrf(both, 10)]
    except Exception as exc:                                    # noqa: BLE001
        order = ["raised:%s" % type(exc).__name__]
    check("agreement between models accumulates", order and order[0] == "S",
          "order=%s (S is 2nd in two models, L is 1st in one)" % order)


def t_figure_for(tmp):
    """Built on its own tiny tree, including the negative case.

    The negative case is the one that matters: a note naming an image that does
    not exist must create no edge, not a nearest match.
    """
    corpus_dir = os.path.join(tmp, "fig")
    imgdir = os.path.join(corpus_dir, "Bilder", "Art", "Experiments-2025")
    os.makedirs(imgdir)
    for name in ("03122025_3.png", "03122025_1.png"):
        with open(os.path.join(imgdir, name), "wb") as fh:
            fh.write(b"\x89PNG" + b"\0" * 32)
    notedir = os.path.join(corpus_dir, "notes")
    os.makedirs(notedir)
    with open(os.path.join(notedir, "real.md"), "w") as fh:
        fh.write("# Note\n\nThe piece 03122025_3.png came out well.\n")
    with open(os.path.join(notedir, "phantom.md"), "w") as fh:
        fh.write("# Note\n\nI meant to make 03122025_9.png but never did.\n")

    m2db = os.path.join(tmp, "fig_m2.db")
    with Store(m2db, model="m2") as s:
        m2_build.build(s, sorted(
            os.path.join(imgdir, n) for n in os.listdir(imgdir)), AS_OF)
        s.rebuild_fts()
    m3db = os.path.join(tmp, "fig_m3.db")
    with Store(m3db, model="m3") as s:
        m3_build.build(s, sorted(
            os.path.join(notedir, n) for n in os.listdir(notedir)), AS_OF)
        s.rebuild_fts()

    # A node with no path, planted in a model store. The mirror selects
    # `WHERE path IS NOT NULL`, and that clause is now the only thing keeping
    # such a node out -- the `if row["path"]` beside it was unreachable and has
    # been removed. Nothing in either fixture produces a path-less node, so
    # without planting one here the clause is untested and could be deleted
    # with every gate still green.
    with Store(m3db, model="m3") as s:
        s.db.execute("INSERT INTO nodes (node_key, kind, subtype, title, body,"
                     " path, first_seen, last_seen) VALUES"
                     " ('pathless', 'virtual', 'm3/-', 'no path', '',"
                     " NULL, ?, ?)", (AS_OF, AS_OF))
        s.db.commit()

    meshdb = os.path.join(tmp, "fig_mesh.db")
    with Mesh({"m2": m2db, "m3": m3db}, mesh_db=meshdb) as mesh:
        try:
            report = mesh.build_edges(AS_OF)
            with Store(meshdb) as _m:
                mirrored_pathless = [
                    r["node_key"] for r in _m.db.execute(
                        "SELECT node_key FROM nodes "
                        "WHERE node_key LIKE '%pathless%'")]
        except Exception as exc:                                # noqa: BLE001
            report = {"edges": {}, "raised": repr(exc)}
            mirrored_pathless = "raised:%s" % type(exc).__name__
        check("a node with no path is not mirrored into mesh",
              mirrored_pathless == [], "%r" % (mirrored_pathless,))
        check("FIGURE_FOR links a note to the image it names",
              report["edges"].get("FIGURE_FOR", 0) >= 1,
              "%d edge(s)%s" % (report["edges"].get("FIGURE_FOR", 0),
                                report.get("raised", "")))

        real = "m3::" + os.path.join(notedir, "real.md")
        phantom = "m3::" + os.path.join(notedir, "phantom.md")
        img = "m2::" + os.path.join(imgdir, "03122025_3.png")

        try:
            trail = mesh.path(real, img)
        except Exception as exc:                                # noqa: BLE001
            trail = None
            print("   mesh.path raised: %r" % exc)
        check("mesh_path from note to image has length 2",
              trail is not None and len(trail) == 2,
              "path=%s" % ([os.path.basename(t) for t in trail]
                           if trail else None))

        with Store(meshdb) as m:
            phantom_edges = m.db.execute(
                "SELECT COUNT(*) c FROM edges e JOIN nodes s ON s.id=e.src "
                "WHERE s.node_key=? AND e.rel='FIGURE_FOR'",
                (phantom,)).fetchone()["c"]
        check("a name that does not exist creates NO edge",
              phantom_edges == 0, "%d phantom edge(s)" % phantom_edges)


def t_no_false_edges(tmp, paths, spec):
    """Pairs with no real relation must not be connected."""
    meshdb = os.path.join(tmp, "mesh.db")
    with Mesh(paths, mesh_db=meshdb) as mesh:
        try:
            report = mesh.build_edges(AS_OF)
        except Exception as exc:                                # noqa: BLE001
            report = {"edges": {}, "raised": repr(exc)}
        print("   mesh edges over the corpus: %s" % report["edges"])
        check("FIGURE_FOR fires on the corpus",
              report["edges"].get("FIGURE_FOR", 0) >= spec["min_figure_for"],
              "%d edge(s) linking notes to the artwork they discuss, floor %d"
              % (report["edges"].get("FIGURE_FOR", 0), spec["min_figure_for"]))

    # The declared pair list: exactly these note->image edges and no others.
    # A count alone would be satisfied by ten wrong edges.
    if spec["figure_for"] is not None:
        with Store(meshdb) as m:
            got = {(r["s"].split("::", 1)[1], r["d"].split("::", 1)[1])
                   for r in m.db.execute(
                       "SELECT s.node_key s, d.node_key d FROM edges e "
                       "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst "
                       "WHERE e.rel='FIGURE_FOR'")}
        check("FIGURE_FOR is exactly the declared set",
              got == spec["figure_for"],
              "%d edge(s); unexpected %s; missing %s"
              % (len(got),
                 [os.path.basename(b) for _, b in
                  sorted(got - spec["figure_for"])][:3],
                 [os.path.basename(b) for _, b in
                  sorted(spec["figure_for"] - got)][:3]))
    else:
        # No check at all here, rather than one that passes by construction.
        # This branch used to emit "FIGURE_FOR is exactly the declared set"
        # with a hard-coded True: a check whose name claims an exact-set
        # property, printed as PASS, having compared nothing. Absent evidence
        # has to look absent. The count gate above still runs and is the only
        # thing the real corpus supports.
        print("SKIP  FIGURE_FOR exact-set check: no declared pair list for "
              "the real corpus (the count gate above still applies)")

    # Drawn from nodes that DO have edges. Ten arbitrary nodes on a sparse
    # graph cannot be connected no matter what mesh.path() does, so the old
    # version passed even if path() had returned None for everything.
    with Store(meshdb) as m:
        linked = [r["node_key"] for r in m.db.execute(
            "SELECT DISTINCT s.node_key FROM edges e "
            "JOIN nodes s ON s.id = e.src ORDER BY s.node_key")]
        far = [r["node_key"] for r in m.db.execute(
            "SELECT DISTINCT d.node_key FROM edges e "
            "JOIN nodes d ON d.id = e.dst ORDER BY d.node_key DESC")]
        one_edge = m.db.execute(
            "SELECT s.node_key a, d.node_key b FROM edges e "
            "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst "
            "LIMIT 1").fetchone()
    with Mesh(paths, mesh_db=meshdb) as mesh:
        # Positive control on THIS graph: a known edge must be findable, or a
        # path() that always returns None would satisfy the negative check.
        check("mesh_path finds a known edge on the graph",
              one_edge is not None
              and mesh.path(one_edge["a"], one_edge["b"], max_depth=2),
              "control edge %s" % (os.path.basename(one_edge["a"])
                                   if one_edge else None))
        if spec["figure_for"] is not None:
            # Every note/image combination the key does NOT list. These are
            # nodes that both carry edges, so a path() that answered blindly
            # would find something; the declared answer is that it must not.
            notes = {a for a, _ in spec["figure_for"]}
            images = {b for _, b in spec["figure_for"]}
            pairs = [("m3::" + n, "m2::" + i) for n in sorted(notes)
                     for i in sorted(images)
                     if (n, i) not in spec["figure_for"]]
        else:
            pairs = [(a, b) for a, b in zip(linked[:10], far[:10]) if a != b]
        connected = [(a, b) for a, b in pairs
                     if mesh.path(a, b, max_depth=2)]
    limit = 0 if spec["figure_for"] is not None else max(1, len(pairs) // 4)
    check("unrelated nodes are not connected",
          pairs and len(connected) <= limit,
          "%d of %d unrelated pair(s) linked within 2 hops, limit %d"
          % (len(connected), len(pairs), limit))


def t_time_travel(paths, spec):
    with Mesh(paths) as mesh:
        now = mesh.search(spec["query"], limit=50)
        then = mesh.search(spec["query"], limit=50, as_of=LAST_WEEK)
        # `len(then) <= len(now)` is satisfied when as_of is ignored entirely
        # and both sides are equal, so it tested nothing. Every node here was
        # first seen today; a working filter must return exactly zero for last
        # week, and the present must be non-empty for the comparison to mean
        # anything at all.
        check("as-of filters by first_seen",
              len(now) > 0 and len(then) == 0,
              "now=%d  last week=%d" % (len(now), len(then)))
        midnight = mesh.search(spec["query"], limit=50, as_of=AS_OF)
        check("as-of today returns the present",
              len(midnight) == len(now),
              "as-of today=%d  unfiltered=%d" % (len(midnight), len(now)))


def t_visualise(tmp, paths, spec):
    """CP-6: 5 000 nodes must render in under 3 s.

    Layout is computed here and shipped as coordinates, so the browser's job is
    a single canvas pass. Both halves are measured: a fast page that took a
    minute to generate is not a fast visualisation.
    """
    from homegraph.visualize import _layout, render

    # The empty graph, checked first. `_layout` divides by the node count and
    # by the number of models, and both used to be wrapped in `max(..., 1)` --
    # guards that could not fire, because the early return already excluded
    # zero. Removing them puts the whole claim on that return, so it has to be
    # exercised rather than assumed.
    try:
        empty = _layout([], [], iterations=5)
    except Exception as exc:                                    # noqa: BLE001
        empty = "raised:%s" % type(exc).__name__
    check("an empty graph lays out to nothing, without dividing by it",
          empty == [], "%r" % (empty,))

    out = os.path.join(tmp, "graph.html")
    t0 = time.time()
    try:
        report = render(paths, out, limit_per_model=2000, iterations=60,
                        title="homegraph")
    except Exception as exc:                                    # noqa: BLE001
        report = {"nodes": 0, "edges": 0, "bytes": 0, "missing": [],
                  "raised": repr(exc)}
    elapsed = time.time() - t0
    check("visualisation renders the graph",
          report["nodes"] > spec["min_graph_nodes"] and os.path.exists(out),
          "%d nodes (floor %d), %d edges, %.0f KB in %.1fs%s"
          % (report["nodes"], spec["min_graph_nodes"], report["edges"],
             report["bytes"] / 1024, elapsed, report.get("raised", "")))

    page = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    # Checked against the page STRUCTURE, not the whole file. The embedded data
    # legitimately contains URLs -- document titles and markdown bodies have
    # them -- so a naive search for "https://" flagged the corpus rather than
    # the page. What must be absent is anything the browser would go and fetch.
    scaffold = (page[:page.index("const D = ")]
                + page[page.index("\n</script>"):]) if "const D = " in page \
        else page
    external = [t for t in ("<script src", "<link rel=\"stylesheet",
                            "@import", "fetch(", "XMLHttpRequest",
                            "//cdn", "//unpkg", "//cdnjs")
                if t in scaffold]
    check("the page fetches nothing", page and not external,
          "no external reference in the page scaffolding%s"
          % ("" if not external else ": %s" % external))

    # 5 000 nodes, synthetic, to hit the number the plan names.
    big_nodes = [{"key": "n%d" % i, "model": "m%d" % (i % 4 + 1),
                  "title": "node %d" % i, "kind": "file", "subtype": ""}
                 for i in range(5000)]
    big_edges = [(i, (i * 7 + 3) % 5000) for i in range(5000)]
    t0 = time.time()
    try:
        positions = _layout(big_nodes, big_edges, iterations=60)
    except Exception as exc:                                    # noqa: BLE001
        positions = {"raised": repr(exc)}
    layout_s = time.time() - t0
    # This measures LAYOUT, which is the stricter of the two readings and the
    # one measurable without a browser. The canvas draw itself is one pass over
    # 5 000 circles and 5 000 lines -- microseconds by comparison, and the
    # reason the layout is precomputed and shipped as coordinates at all.
    check("5 000-node layout under 3s",
          layout_s < 3.0 and len(positions) == 5000,
          "%.2fs for %d nodes" % (layout_s, len(positions)))

    # Deterministic: the same graph must draw the same picture twice, or a
    # week-on-week comparison is meaningless.
    again = _layout(big_nodes, big_edges, iterations=60)
    check("layout is deterministic", positions == again,
          "two runs, identical coordinates")

    partial = dict(paths)
    partial["m2"] = os.path.join(tmp, "gone.db")
    rep = render(partial, os.path.join(tmp, "partial.html"),
                 limit_per_model=200, iterations=10)
    page = open(os.path.join(tmp, "partial.html"), encoding="utf-8").read()
    check("a missing model is declared in the page",
          rep["missing"] == ["m2"] and "DELVIS" in page,
          "missing=%s, banner present" % rep["missing"])


def t_queries_never_create_a_store(tmp):
    """A read query must not bring the thing it reads into existence.

    `mcp_server` promises "read-only by construction", and an MCP server is
    driven unattended, so this is the wrong place to be approximately right.
    `Store(path)` connects with sqlite3 -- which creates the file -- and then
    migrates it, so `Mesh.neighbours` against a mesh that does not exist used
    to leave a fully-formed empty database behind and answer `count: 0`.

    With `mesh_db=None` it was worse than pointless: the path became the string
    "None" and the file landed in whatever directory the process started in.

    Two gates, because they fail for different reasons and a reader deserves to
    know which: refusing, and not writing.
    """
    work = os.path.join(tmp, "readonly")
    os.makedirs(work)
    absent = os.path.join(work, "not-built-yet.db")

    outcomes, created = {}, {}
    for label, mesh_db in (("absent path", absent), ("no path at all", None)):
        before = set(os.listdir(work))
        with Mesh({}, mesh_db=mesh_db) as mesh:
            try:
                mesh.neighbours("m3::whatever")
                outcomes[label] = "returned"
            except ModelUnavailable:
                outcomes[label] = "refused"
            except Exception as exc:                            # noqa: BLE001
                outcomes[label] = "raised:%s" % type(exc).__name__
        created[label] = sorted(set(os.listdir(work)) - before)

    check("a query against a mesh that does not exist refuses",
          all(v == "refused" for v in outcomes.values()), str(outcomes))
    check("and it creates no database while refusing",
          not any(created.values()) and not os.path.exists(absent),
          "created %s" % created)
    # The same for `path`, which had the identical construction.
    with Mesh({}, mesh_db=None) as mesh:
        try:
            mesh.path("a", "b")
            second = "returned"
        except ModelUnavailable:
            second = "refused"
        except Exception as exc:                                # noqa: BLE001
            second = "raised:%s" % type(exc).__name__
    check("mesh_path refuses on a missing mesh too", second == "refused",
          second)


def t_mcp(tmp, paths, spec):
    """The MCP server speaks the protocol and refuses what it should."""
    import io

    from homegraph.mcp_server import TOOLS, Server

    srv = Server(paths, mesh_db=os.path.join(tmp, "mesh.db"))

    def handle(msg):
        try:
            return srv.handle(msg)
        except Exception as exc:                                # noqa: BLE001
            return {"raised": repr(exc), "result": {}, "error": {}}

    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {}})
    check("MCP initialize returns a protocol version",
          init["result"].get("protocolVersion")
          and init["result"].get("serverInfo"),
          str(init["result"].get("protocolVersion")))

    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"].get("tools", [])}
    check("all four mesh tools are advertised",
          names == {"mesh_search", "mesh_neighbors", "mesh_path",
                    "mesh_explain"}, str(sorted(names)))
    # Read off the wire, not off the constant. Checking TOOLS proves the
    # module-level list is well formed and says nothing about what a client
    # receives: a `tools/list` that strips `inputSchema` on the way out left
    # this green, and an agent with no schema cannot call anything correctly.
    advertised = listed["result"].get("tools", [])
    check("every tool declares a schema",
          advertised and len(advertised) == len(TOOLS)
          and all(t.get("inputSchema", {}).get("properties")
                  for t in advertised),
          "%d of %d advertised tool(s) carry a schema"
          % (sum(1 for t in advertised
                 if t.get("inputSchema", {}).get("properties")),
             len(advertised)))

    call = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "mesh_search",
                              "arguments": {"query": spec["query"],
                                            "limit": 5}}})
    body = json.loads(call["result"]["content"][0]["text"])
    check("mesh_search answers over stdio", body["hits"] and
          body["status"] == "complete", "%d hit(s), status=%s"
          % (len(body["hits"]), body["status"]))

    # The property that matters for an unattended client: a degraded answer
    # must announce itself, because an agent never sees the model list.
    broken = dict(paths)
    broken["m2"] = os.path.join(tmp, "absent.db")
    degraded = Server(broken).handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "mesh_search",
                    "arguments": {"query": spec["query2"]}}})
    dbody = json.loads(degraded["result"]["content"][0]["text"])
    check("a partial answer is labelled partial over MCP",
          dbody["status"] == "partial" and "m2" in dbody["models_missing"]
          and any("PARTIAL" in w for w in dbody["warnings"]),
          "status=%s missing=%s" % (dbody["status"], dbody["models_missing"]))

    unknown = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "rm_rf", "arguments": {}}})
    check("an unknown tool is refused, not guessed",
          unknown.get("error", {}).get("code") == -32601,
          str(unknown.get("error", {}).get("message"))[:50])

    bad = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                  "params": {"name": "mesh_path",
                             "arguments": {"src": "nope"}}})
    check("missing arguments are a protocol error, not a crash",
          bad.get("error", {}).get("code") == -32602,
          str(bad.get("error", {}).get("message"))[:44])

    notice = srv.handle({"jsonrpc": "2.0",
                         "method": "notifications/initialized"})
    check("notifications get no reply", notice is None, "returned None")

    # Wrapped, because the failure mode under test is a session that dies. An
    # unwrapped call turns "the loop stopped" into an exception that takes the
    # whole checkpoint with it, and the mutation harness scores that as
    # detected-only-by-a-crash: the weakest signal there is, and one that
    # cannot distinguish this gate from any other.
    stdout = io.StringIO()
    try:
        srv.serve(io.StringIO('{"jsonrpc":"2.0","id":9,"method":"ping"}\n'
                              'not json at all\n'), stdout)
    except Exception as exc:                                    # noqa: BLE001
        print("   serve() raised: %r" % exc)
    lines = []
    for x in stdout.getvalue().splitlines():
        try:
            lines.append(json.loads(x))
        except json.JSONDecodeError:
            lines.append({"id": None, "error": {"code": None}})
    check("a malformed line does not kill the session",
          len(lines) == 2 and lines[0]["id"] == 9
          and lines[1]["error"]["code"] == -32700,
          "%d response(s); the parse error is reported and the loop continues"
          % len(lines))


def main():
    spec = corpus()
    tmp = tempfile.mkdtemp(prefix="cp6-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        by_label = classify_all(spec)
        print("corpus: %s  %s\n"
              % (spec["name"], {k: len(v) for k, v in sorted(by_label.items())}))
        paths = build_models(tmp, by_label, spec)
        t_complete_is_labelled(paths, spec)
        t_degradation(tmp, paths, spec)
        t_rrf_ranking()
        t_figure_for(tmp)
        t_no_false_edges(tmp, paths, spec)
        t_time_travel(paths, spec)
        t_visualise(tmp, paths, spec)
        t_queries_never_create_a_store(tmp)
        t_mcp(tmp, paths, spec)
        t_federated_beats_single(paths, spec)
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

def test_checkpoint_cp6():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
