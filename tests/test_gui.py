#!/usr/bin/env python3
"""CP-GUI -- the GUI's payload builders and HTTP routes.

Every check here is about what Python decides, because the page decides
nothing. The structural checks run against a synthetic M3 store built in a
tempdir, the way every other checkpoint builds its own corpus, so this file
is not the one checkpoint in `tests/` that silently does nothing on a machine
without `~/.homegraph/real-*.db`. The one check that still needs the real
corpus is `t_isolated_matches_md_gaps`: it ties this surface to an
already-measured fact, that the set `/graph` calls isolated is the set
`isolated_notes` reports, so the GUI and `md gaps` cannot drift apart without
anyone saying so -- and it prints a named SKIPPED line rather than vanishing
when the real store is absent.

Run:
    python3 tests/test_gui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter                                       # noqa: E402

from homegraph import gui                                          # noqa: E402
from homegraph.models.m3_build import build, isolated_notes        # noqa: E402
from homegraph.store import Store                                  # noqa: E402

results, check = reporter(50)


def _build_synthetic_m3(root):
    """A tiny M3 store: a linked pair and a file nothing points at or from.

    Not `tests/fixtures/synthetic.py`: that fixture plants a declared answer
    key for six checkpoints of classification and extraction (CP-0..CP-6).
    `graph_payload` never classifies or extracts anything -- it reads nodes
    and edges `m3_build.build` already wrote -- so the only property this
    needs from a corpus is one file that links to another, and one that
    links to nothing, which three files on disk are enough to state.
    """
    def write(name, text):
        path = os.path.join(root, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    paths = [
        write("linked-a.md",
              "---\ntags: [demo]\n---\n# A\n\n## Sub\n\n[[linked-b]]\n"),
        write("linked-b.md", "# B\n\nSome text.\n"),
        write("isolated.md", "# Isolated\n\nNothing links here.\n"),
    ]
    db = os.path.join(root, "synthetic-m3.db")
    with Store(db, model="m3") as store:
        build(store, paths, date(2026, 7, 22))
    return db


def t_file_kinds_are_the_measured_four():
    """document, image, file, code -- and nothing else.

    Named explicitly rather than derived from a suffix or a name: M1's
    `reference` (180 of them) and M4's `archive_entry` (69) are about files
    without being files, and any rule that guessed would take them.
    """
    check("FILE_KINDS is exactly the four file-bearing kinds",
          gui.FILE_KINDS == frozenset({"file", "document", "image", "code"}),
          repr(sorted(gui.FILE_KINDS)))


def t_full_read_has_every_planted_file(m3_db):
    """Uncapped, nothing is lost to the per-model cap.

    Real-corpus measurement of this same property: `collect` caps per model
    BEFORE any filter runs, so at limit 2000, M3's 602 files lose 325 of
    themselves to its 6035 sections. That exact figure needs the real store
    to reproduce; the property -- an uncapped read returns every planted file
    and reports no truncation -- does not, so it runs here on a corpus of
    three.
    """
    payload = gui.graph_payload({"m3": m3_db})
    check("every planted file node reaches the payload",
          len(payload["nodes"]) == 3, "%d node(s)" % len(payload["nodes"]))
    check("an uncapped read truncates nothing",
          payload["truncated"] == [], repr(payload["truncated"]))


def t_capped_read_names_the_model_it_capped(m3_db):
    """The guard. A ceiling that does not announce itself is the defect.

    The synthetic corpus holds 3 files + 3 sections + 1 tag = 7 raw nodes;
    a limit below that forces the same cap `collect` applies on the real
    store, just at a size this file does not need a fixture library to reach.
    """
    payload = gui.graph_payload({"m3": m3_db}, limit_per_model=5)
    check("a capped read reports which model was cut",
          payload["truncated"] == ["m3"], repr(payload["truncated"]))


def t_payload_drops_non_file_kinds(m3_db):
    """The kind filter, exercised: the synthetic corpus has sections and a
    tag, so a payload containing only `file` proves something was removed,
    not merely that nothing else was ever there.
    """
    raw_nodes, _edges, _missing = gui.collect({"m3": m3_db}, gui.NO_LIMIT)
    raw_kinds = {n["kind"] for n in raw_nodes}
    payload = gui.graph_payload({"m3": m3_db})
    kinds = {n["kind"] for n in payload["nodes"]}
    check("the raw read actually contains non-file kinds to filter",
          bool(raw_kinds - gui.FILE_KINDS), "raw kinds=%s" % sorted(raw_kinds))
    check("no section, tag or wikilink node reaches the payload",
          kinds <= gui.FILE_KINDS, "kinds=%s" % sorted(kinds))


def t_isolated_computation(m3_db):
    """The linked pair is connected; the third file stands alone.

    `linked-a.md` WIKILINKS_TO `linked-b.md`, so both are linked; `isolated.md`
    has no edge to or from another file node (its only edge is CONTAINS into
    its own section, a non-file kind already gone by the time `isolated` is
    computed).
    """
    payload = gui.graph_payload({"m3": m3_db})
    isolated_paths = {n["path"] for n in payload["nodes"]
                       if n["key"] in set(payload["isolated"])}
    names = {os.path.basename(p) for p in isolated_paths}
    check("only the file with no file-to-file edge is isolated",
          names == {"isolated.md"}, "isolated=%s" % sorted(names))


def t_isolated_matches_md_gaps(m3_db):
    """The cross-check against the real corpus. 315 of 602, if it is here.

    Kept as an additional check on the real store rather than folded into the
    structural check above: the synthetic corpus proves the isolated
    computation is correct in shape, but only the real corpus proves it
    agrees with `isolated_notes()` at the scale the defect was measured at.
    """
    if m3_db is None:
        check("isolated set equals isolated_notes()", True,
              "SKIPPED -- ~/.homegraph/real-m3.db not present on this machine")
        return
    with Store(m3_db) as store:
        gold_paths, gold_total = isolated_notes(store)
    payload = gui.graph_payload({"m3": m3_db})
    got = {n["path"] for n in payload["nodes"]
           if n["key"] in set(payload["isolated"])}
    check("isolated set equals isolated_notes()",
          got == set(gold_paths),
          "gui=%d gold=%d of %d" % (len(got), len(gold_paths), gold_total))


def real_m3():
    """Path to the real M3 store, or None.

    Deliberately decoupled from the mesh: no check in this file calls
    `mesh_path` or `mesh_neighbors`, so a missing `real-mesh.db` must not
    disable the checks that only ever touch M3. Tasks 3 and 4 add their own
    mesh requirement where they add the checks that actually need one.
    """
    m3 = os.path.expanduser("~/.homegraph/real-m3.db")
    return m3 if os.path.exists(m3) else None


def main():
    t_file_kinds_are_the_measured_four()

    with tempfile.TemporaryDirectory(prefix="gui-cp-") as tmp:
        synthetic_db = _build_synthetic_m3(tmp)
        t_full_read_has_every_planted_file(synthetic_db)
        t_capped_read_names_the_model_it_capped(synthetic_db)
        t_payload_drops_non_file_kinds(synthetic_db)
        t_isolated_computation(synthetic_db)

    t_isolated_matches_md_gaps(real_m3())

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
