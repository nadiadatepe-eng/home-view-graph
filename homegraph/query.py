#!/usr/bin/env python3
"""A closed query language over the graph. Grammar in DECISIONS.md section 25.

Three stages, each testable on its own: `tokenize` -> `parse` -> `compile_`.
They are separate because a single function that goes from text to rows can
only be tested end to end, and then a parser bug and a SQL bug look identical
from the outside.

**Everything outside the grammar is refused, and the refusal names what is
missing.** `OR`, `DISTINCT`, `OPTIONAL MATCH`, variable-length paths,
aggregation, reverse arrows and `LIKE` are each recognised on purpose so the
message can say "this language has no OR" rather than "unexpected token at
position 31". A subset without a documented edge is what this was written to
avoid: `codebase-memory-mcp` rejects an anti-pattern with `expected token type
85, got 67 at pos 30`, which tells the reader about its tokeniser and nothing
about the language.

**`AS OF` is not compiled into SQL at all.** It calls `Store.edges_as_of()`
and filters the result to the edge ids that function reports alive. Expressing
the date predicate here as well would put the temporal invariant in two layers
-- the mistake DECISIONS section 16 records and CP-8 has already caught once,
and one that hides perfectly because both copies agree until someone edits
one. Not writing the predicate twice is stronger than testing that two copies
match.

**Identifiers never reach SQL as text.** Property names, labels and relation
types are checked against whitelists read out of the store itself; literals
are bound parameters. There is no f-string containing user input in this file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# Recognised in order; longest first where prefixes overlap.
_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<string>'[^']*'|"[^"]*")
  | (?P<arrow_l>\]->)
  | (?P<arrow_r_bad><-\[)
  | (?P<edge_open>-\[)
  | (?P<number>-?\d+(?:\.\d+)?)
  | (?P<op>!=|<=|>=|=|<|>)
  | (?P<punct>[():.,\[\]*;])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
""", re.X)

# Named so the refusal can say which capability is absent rather than which
# character surprised the tokeniser.
UNSUPPORTED = {
    "OR": "this language has no OR; use one condition per query",
    "NOT": "this language has no NOT",
    "DISTINCT": "this language has no DISTINCT",
    "OPTIONAL": "this language has no OPTIONAL MATCH",
    "LIKE": "this language has no LIKE; use PREFIX or CONTAINS",
    "ORDER": "results are always ordered by the returned columns",
    "LIMIT": "this language has no LIMIT",
    "UNION": "this language has no UNION",
    "WITH": "this language has no WITH",
    "COUNT": "this language has no aggregation",
    "SUM": "this language has no aggregation",
    "AVG": "this language has no aggregation",
}

KEYWORDS = {"MATCH", "WHERE", "AND", "AS", "OF", "RETURN", "NAMED",
            "PREFIX", "CONTAINS"}

COMPARISONS = {"=", "!=", "<", "<=", ">", ">="}


class QueryError(Exception):
    """Refused. `missing` names the capability, for the message the user reads."""

    def __init__(self, message: str, missing: str | None = None) -> None:
        super().__init__(message)
        self.missing = missing


@dataclass
class Token:
    kind: str
    text: str
    pos: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(text):
        m = _TOKEN_RE.match(text, i)
        if m is None:
            raise QueryError("cannot read %r at position %d"
                             % (text[i], i), missing="valid syntax")
        i = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        value = m.group()
        if kind == "arrow_r_bad":
            raise QueryError(
                "edges are written left to right: (a)-[:REL]->(b)",
                missing="reverse arrows")
        if kind == "punct" and value == "*":
            raise QueryError("this language has no variable-length paths",
                             missing="variable-length paths")
        if kind == "punct" and value == ";":
            raise QueryError("one query per call; ';' is not a separator here",
                             missing="statement separators")
        if kind == "ident":
            upper = value.upper()
            if upper in UNSUPPORTED:
                raise QueryError(UNSUPPORTED[upper], missing=upper)
            if upper in KEYWORDS:
                kind = "kw"
                value = upper
        out.append(Token(kind, value, m.start()))
    return out


# -- AST -------------------------------------------------------------------

@dataclass
class NodePat:
    var: str | None
    label: str | None


@dataclass
class EdgePat:
    var: str | None
    rel: str | None


@dataclass
class Condition:
    var: str
    prop: str | None          # None means the NAMED form
    op: str
    value: Any


@dataclass
class Query:
    src: NodePat
    edge: EdgePat
    dst: NodePat
    where: list[Condition] = field(default_factory=list)
    as_of: str | None = None
    returns: list[tuple[str, str]] = field(default_factory=list)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise QueryError("query ends early", missing="a complete query")
        self.i += 1
        return tok

    def expect(self, kind: str, text: str | None = None) -> Token:
        tok = self.next()
        if tok.kind != kind or (text is not None and tok.text != text):
            raise QueryError("expected %s at position %d, found %r"
                             % (text or kind, tok.pos, tok.text),
                             missing="valid syntax")
        return tok

    def at(self, kind: str, text: str | None = None) -> bool:
        tok = self.peek()
        return (tok is not None and tok.kind == kind
                and (text is None or tok.text == text))

    # -- productions ------------------------------------------------------

    def node(self) -> NodePat:
        self.expect("punct", "(")
        var = label = None
        if self.at("ident"):
            var = self.next().text
        if self.at("punct", ":"):
            self.next()
            label = self.expect("ident").text
        self.expect("punct", ")")
        return NodePat(var, label)

    def edge(self) -> EdgePat:
        self.expect("edge_open")
        var = rel = None
        if self.at("ident"):
            var = self.next().text
        if self.at("punct", ":"):
            self.next()
            rel = self.expect("ident").text
        self.expect("arrow_l")
        return EdgePat(var, rel)

    def condition(self) -> Condition:
        var = self.expect("ident").text
        if self.at("kw", "NAMED"):
            self.next()
            return Condition(var, None, "NAMED", _unquote(self.expect("string")))
        self.expect("punct", ".")
        prop = self.expect("ident").text
        tok = self.next()
        if tok.kind == "op" and tok.text in COMPARISONS:
            op = tok.text
        elif tok.kind == "kw" and tok.text in ("PREFIX", "CONTAINS"):
            op = tok.text
        else:
            raise QueryError("%r is not a comparison in this language "
                             "(=, !=, <, <=, >, >=, PREFIX, CONTAINS)"
                             % tok.text, missing="operator %s" % tok.text)
        val_tok = self.next()
        if val_tok.kind == "string":
            value: Any = _unquote(val_tok)
        elif val_tok.kind == "number":
            value = float(val_tok.text) if "." in val_tok.text \
                else int(val_tok.text)
        else:
            raise QueryError("expected a value after %s, found %r"
                             % (op, val_tok.text), missing="valid syntax")
        return Condition(var, prop, op, value)

    def parse(self) -> Query:
        self.expect("kw", "MATCH")
        src = self.node()
        edge = self.edge()
        dst = self.node()
        q = Query(src, edge, dst)

        if self.at("kw", "WHERE"):
            self.next()
            q.where.append(self.condition())
            while self.at("kw", "AND"):
                self.next()
                q.where.append(self.condition())

        if self.at("kw", "AS"):
            self.next()
            self.expect("kw", "OF")
            tok = self.next()
            if tok.kind == "string":
                q.as_of = _unquote(tok)
            else:
                raise QueryError("AS OF takes a quoted ISO date",
                                 missing="valid syntax")

        if not self.at("kw", "RETURN"):
            raise QueryError("every query must RETURN something",
                             missing="RETURN clause")
        self.next()
        while True:
            var = self.expect("ident").text
            self.expect("punct", ".")
            prop = self.expect("ident").text
            q.returns.append((var, prop))
            if self.at("punct", ","):
                self.next()
                continue
            break

        trailing = self.peek()
        if trailing is not None:
            raise QueryError("trailing input from position %d: %r"
                             % (trailing.pos, trailing.text),
                             missing="valid syntax")
        return q


def parse(tokens: list[Token]) -> Query:
    return _Parser(tokens).parse()


def _unquote(tok: Token) -> str:
    return tok.text[1:-1]


# -- compilation -----------------------------------------------------------

@dataclass
class Result:
    status: str                       # complete | partial | ambiguous
    columns: list[str]
    rows: list[tuple[Any, ...]]
    warnings: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


class Schema:
    """The whitelists, read out of the store rather than written down here.

    A hand-maintained list of column names is a second copy of the schema, and
    the whole point of checking identifiers against a whitelist is that the
    whitelist is right.
    """

    def __init__(self, store: Any) -> None:
        self.node_props = {r["name"] for r in
                           store.db.execute("PRAGMA table_info(nodes)")}
        self.edge_props = {r["name"] for r in
                           store.db.execute("PRAGMA table_info(edges)")}
        self.labels = {r["subtype"] for r in store.db.execute(
            "SELECT DISTINCT subtype FROM nodes WHERE subtype IS NOT NULL")}
        self.rels = {r["rel"] for r in
                     store.db.execute("SELECT DISTINCT rel FROM edges")}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_named(store: Any, name: str) -> list[Any]:
    """Nodes whose basename, with or without extension, is `name`.

    The LIKE is a prefilter only; the match is decided in Python so that a
    name containing a wildcard cannot widen it. More than one hit is an
    ambiguity the caller refuses -- it does not pick one.
    """
    like = "%" + _escape_like(name) + "%"
    # `node_key = path` is the node that IS the file. Sections carry their
    # file's path with a `#n` key, so without this a name matched the file
    # and every one of its sections: on the real corpus one name came back as
    # fifteen candidates where the answer was two files. Nine green
    # checkpoints did not see it, because the fixture had no sections -- the
    # fourth defect this project has found by running on real data after the
    # gates went green.
    #
    # Tags and broken-link stubs have no path at all and are excluded by the
    # same condition, which is right: they have no filename to be named by.
    rows = store.db.execute(
        "SELECT id, node_key, path FROM nodes "
        "WHERE path IS NOT NULL AND node_key = path "
        "AND path LIKE ? ESCAPE '\\' ORDER BY node_key", (like,)).fetchall()
    hits = []
    for row in rows:
        base = os.path.basename(row["path"])
        if base == name or os.path.splitext(base)[0] == name:
            hits.append(row)
    return hits


def compile_(q: Query, schema: Schema,
             named_ids: dict[str, int] | None = None
             ) -> tuple[str, list[Any], dict[str, str]]:
    """(sql, params, alias map). Raises QueryError for anything unknown.

    Returns the alias each variable was bound to so the caller can map
    `RETURN a.path` onto a column without the compiler emitting the variable
    name into SQL.
    """
    alias = {}
    for pat, side in ((q.src, "s"), (q.dst, "d")):
        if pat.var:
            alias[pat.var] = side
    if q.edge.var:
        alias[q.edge.var] = "e"

    def column(var: str, prop: str) -> str:
        tag = alias.get(var)
        if tag is None:
            raise QueryError("unknown variable %r; the query binds %s"
                             % (var, ", ".join(sorted(alias)) or "nothing"),
                             missing="variable %s" % var)
        allowed = schema.edge_props if tag == "e" else schema.node_props
        if prop not in allowed:
            raise QueryError(
                "%s has no property %r; it has %s"
                % ("edges" if tag == "e" else "nodes", prop,
                   ", ".join(sorted(allowed))), missing="property %s" % prop)
        # `tag` and `prop` are both from closed sets checked above, so this
        # is the one place a name is formatted into SQL and it cannot carry
        # user text.
        return "%s.%s" % (tag, prop)

    for pat in (q.src, q.dst):
        if pat.label and pat.label not in schema.labels:
            raise QueryError("no label %r in this store; it has %s"
                             % (pat.label, ", ".join(sorted(schema.labels))),
                             missing="label %s" % pat.label)
    if q.edge.rel and q.edge.rel not in schema.rels:
        raise QueryError("no relation %r in this store; it has %s"
                         % (q.edge.rel, ", ".join(sorted(schema.rels))),
                         missing="relation %s" % q.edge.rel)

    where: list[str] = []
    params: list[Any] = []
    if q.src.label:
        where.append("s.subtype = ?")
        params.append(q.src.label)
    if q.dst.label:
        where.append("d.subtype = ?")
        params.append(q.dst.label)
    if q.edge.rel:
        where.append("e.rel = ?")
        params.append(q.edge.rel)

    for cond in q.where:
        if cond.prop is None:
            continue                       # NAMED is resolved by the caller
        col = column(cond.var, cond.prop)
        if cond.op == "PREFIX":
            where.append("%s LIKE ? ESCAPE '\\'" % col)
            params.append(_escape_like(str(cond.value)) + "%")
        elif cond.op == "CONTAINS":
            where.append("%s LIKE ? ESCAPE '\\'" % col)
            params.append("%" + _escape_like(str(cond.value)) + "%")
        else:
            where.append("%s %s ?" % (col, cond.op))
            params.append(cond.value)

    # Resolved NAMED bindings become an id equality, built here with the rest
    # of the WHERE rather than spliced into finished SQL. The first version
    # did the splice, which meant the shape of the generated string was
    # load-bearing -- one more clause and it would have attached the
    # condition to the wrong place, silently and with plausible results.
    for var, nid in (named_ids or {}).items():
        bound = alias.get(var)
        if bound is None:
            raise QueryError("unknown variable %r" % var,
                             missing="variable %s" % var)
        where.append("%s.id = ?" % bound)
        params.append(nid)

    select = [column(v, p) for v, p in q.returns]
    sql = ("SELECT " + ", ".join(select) + ", e.id "
           "FROM edges e JOIN nodes s ON s.id = e.src "
           "JOIN nodes d ON d.id = e.dst")
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Ordering is part of the language: an answer key cannot be compared
    # against a result whose order is undefined.
    sql += " ORDER BY " + ", ".join(select)
    return sql, params, alias


def run(store: Any, text: str) -> Result:
    """Text in, `Result` out. The only entry point callers need."""
    from .store import provenance_note

    q = parse(tokenize(text))
    schema = Schema(store)

    # NAMED first: an ambiguity is refused before any rows are read, so the
    # answer is never a subset of a question that could not be answered.
    named_ids: dict[str, int] = {}
    empty = False
    for cond in q.where:
        if cond.prop is not None:
            continue
        hits = resolve_named(store, str(cond.value))
        if len(hits) > 1:
            return Result("ambiguous", [], [],
                          warnings=["%r names %d files; say which one"
                                    % (cond.value, len(hits))],
                          candidates=[h["node_key"] for h in hits])
        if not hits:
            empty = True
        else:
            named_ids[cond.var] = hits[0]["id"]

    sql, params, _ = compile_(q, schema, named_ids)
    columns = ["%s.%s" % (v, p) for v, p in q.returns]
    if empty:
        # A NAMED that matched nothing is an empty answer, not an error: the
        # question was well formed and the store has no such file.
        return Result("complete", columns, [])

    rows = store.db.execute(sql, params).fetchall()

    if q.as_of is not None:
        # The one place time is decided, and it is not decided here. Anything
        # that re-expressed `first_seen <= d AND last_seen >= d` in the SQL
        # above would be a second opinion about what "alive on a date" means.
        alive = {r["id"] for r in store.edges_as_of(q.as_of, q.edge.rel)}
        rows = [r for r in rows if r["id"] in alive]

    ids = [r["id"] for r in rows]
    warnings = []
    if ids:
        marks = store.db.execute(
            "SELECT method, confidence FROM edges WHERE id IN (%s)"
            % ",".join("?" * len(ids)), ids).fetchall()
        note = provenance_note(marks)
        if note:
            warnings.append(note)

    out = [tuple(r[i] for i in range(len(columns))) for r in rows]
    return Result("partial" if warnings else "complete", columns, out,
                  warnings=warnings)
