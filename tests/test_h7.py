#!/usr/bin/env python3
"""CP-H7 -- connect-time catch-up and per-hit staleness.

Four states per stored node, a warning that counts the returned window rather
than the corpus, and one reconciliation with two answers: MCP reports, `watch`
updates.

**The answer key went first**, before any of this code:
`tests/gold/FASIT-h7.md` fixes the four states, the worked example's five nodes,
and what MCP is allowed to do. It was amended twice while H7 was being built,
both times before the code existed and both times with a date -- once because
`absent` was doing the work of two different facts in `embedding_status`, and
once because a node without a stat can still be asked whether its path is there.

Run:
    python3 tests/test_h7.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from homegraph import incremental as inc                      # noqa: E402
from homegraph.mesh import Mesh                               # noqa: E402
from homegraph.store import Store                             # noqa: E402

results, check = reporter(58)

FASIT = os.path.join(HERE, "gold", "FASIT-h7.md")
_DB = ""


def states_from_key():
    """The worked example's five rows, read out of the key.

    Restating them here would make the gate agree with itself. The key holds
    the table; this reads the node name and the state out of each row.
    """
    with open(FASIT, encoding="utf-8") as fh:
        text = fh.read()
    rows = re.findall(
        r"^\| `([^`]+)` \| \w+ \|[^|]*\|[^|]*\| `(\w+)`", text, re.M)
    return dict(rows)


def build_corpus():
    """A store and a filesystem that has moved on since it was written."""
    work = tempfile.mkdtemp(prefix="h7-")
    # `f.md` is stored WITHOUT a stat and then deleted: the only node that can
    # tell "a stub is never asked whether its path exists" from the truth. The
    # mutation for it survived until this file existed (2026-08-02).
    # `h.md` and `i.md` isolate the two halves of the `(size, mtime)` test.
    # Every other drifted file here is rewritten LONGER after a sleep, so both
    # differ at once and the conjunction has no discriminating input -- three
    # mutations survived on that alone (audit, 2026-08-02): size-only,
    # mtime-only, and a tolerance of 1e9.
    files = {"a.md": "alpha quaywise", "b.md": "beta quaywise",
             "c.md": "gamma quaywise", "d.md": "delta quaywise",
             "f.md": "zeta quaywise", "h.md": "eta quaywise!!",
             "i.md": "theta quaywise"}
    for name, body in files.items():
        with open(os.path.join(work, name), "w", encoding="utf-8") as fh:
            fh.write(body)

    db = os.path.join(work, "m3.db")
    with Store(db, model="m3") as store:
        for name in files:
            path = os.path.join(work, name)
            st = os.stat(path)
            # `d.md` is stored WITHOUT a stat: the node exists, the question
            # was never asked of it. That is `absent`, and it is a real state
            # in this store -- 6 035 of m3's nodes on the real corpus.
            stat = {} if name in ("d.md", "f.md") else {"size": st.st_size,
                                              "mtime": st.st_mtime}
            store.upsert_node(path, kind="file", path=path, title=name,
                              body=files[name], as_of="2026-08-02", **stat)
        # A section carries its file's path and no stat of its own.
        store.upsert_node("%s#2" % os.path.join(work, "b.md"), kind="section",
                          path=os.path.join(work, "b.md"), title="b/2",
                          body="beta quaywise section", as_of="2026-08-02")
        # A node with no path at all. `os.stat(None)` raises TypeError, which
        # R4 forbids reaching the caller -- found by CP-6's MCP gate.
        store.upsert_node("tag:quaywise", kind="tag", title="quaywise",
                          body="quaywise", as_of="2026-08-02")
        store.rebuild_fts()

    # Now the filesystem moves on: b is edited, c is deleted, a and d are not.
    time.sleep(0.01)
    with open(os.path.join(work, "b.md"), "w", encoding="utf-8") as fh:
        fh.write("beta quaywise, rewritten and longer than before")
    os.remove(os.path.join(work, "c.md"))
    os.remove(os.path.join(work, "f.md"))
    # Same length, new mtime: an in-place edit, a formatter, a sync tool.
    h = os.path.join(work, "h.md")
    h_before = os.stat(h)
    with open(h, "w", encoding="utf-8") as fh:
        fh.write("ETA QUAYWISE!!")
    assert os.stat(h).st_size == h_before.st_size, "h.md must keep its size"
    # New length, mtime restored: a same-second edit, or a tool that puts the
    # timestamp back.
    i = os.path.join(work, "i.md")
    i_before = os.stat(i)
    with open(i, "w", encoding="utf-8") as fh:
        fh.write("theta quaywise, longer than it was")
    os.utime(i, ns=(i_before.st_atime_ns, i_before.st_mtime_ns))
    return work, db


def t_the_four_states(work, db):
    key = states_from_key()
    check("key: the key fixes five rows of the worked example",
          len(key) == 5, "%r" % (key,))
    with Store(db) as store:
        got = inc.reconcile(store)
    named = {os.path.basename(k): v for k, v in got.items()}
    want = {"a.md": inc.CURRENT, "b.md": inc.STALE, "c.md": inc.MISSING,
            "d.md": inc.ABSENT}
    wrong = [(n, named.get(n), s) for n, s in want.items()
             if named.get(n) != s]
    check("R1: current, stale, missing and absent are four different answers",
          not wrong, "; ".join("%s got %r want %r" % w for w in wrong))
    # The key names the same four, so a rename in one place fails here.
    from_key = {os.path.basename(n): s for n, s in key.items()
                if not n.endswith("#2")}
    check("R1: and they are the states the key names",
          all(named.get(n) == s for n, s in from_key.items()),
          "%r vs key %r" % (named, from_key))


def t_either_half_of_the_stat_is_enough(work, db):
    """size alone and mtime alone each catch a drift the other misses."""
    named = _reconcile_named(db)
    check("stat: a same-length edit with a new mtime is stale",
          named.get("h.md") == inc.STALE, "%r" % (named.get("h.md"),))
    check("stat: a longer file with its mtime restored is stale",
          named.get("i.md") == inc.STALE, "%r" % (named.get("i.md"),))
    # And the fixture really is what it claims -- otherwise the two checks
    # above pass for the wrong reason.
    h = os.stat(os.path.join(work, "h.md"))
    with Store(db) as store:
        rows = {r["node_key"]: r for r in store.db.execute(
            "SELECT node_key, size, mtime FROM nodes WHERE path IS NOT NULL")}
    hrow = rows[os.path.join(work, "h.md")]
    check("stat: and h.md really did keep its size",
          hrow["size"] == h.st_size and abs(hrow["mtime"] - h.st_mtime) > 1e-6,
          "size %r/%r mtime %r/%r"
          % (hrow["size"], h.st_size, hrow["mtime"], h.st_mtime))


def t_the_worse_state_wins_on_a_shared_path(work):
    """R2b: two stored nodes on one path resolve the same way every run."""
    duo = os.path.join(work, "duo")
    os.makedirs(duo, exist_ok=True)
    path = os.path.join(duo, "shared.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("shared quaywise")
    db = os.path.join(duo, "m3.db")
    st = os.stat(path)
    with Store(db, model="m3") as store:
        # Two stat-bearing nodes on ONE path: one indexed before an edit, one
        # after. Their states disagree, and which one a section inherits must
        # not depend on the order SQLite returns rows in.
        store.upsert_node("fresh", kind="file", path=path, title="fresh",
                          body="shared quaywise", size=st.st_size,
                          mtime=st.st_mtime, as_of="2026-08-02")
        store.upsert_node("stale", kind="file", path=path, title="stale",
                          body="shared quaywise", size=st.st_size + 99,
                          mtime=st.st_mtime, as_of="2026-08-02")
        store.upsert_node("%s#1" % path, kind="section", path=path,
                          title="sec", body="shared quaywise",
                          as_of="2026-08-02")
        got = inc.reconcile(store)
    check("R2b: the section inherits the WORSE of two states on its path",
          got.get("%s#1" % path) == inc.STALE, "%r" % (got,))
    check("R2b: and each node keeps its own answer",
          got.get("fresh") == inc.CURRENT and got.get("stale") == inc.STALE,
          "%r" % (got,))


def t_a_section_inherits(work, db):
    with Store(db) as store:
        got = inc.reconcile(store)
    section = "%s#2" % os.path.join(work, "b.md")
    check("R2: a section is stale because its file is",
          got.get(section) == inc.STALE, "%r" % (got.get(section),))
    # And the negative: a section whose file the store does not hold has
    # nothing to inherit, and must not invent a state from its own path.
    orphan = os.path.join(work, "gone.md")
    with open(orphan, "w", encoding="utf-8") as fh:
        fh.write("x")
    with Store(db, model="m3") as store:
        store.upsert_node("%s#1" % orphan, kind="section", path=orphan,
                          title="orphan", body="x", as_of="2026-08-02")
        got2 = inc.reconcile(store)
    check("R2: a section with no file node in the store is absent",
          got2.get("%s#1" % orphan) == inc.ABSENT,
          "%r" % (got2.get("%s#1" % orphan),))


def _reconcile_named(db):
    with Store(db) as store:
        return {os.path.basename(k): v
                for k, v in inc.reconcile(store).items()}


def t_a_stub_without_stat_is_still_asked_if_it_exists(work):
    """The amendment: two questions hide in one."""
    here = os.path.join(work, "a.md")
    check("stub: a path that exists but has no stored stat is absent",
          inc.node_state(here, None, None) == inc.ABSENT,
          inc.node_state(here, None, None))
    check("stub: a path that is gone is missing, stat or no stat",
          inc.node_state(os.path.join(work, "c.md"), None, None)
          == inc.MISSING,
          inc.node_state(os.path.join(work, "c.md"), None, None))
    check("stub: and reconcile says so too, not only node_state",
          _reconcile_named(_DB).get("f.md") == inc.MISSING,
          "%r" % (_reconcile_named(_DB).get("f.md"),))
    check("stub: and a node with no path at all is absent, not an exception",
          inc.node_state(None, None, None) == inc.ABSENT,
          inc.node_state(None, None, None))


def t_search_and_reconcile_agree_about_a_section(work):
    """The two halves of one feature must not disagree about one node.

    In the main fixture a section shares its file's `path`, so the fusion key
    merges the two and the section never surfaces on its own -- which is why
    the gate was green while `search` answered `absent` for a node `reconcile`
    called `stale`. Here the section matches the query and the file does not,
    so the section is the hit. Found by codex 2026-08-02.
    """
    solo = os.path.join(work, "solo")
    os.makedirs(solo, exist_ok=True)
    path = os.path.join(solo, "s.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("alpha only")
    db = os.path.join(solo, "m3.db")
    st = os.stat(path)
    with Store(db, model="m3") as store:
        store.upsert_node(path, kind="file", path=path, title="s",
                          body="alpha only", size=st.st_size,
                          mtime=st.st_mtime, as_of="2026-08-02")
        store.upsert_node("%s#2" % path, kind="section", path=path,
                          title="s/2", body="quaywise section text",
                          as_of="2026-08-02")
        store.rebuild_fts()
    time.sleep(0.01)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("alpha only, rewritten and much longer than before")

    hits = Mesh({"m3": db}).search("quaywise", limit=10).hits
    with Store(db) as store:
        rec = inc.reconcile(store)
    check("agree: the section is the hit, and it is stale, not absent",
          hits and hits[0]["staleness"] == inc.STALE,
          "%r" % ([(h["node_key"], h["staleness"]) for h in hits],))
    check("agree: and search says exactly what reconcile says",
          all(rec[h["node_key"]] == h["staleness"] for h in hits),
          "%r vs %r" % ([h["staleness"] for h in hits], rec))


def t_the_warning_counts_the_window(work, db):
    mesh = Mesh({"m3": db})
    res = mesh.search("quaywise", limit=20)
    stale = [h for h in res.hits if h["staleness"] in inc.AFFECTED]
    check("R3: the search reports per-hit staleness",
          all("staleness" in h for h in res.hits), "%r" % (res.hits[:1],))
    check("R3: and something in this corpus is affected",
          len(stale) >= 2, "%r" % ([h["staleness"] for h in res.hits],))
    banner = [w for w in res.warnings if "reindex to refresh" in w]
    check("R3: one warning line, not one per file",
          len(banner) == 1, "%r" % (res.warnings,))
    check("R3: and it counts the returned window, not the corpus",
          ("among %d hit(s)" % len(res.hits)) in banner[0], banner[0])


def t_a_clean_corpus_says_nothing(work):
    """Prediction 1: the line is absent, not empty and not "0 stale"."""
    clean = os.path.join(work, "clean")
    os.makedirs(clean, exist_ok=True)
    path = os.path.join(clean, "e.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("epsilon quaywise")
    db = os.path.join(clean, "m3.db")
    st = os.stat(path)
    stat_less = os.path.join(clean, "g.md")
    with open(stat_less, "w", encoding="utf-8") as fh:
        fh.write("eta quaywise")
    with Store(db, model="m3") as store:
        store.upsert_node(path, kind="file", path=path, title="e",
                          body="epsilon quaywise", size=st.st_size,
                          mtime=st.st_mtime, as_of="2026-08-02")
        # Present on disk, stored without a stat: `absent`, and nothing is
        # wrong with it. A corpus with no such node cannot tell "absent counts
        # as trouble" from the truth -- that mutation survived until this
        # existed (2026-08-02).
        store.upsert_node(stat_less, kind="file", path=stat_less, title="g",
                          body="eta quaywise", as_of="2026-08-02")
        store.rebuild_fts()
    res = Mesh({"m3": db}).search("quaywise", limit=20)
    check("prediction 1: the clean corpus does contain an absent node",
          any(h["staleness"] == inc.ABSENT for h in res.hits),
          "%r" % ([h["staleness"] for h in res.hits],))
    check("prediction 1: a clean corpus produces no staleness warning at all",
          not [w for w in res.warnings if "reindex" in w],
          "%r" % (res.warnings,))
    check("prediction 1: and the current hit says so on its own",
          any(h["staleness"] == inc.CURRENT for h in res.hits),
          "%r" % ([h["staleness"] for h in res.hits],))


def t_search_stats_only_the_window(work, db):
    """R4: the bound is the returned window, not the corpus."""
    calls = []
    real = inc.node_state

    def counting(*a, **kw):
        calls.append(a[0])
        return real(*a, **kw)

    inc.node_state = counting
    try:
        res = Mesh({"m3": db}).search("quaywise", limit=3)
    finally:
        inc.node_state = real
    check("R4: one stat per returned hit, and no more",
          len(calls) == len(res.hits) and len(res.hits) <= 3,
          "%d call(s) for %d hit(s)" % (len(calls), len(res.hits)))


def t_embedding_status(work, db):
    with Store(db) as store:
        rows = {r["node_key"]: r["id"] for r in
                store.db.execute("SELECT node_key, id FROM nodes")}
    res = Mesh({"m3": db}).search("quaywise", limit=20)
    check("R5: with no vectors anywhere the status is off, not none",
          all(h["embedding_status"] == "off" for h in res.hits),
          "%r" % ([h["embedding_status"] for h in res.hits],))
    # Embed exactly one node, and the other hits must become `none` -- the
    # store now holds vectors, they just do not have one.
    current = os.path.join(work, "a.md")
    with Store(db, model="m3") as store:
        store.upsert_embedding(rows[current], "test", "m", 2, [0.5, 0.5])
    res2 = Mesh({"m3": db}).search("quaywise", limit=20)
    got = {os.path.basename(h["node_key"]): h["embedding_status"]
           for h in res2.hits}
    check("R5: the embedded node whose file is current reports current",
          got.get("a.md") == inc.CURRENT, "%r" % (got,))
    check("R5: an unembedded node reports none, not off and not absent",
          got.get("b.md") == "none", "%r" % (got,))
    # And the derived half: embed the stale one, and the vector is stale too.
    stale = os.path.join(work, "b.md")
    with Store(db, model="m3") as store:
        store.upsert_embedding(rows[stale], "test", "m", 2, [0.5, 0.5])
    got3 = {os.path.basename(h["node_key"]): h["embedding_status"]
            for h in Mesh({"m3": db}).search("quaywise", limit=20).hits}
    check("R5: a vector on a stale file is stale, derived not stored",
          got3.get("b.md") == inc.STALE, "%r" % (got3,))
    # The node with no stat: a vector on it can be judged by nothing.
    unknown = os.path.join(work, "d.md")
    with Store(db, model="m3") as store:
        store.upsert_embedding(rows[unknown], "test", "m", 2, [0.5, 0.5])
    got4 = {os.path.basename(h["node_key"]): h["embedding_status"]
            for h in Mesh({"m3": db}).search("quaywise", limit=20).hits}
    check("R5: and a vector on an absent-state file is unknown, not current",
          got4.get("d.md") == "unknown", "%r" % (got4,))


def write_watcher(db):
    """A held-open connection that reports whether anyone else wrote.

    `PRAGMA data_version` is SQLite's own answer to this question: it changes
    when another connection commits. Three weaker instruments were tried first
    and all three were fooled:

      * `os.stat(db).st_mtime_ns` -- `journal_mode = WAL` puts the commit in
        `db-wal` and leaves the main file's mtime alone. This was the ONLY
        thing enforcing R6, and a search that wrote to every stale node passed
        under it (audit, 2026-08-02).
      * the same over `db-wal` and `db-shm` -- better, but those files are
        created and checkpointed away on connection close, so whether they
        exist at the moment of the check depends on timing rather than on
        whether anything was written.
      * a no-op write (`SET title = title`) as the mutation -- SQLite optimises
        it away, so the first version of the mutation was not a write at all
        and proved nothing about the gate.

    The connection must have completed a read before the baseline, or the first
    `data_version` is not yet meaningful.
    """
    import sqlite3

    watcher = sqlite3.connect(db)
    watcher.execute("SELECT COUNT(*) FROM nodes").fetchone()

    def version():
        return watcher.execute("PRAGMA data_version").fetchone()[0]

    return watcher, version


def t_mcp_reconciles_and_never_writes(work, db):
    """Prediction 4, and rule R6's first half."""
    import shutil

    from homegraph.mcp_server import Server

    # A FRESH copy. Earlier checks in this file search the shared store, so a
    # build that writes during search has already done its damage by the time
    # this runs -- and a second write of the same value moves nothing. The
    # mutation for this survived on exactly that (audit follow-up 2026-08-02).
    fresh = os.path.join(work, "untouched.db")
    shutil.copy2(db, fresh)
    watcher, version = write_watcher(fresh)
    before = version()
    srv = Server({"m3": fresh})
    srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {}})
    reply = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "mesh_search",
                                   "arguments": {"query": "quaywise"}}})
    assert reply is not None, "the server answered nothing"
    after = version()
    watcher.close()
    check("R6: an MCP connect and search commit nothing, WAL included",
          before == after, "data_version %r -> %r" % (before, after))
    import json
    payload = json.loads(reply["result"]["content"][0]["text"])
    check("R6: and the hits carry staleness over the wire",
          payload["hits"] and "staleness" in payload["hits"][0],
          "%r" % (payload["hits"][:1],))
    check("R6: and embedding_status too",
          payload["hits"] and "embedding_status" in payload["hits"][0],
          "%r" % (payload["hits"][:1],))


def t_the_banner_names_both_kinds_of_trouble(work):
    """Either half of AFFECTED can be dropped, and the gate must notice.

    The main fixture always holds a stale hit AND a missing one, so `if
    affected:` fires whichever half the code counts -- both mutations survived
    on that (audit 2026-08-02). These two corpora hold one kind each.
    """
    for kind in ("stale", "missing"):
        room = os.path.join(work, "only-%s" % kind)
        os.makedirs(room, exist_ok=True)
        path = os.path.join(room, "x.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("quaywise only")
        db = os.path.join(room, "m3.db")
        st = os.stat(path)
        with Store(db, model="m3") as store:
            store.upsert_node(path, kind="file", path=path, title="x",
                              body="quaywise only", size=st.st_size,
                              mtime=st.st_mtime, as_of="2026-08-02")
            store.rebuild_fts()
        time.sleep(0.01)
        if kind == "stale":
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("quaywise only, rewritten longer than before")
        else:
            os.remove(path)
        res = Mesh({"m3": db}).search("quaywise", limit=10)
        banner = [w for w in res.warnings if "reindex" in w]
        check("banner: a corpus of only %s hits still warns" % kind,
              len(banner) == 1 and kind in banner[0],
              "%r for %r" % (res.warnings,
                             [h["staleness"] for h in res.hits]))


def t_a_vector_on_a_missing_file_is_stale(work):
    """`missing` is not `absent`, and the vector inherits that too."""
    room = os.path.join(work, "vec-missing")
    os.makedirs(room, exist_ok=True)
    path = os.path.join(room, "v.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("quaywise vector")
    db = os.path.join(room, "m3.db")
    st = os.stat(path)
    with Store(db, model="m3") as store:
        node_id = store.upsert_node(path, kind="file", path=path, title="v",
                                    body="quaywise vector", size=st.st_size,
                                    mtime=st.st_mtime, as_of="2026-08-02")
        store.upsert_embedding(node_id, "test", "m", 2, [0.5, 0.5])
        store.rebuild_fts()
    os.remove(path)
    hits = Mesh({"m3": db}).search("quaywise", limit=10).hits
    check("R5: a vector whose file is gone is stale, not unknown",
          hits and hits[0]["embedding_status"] == inc.STALE,
          "%r" % ([(h["staleness"], h["embedding_status"]) for h in hits],))


def t_watch_catch_up_reports_what_drifted(work, db):
    """R6's second half: same reconciliation, the other answer."""
    from homegraph.cli import watch_catch_up

    class Args:
        model = ["m3=%s" % db]

    line = watch_catch_up(Args())
    check("R6: catch-up names what drifted while the watcher was off",
          "stale" in line and "missing" in line, repr(line))
    clean_db = os.path.join(work, "clean", "m3.db")

    class CleanArgs:
        model = ["m3=%s" % clean_db]

    check("R6: and says nothing at all when nothing drifted",
          watch_catch_up(CleanArgs()) == "",
          repr(watch_catch_up(CleanArgs())))

    class GoneArgs:
        model = ["m3=%s" % os.path.join(work, "nope.db")]

    check("R6: a store that is not there is not reported as clean",
          "could not be read" in watch_catch_up(GoneArgs()),
          repr(watch_catch_up(GoneArgs())))

    class MixedArgs:
        model = ["m3=%s" % db, "m1=%s" % os.path.join(work, "nope.db")]

    # One good store and one missing used to produce a summary byte-identical
    # to the good store alone -- the unreadable model vanished without a word,
    # which is the failure the docstring claims to have closed. Found by audit
    # 2026-08-02; the discriminating fixture is a two-element `args.model`.
    check("R6: and an unreadable model is named even when another answered",
          "could not be read" in watch_catch_up(MixedArgs())
          and watch_catch_up(MixedArgs()) != line,
          "%r vs %r" % (watch_catch_up(MixedArgs()), line))


def t_corpus_numbers_live_in_explain(work, db):
    out = Mesh({"m3": db}).explain("quaywise", limit=20)
    counts = out.get("staleness", {}).get("m3", {})
    # Two stale, not one: `b.md` and the section that inherits from it. The
    # corpus view counts NODES, because nodes are what a search returns -- a
    # file count here would not add up against the window the warning reports.
    # Four stale: `b.md`, `h.md`, `i.md` and the section that inherits from
    # `b.md`. The corpus view counts NODES, because nodes are what a search
    # returns -- a file count would not add up against the window the warning
    # reports.
    check("R3: explain carries the corpus-wide counts, over nodes",
          counts.get(inc.MISSING) == 2 and counts.get(inc.STALE) == 4,
          "%r" % (counts,))
    # `absent` must be published too. Dropping it is "reports a clean corpus it
    # never checked" happening in the one place the corpus numbers appear --
    # and that mutation survived until this check existed (audit 2026-08-02).
    check("R3: including absent, which is not the same as nothing wrong",
          counts.get(inc.ABSENT) == 2, "%r" % (counts,))
    # NOT `sum(counts) >= len(hits)`: that is true by construction for any
    # corpus with two hits and cannot say no. What can: the corpus knows about
    # nodes the window never returned.
    window = {h["node_key"] for h in
              Mesh({"m3": db}).search("quaywise", limit=2).hits}
    with Store(db) as store:
        everything = set(inc.reconcile(store))
    check("R3: and the corpus view covers nodes the window never returned",
          len(everything - window) >= 4,
          "%d beyond the window" % len(everything - window))


def main() -> int:
    global _DB
    work, db = build_corpus()
    _DB = db
    t_the_four_states(work, db)
    t_either_half_of_the_stat_is_enough(work, db)
    t_the_worse_state_wins_on_a_shared_path(work)
    t_a_section_inherits(work, db)
    t_search_and_reconcile_agree_about_a_section(work)
    t_a_stub_without_stat_is_still_asked_if_it_exists(work)
    t_the_warning_counts_the_window(work, db)
    t_a_clean_corpus_says_nothing(work)
    t_search_stats_only_the_window(work, db)
    t_mcp_reconciles_and_never_writes(work, db)
    t_the_banner_names_both_kinds_of_trouble(work)
    t_a_vector_on_a_missing_file_is_stale(work)
    t_watch_catch_up_reports_what_drifted(work, db)
    t_corpus_numbers_live_in_explain(work, db)
    t_embedding_status(work, db)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h7():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
