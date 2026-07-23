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
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900

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


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp9.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut9-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns(
                                "__pycache__", ".git", ".venv", ".mypy_cache",
                                ".ruff_cache", ".pytest_cache"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-52s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-52s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-52s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-52s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-52s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %-52s %s" % (name, why))
    return 1 if (survived or crashes or misattributed) else 0


if __name__ == "__main__":
    sys.exit(main())
