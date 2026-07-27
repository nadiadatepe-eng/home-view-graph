#!/usr/bin/env python3
"""Mutation test for CP-9 -- provenance on edges.

The failure this checkpoint guards is not a wrong answer. It is a right answer
delivered with more confidence than the evidence supports, which no correctness
gate anywhere else in the package can see: the edge is in the graph, the count
is right, the search returns it, and the only thing wrong is that a guess is
wearing the same clothes as a fact.

So the mutations come in pairs. Marking nothing and marking everything both
leave a system that looks entirely reasonable, and a gate that only checks one
direction passes one of them. The same for the note: `return None` and
`return "warning"` are each a one-line change that satisfies half the suite.

Run:
    python3 tests/mutate_cp9.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # -- the marker, both directions ------------------------------------
    ("ambiguous wikilinks are written as exact",
     "homegraph/models/m3_build.py",
     '                                  method="path_prefix" if ambiguous\n'
     '                                  else "exact")',
     '                                  method="exact")  # mutated',
     "the ambiguous link is marked path_prefix"),

    ("every wikilink is written as derived",
     "homegraph/models/m3_build.py",
     '                                  method="path_prefix" if ambiguous\n'
     '                                  else "exact")',
     '                                  method="path_prefix")  # mutated',
     "unambiguous wikilinks are NOT marked"),

    # -- the note, both directions --------------------------------------
    ("nothing is ever reported as derived",
     "homegraph/store.py",
     "    if not seen:\n        return None",
     "    return None  # mutated: never warn\n"
     "    if not seen:\n        return None",
     "a derived row produces a note naming the method"),

    ("everything is reported as derived",
     "homegraph/store.py",
     "        if conf is not None and conf < 1.0:",
     "        if True:  # mutated: every row counts as a guess",
     "all-exact rows produce no note"),

    ("the note is computed and not printed",
     "homegraph/cli.py",
     '        if note:\n            print("\\nPARTIAL -- %s" % note)\n    return 0\n\n\ndef cmd_md_broken',
     '    return 0\n\n\ndef cmd_md_broken',
     "the CLI prints the warning, not just computes it"),

    # -- the write barrier on provenance --------------------------------
    ("an unknown method is stored instead of refused",
     "homegraph/store.py",
     "        if method not in EDGE_METHODS:\n"
     "            raise ValueError(\"unknown edge method %r; known: %s\"\n"
     "                             % (method, \", \".join(sorted(EDGE_METHODS))))\n"
     "        confidence = EDGE_METHODS[method]",
     "        confidence = EDGE_METHODS.get(method, 1.0)  # mutated",
     "an unknown method is refused"),

    ("method gets a default and can be inherited by omission",
     "homegraph/store.py",
     "                    as_of: str | None = None, *, method: str) -> None:",
     '                    as_of: str | None = None, *, '
     'method: str = "exact") -> None:',
     "method cannot be omitted"),

    # -- re-assertion, both directions ----------------------------------
    ("re-asserting keeps the old provenance",
     "homegraph/store.py",
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=?", (as_of, method, confidence, row["id"]))',
     '                "UPDATE edges SET last_seen=? WHERE id=?",\n'
     '                (as_of, row["id"]))  # mutated: provenance frozen',
     "re-asserting downgrades when the evidence weakened"),

    ("re-asserting only ever raises confidence",
     "homegraph/store.py",
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=?", (as_of, method, confidence, row["id"]))',
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=? AND confidence <= ?",  # mutated: no downgrade\n'
     '                (as_of, method, confidence, row["id"], confidence))',
     "re-asserting downgrades when the evidence weakened"),

    # -- the scale is a scale -------------------------------------------
    ("a derived method is worth as much as an exact one",
     "homegraph/store.py",
     '    "path_prefix": 0.7,',
     '    "path_prefix": 1.0,  # mutated',
     "every other method is strictly below 1.0"),

    # -- each mechanism keeps its own method ----------------------------
    #
    # These are invisible to every other checkpoint: the edge is still built,
    # still counted, still returned. Only its provenance changes.
    ("figure matching claims to be exact",
     "homegraph/mesh.py",
     '                                         "FIGURE_FOR", as_of,\n'
     '                                         method="basename")',
     '                                         "FIGURE_FOR", as_of,\n'
     '                                         method="exact")  # mutated',
     "method basename     is produced by a build"),

    ("temporal cohorts claim to be exact",
     "homegraph/mesh.py",
     '                                 "TEMPORAL_COHORT", as_of, method="cohort")',
     '                                 "TEMPORAL_COHORT", as_of, '
     'method="exact")  # mutated',
     "method cohort       is produced by a build"),

    ("a path named in prose claims to be exact",
     "homegraph/models/m3_build.py",
     '                store.upsert_edge(path, resolved, "MENTIONS_PATH", as_of,\n'
     '                                  method="mention")',
     '                store.upsert_edge(path, resolved, "MENTIONS_PATH", as_of,\n'
     '                                  method="exact")  # mutated',
     "method mention      is produced by a build"),

    ("exact stops being the only method worth 1.0",
     "homegraph/store.py",
     '    "cohort": 0.4,',
     '    "cohort": 1.0,  # mutated',
     "exact is the only method worth 1.0"),

    ("the note names every known method, used or not",
     "homegraph/store.py",
     "    parts = \", \".join(\"%d by %s (%.1f)\" % (n, m, EDGE_METHODS.get(m, 0.0))\n"
     "                      for m, n in sorted(seen.items()))",
     "    parts = \", \".join(\"%d by %s (%.1f)\" % (seen.get(m, 0), m, c)\n"
     "                      for m, c in sorted(EDGE_METHODS.items()))  # mutated",
     "the note does not name methods that were not used"),

    ("re-asserting never recovers from a downgrade",
     "homegraph/store.py",
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=?", (as_of, method, confidence, row["id"]))',
     '                "UPDATE edges SET last_seen=?, method=?, confidence=? "\n'
     '                "WHERE id=? AND confidence >= ?",  # mutated: no upgrade\n'
     '                (as_of, method, confidence, row["id"], confidence))',
     "re-asserting upgrades when the ambiguity is gone"),

    ("the collision resolves to the losing candidate",
     "homegraph/models/m3_build.py",
     "                store.upsert_edge(path, resolve_target(target, path, index),",
     "                store.upsert_edge(path, sorted(hits)[0],  # mutated",
     "the ambiguous link resolved to the nearest prefix"),

    ("backlinks drops the note it computed",
     "homegraph/models/m3_build.py",
     "    return [r[\"node_key\"] for r in rows], provenance_note(rows)",
     "    return [r[\"node_key\"] for r in rows], None  # mutated",
     "backlinks to the contested note returns the note"),

    # -- the picture is a read path too ---------------------------------
    #
    # The state the visualisation was in until now: it selected `e.rel` and
    # drew a guessed relation exactly like a stated one. No behavioural gate
    # anywhere saw it, because the graph is still correct -- it is only
    # silent about how sure it is.
    ("the picture stops reading provenance out of the store",
     "homegraph/visualize.py",
     '                    "SELECT s.node_key a, d.node_key b, e.rel r, "\n'
     '                    "e.method m, e.confidence c FROM edges e "',
     '                    "SELECT s.node_key a, d.node_key b, e.rel r, "\n'
     '                    "\'exact\' m, 1.0 c FROM edges e "  # mutated',
     "the page counts the derived edges the store holds"),

    ("the picture drops the warning it computed",
     "homegraph/visualize.py",
     '        "note": note or "",',
     '        "note": "",  # mutated',
     "the page carries the same warning the text answers carry"),

    ("every edge in the picture is called derived",
     "homegraph/visualize.py",
     '        "derived": sum(1 for *_, c in edges if c is not None and c < 1.0),',
     '        "derived": len(edges),  # mutated',
     "a graph of stated edges claims nothing derived"),

    ("the picture forgets which method each edge used",
     "homegraph/visualize.py",
     '        "edges": [[a, b, r, m, c] for a, b, r, m, c in edges],',
     '        "edges": [[a, b, r] for a, b, r, m, c in edges],  # mutated',
     "every edge in the page carries its method and confidence"),

    # -- the results list -----------------------------------------------
    ("every node is offered as a file to open",
     "homegraph/visualize.py",
     '                    "link": bool(r["node_key"].startswith("/")),',
     '                    "link": True,  # mutated: link everything',
     "nodes with nothing behind them are not"),

    ("nothing is offered as a file to open",
     "homegraph/visualize.py",
     '                    "link": bool(r["node_key"].startswith("/")),',
     '                    "link": False,  # mutated: link nothing',
     "nodes that stand for a file are marked linkable"),

    ("the page falls back to the model codes",
     "homegraph/visualize.py",
     '        "names": MODEL_NAMES,',
     '        "names": {},  # mutated',
     "the page carries a readable name for every model shown"),

    ("the results list has no cap to declare",
     "homegraph/visualize.py",
     "MAX_HITS = 200",
     "MAX_HITS = 0  # mutated",
     "the results list declares its cap"),

    ("the tooltip goes back to building an HTML string",
     "homegraph/visualize.py",
     "    tip.textContent = '';\n"
     "    const b = document.createElement('b');",
     "    tip.innerHTML = '';  // mutated\n"
     "    const b = document.createElement('b');",
     "the page never assigns innerHTML"),

    ("a section links to the section instead of the file",
     "homegraph/visualize.py",
     "  const h = p.indexOf('#');\n  return h < 0 ? p : p.slice(0, h);",
     "  return p;  // mutated: keep the #n",
     "a section's link points at the file it is part of"),

    ("the link is built without escaping the path",
     "homegraph/visualize.py",
     "  return 'file://' + encodeURI(fileOf(key));",
     "  return 'file://' + fileOf(key);  // mutated: unescaped",
     "spaces and non-ASCII survive the round trip"),

    # -- the migration --------------------------------------------------
    #
    # No mutation for "the migration ran". Skipping migration 2 leaves
    # `upsert_edge` writing to a column that does not exist, so it is
    # detected by the process dying rather than by a gate saying no --
    # DECISIONS section 21: a mutation that cannot produce a wrong answer
    # only tests error handling. The check stays as the precondition for the
    # two below it, which do have mutations.
    ("the migration rewrites the rows it touches",
     "homegraph/store.py",
     "ALTER TABLE edges ADD COLUMN method     TEXT NOT NULL DEFAULT 'exact';",
     "ALTER TABLE edges ADD COLUMN method     TEXT NOT NULL DEFAULT 'exact';\n"
     "UPDATE edges SET last_seen = first_seen;  -- mutated",
     "every v1 edge survived, unchanged in its own columns"),

    ("the migration invents a confidence for old edges",
     "homegraph/store.py",
     "ALTER TABLE edges ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;",
     "ALTER TABLE edges ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5;",
     "migrated edges default to exact/1.0"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp9.py", prefix="mut9-", timeout=900,
                 ignore=(".venv", ".mypy_cache", ".ruff_cache", ".pytest_cache")))
