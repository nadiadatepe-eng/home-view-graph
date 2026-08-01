#!/usr/bin/env python3
"""CP-H5 -- files an author committed together become one mesh edge.

Mechanical cohort provenance: no embeddings, no text, only what the history
says. `TEMPORAL_COHORT` already knows that two things moved on the same day;
this knows that someone put them in one commit, which is a statement rather
than a coincidence.

**The answer key was written first**, before a line of H5 code:
`tests/gold/FASIT-h5.md` fixes a five-commit repository, all seven co-occurring
pairs with their shared counts, and the single edge that survives the rules.
The expected pairs below are READ OUT OF THAT FILE.

The rule most likely to be skipped is R3 -- an edge only when BOTH endpoints
are already nodes -- because skipping it produces MORE edges and every count
still looks healthy. The fixture is built so that skipping it is loud: the two
pairs it would wrongly add are over the threshold at a HIGHER ratio than the
one real edge.

Run:
    python3 tests/test_h5.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from homegraph.mesh import Mesh                             # noqa: E402
from homegraph.models import m3_build                       # noqa: E402
from homegraph.store import EDGE_METHODS, Store             # noqa: E402

results, check = reporter(58)

FASIT = os.path.join(HERE, "gold", "FASIT-h5.md")

# The commits, in the order the key states them. `notes.txt` is deliberately
# NOT a node: it is in the repository and not in the graph.
COMMITS = [
    ("alpha.md", "beta.md", "notes.txt"),
    ("alpha.md", "beta.md", "notes.txt"),
    ("alpha.md", "beta.md", "gamma.md", "notes.txt"),
    ("alpha.md", "gamma.md"),
    ("beta.md", "delta.md"),
]
NODES = ("alpha.md", "beta.md", "gamma.md", "delta.md")


def expected_pairs():
    """`{(a, b): shared}` from the key's table, and nothing computed here.

    The row for `gamma.md`/`delta.md` carries em-dashes rather than numbers --
    they never appear in a commit together -- and is skipped by the digit
    match rather than by naming it, so adding another such row needs no edit.
    """
    with open(FASIT, encoding="utf-8") as fh:
        head, _, tail = fh.read().partition("\n## Addendum")
    return _table(head), _table(tail)


def _table(text):
    """The `| a | b | shared | union |` rows of one section.

    Split on the addendum heading, because both tables start numbering their
    own rows and a single pass lets the second silently join the first. That is
    the same trap CP-H4's key sprang on its gate's first run, and it sprang
    here too -- eleven pairs where seven were expected.
    """
    out = {}
    for a, b, shared, union in re.findall(
            r"^\| `([^`]+)` \| `([^`]+)` \| \*?\*?(\d+)\*?\*? \| (\d+) \|",
            text, re.M):
        # The union is read too. It was not, until 2026-08-01, and a needle
        # that returned `len(touched[a])` instead of the union passed all
        # fourteen checks -- the ratios the key prints, and the 0.348 and 0.800
        # in its measurements, were numbers nothing could tell from arbitrary.
        out[(a, b)] = (int(shared), int(union))
    return out


def build_repo():
    work = tempfile.mkdtemp(prefix="h5-repo-")
    def run(*a):
        subprocess.run(["git", "-C", work, *a], check=True,
                       capture_output=True)

    run("init", "-q", ".")
    run("config", "user.email", "gate@example.invalid")
    run("config", "user.name", "gate")
    for i, files in enumerate(COMMITS):
        for name in files:
            path = os.path.join(work, name)
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as fh:
                # A heading in each markdown file, so M3 writes section nodes
                # for it. The sections change no count in the key -- co-change
                # is over commits, not content -- but without them the store
                # holds no node that carries a file's path WITHOUT being that
                # file, and the mutation where section nodes shadow their file
                # in the path index has nothing to shadow with. Measured: that
                # mutation survived until these three words existed.
                if new and name.endswith(".md"):
                    fh.write("# %s\n\n" % name)
                fh.write("rev %d\n" % i)
        run("add", "-A")
        run("commit", "-q", "-m", "c%d" % i)
    return work


def t_key_and_pairs(repo):
    want, _addendum = expected_pairs()
    check("key: the answer key names seven counted pairs", len(want) == 7,
          "%d found in %s" % (len(want), os.path.relpath(FASIT, ROOT)))
    got = Mesh.co_change_pairs(repo)
    wrong = [(p, got.get(p), v) for p, v in want.items() if got.get(p) != v]
    check("pairs: every shared count and union matches the key", not wrong,
          "; ".join("%s got %r want %r" % w for w in wrong))
    # Seven counted pairs and no eighth. Without this an implementation that
    # finds the seven right ones AND invents extras -- a rename's brace form,
    # a directory, a quoted name -- passes every other check here.
    check("pairs: and there is no eighth pair", len(got) == len(want),
          "%d found, %d in the key: extra=%s"
          % (len(got), len(want), sorted(set(got) - set(want))))
    # Two files that never share a commit must not appear at all. An
    # implementation that enumerates every pair and filters afterwards is
    # indistinguishable from one that enumerates co-occurrences until it is
    # asked for a pair that has none.
    check("pairs: two files never committed together are absent",
          ("delta.md", "gamma.md") not in got
          and ("gamma.md", "delta.md") not in got,
          "%r" % [p for p in got if "delta.md" in p])
    return got


ADDENDUM = [
    ("sub/note.md", "h\u00f8st.md"),
    ("sub/note.md", "h\u00f8st.md"),
    ("sub/note.md", "h\u00f8st.md", "old.md"),
]


def build_addendum_repo():
    """The three inputs the worked example cannot contain -- see the key.

    A file in a subdirectory, a name outside ASCII, and a rename. Every file in
    the worked example sits at the root with an ASCII name and is never
    renamed, so four realistic mistakes passed the whole gate until this
    existed: a `basename` join, a missing `-z`, and two ways of misreading a
    rename.
    """
    work = tempfile.mkdtemp(prefix="h5-add-")

    def run(*a):
        subprocess.run(["git", "-C", work, *a], check=True,
                       capture_output=True)

    run("init", "-q", ".")
    run("config", "user.email", "gate@example.invalid")
    run("config", "user.name", "gate")
    for files in ADDENDUM:
        for name in files:
            path = os.path.join(work, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as fh:
                if new:
                    fh.write("# %s\n\n" % os.path.basename(name))
                fh.write("x\n")
        run("add", "-A")
        run("commit", "-q", "-m", "c")
    run("mv", "old.md", "new.md")
    with open(os.path.join(work, "h\u00f8st.md"), "a",
              encoding="utf-8") as fh:
        fh.write("y\n")
    run("add", "-A")
    run("commit", "-q", "-m", "c3")
    return work


def t_addendum():
    _main, want = expected_pairs()
    repo = build_addendum_repo()
    got = Mesh.co_change_pairs(repo)
    check("addendum: the key names four pairs for it", len(want) == 4,
          "%d found" % len(want))
    wrong = [(p, got.get(p), v) for p, v in want.items() if got.get(p) != v]
    check("addendum: a subdirectory path and a non-ASCII name pair up",
          not wrong, "; ".join("%s got %r want %r" % w for w in wrong))
    # The rename: both halves present under their own names, and no third
    # name. Without `-z` git prints `old.md => new.md` as ONE field, which is
    # a path that exists nowhere and can never be an endpoint.
    names = {n for p in got for n in p}
    check("addendum: a rename yields the new name, not a brace form",
          "new.md" in names and not any("=>" in n for n in names),
          "%r" % sorted(names))
    check("addendum: and exactly four pairs, so no path was invented",
          len(got) == 4, "%d: %r" % (len(got), sorted(got)))
    # The edge itself, through a build: the subdirectory join is only exercised
    # end to end here.
    work = tempfile.mkdtemp(prefix="h5-add-mesh-")
    m3, mesh_db = os.path.join(work, "m3.db"), os.path.join(work, "mesh.db")
    with Store(m3, model="m3") as store:
        m3_build.build(store, [os.path.join(repo, n) for n in
                               ("sub/note.md", "h\u00f8st.md", "new.md")],
                       "2026-08-01")
    Mesh({"m3": m3}, mesh_db=mesh_db).build_edges("2026-08-01",
                                                 repo_root=repo)
    rows = edges(mesh_db)
    check("addendum: the subdirectory pair becomes an edge", len(rows) == 1,
          "%d: %r" % (len(rows), rows))

    # A repository root that is NOT the top level. `git log` writes paths
    # relative to the top, so joining them onto the directory the caller named
    # gives paths that do not exist -- zero edges, no error, and a report
    # saying the repository was read.
    deep = Mesh({"m3": m3}, mesh_db=os.path.join(work, "m2.db")).build_edges(
        "2026-08-01", repo_root=os.path.join(repo, "sub"))
    check("addendum: a subdirectory as --repo-root finds the same edges",
          deep["edges"].get("CO_CHANGED_WITH") == 1,
          "%r · reported %r" % (deep["edges"].get("CO_CHANGED_WITH"),
                                deep.get("co_change")))


def t_method_is_registered():
    check("method: `co-change` is a registered edge method",
          "co-change" in EDGE_METHODS, ", ".join(sorted(EDGE_METHODS)))
    # Placed between the two it is argued against in `store.py`. Asserting the
    # ORDER rather than the number: 0.45 is a judgement, but "stronger than a
    # same-day coincidence, weaker than one file naming the other" is the
    # claim, and it is the claim that must not silently invert.
    check("method: it sits above cohort and below mention",
          EDGE_METHODS.get("cohort", 1) < EDGE_METHODS.get("co-change", 0)
          < EDGE_METHODS.get("mention", 0),
          "cohort=%r co-change=%r mention=%r"
          % (EDGE_METHODS.get("cohort"), EDGE_METHODS.get("co-change"),
             EDGE_METHODS.get("mention")))


def build_mesh(repo, with_repo=True):
    work = tempfile.mkdtemp(prefix="h5-mesh-")
    m3, mesh_db = os.path.join(work, "m3.db"), os.path.join(work, "mesh.db")
    with Store(m3, model="m3") as store:
        m3_build.build(store, [os.path.join(repo, n) for n in NODES],
                       "2026-08-01")
    report = Mesh({"m3": m3}, mesh_db=mesh_db).build_edges(
        "2026-08-01", repo_root=repo if with_repo else None)
    return mesh_db, report


def edges(mesh_db):
    with Store(mesh_db) as mesh:
        return [(os.path.basename(r["a"]), os.path.basename(r["b"]),
                 r["method"], r["confidence"])
                for r in mesh.db.execute(
                    "SELECT s.node_key a, d.node_key b, e.method, e.confidence "
                    "FROM edges e JOIN nodes s ON s.id = e.src "
                    "JOIN nodes d ON d.id = e.dst "
                    "WHERE e.rel = 'CO_CHANGED_WITH' ORDER BY a, b")]


def t_the_one_edge(repo):
    mesh_db, report = build_mesh(repo)
    rows = edges(mesh_db)
    check("edge: exactly one edge is written", len(rows) == 1,
          "%d: %s" % (len(rows), [(a, b) for a, b, _m, _c in rows]))
    check("edge: it is alpha.md -- beta.md",
          len(rows) == 1 and {rows[0][0], rows[0][1]} == {"alpha.md",
                                                          "beta.md"},
          "%r" % (rows[:1],))
    check("edge: carrying the co-change method and its confidence",
          len(rows) == 1 and rows[0][2] == "co-change"
          and rows[0][3] == EDGE_METHODS["co-change"],
          "%r" % (rows[0][2:] if rows else None,))
    check("edge: and the report names the repository it read",
          report.get("co_change") == os.path.abspath(repo),
          repr(report.get("co_change")))
    return mesh_db


def t_threshold_and_membership(mesh_db):
    """The two rules the fixture exists to separate.

    `alpha.md`/`gamma.md` sits at exactly 2 and is the only input that tells
    `>= 3` from `>= 2`. `notes.txt` is over the threshold twice, at a higher
    ratio than the one real edge, and is not a node.
    """
    pairs = {frozenset((a, b)) for a, b, _m, _c in edges(mesh_db)}
    check("threshold: a pair sharing exactly 2 commits gets no edge",
          frozenset(("alpha.md", "gamma.md")) not in pairs,
          "%r" % [sorted(p) for p in pairs])
    check("membership: a file over the threshold but absent from the graph "
          "gets no edge",
          not any("notes.txt" in p for p in pairs),
          "%r" % [sorted(p) for p in pairs if "notes.txt" in p])


def t_stored_once(mesh_db):
    """A symmetric relation is one row, not two."""
    with Store(mesh_db) as mesh:
        n = mesh.db.execute(
            "SELECT COUNT(*) c FROM edges WHERE rel='CO_CHANGED_WITH'"
        ).fetchone()["c"]
    check("symmetry: the pair is stored once, not in both directions", n == 1,
          "%d row(s)" % n)


def t_absent_is_not_zero(repo):
    """"No repository was given" and "no edges were found" are different.

    `code_inventory` says `absent` for the same reason, and it says it because
    reporting 0 for a computation that never ran reads as a measurement.
    """
    _mesh_db, report = build_mesh(repo, with_repo=False)
    check("report: with no repository the report says absent, not zero",
          report.get("co_change") == "absent", repr(report.get("co_change")))
    check("report: and no co-change edge is written",
          "CO_CHANGED_WITH" not in report.get("edges", {}),
          repr(report.get("edges")))


def main() -> int:
    repo = build_repo()
    t_key_and_pairs(repo)
    t_method_is_registered()
    t_addendum()
    mesh_db = t_the_one_edge(repo)
    t_threshold_and_membership(mesh_db)
    t_stored_once(mesh_db)
    t_absent_is_not_zero(repo)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h5():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
