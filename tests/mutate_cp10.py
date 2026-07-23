#!/usr/bin/env python3
"""Mutation test for CP-10 -- the query language.

Two failures matter more than the rest here, and neither produces an error.

**A permissive parser.** Accepting `OR` and ignoring it, or accepting a
variable-length path and treating it as one hop, returns rows. Plausible rows.
The user gets an answer to a question they did not ask and nothing anywhere
says so -- which is the whole reason the grammar is published and closed.

**A second copy of the temporal predicate.** Inlining `first_seen <= d AND
last_seen >= d` into the compiled SQL is faster and looks identical, and stays
identical until someone edits one of the two. The mutation below inlines it
with a single character wrong (`>` for `>=`), which is exactly how that
divergence arrives in practice.

Run:
    python3 tests/mutate_cp10.py
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
    # -- the edge of the language ---------------------------------------
    ("unsupported keywords are accepted and ignored",
     "homegraph/query.py",
     "            if upper in UNSUPPORTED:\n"
     "                raise QueryError(UNSUPPORTED[upper], missing=upper)",
     "            if False:  # mutated: nothing is unsupported\n"
     "                raise QueryError(UNSUPPORTED[upper], missing=upper)",
     "every refusal names the capability the key expects"),

    ("variable-length paths are silently read as one hop",
     "homegraph/query.py",
     '            raise QueryError("this language has no variable-length paths",\n'
     '                             missing="variable-length paths")',
     "            pass  # mutated: '*' ignored",
     "every refusal names the capability the key expects"),

    ("a trailing statement is ignored instead of refused",
     "homegraph/query.py",
     '            raise QueryError("one query per call; \';\' is not a '
     'separator here",\n'
     '                             missing="statement separators")',
     "            continue  # mutated: ';' skipped",
     "every refusal names the capability the key expects"),

    ("RETURN becomes optional",
     "homegraph/query.py",
     '        if not self.at("kw", "RETURN"):\n'
     '            raise QueryError("every query must RETURN something",\n'
     '                             missing="RETURN clause")\n'
     "        self.next()",
     '        if not self.at("kw", "RETURN"):  # mutated\n'
     "            return q\n"
     "        self.next()",
     "every out-of-grammar query is refused"),

    ("refusals stop naming the missing capability",
     "homegraph/cli.py",
     '            if exc.missing:\n'
     '                print("         (not supported: %s)" % exc.missing,\n'
     "                      file=sys.stderr)",
     "            pass  # mutated: refuse without saying what is missing",
     "every refusal names the capability the key expects"),

    # -- time is decided in one place -----------------------------------
    #
    # The mutation this checkpoint exists for. Both versions agree on most
    # dates; they differ only where an edge's last_seen equals the date.
    ("the temporal predicate is re-expressed in the compiled SQL",
     "homegraph/query.py",
     '        alive = {r["id"] for r in store.edges_as_of(q.as_of, q.edge.rel)}',
     '        alive = {r["id"] for r in store.db.execute(  # mutated: 2nd copy\n'
     '            "SELECT id FROM edges WHERE first_seen <= ? '
     'AND last_seen > ?",\n'
     "            (q.as_of, q.as_of))}",
     "AS OF 2026-01-15 equals edges_as_of, columns included"),

    ("AS OF stops filtering at all",
     "homegraph/query.py",
     "        rows = [r for r in rows if r[\"id\"] in alive]",
     "        rows = list(rows)  # mutated: no temporal filter",
     "AS OF 2026-02-01 equals edges_as_of, columns included"),

    # -- ambiguity, both directions -------------------------------------
    ("an ambiguous name resolves to the first candidate",
     "homegraph/query.py",
     "        if len(hits) > 1:\n"
     '            return Result("ambiguous", [], [],',
     "        if False:  # mutated: pick one and look certain\n"
     '            return Result("ambiguous", [], [],',
     "an ambiguous NAMED refuses with exit 2"),

    ("every name is treated as ambiguous",
     "homegraph/query.py",
     "        if len(hits) > 1:",
     "        if len(hits) >= 1:  # mutated",
     "an unambiguous NAMED answers"),

    ("a wildcard in a name widens the match",
     "homegraph/query.py",
     "        if base == name or os.path.splitext(base)[0] == name:\n"
     "            hits.append(row)",
     "        hits.append(row)  # mutated: the LIKE decides",
     "the LIKE prefilter does not decide the match"),

    ("a name resolves to a file and to each of its sections",
     "homegraph/query.py",
     '        "WHERE path IS NOT NULL AND node_key = path "\n'
     '        "AND path LIKE ? ESCAPE \'\\\\\' ORDER BY node_key", (like,)).fetchall()',
     '        "WHERE path IS NOT NULL "  # mutated: sections count too\n'
     '        "AND path LIKE ? ESCAPE \'\\\\\' ORDER BY node_key", (like,)).fetchall()',
     "a name resolves to the file, not to its sections"),

    # -- identifiers cannot become SQL ----------------------------------
    ("the property whitelist is dropped",
     "homegraph/query.py",
     "        if prop not in allowed:",
     "        if False:  # mutated: any property name",
     "every out-of-grammar query is refused"),

    ("the label whitelist is dropped",
     "homegraph/query.py",
     "        if pat.label and pat.label not in schema.labels:",
     "        if False:  # mutated: any label",
     "hostile identifiers are refused"),

    ("a literal is interpolated instead of bound",
     "homegraph/query.py",
     '            where.append("%s %s ?" % (col, cond.op))\n'
     "            params.append(cond.value)",
     '            where.append("%s %s \'%s\'" % (col, cond.op, cond.value))'
     "  # mutated",
     "a WHERE value is bound, not interpolated"),

    # -- the answer key is the point ------------------------------------
    #
    # None of these raise. Each returns rows, in the right shape, that answer
    # a slightly different question than the one asked -- which is why there
    # is a hand-written key rather than a smoke test.
    ("PREFIX matches anywhere in the value",
     "homegraph/query.py",
     '        if cond.op == "PREFIX":\n'
     '            where.append("%s LIKE ? ESCAPE \'\\\\\'" % col)\n'
     "            params.append(_escape_like(str(cond.value)) + \"%\")",
     '        if cond.op == "PREFIX":\n'
     '            where.append("%s LIKE ? ESCAPE \'\\\\\'" % col)\n'
     '            params.append("%" + _escape_like(str(cond.value)) + "%")',
     "every accepted query matches the answer key"),

    ("CONTAINS anchors at the start",
     "homegraph/query.py",
     '            params.append("%" + _escape_like(str(cond.value)) + "%")\n'
     "        else:",
     '            params.append(_escape_like(str(cond.value)) + "%")\n'
     "        else:",
     "every accepted query matches the answer key"),

    ("a node label is matched against kind instead of subtype",
     "homegraph/query.py",
     '        where.append("s.subtype = ?")',
     '        where.append("s.kind = ?")  # mutated',
     "every accepted query matches the answer key"),

    ("the relation filter is dropped",
     "homegraph/query.py",
     '    if q.edge.rel:\n        where.append("e.rel = ?")\n'
     "        params.append(q.edge.rel)",
     "    if False:  # mutated: every relation matches\n"
     '        where.append("e.rel = ?")\n        params.append(q.edge.rel)',
     "every accepted query matches the answer key"),

    ("the pattern binds source and destination the wrong way round",
     "homegraph/query.py",
     '    for pat, side in ((q.src, "s"), (q.dst, "d")):',
     '    for pat, side in ((q.src, "d"), (q.dst, "s")):  # mutated',
     "every accepted query matches the answer key"),

    ("keywords stay ordinary identifiers",
     "homegraph/query.py",
     '            if upper in KEYWORDS:\n                kind = "kw"\n'
     "                value = upper",
     "            if False:  # mutated: no keywords\n"
     '                kind = "kw"\n                value = upper',
     "the tokeniser keeps keywords apart from identifiers"),

    ("a name must include its extension to match",
     "homegraph/query.py",
     '        if base == name or os.path.splitext(base)[0] == name:',
     "        if base == name:  # mutated: no stem match",
     "resolve_named finds both collisions"),

    ("an ambiguous query answers with rows anyway",
     "homegraph/cli.py",
     '        for c in res.candidates:\n            print("  %s" % c, '
     "file=sys.stderr)\n        return 2",
     '        for c in res.candidates:\n            print("  %s" % c, '
     "file=sys.stderr)\n        return 0  # mutated: refuse, then answer",
     "an ambiguous NAMED refuses with exit 2"),

    ("only the first candidate is named",
     "homegraph/cli.py",
     "        for c in res.candidates:",
     "        for c in res.candidates[:1]:  # mutated",
     "the refusal lists both candidates"),

    # -- the answer key depends on the order being defined ---------------
    ("results come back in whatever order SQLite chooses",
     "homegraph/query.py",
     '    sql += " ORDER BY " + ", ".join(select)',
     "    pass  # mutated: no ORDER BY",
     "the compiler orders by the returned columns"),

    # -- provenance reaches the answer, both directions ------------------
    ("a query result never reports derived edges",
     "homegraph/query.py",
     "        note = provenance_note(marks)\n"
     "        if note:\n"
     "            warnings.append(note)",
     "        pass  # mutated: query answers never say partial",
     "a query returning a derived edge says partial"),

    ("every query result claims to be partial",
     "homegraph/query.py",
     '    return Result("partial" if warnings else "complete", columns, out,',
     '    return Result("partial", columns, out,  # mutated',
     "a query returning only stated edges does not"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp10.py")],
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
        tree = tempfile.mkdtemp(prefix="mut10-",
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
