#!/usr/bin/env python3
"""CP-8 -- `homegraph update`. One claim, and it is an equivalence.

    a store built on corpus A and updated to corpus B must be
    indistinguishable from a store built on corpus B from scratch.

Everything else in this checkpoint exists to stop that claim from being true
vacuously, because it is unusually easy to make it so:

  * **Compare sets, not counts.** Node and edge counts are equal when one node
    has been swapped for another, which is precisely what a broken incremental
    path produces.
  * **Corpus B must differ from A on all five axes.** `added`, `changed`,
    `touched`, `unchanged`, `removed` -- declared in the fixture, asserted
    here. A mutation that ignores `removed` cannot be killed by a corpus where
    nothing was removed, and an equivalence gate over two identical corpora
    passes for any update path at all, including one that does nothing.
  * **The cost claim is counted, not timed.** Wall clock on a 856-file fixture
    measures the machine's mood. What `update` promises is that it does not
    reparse files that did not change, so the gate counts files reparsed.

Timestamps are excluded from the comparison, deliberately and with a separate
gate to cover the exclusion: `first_seen` is exactly what an update preserves
and a rebuild cannot, so demanding it be equal would demand that update lose
the history it exists to keep. That it *is* preserved is checked on its own.

Run:
    python3 tests/test_cp8.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import update as up                             # noqa: E402
from homegraph import userconfig                               # noqa: E402
from homegraph.models.m3_build import build as m3_build        # noqa: E402
from homegraph.store import Store                              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AS_OF_A = "2026-07-22"
AS_OF_B = "2026-07-23"

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-50s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


# Everything except the two timestamps. `activity_datelist`/`datelist_int` are
# also excluded: they encode WHEN a node was observed, which is history, and
# history is the thing update keeps and a rebuild does not have.
NODE_COLS = ("node_key", "kind", "subtype", "path", "title", "body",
             "size", "mtime", "content_hash")


def nodes_of(db):
    with Store(db) as s:
        return {tuple(r[c] for c in NODE_COLS) for r in
                s.db.execute("SELECT %s FROM nodes" % ", ".join(NODE_COLS))}


def edges_of(db):
    with Store(db) as s:
        return {(r["src_key"], r["rel"], r["dst_key"]) for r in s.db.execute(
            "SELECT s.node_key src_key, e.rel, d.node_key dst_key FROM edges e "
            "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst")}


def first_seen_of(db):
    """File nodes only.

    A section of a file that changed IS new -- it was deleted with the rest of
    the old parse and written again -- so demanding it keep a first_seen would
    demand that update pretend the old heading is the new one. The claim is
    about files: a file that still exists is the same file, whatever its
    contents now say.
    """
    with Store(db) as s:
        return {r["node_key"]: r["first_seen"] for r in s.db.execute(
            "SELECT node_key, first_seen FROM nodes "
            "WHERE path IS NOT NULL AND node_key = path")}


def markdown_paths(root, cfg):
    return up.corpus_paths(root, "markdown", config=cfg)


def image_paths(root, cfg):
    return up.corpus_paths(root, "image", config=cfg)


def main():
    from tests.fixtures import synthetic as syn

    tmp = tempfile.mkdtemp(prefix="cp8-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        return run(tmp, syn)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        syn.use_config(syn.CONFIG)


def run(tmp, syn):
    root = os.path.join(tmp, "corpus")
    syn.build(root)
    cfg_path = syn._config_for(root)
    syn.use_config(cfg_path)
    cfg = userconfig.load(cfg_path)

    # -- A: the starting state ------------------------------------------
    updated_db = os.path.join(tmp, "m3-updated.db")
    paths_a = markdown_paths(root, cfg)
    with Store(updated_db, model="m3") as s:
        m3_build(s, paths_a, AS_OF_A)
        s.rebuild_fts()
        up.write_fingerprint(s, up.fingerprint(cfg))
    before = nodes_of(updated_db)
    first_seen_a = first_seen_of(updated_db)
    check("corpus A built at all", len(paths_a) > 10 and len(before) > 30,
          "%d markdown file(s), %d node(s)" % (len(paths_a), len(before)))

    # -- B: what changed, declared --------------------------------------
    declared = syn.evolve(root)
    paths_b = markdown_paths(root, cfg)
    md_declared = {axis: [r for r in rels if r.endswith((".md", ".mdx"))]
                   for axis, rels in declared.items()}
    # An equivalence over two identical corpora is the emptiest gate here.
    check("corpus B differs from A on every axis",
          all(md_declared[a] for a in ("added", "changed", "touched",
                                       "removed"))
          and len(paths_b) == len(paths_a) + len(md_declared["added"])
          - len(md_declared["removed"]),
          "added %d changed %d touched %d removed %d, %d -> %d file(s)"
          % (len(md_declared["added"]), len(md_declared["changed"]),
             len(md_declared["touched"]), len(md_declared["removed"]),
             len(paths_a), len(paths_b)))

    # -- the reference: a full build of B --------------------------------
    full_db = os.path.join(tmp, "m3-full.db")
    with Store(full_db, model="m3") as s:
        m3_build(s, paths_b, AS_OF_B)
        s.rebuild_fts()
    full_nodes, full_edges = nodes_of(full_db), edges_of(full_db)
    check("the reference rebuild is not empty", len(full_nodes) > 30,
          "%d node(s), %d edge(s)" % (len(full_nodes), len(full_edges)))

    # -- the update ------------------------------------------------------
    reparsed = []
    def counting_builder(store, paths, as_of, all_paths=None):
        reparsed.extend(paths)
        return m3_build(store, paths, as_of, index_paths=all_paths or paths)

    failure = None
    with Store(updated_db, model="m3") as s:
        try:
            report = up.update(s, "m3", paths_b, AS_OF_B, cfg,
                               builder=counting_builder)
        except Exception as exc:                                # noqa: BLE001
            # Recorded and failed here rather than propagated. An update that
            # blows up mid-way leaves a half-applied store, which is a result
            # the gates below can and should judge -- letting it kill the
            # process instead means the mutation harness reports a crash, the
            # weakest kind of detection there is.
            failure = "raised:%s" % type(exc).__name__
            report = up.UpdateReport(model="m3", changes={
                k: -1 for k in ("added", "changed", "touched", "unchanged",
                                "removed")})
    check("the update completed without raising", failure is None,
          failure or "no exception")
    print("\nupdate report: %s\n" % report.summary())

    # The diff must agree with what the fixture declared it did. Without this,
    # every gate below is testing an update against a diff that could be
    # anything at all.
    got = report.changes
    want = {a: len(md_declared[a]) for a in ("added", "changed", "touched",
                                             "removed")}
    check("the diff matches the declared change",
          all(got[a] == want[a] for a in want)
          and got["unchanged"] == len(paths_a) - want["changed"]
          - want["touched"] - want["removed"],
          "%s vs declared %s" % (got, want))

    upd_nodes, upd_edges = nodes_of(updated_db), edges_of(updated_db)

    # -- the equivalence -------------------------------------------------
    only_upd = sorted(n[0] for n in upd_nodes - full_nodes)
    only_full = sorted(n[0] for n in full_nodes - upd_nodes)
    check("updated nodes equal a full rebuild's",
          upd_nodes == full_nodes and full_nodes,
          "%d only after update, %d only after rebuild%s"
          % (len(only_upd), len(only_full),
             "" if not (only_upd or only_full)
             else "  e.g. %s" % (only_upd or only_full)[:2]))
    e_only_upd = sorted(upd_edges - full_edges)
    e_only_full = sorted(full_edges - upd_edges)
    check("updated edges equal a full rebuild's",
          upd_edges == full_edges and full_edges,
          "%d only after update, %d only after rebuild%s"
          % (len(e_only_upd), len(e_only_full),
             "" if not (e_only_upd or e_only_full)
             else "  e.g. %s" % (e_only_upd or e_only_full)[:1]))

    # -- removal is real -------------------------------------------------
    removed_md = [os.path.join(root, r) for r in md_declared["removed"]]
    with Store(updated_db) as s:
        still = [p for p in removed_md
                 if s.db.execute("SELECT COUNT(*) c FROM nodes WHERE path = ?",
                                 (p,)).fetchone()["c"]]
        # `all()` over an empty list is True, so the count of things that had
        # to go is its own check.
        check("something was actually removed", len(removed_md) > 0,
              "%d file(s) declared removed" % len(removed_md))
        check("removed files leave no node", not still,
              "%d still present" % len(still))
        dangling = s.db.execute(
            "SELECT COUNT(*) c FROM edges e "
            "WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.src) "
            "OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.dst)"
        ).fetchone()["c"]
        check("no edge points at a node that is gone", dangling == 0,
              "%d dangling endpoint(s)" % dangling)
        orphans = s.fts_orphans()
        check("no FTS row survives its node", not orphans,
              "%d orphan(s)" % len(orphans))
        # An index that does not cover the nodes added by this update returns
        # silence for them, which is indistinguishable from their not existing.
        check("the FTS index covers the updated store",
              not s.fts_is_stale() and s.fts_count() == s.node_count(),
              "%d fts row(s), %d node(s)" % (s.fts_count(), s.node_count()))

    # -- history is what update keeps ------------------------------------
    first_seen_b = first_seen_of(updated_db)
    survivors = [k for k in first_seen_a if k in first_seen_b]
    kept = [k for k in survivors if first_seen_b[k] == first_seen_a[k]]
    check("surviving nodes keep their first_seen",
          survivors and len(kept) == len(survivors),
          "%d of %d survivors kept it" % (len(kept), len(survivors)))
    with Store(full_db) as s:
        rebuilt_first = {r["first_seen"] for r in
                         s.db.execute("SELECT first_seen FROM nodes")}
    check("a full rebuild cannot keep it -- which is why it is excluded",
          rebuilt_first == {AS_OF_B}, "rebuild first_seen = %s"
          % sorted(rebuilt_first))

    # -- the cost claim, counted -----------------------------------------
    expected_reparse = sorted(
        os.path.join(root, r) for r in
        md_declared["added"] + md_declared["changed"]
        + syn.EVOLUTION_NEIGHBOURS)
    check("exactly the declared files were reparsed",
          sorted(reparsed) == expected_reparse,
          "%d reparsed, %d expected, %d in the corpus"
          % (len(reparsed), len(expected_reparse), len(paths_b)))
    # The neighbours are the interesting half and the easiest to lose: they did
    # not change, so a diff cannot see them, and an expansion that silently
    # returns nothing shows up only here and in the equivalence gate.
    check("neighbours of the change were rebuilt too",
          report.neighbours == len(syn.EVOLUTION_NEIGHBOURS)
          and report.neighbours > 0,
          "%d neighbour(s), declared %d"
          % (report.neighbours, len(syn.EVOLUTION_NEIGHBOURS)))
    check("the update reparsed strictly less than a rebuild",
          0 < len(reparsed) < len(paths_b),
          "%d of %d file(s)" % (len(reparsed), len(paths_b)))
    touched_paths = [os.path.join(root, r) for r in md_declared["touched"]]
    check("touched files cost a stat and no reparse",
          touched_paths and not any(p in reparsed for p in touched_paths)
          and report.restatted == len(touched_paths),
          "%d touched, %d restatted, 0 reparsed"
          % (len(touched_paths), report.restatted))

    # -- refusals ---------------------------------------------------------
    t_refusals(tmp, root, cfg, paths_b)
    t_mesh_forgets(tmp, root, cfg)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def t_refusals(tmp, root, cfg, paths):
    empty_db = os.path.join(tmp, "m3-empty.db")
    with Store(empty_db, model="m3") as s:
        raised = None
        try:
            up.update(s, "m3", paths, AS_OF_B, cfg)
        except up.NotBuilt as exc:
            raised = str(exc)
        except Exception as exc:                                # noqa: BLE001
            raised = "wrong exception: %r" % exc
    check("update on an unbuilt store refuses",
          raised is not None and "build it first" in (raised or "").lower(),
          (raised or "no exception")[:60])

    # A layout change is not a file change: files that were `image` become
    # `EXCLUDED` and back, and no diff over paths can see that.
    moved = userconfig.UserConfig(path=cfg.path, root=cfg.root,
                                  roles={"image": ("Somewhere-else",)})
    built = os.path.join(tmp, "m3-updated.db")
    with Store(built, model="m3") as s:
        raised = None
        try:
            up.update(s, "m3", paths, AS_OF_B, moved)
        except up.ConfigChanged as exc:
            raised = str(exc)
        except Exception as exc:                                # noqa: BLE001
            raised = "wrong exception: %r" % exc
    check("update refuses after the layout changed",
          raised is not None and "rebuild" in (raised or ""),
          (raised or "no exception")[:60])

    with Store(built, model="m3") as s:
        forced = None
        try:
            up.update(s, "m3", paths, AS_OF_B, moved,
                      allow_config_change=True)
            forced = "applied"
        except Exception as exc:                                # noqa: BLE001
            forced = "raised:%s" % type(exc).__name__
    check("the refusal can be overridden explicitly", forced == "applied",
          forced)

    # M4 is named, not approximated.
    raised = None
    with Store(os.path.join(tmp, "m4.db"), model="m4") as s:
        s.upsert_node("x", kind="file", path="/x", as_of=AS_OF_A)
        try:
            up.update(s, "m4", [], AS_OF_B, cfg)
        except up.CannotUpdate as exc:
            raised = str(exc)
        except Exception as exc:                                # noqa: BLE001
            raised = "wrong exception: %r" % exc
    check("a model with no correct incremental path says so",
          raised is not None and "rollup" in (raised or ""),
          (raised or "no exception")[:60])

    # And the CLI turns each of those into exit 2 rather than a tidy zero.
    proc = subprocess.run(
        [sys.executable, "-m", "homegraph.cli", "update",
         "--model", "m4=%s" % os.path.join(tmp, "m4.db"), "--root", root],
        capture_output=True, text=True, cwd=REPO, timeout=300,
        env=dict(os.environ, HOMEGRAPH_CONFIG=cfg.path))
    check("the CLI exits 2 on a refused update", proc.returncode == 2,
          "exit %d" % proc.returncode)


def t_mesh_forgets(tmp, root, cfg):
    """The federation is derived. It must stop answering about deleted files."""
    from homegraph.mesh import Mesh
    from homegraph.models.m2_build import build as m2_build

    m2db = os.path.join(tmp, "m2.db")
    meshdb = os.path.join(tmp, "mesh.db")
    paths = image_paths(root, cfg)
    with Store(m2db, model="m2") as s:
        m2_build(s, paths, AS_OF_B)
        s.rebuild_fts()
        up.write_fingerprint(s, up.fingerprint(cfg))
    with Mesh({"m2": m2db}, mesh_db=meshdb) as mesh:
        mesh.build_edges(AS_OF_B)

    victim = paths[0]
    key = "m2::%s" % victim
    with Store(meshdb) as s:
        present = s.node_id(key) is not None
    check("the federation mirrors the model before the deletion", present, key)

    os.remove(victim)
    with Store(m2db, model="m2") as s:
        up.update(s, "m2", image_paths(root, cfg), AS_OF_B, cfg)
    result = up.refresh_mesh({"m2": m2db}, meshdb, AS_OF_B)
    with Store(meshdb) as s:
        gone = s.node_id(key) is None
        left = s.node_count()
    check("the federation forgets a file the model dropped", gone,
          "%d stub(s) removed, %d left" % (result["stubs_removed"], left))
    check("it did not simply empty itself", left > 5, "%d node(s) left" % left)


def test_checkpoint_cp8():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
