#!/usr/bin/env python3
"""CP-H2 -- a title carries how it was derived.

Borrowed idea (idea harvest 2026-07-24: Cirilcetra doc-linker, colby markers):
an inferred label -- a guess pressed into service as a fact -- must carry its
method and confidence, or it is cited downstream as if an author had written it
([[inferred-facts-become-citations]]). homegraph already does this for edges
(CP-9, EDGE_METHODS); H2 extends it to the one node label that is genuinely
inferred today: m3's title, which is DECLARED from frontmatter, read VERBATIM
from the filename, or INFERRED from the first heading when neither exists.

The load-bearing check is the negative: the first-heading fallback is tagged
`inferred` with confidence 0.5, NOT silently `declared`/1.0. A guess that records
itself as a fact is exactly the failure this checkpoint exists to prevent. And
an untagged title records NULL -- honest absence -- never a default `verbatim`
that would assert a derivation the caller never claimed.

Run:
    python3 tests/test_h2.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.models.m3_build import build as m3_build         # noqa: E402
from homegraph.store import TITLE_METHODS, Store                 # noqa: E402

results, check = reporter(56)


def _tmp():
    return tempfile.mkdtemp(prefix="h2-", dir=os.path.expanduser("~/.homegraph"))


def t_upsert_node_contract():
    d = _tmp()
    try:
        with Store(os.path.join(d, "s.db"), model="m3") as s:
            try:
                s.upsert_node("k1", "file", title="x", title_method="guessed")
                check("an unknown title method is refused", False, "no raise")
            except ValueError:
                check("an unknown title method is refused", True)
            except Exception as exc:                            # noqa: BLE001
                # A KeyError here means the validation was removed and the
                # confidence lookup blew up -- still a failure of "refuse
                # cleanly", named rather than a crash that hides the gate.
                check("an unknown title method is refused", False,
                      "raised %s, not a clean ValueError" % type(exc).__name__)

            nid = s.upsert_node("k2", "file", title="x", title_method="inferred")
            r = s.db.execute("SELECT title_method m, title_confidence c "
                             "FROM nodes WHERE id=?", (nid,)).fetchone()
            check("an inferred title carries confidence 0.5",
                  r["m"] == "inferred" and r["c"] == 0.5,
                  "method=%r conf=%r" % (r["m"], r["c"]))

            # The whole point: an untagged title is NULL, not a false 'verbatim'.
            nid = s.upsert_node("k3", "file", title="y")
            r = s.db.execute("SELECT title_method m, title_confidence c "
                             "FROM nodes WHERE id=?", (nid,)).fetchone()
            check("an untagged title records NULL, not a claim of fact",
                  r["m"] is None and r["c"] is None,
                  "method=%r conf=%r" % (r["m"], r["c"]))

            check("confidence is looked up, never passed (cannot drift)",
                  TITLE_METHODS["declared"] == 1.0
                  and TITLE_METHODS["inferred"] == 0.5)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_m3_title_provenance():
    d = _tmp()
    try:
        files = {
            "declared.md": "---\ntitle: The Real Name\n---\n# A Heading\nbody\n",
            "inferred.md": "# First Heading\nsome body text\n",
            "plain.md": "just body text, no heading and no frontmatter\n",
        }
        paths = []
        for name, text in files.items():
            p = os.path.join(d, name)
            open(p, "w").write(text)
            paths.append(p)
        db = os.path.join(d, "m3.db")
        with Store(db, model="m3") as s:
            m3_build(s, sorted(paths), "2026-07-22")
        with Store(db) as s:
            got = {}
            for r in s.db.execute(
                    "SELECT node_key, title, title_method m, title_confidence c "
                    "FROM nodes WHERE kind='file'"):
                got[os.path.basename(r["node_key"])] = (
                    r["title"], r["m"], r["c"])

        check("a frontmatter title is declared (1.0)",
              got["declared.md"][1:] == ("declared", 1.0),
              "%r" % (got["declared.md"],))
        # LOAD-BEARING: the guess is tagged a guess, not a fact.
        check("a first-heading fallback is inferred (0.5), never a fact",
              got["inferred.md"][1:] == ("inferred", 0.5),
              "%r" % (got["inferred.md"],))
        check("a titleless file falls back to the filename, verbatim (1.0)",
              got["plain.md"][1:] == ("verbatim", 1.0),
              "%r" % (got["plain.md"],))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_readside_surfaces_confidence():
    """The other half of the guarantee: the provenance is not just recorded, it
    reaches the consumer. Without this an inferred title is served to an agent
    identically to a declared one, and the guess is cited as fact -- the exact
    failure H2 exists to end. (sim-auditor CP-H2 #1/#2.)
    """
    from homegraph.mesh import Mesh
    from homegraph.search import fts_search

    d = _tmp()
    try:
        # No frontmatter -> the title is INFERRED from the heading (0.5).
        p = os.path.join(d, "guessed.md")
        open(p, "w").write("# Reciprocal Rank Fusion\nbody about fusing ranks\n")
        db = os.path.join(d, "m3.db")
        with Store(db, model="m3") as s:
            m3_build(s, [p], "2026-07-22")
            s.rebuild_fts()

        with Store(db) as s:
            fh = next(h for h in fts_search(s, "Reciprocal Rank Fusion")
                      if h["node_key"] == p)
            check("fts_search surfaces title_confidence for an inferred title",
                  fh.get("title_confidence") == 0.5,
                  "%r" % fh.get("title_confidence"))

        # The mesh is what the MCP server actually answers from.
        res = Mesh({"m3": db}).search("Reciprocal Rank Fusion")
        mh = next(h for h in res.hits if h["node_key"] == p)
        check("mesh_search surfaces title_confidence (the MCP consumer sees the guess)",
              mh.get("title_confidence") == 0.5,
              "%r" % mh.get("title_confidence"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    t_upsert_node_contract()
    t_m3_title_provenance()
    t_readside_surfaces_confidence()
    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h2():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
