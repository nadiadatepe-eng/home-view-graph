#!/usr/bin/env python3
"""CP-GUI -- the GUI's payload builders and HTTP routes.

Every check here is about what Python decides, because the page decides
nothing. The one that ties this surface to an already-measured fact is
`t_isolated_matches_md_gaps`: the set `/graph` calls isolated must be the set
`isolated_notes` reports, or the GUI and `md gaps` can drift apart without
anyone saying so.

Run:
    python3 tests/test_gui.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import reporter                                       # noqa: E402

from homegraph import gui                                          # noqa: E402
from homegraph.models.m3_build import isolated_notes               # noqa: E402
from homegraph.store import Store                                  # noqa: E402

results, check = reporter(50)


def t_file_kinds_are_the_measured_four():
    """document, image, file, code -- and nothing else.

    Named explicitly rather than derived from a suffix or a name: M1's
    `reference` (180 of them) and M4's `archive_entry` (69) are about files
    without being files, and any rule that guessed would take them.
    """
    check("FILE_KINDS is exactly the four file-bearing kinds",
          gui.FILE_KINDS == frozenset({"file", "document", "image", "code"}),
          repr(sorted(gui.FILE_KINDS)))


def t_all_file_nodes_survive_the_read(m3_db):
    """The measured defect, as a check.

    `collect` caps per model before anything else. At limit 2000, M3's 602
    files compete with its 6035 sections and 325 file nodes never arrive.
    """
    payload = gui.graph_payload({"m3": m3_db})
    check("every M3 file node reaches the payload",
          len(payload["nodes"]) == 602, "%d node(s)" % len(payload["nodes"]))


def t_capped_read_names_the_model_it_capped(m3_db):
    """The guard. A ceiling that does not announce itself is the defect."""
    payload = gui.graph_payload({"m3": m3_db}, limit_per_model=2000)
    check("a capped read reports which model was cut",
          payload["truncated"] == ["m3"], repr(payload["truncated"]))


def t_payload_drops_non_file_kinds(m3_db):
    payload = gui.graph_payload({"m3": m3_db})
    kinds = {n["kind"] for n in payload["nodes"]}
    check("no section, tag or wikilink node reaches the payload",
          kinds <= gui.FILE_KINDS, "kinds=%s" % sorted(kinds))


def t_isolated_matches_md_gaps(m3_db):
    """The cross-check. 315 of 602 on the real corpus."""
    with Store(m3_db) as store:
        gold_paths, gold_total = isolated_notes(store)
    payload = gui.graph_payload({"m3": m3_db})
    got = {n["path"] for n in payload["nodes"]
           if n["key"] in set(payload["isolated"])}
    check("isolated set equals isolated_notes()",
          got == set(gold_paths),
          "gui=%d gold=%d of %d" % (len(got), len(gold_paths), gold_total))


def corpus():
    """The real M3 store and the mesh, or (None, None).

    The isolated cross-check needs a corpus with links in it; the synthetic
    fixture has fifteen declared relations and would pass trivially.

    The mesh path is returned alongside because `Mesh._read_mesh` REFUSES when
    `mesh_db` is falsy -- deliberately, so a read against an absent mesh cannot
    leave an empty database behind and answer `count: 0`. `mesh_path` and
    `mesh_neighbors` therefore cannot run without it, and the route checks in
    Tasks 3 and 4 must pass it or they fail for a reason that is not a defect
    in the code under test.
    """
    m3 = os.path.expanduser("~/.homegraph/real-m3.db")
    mesh = os.path.expanduser("~/.homegraph/real-mesh.db")
    if os.path.exists(m3) and os.path.exists(mesh):
        return m3, mesh
    return None, None


def main():
    t_file_kinds_are_the_measured_four()
    m3_db, mesh_db = corpus()
    if m3_db:
        t_all_file_nodes_survive_the_read(m3_db)
        t_capped_read_names_the_model_it_capped(m3_db)
        t_payload_drops_non_file_kinds(m3_db)
        t_isolated_matches_md_gaps(m3_db)
    else:
        print("no real-m3.db -- payload checks skipped, and this line is the "
              "report that they were")

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter (why one test per checkpoint: CONTRIBUTING.md) ----------

def test_checkpoint_gui():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
