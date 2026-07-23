#!/usr/bin/env python3
"""CP-10 -- the query language. One claim, and it is about the edge of it.

    every query in the grammar answers what the key says, and every
    query outside it is refused with the missing capability named.

The answer key is `tests/gold/cp10-queries.tsv`, **written before
`homegraph/query.py` existed** and derived by hand from the graph declared
below. A key produced by running the parser is a photograph of the parser.

Four things this checkpoint is built to stop:

  * **A permissive parser.** Twelve of the thirty-two rows must be REFUSED.
    A parser that accepts everything passes twenty checks and fails twelve,
    which is why the refusals are counted as their own gate rather than
    folded into a pass rate.
  * **A refusal that says nothing.** Each rejection must name the capability.
    `expected token type 85, got 67 at pos 30` is a message about a
    tokeniser; this asserts the message is about the language.
  * **A second copy of the temporal predicate.** `AS OF` must agree with
    `Store.edges_as_of()` on `(src, rel, dst, first_seen, last_seen)` -- the
    columns, not just the triple, because CP-8 already found a divergence
    that a set comparison could not see.
  * **A query that runs but is never reachable.** The key is driven through
    the CLI, not through `run()`. A check that calls the helper passes with
    the command unwired from argparse, which has happened here before.

Run:
    python3 tests/test_cp10.py
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import cli                                        # noqa: E402
from homegraph.query import (Schema, compile_, parse,            # noqa: E402
                             resolve_named, tokenize)
from homegraph.store import Store                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD = os.path.join(HERE, "gold", "cp10-queries.tsv")

# The graph the answer key was derived from. Declared, never computed: the
# key and this table are two independent statements of the same fixture, and
# if they disagree the checkpoint says so rather than agreeing with itself.
GRAPH_NODES = [
    # node_key,               kind,      path,                subtype,     title
    ("/w/notes/alpha.md",     "file",    "/w/notes/alpha.md",     "note",      "Alpha"),
    ("/w/notes/beta.md",      "file",    "/w/notes/beta.md",      "note",      "Beta"),
    ("/w/raw/index.md",       "file",    "/w/raw/index.md",       "note",      "Index (raw)"),
    ("/w/summaries/index.md", "file",    "/w/summaries/index.md", "generated", "Index (summary)"),
    ("/w/img/plot.png",       "file",    "/w/img/plot.png",       "image",     "plot"),
    ("/w/docs/paper.pdf",     "file",    "/w/docs/paper.pdf",     "pdf",       "Paper"),
    # A section, carrying its file's path with a `#n` key -- the shape that
    # made one name resolve to fifteen candidates on the real corpus. Without
    # a section in the fixture, `NAMED` cannot be shown to answer about files
    # rather than about parts of them.
    ("/w/notes/alpha.md#0",   "section", "/w/notes/alpha.md",     "note",      "Alpha, part 1"),
]

GRAPH_EDGES = [
    # src,                 dst,                   rel,             method,       first,        last
    ("/w/notes/alpha.md", "/w/notes/beta.md",     "WIKILINKS_TO",  "exact",       "2026-01-01", "2026-03-01"),
    ("/w/notes/alpha.md", "/w/summaries/index.md", "WIKILINKS_TO", "path_prefix", "2026-01-01", "2026-03-01"),
    ("/w/notes/beta.md",  "/w/raw/index.md",      "WIKILINKS_TO",  "exact",       "2026-01-01", "2026-01-15"),
    ("/w/notes/alpha.md", "/w/img/plot.png",      "EMBEDS",        "exact",       "2026-02-01", "2026-03-01"),
    ("/w/notes/beta.md",  "/w/docs/paper.pdf",    "MENTIONS_PATH", "mention",     "2026-02-01", "2026-03-01"),
    ("/w/notes/alpha.md", "/w/docs/paper.pdf",    "MENTIONS_PATH", "mention",     "2026-01-01", "2026-03-01"),
]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def build(db):
    with Store(db, model="m3") as s:
        for key, kind, path, subtype, title in GRAPH_NODES:
            s.upsert_node(key, kind=kind, subtype=subtype, path=path,
                          title=title, as_of="2026-01-01")
        for src, dst, rel, method, first, last in GRAPH_EDGES:
            s.upsert_edge(src, dst, rel, as_of=first, method=method)
            s.db.execute("UPDATE edges SET first_seen=?, last_seen=? "
                         "WHERE src=(SELECT id FROM nodes WHERE node_key=?) "
                         "AND dst=(SELECT id FROM nodes WHERE node_key=?) "
                         "AND rel=?", (first, last, src, dst, rel))
        s.commit()


def read_gold():
    rows = []
    with open(GOLD, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            qid, query, expected = line.split("\t")
            rows.append((qid, query, expected))
    return rows


def via_cli(db, query):
    """(exit code, stdout, stderr) from the command a user runs."""
    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = cli.main(["query", db, query])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:                                # noqa: BLE001
        # Reported, not raised. CP-0's rule: a call site that lets an
        # exception escape kills the suite before any gate can say no, and
        # the mutation harness scores that as detected-only-by-a-crash, the
        # weakest signal there is. Two mutations landed here on the first
        # run -- a dropped whitelist and an optional RETURN both reached
        # SQLite and raised.
        code, err = 99, "raised: %r" % exc
        sys.stdout, sys.stderr = old_out, old_err
        return code, out.getvalue(), err
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, out.getvalue(), err.getvalue()


ROW_COUNT = re.compile(r"^\d+ row\(s\)")


def parse_rows(stdout):
    """The data rows the CLI printed, as the key writes them.

    Bounded by the count line rather than by `lines[1:-1]`. The slice version
    silently swallowed the count line whenever a PARTIAL warning followed it,
    so every derived-edge query in the key compared one row too many -- a
    test-side bug that read exactly like a parser bug.
    """
    lines = [ln for ln in stdout.split("\n") if ln.strip()]
    body = []
    for ln in lines[1:]:
        if ROW_COUNT.match(ln.strip()):
            break
        body.append(ln)
    return [",".join(p for p in ln.split("  ") if p) for ln in body]


# -- 1. the fixture and the key agree about the fixture --------------------

def t_key_matches_the_declared_graph(db):
    """Two independent statements of the same corpus.

    The key's header restates the graph in prose. If the table above is
    edited and the key is not, every answer below shifts together and the
    checkpoint stays green while measuring a different fixture.
    """
    with Store(db) as s:
        nodes = s.node_count()
        edges = s.edge_count()
        derived = s.db.execute(
            "SELECT COUNT(*) n FROM edges WHERE confidence < 1.0").fetchone()["n"]
    check("the built graph matches the declared node count",
          nodes == len(GRAPH_NODES), "%d built, %d declared"
          % (nodes, len(GRAPH_NODES)))
    check("the built graph matches the declared edge count",
          edges == len(GRAPH_EDGES), "%d built, %d declared"
          % (edges, len(GRAPH_EDGES)))
    check("the declared derived edges survived the build",
          derived == sum(1 for e in GRAPH_EDGES if e[3] != "exact"),
          "%d derived" % derived)


# -- 2. the answer key, through the CLI ------------------------------------

def t_gold_set(db):
    gold = read_gold()
    accepted = [g for g in gold if not g[2].startswith("REJECT")]
    rejected = [g for g in gold if g[2].startswith("REJECT")]
    check("the key has enough accepted queries to mean something",
          len(accepted) >= 20, "%d accepted" % len(accepted))
    check("the key has enough refusals to mean something",
          len(rejected) >= 10, "%d refusals" % len(rejected))

    wrong = []
    for qid, query, expected in accepted:
        code, out, err = via_cli(db, query)
        if expected.startswith("AMBIGUOUS:"):
            want = int(expected.split(":")[1])
            got = len([ln for ln in err.split("\n") if ln.startswith("  ")])
            if code != 2 or "AMBIGUOUS" not in err or got != want:
                wrong.append("%s: expected ambiguous/%d, got exit %d, %d "
                             "candidate(s)" % (qid, want, code, got))
            continue
        if code != 0:
            wrong.append("%s: exit %d  %s" % (qid, code, err.strip()[:70]))
            continue
        got_rows = parse_rows(out)
        want_rows = [] if expected == "EMPTY" else expected.split(";")
        if got_rows != want_rows:
            wrong.append("%s: got %s, key says %s" % (qid, got_rows, want_rows))
    check("every accepted query matches the answer key", not wrong,
          "%d wrong%s" % (len(wrong), "" if not wrong else "  " + wrong[0]))

    # An empty answer must be distinguishable from a query that did not run.
    # `all()` over an empty list is True; so is "no rows differed".
    empties = [q for q in accepted if q[2] == "EMPTY"]
    check("the key exercises the empty answer", len(empties) >= 3,
          "%d EMPTY row(s) in the key" % len(empties))


def t_refusals_name_what_is_missing(db):
    """The capability is part of the key, not just the refusal.

    Counting refusals is not enough: a parser that rejects everything for the
    same reason passes that count. Six mutations survived the first version of
    this gate by degrading the *message* while keeping the refusal -- an `OR`
    that falls through the keyword table is still refused, but as "every query
    must RETURN something", which sends the reader to look for a missing
    clause in a query that has one.
    """
    gold = [g for g in read_gold() if g[2].startswith("REJECT")]
    accepted_anyway, wrong_reason = [], []
    for qid, query, expected in gold:
        want = expected.split(":", 1)[1]
        code, out, err = via_cli(db, query)
        if code != 2:
            accepted_anyway.append("%s (exit %d)" % (qid, code))
        elif "not supported: %s" % want not in err:
            wrong_reason.append(
                "%s: wanted %r, said %r" % (qid, want,
                                            err.strip().splitlines()[-1][:44]))
    check("every out-of-grammar query is refused", not accepted_anyway,
          "%d accepted: %s" % (len(accepted_anyway), accepted_anyway[:3]))
    check("every refusal names the capability the key expects",
          not wrong_reason,
          "%d wrong: %s" % (len(wrong_reason), wrong_reason[:2]))


# -- 3. time is decided in one place ---------------------------------------

def t_as_of_agrees_with_edges_as_of(db):
    """Compared on the columns, not the triple.

    A regression guard rather than a discovery: `run()` filters on the ids
    `edges_as_of` reports, so there is no second predicate to disagree with
    today. The gate exists for the day someone inlines the dates into the
    SQL for speed, which is exactly the shape DECISIONS section 16 records
    and which agrees with itself until one copy is edited.
    """
    for when in ("2026-01-10", "2026-02-01", "2026-01-15", "2026-03-02"):
        code, out, _ = via_cli(
            db, "MATCH (a)-[e:WIKILINKS_TO]->(b) AS OF '%s' "
                "RETURN a.path, b.path, e.first_seen, e.last_seen" % when)
        got = {tuple(r.split(",")) for r in parse_rows(out)}
        with Store(db) as s:
            want = {(r["src_key"], r["dst_key"], r["first_seen"],
                     r["last_seen"])
                    for r in s.edges_as_of(when, "WIKILINKS_TO")}
        check("AS OF %s equals edges_as_of, columns included" % when,
              code == 0 and got == want,
              "%d vs %d%s" % (len(got), len(want),
                              "" if got == want else "  diff=%s"
                              % sorted(got ^ want)[:1]))

    # And the gate must be able to fail: two of those dates have to differ
    # from each other, or "equal on every date" is true of a filter that
    # never filters.
    counts = []
    for when in ("2026-01-10", "2026-02-01"):
        with Store(db) as s:
            counts.append(len(s.edges_as_of(when, "WIKILINKS_TO")))
    check("the dates chosen actually separate live from expired",
          counts[0] != counts[1], "%s live at the two dates" % counts)


# -- 4. ambiguity is refused, not resolved ---------------------------------

def t_ambiguity_is_refused(db):
    code, out, err = via_cli(
        db, "MATCH (a)-[e]->(b) WHERE b NAMED 'index' RETURN a.path")
    check("an ambiguous NAMED refuses with exit 2", code == 2,
          "exit %d" % code)
    check("the refusal lists both candidates",
          "/w/raw/index.md" in err and "/w/summaries/index.md" in err,
          err.strip().splitlines()[-1][:60] if err else "(silent)")
    check("an ambiguous query answers nothing", not parse_rows(out),
          "%d row(s) printed" % len(parse_rows(out)))

    # The negative control: an unambiguous name must answer, or "refuses"
    # is being tested against a resolver that refuses everything.
    code2, out2, _ = via_cli(
        db, "MATCH (a)-[e]->(b) WHERE a NAMED 'alpha' RETURN e.rel")
    check("an unambiguous NAMED answers", code2 == 0 and parse_rows(out2),
          "exit %d, %d row(s)" % (code2, len(parse_rows(out2))))


# -- 5. identifiers cannot become SQL --------------------------------------

def t_identifiers_are_whitelisted(db):
    attacks = [
        "MATCH (a)-[e]->(b) RETURN a.path' UNION SELECT 1 --",
        "MATCH (a:nosuch)-[e]->(b) RETURN a.path",
        "MATCH (a)-[e]->(b) RETURN a.\"path\"",
    ]
    survived = []
    for text in attacks:
        code, _, _ = via_cli(db, text)
        if code != 2:
            survived.append(text[:40])
    check("hostile identifiers are refused", not survived,
          "%d accepted: %s" % (len(survived), survived[:1]))

    with Store(db) as s:
        still = s.db.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
    check("the store is intact after the attempts",
          still == len(GRAPH_NODES), "%d nodes" % still)

    # Literals are parameters, so a value that looks like SQL is just a
    # value. This must ANSWER (with no rows), not refuse: refusing here
    # would mean the defence is a blocklist on content.
    code, out, _ = via_cli(
        db, "MATCH (a)-[e]->(b) WHERE a.path = "
            "'x; DROP TABLE nodes--' RETURN a.path")
    with Store(db) as s:
        alive = s.db.execute(
            "SELECT COUNT(*) n FROM nodes").fetchone()["n"]
    check("a literal that looks like SQL is treated as a value",
          code == 0 and not parse_rows(out) and alive == len(GRAPH_NODES),
          "exit %d, %d rows, %d nodes" % (code, len(parse_rows(out)), alive))


# -- 6. the three stages are separable -------------------------------------

def t_stages_are_independent(db):
    """Each stage is checked alone, so a parser bug and a SQL bug do not
    look the same from the outside."""
    toks = tokenize("MATCH (a:note)-[e:EMBEDS]->(b) RETURN a.path")
    check("the tokeniser keeps keywords apart from identifiers",
          [t.kind for t in toks[:2]] == ["kw", "punct"],
          "%s" % [(t.kind, t.text) for t in toks[:3]])

    q = parse(toks)
    check("the parser binds the pattern",
          (q.src.var, q.src.label, q.edge.var, q.edge.rel, q.dst.var)
          == ("a", "note", "e", "EMBEDS", "b"),
          "%s %s %s" % (q.src, q.edge, q.dst))

    q2 = parse(tokenize("MATCH (a:note)-[e:EMBEDS]->(b) "
                        "WHERE a.title = 'Alpha' RETURN a.path"))
    with Store(db) as s:
        sql, params, _ = compile_(q, Schema(s))
        sql2, params2, _ = compile_(q2, Schema(s))
    check("the compiler emits no literal into the SQL",
          "note" not in sql and "EMBEDS" not in sql and len(params) == 2,
          "%d param(s)" % len(params))
    # The WHERE value specifically: labels and relations were already bound
    # separately, so a query without a condition could not see a literal
    # being interpolated into the comparison.
    check("a WHERE value is bound, not interpolated",
          "Alpha" not in sql2 and "Alpha" in params2,
          "%d param(s), value in sql: %s" % (len(params2), "Alpha" in sql2))
    check("the compiler orders by the returned columns",
          sql.rstrip().endswith("ORDER BY s.path"), sql[-30:])

    with Store(db) as s:
        hits = resolve_named(s, "index")
        sectioned = resolve_named(s, "alpha")
    check("resolve_named finds both collisions", len(hits) == 2,
          "%d hit(s)" % len(hits))
    # A name resolves to the file, not to the file plus each of its sections.
    # Sections share their file's `path`, so a resolver that matches on path
    # alone reports one file as many candidates and refuses a question that
    # has exactly one answer.
    check("a name resolves to the file, not to its sections",
          len(sectioned) == 1
          and sectioned[0]["node_key"] == "/w/notes/alpha.md",
          "%d hit(s): %s" % (len(sectioned),
                             [h["node_key"] for h in sectioned]))
    # A wildcard in the name must not widen the match: the LIKE is a
    # prefilter and the decision is made on the basename.
    with Store(db) as s:
        wild = resolve_named(s, "%")
        broad = resolve_named(s, "w")
    check("a wildcard name matches nothing", not wild,
          "%d hit(s) for '%%'" % len(wild))
    # Every path here contains "w", so the LIKE prefilter returns all six.
    # No basename is "w", so the answer is none. A resolver that trusts the
    # prefilter returns all six and looks like it found something.
    check("the LIKE prefilter does not decide the match", not broad,
          "%d hit(s) for 'w'" % len(broad))


# -- 7. provenance reaches the query answer --------------------------------

def t_derived_rows_make_the_answer_partial(db):
    code, out, _ = via_cli(
        db, "MATCH (a)-[e:WIKILINKS_TO]->(b) WHERE e.method = 'path_prefix' "
            "RETURN a.path, b.path")
    check("a query returning a derived edge says partial",
          code == 0 and "PARTIAL" in out and "path_prefix" in out,
          out.strip().splitlines()[-1][:64] if out else "(silent)")

    code2, out2, _ = via_cli(
        db, "MATCH (a)-[e:EMBEDS]->(b) RETURN a.path, b.path")
    check("a query returning only stated edges does not",
          code2 == 0 and "PARTIAL" not in out2 and "complete" in out2,
          out2.strip().splitlines()[-1][:50] if out2 else "")


def main():
    tmp = tempfile.mkdtemp(prefix="cp10-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        db = os.path.join(tmp, "q.db")
        build(db)
        t_key_matches_the_declared_graph(db)
        t_gold_set(db)
        t_refusals_name_what_is_missing(db)
        t_as_of_agrees_with_edges_as_of(db)
        t_ambiguity_is_refused(db)
        t_identifiers_are_whitelisted(db)
        t_stages_are_independent(db)
        t_derived_rows_make_the_answer_partial(db)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_cp10():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
