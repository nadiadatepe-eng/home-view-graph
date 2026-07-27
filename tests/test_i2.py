#!/usr/bin/env python3
"""CP-I2 -- a vector is a claim about text, and it expires when the text does.

Three defects, found by using CP-I1 rather than by reviewing it, and they are
one defect seen from three sides:

  * **`update` left a stale vector behind.** `forget(keep_self=True)` deletes a
    changed file's derived nodes and outbound edges, because those describe
    content that is gone -- but its own node survives, and so did its
    embedding. A file edited from "retrieval search ranking" to "onion onion
    onion" kept the vector pointing at `retrieval` and went on answering for a
    query it no longer contains. A MISSING vector makes a node unfindable,
    which someone notices; a STALE one makes it findable for the wrong thing,
    which nobody does.
  * **Nothing reported partial coverage.** `fts_stale` has always existed;
    there was no equivalent for vectors. On the author's own store an `update`
    added 1173 nodes, `vector_search` still ran because the namespace was not
    empty, and the search reported `_out_mode=hybrid` over 82% of the corpus
    with nothing marking it partial.
  * **`embed` re-embedded the whole store every time**, which is what made the
    first two hurt: closing a 1320-node gap cost twenty minutes, so it did not
    get closed.

**The order is load-bearing and the gates below assert it.** Incremental embed
is only safe because `forget` now clears the vector of a rebuilt node. Without
that, a changed file would keep its old vector, count as covered, and be
skipped forever -- the cheap fix would have made the expensive bug permanent.
Neither change is correct alone, which is why they ship as one checkpoint.

Run:
    python3 tests/test_i2.py
"""
from __future__ import annotations

import array
import json
import os
import shutil
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import update as up                                 # noqa: E402
from homegraph.models.m3_build import build as m3_build            # noqa: E402
from homegraph.providers import static_embed as se                 # noqa: E402
from homegraph.search import vector_search                          # noqa: E402
from homegraph.store import Store                                   # noqa: E402

results, check = reporter(58)


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="i2-", dir=os.path.expanduser("~/.homegraph"))


# Two orthogonal axes, so "the vector still points at the OLD text" is a
# statement anyone can check by eye: X is what the file said before, Y is what
# it says after, and they share nothing.
_DIM = 2
_MODEL = "syn-i2"
_VOCAB = {"retrieval": [1.0, 0.0], "search": [1.0, 0.0], "ranking": [1.0, 0.0],
          "onion": [0.0, 1.0], "shallot": [0.0, 1.0], "garlic": [0.0, 1.0]}
_BEFORE = "retrieval search ranking\n"          # -> [1, 0]
_AFTER = "onion shallot garlic\n"               # -> [0, 1]


class _Cfg:
    """The minimum `update` reads. A real UserConfig would drag in a config file."""
    roles: dict = {}
    own_owners: tuple = ()
    generated_dirs: tuple = ()
    embeddings = None

    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, "config.toml")


def _matrix(d: str, model: str = _MODEL) -> str:
    path = os.path.join(d, "m.json")
    toks = list(_VOCAB)
    json.dump({"provider": "static", "model": model, "dim": _DIM,
               "tokens": toks, "matrix": [_VOCAB[t] for t in toks]},
              open(path, "w"))
    return path


def _build_and_embed(d: str, text: str = _BEFORE) -> tuple[str, str, object]:
    """One markdown file, built and fully embedded. Returns (db, path, embedder)."""
    p = os.path.join(d, "a.md")
    open(p, "w").write(text)
    db = os.path.join(d, "m3.db")
    with Store(db, model="m3") as s:
        m3_build(s, [p], "2026-07-24")
        s.rebuild_fts()
    emb = se.load(_matrix(d))
    prov, model, dim = emb.namespace
    with Store(db, model="m3") as s:
        s.begin_immediate()
        for r in s.db.execute("SELECT id, title, body FROM nodes").fetchall():
            t = " ".join(x for x in (r["title"], r["body"]) if x).strip()
            if t:
                v = emb.embed(t)
                if any(v):
                    s.upsert_embedding(r["id"], prov, model, dim, v)
    return db, p, emb


def _vector(db: str, node_key: str) -> list[float] | None:
    with Store(db) as s:
        row = s.db.execute(
            "SELECT e.vec FROM embeddings e JOIN nodes n ON n.id = e.node_id "
            "WHERE n.node_key = ?", (node_key,)).fetchone()
    if row is None:
        return None
    a = array.array("f")
    a.frombytes(row["vec"])
    return list(a)


def _update(db: str, d: str, paths: list[str], as_of: str = "2026-07-25"):
    with Store(db, model="m3") as s:
        s.begin_immediate()
        return up.update(s, "m3", paths, as_of, _Cfg(d))


# -- gates ------------------------------------------------------------------


def t_a_changed_file_loses_its_vector():
    """The load-bearing one. Reproduced before it was fixed."""
    d = _tmp()
    try:
        db, p, _ = _build_and_embed(d)
        before = _vector(db, p)
        check("the file starts with a vector pointing at its text",
              before is not None and before[0] > 0.9 and before[1] < 0.1,
              str(before))

        open(p, "w").write(_AFTER)          # same file, entirely different text
        rep = _update(db, d, [p])
        after = _vector(db, p)
        check("update sees the change",
              rep.summary()["changed"] == 1, str(rep.summary()["changed"]))
        # The vector must be GONE, not merely different: `update` has no
        # embedder and must not grow one -- re-embedding here would make a
        # filesystem diff decide to spend a network round trip per node.
        check("the stale vector is deleted rather than left behind",
              after is None, "still %s" % (after,))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_stale_vector_would_rank_for_the_wrong_query():
    """Why the deletion matters, stated as retrieval rather than as bookkeeping.

    With the old behaviour the node still answered a query about its FORMER
    contents. This asserts the consequence, so the gate fails on the symptom a
    user would meet and not only on a row count.
    """
    d = _tmp()
    try:
        db, p, emb = _build_and_embed(d)
        open(p, "w").write(_AFTER)
        _update(db, d, [p])
        # Re-index the text so the FTS shortlist reflects the new content, which
        # is what a real `update` does; the vector table is what is under test.
        with Store(db, model="m3") as s:
            s.begin_immediate()
            s.rebuild_fts()
        with Store(db, embeddings={"provider": "static", "model": _MODEL}) as s:
            hits = vector_search(s, "onion shallot", limit=5, embedder=emb)
        # Nothing is embedded any more, so the honest answer is "did not run".
        check("with no vectors left, the vector path reports None (not a rank)",
              hits is None, repr(hits)[:40])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_embed_is_incremental():
    """Only the uncovered nodes are embedded, and the covered ones are counted."""
    from homegraph import cli

    d = _tmp()
    try:
        db, p, emb = _build_and_embed(d)
        prov, model, dim = emb.namespace
        with Store(db) as s:
            first = s.embedding_count(prov, model, dim)

        # A second file appears: exactly the shape an `update` leaves behind.
        p2 = os.path.join(d, "b.md")
        open(p2, "w").write("onion garlic\n")
        _update(db, d, [p, p2])

        n, degenerate, skipped = cli._embed_store(db, emb)
        check("only the new node is embedded",
              n == 1 and skipped == first,
              "embedded=%d skipped=%d (was %d)" % (n, skipped, first))
        with Store(db) as s:
            total = s.embedding_count(prov, model, dim)
        check("and the namespace is complete afterwards", total == first + 1,
              "%d" % total)

        # A no-op run must embed nothing at all -- that is the whole saving.
        n2, _d2, skipped2 = cli._embed_store(db, emb)
        check("a second run embeds nothing", n2 == 0 and skipped2 == total,
              "embedded=%d skipped=%d" % (n2, skipped2))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_force_re_embeds_everything():
    """`--force` exists for the case the namespace cannot see: a changed matrix.

    Same provider, same model name, same dim -- different numbers. Nothing in
    the store can tell, so the user has to.
    """
    from homegraph import cli

    d = _tmp()
    try:
        db, p, emb = _build_and_embed(d)
        before = _vector(db, p)

        # Same namespace, different vectors: `retrieval` now points along Y.
        toks = list(_VOCAB)
        moved = dict(_VOCAB, retrieval=[0.0, 1.0], search=[0.0, 1.0],
                     ranking=[0.0, 1.0])
        path2 = os.path.join(d, "m2.json")
        json.dump({"provider": "static", "model": _MODEL, "dim": _DIM,
                   "tokens": toks, "matrix": [moved[t] for t in toks]},
                  open(path2, "w"))
        emb2 = se.load(path2)
        check("the two matrices share a namespace",
              emb.namespace == emb2.namespace, str(emb2.namespace))

        n_skip, _d, skipped = cli._embed_store(db, emb2)
        unchanged = _vector(db, p)
        check("without --force the changed matrix is skipped entirely",
              n_skip == 0 and unchanged == before,
              "embedded=%d skipped=%d" % (n_skip, skipped))

        n_force, _d2, skipped2 = cli._embed_store(db, emb2, force=True)
        after = _vector(db, p)
        check("--force re-embeds every node", n_force > 0 and skipped2 == 0,
              "embedded=%d skipped=%d" % (n_force, skipped2))
        check("and the vectors actually moved", after != before,
              "%s -> %s" % (before, after))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_status_reports_the_gap():
    """A partial namespace is visible, per namespace, with a denominator."""
    d = _tmp()
    try:
        db, p, emb = _build_and_embed(d)
        with Store(db) as s:
            full = s.embedding_coverage()
        check("a complete namespace is not marked stale",
              len(full) == 1 and not full[0]["stale"], str(full))

        p2 = os.path.join(d, "b.md")
        open(p2, "w").write("onion garlic\n")
        _update(db, d, [p, p2])
        with Store(db) as s:
            partial = s.embedding_coverage()
        check("after an update the namespace is marked stale",
              len(partial) == 1 and partial[0]["stale"], str(partial))
        check("and the count names how many are missing",
              partial[0]["of"] > partial[0]["embedded"],
              "%d of %d" % (partial[0]["embedded"], partial[0]["of"]))

        # The denominator is nodes WITH TEXT. Counting textless ones as
        # missing would put full coverage permanently out of reach, and a
        # number that can never read 100% is one people learn to ignore.
        #
        # A textless node is INSERTED here rather than hoped for. The first cut
        # compared the reported denominator against the same query re-run in
        # the test, over a fixture where every node happened to have text -- so
        # `WHERE 1=1` and the real filter returned the same count and the
        # mutation survived untouched. The gate could not fail because the
        # fixture held no case to fail on. (mutate_i2 found this.)
        with Store(db, model="m3") as s:
            s.begin_immediate()
            s.db.execute(
                "INSERT INTO nodes (node_key, kind, title, body, first_seen, "
                "last_seen) VALUES ('pathless::empty', 'tag', '', '', "
                "'2026-07-25', '2026-07-25')")
            total_nodes = s.node_count()
        with Store(db) as s:
            after_empty = s.embedding_coverage()
        check("a textless node exists for this check to discriminate on",
              after_empty[0]["of"] < total_nodes,
              "of=%d, nodes=%d" % (after_empty[0]["of"], total_nodes))
        check("the denominator counts nodes with text, not all nodes",
              after_empty[0]["of"] == total_nodes - 1,
              "%d vs %d node(s)" % (after_empty[0]["of"], total_nodes))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_removed_file_takes_its_vector_with_it():
    """The cascade already worked; asserted so the new DELETE cannot break it."""
    d = _tmp()
    try:
        db, p, emb = _build_and_embed(d)
        prov, model, dim = emb.namespace
        os.remove(p)
        _update(db, d, [])
        with Store(db) as s:
            left = s.embedding_count(prov, model, dim)
        check("a removed file leaves no vector behind", left == 0,
              "%d left" % left)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    for fn in (t_a_changed_file_loses_its_vector,
               t_a_stale_vector_would_rank_for_the_wrong_query,
               t_embed_is_incremental, t_force_re_embeds_everything,
               t_status_reports_the_gap,
               t_a_removed_file_takes_its_vector_with_it):
        fn()
    bad = [r for r in results if not r[1]]
    print("\nCP-I2: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_checkpoint_i2():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
