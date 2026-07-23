#!/usr/bin/env python3
"""CP-1 -- the substrate and time layer checkpoint.

Written to fail. The two checks that matter most are the ones asserting that
something is *absent*: search returning nothing for a term nobody wrote, and a
sentence query returning nothing when embeddings are off. A search layer that
cannot return zero is not a search layer, it is a random ranker.

Run:
    python3 tests/test_cp1.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import temporal                                    # noqa: E402
from homegraph.incremental import diff, scan           # noqa: E402
from homegraph.search import hybrid_search, rrf_fuse              # noqa: E402
from homegraph.store import Store                                 # noqa: E402

results = []
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-40s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def scratch():
    d = os.path.expanduser("~/.homegraph/cp1-scratch")
    os.makedirs(d, exist_ok=True)
    return tempfile.mkdtemp(prefix="cp1-", dir=d)


# -- 1. round trip ---------------------------------------------------------

def t_roundtrip(tmp):
    with Store(os.path.join(tmp, "rt.db"), model="test") as s:
        written = {}
        for i in range(1000):
            key = "node:%04d" % i
            written[key] = ("title %d" % i, "body text number %d" % i, i * 7)
            s.upsert_node(key, kind="file", title=written[key][0],
                          body=written[key][1], size=written[key][2],
                          path="/synthetic/%04d.md" % i, mtime=1.0 * i)
        s.commit()
        bad = []
        for key, (title, body, size) in written.items():
            row = s.get_node(key)
            if row is None or (row["title"], row["body"], row["size"]) != (
                    title, body, size):
                bad.append(key)
        check("round trip 1000 nodes", not bad and s.node_count() == 1000,
              "%d nodes, %d mismatched" % (s.node_count(), len(bad)))
        check("schema version is 1 from the start", s.version == 1,
              "version %d" % s.version)


# -- 2. FTS postprocessing -------------------------------------------------

def t_fts(tmp):
    with Store(os.path.join(tmp, "fts.db"), model="test") as s:
        for i in range(50):
            s.upsert_node("n%d" % i, kind="file", title="doc %d" % i,
                          body="alpha beta gamma %d" % i)
        s.commit()
        check("FTS is empty until postprocessing runs", s.fts_count() == 0,
              "%d rows before rebuild_fts()" % s.fts_count())
        check("stale index is detectable", s.fts_is_stale(), "fts_is_stale()")
        n = s.rebuild_fts()
        check("FTS rows == node count after rebuild",
              n == s.node_count() == 50, "%d == %d" % (n, s.node_count()))
        check("index no longer stale", not s.fts_is_stale(), "")

        # Mutation testing caught this gap: with only one rebuild ever called,
        # a rebuild that forgets to clear first is indistinguishable from one
        # that clears. Two runs must give the same count, not double it.
        # Wrapped because a rebuild that forgets to clear raises on the
        # duplicate rowid rather than returning a wrong count. Both are
        # failures; only one of them is legible in the output.
        try:
            again = s.rebuild_fts()
            detail = "%d rows after 2nd run" % again
        except Exception as exc:                                # noqa: BLE001
            again, detail = -1, "raised %s" % type(exc).__name__
        check("rebuild_fts is idempotent", again == 50, detail)


# -- 3. search must be able to say no --------------------------------------

def t_search_says_no(tmp):
    with Store(os.path.join(tmp, "search.db"), model="test") as s:
        s.upsert_node("a", kind="file", title="trading journal",
                      body="corvid strategy backtest results")
        s.upsert_node("b", kind="file", title="wiki note",
                      body="associative trails between notes")
        s.rebuild_fts()

        hit = hybrid_search(s, "corvid")
        check("real term finds its document", len(hit) == 1,
              "%d hits, mode=%s" % (len(hit), hit._out_mode))

        miss = hybrid_search(s, "zzzznonexistentterm")
        check("absent term returns 0, not BM25 noise", len(miss) == 0,
              "%d hits" % len(miss))

        # The #711 failure, asserted rather than avoided.
        sentence = hybrid_search(
            s, "how should I think about the relationship between my notes?")
        check("sentence query without embeddings returns 0",
              len(sentence) == 0, "%d hits" % len(sentence))
        check("_out_mode reports 'fts'", sentence._out_mode == "fts",
              repr(sentence._out_mode))
        check("and it warns loudly",
              any("embeddings are OFF" in w for w in sentence.warnings),
              "%d warning(s)" % len(sentence.warnings))

        # The other warning, which had no gate at all. Deleting the stale-index
        # branch outright left the whole suite green: a search over an index
        # covering a fraction of the store returned its partial answer without
        # a word. `fts_is_stale()` was checked; the fact that anyone is TOLD
        # was not.
        s.upsert_node("c", kind="file", title="added after the rebuild",
                      body="corvid arrived late")
        s.commit()
        stale = hybrid_search(s, "corvid")
        check("a search over a stale index says so",
              any("results are incomplete" in w for w in stale.warnings),
              "%d warning(s): %s" % (len(stale.warnings),
                                     [w[:32] for w in stale.warnings]))
        s.rebuild_fts()

        check("embeddings are off by default", s.embeddings is None, "")
        try:
            Store(os.path.join(tmp, "bad.db"), embeddings={"provider": "x"})
            ok = False
        except ValueError:
            ok = True
        check("embeddings need provider AND model", ok, "half-config rejected")


# -- 4. RRF fuses ranks, not scores ---------------------------------------

def t_rrf():
    lex = [{"node_id": 1, "node_key": "a"}, {"node_id": 2, "node_key": "b"}]
    vec = [{"node_id": 2, "node_key": "b"}, {"node_id": 3, "node_key": "c"}]
    fused = rrf_fuse({"fts": lex, "vector": vec})
    check("RRF ranks the doubly-found item first",
          fused[0]["node_id"] == 2, "order %s" % [h["node_id"] for h in fused])
    check("RRF keeps provenance per hit",
          set(fused[0]["sources"]) == {"fts#2", "vector#1"},
          str(fused[0]["sources"]))

    # The check above is insensitive to HOW rank is weighted: node 2 appears in
    # both lists and wins under almost any weighting. Mutation testing proved
    # it, so here is a case where a wrong weighting inverts the order.
    # A is rank 1 in both lists; B is rank 5 in one. Only a scheme that rewards
    # BETTER (lower) ranks puts A first.
    lex2 = [{"node_id": 10}, {"node_id": 91}, {"node_id": 92},
            {"node_id": 93}, {"node_id": 20}]
    vec2 = [{"node_id": 10}]
    order = [h["node_id"] for h in rrf_fuse({"fts": lex2, "vector": vec2})]
    check("RRF rewards better ranks, not larger ones", order[0] == 10,
          "order %s" % order[:3])

    # Raw scores from different retrievers are not commensurable. RRF must not
    # read them at all -- the field is present here purely as a trap.
    #
    # The score used to be -999.0, which is a trap that springs the wrong way:
    # summing raw scores ALSO puts node 2 first, so a fusion that read them
    # produced the expected order and the gate stayed green. It has to be a
    # score large enough that reading it would change the answer.
    lex3 = [{"node_id": 1, "score": 999.0}, {"node_id": 2, "score": 0.0001}]
    vec3 = [{"node_id": 2, "score": 0.99}]
    order3 = [h["node_id"] for h in rrf_fuse({"fts": lex3, "vector": vec3})]
    check("RRF ignores incoming raw scores", order3 == [2, 1],
          "order %s" % order3)


# -- 5. incremental --------------------------------------------------------

def t_incremental(tmp):
    corpus = os.path.join(tmp, "corpus")
    os.makedirs(corpus)
    paths = []
    for i in range(1000):
        p = os.path.join(corpus, "f%04d.md" % i)
        with open(p, "w") as fh:
            fh.write("content %d\n" % i)
        paths.append(p)

    with Store(os.path.join(tmp, "inc.db"), model="test") as s:
        from homegraph.incremental import hash_file
        for p in paths:
            st = os.stat(p)
            s.upsert_node(p, kind="file", path=p, size=st.st_size,
                          mtime=st.st_mtime, content_hash=hash_file(p))
        s.commit()

        with open(paths[500], "w") as fh:
            fh.write("content 500 EDITED\n")
        os.utime(paths[900], (1e9, 1e9))          # touched, bytes unchanged
        target = paths[999]
        os.remove(target)

        current = scan([p for p in paths if p != target])
        d = diff(s, current, use_hash=True)
        check("exactly 1 file reported changed", len(d.changed) == 1,
              "changed=%s" % d.summary())
        check("the changed one is the edited one",
              d.changed == [paths[500]], os.path.basename(d.changed[0]))
        check("mtime-only change is 'touched', not 'changed'",
              d.touched == [paths[900]], "touched=%d" % len(d.touched))
        check("deleted file is reported removed", d.removed == [target],
              "removed=%d" % len(d.removed))

        s.rebuild_fts()
        s.delete_node(target)
        # Checked BEFORE any rebuild. Rebuilding first would clear the index and
        # hide the orphan entirely -- which is precisely how this check passed
        # against a delete_node() that never touched FTS at all.
        check("no FTS orphans after deletion", not s.fts_orphans(),
              "%d orphan rows" % len(s.fts_orphans()))
        s.rebuild_fts()
        check("FTS count follows node count",
              s.fts_count() == s.node_count() == 999,
              "%d / %d" % (s.fts_count(), s.node_count()))


# -- 6. M2 never hashes ----------------------------------------------------

def t_no_hash_path(tmp):
    corpus = os.path.join(tmp, "images")
    os.makedirs(corpus)
    p = os.path.join(corpus, "03122025_1.png")
    with open(p, "wb") as fh:
        fh.write(b"\x89PNG" + b"\0" * 100)

    with Store(os.path.join(tmp, "m2.db"), model="m2") as s:
        st = os.stat(p)
        s.upsert_node(p, kind="image", path=p, size=st.st_size,
                      mtime=st.st_mtime, content_hash=None)
        s.commit()
        os.utime(p, (1e9, 1e9))
        d = diff(s, scan([p]), use_hash=False)
        check("M2 detects change without hashing", d.changed == [p],
              "changed=%d" % len(d.changed))
        check("M2 stores no content hash",
              s.get_node(p)["content_hash"] is None, "hash is NULL")


# -- 7. datelist_int known-answer test -------------------------------------

def t_datelist():
    anchor = date(2026, 7, 22)
    cases = [
        ([anchor], 0b1),
        ([anchor - timedelta(days=1)], 0b10),
        ([anchor - timedelta(days=31)], 1 << 31),
        ([anchor, anchor - timedelta(days=2)], 0b101),
        ([], 0),
    ]
    bad = [(d, want, temporal.encode_datelist(d, anchor))
           for d, want in cases if temporal.encode_datelist(d, anchor) != want]
    check("bitmask encodes to known values", not bad, "%d wrong" % len(bad))

    # 10 files with constructed dates, decoded back to exactly the same set.
    mismatched = []
    for i in range(10):
        dates = sorted({(anchor - timedelta(days=(i * 3 + j * 7) % 32)
                         ).isoformat() for j in range(4)})
        mask = temporal.encode_datelist(dates, anchor)
        if temporal.decode_datelist(mask, anchor) != dates:
            mismatched.append(i)
    check("10 constructed date sets survive a round trip", not mismatched,
          "%d mismatched" % len(mismatched))

    outside = [anchor - timedelta(days=40)]
    check("dates outside the 32-day window are dropped",
          temporal.encode_datelist(outside, anchor) == 0, "mask 0")

    # 40 days out is not where an off-by-one lives. Widening the comparison to
    # `delta <= WINDOW_DAYS` left the check above green -- 40 is still out --
    # while day 32 started setting bit 32 and the mask stopped being 32 bits
    # wide. The boundary is the only place worth testing on a bounded window.
    edge_in = temporal.encode_datelist(
        [anchor - timedelta(days=temporal.WINDOW_DAYS - 1)], anchor)
    edge_out = temporal.encode_datelist(
        [anchor - timedelta(days=temporal.WINDOW_DAYS)], anchor)
    check("the window boundary is exact: day 31 in, day 32 out",
          edge_in == 1 << (temporal.WINDOW_DAYS - 1) and edge_out == 0,
          "day %d -> %d, day %d -> %d"
          % (temporal.WINDOW_DAYS - 1, edge_in, temporal.WINDOW_DAYS, edge_out))
    check("the mask never exceeds its declared width",
          edge_in.bit_length() <= temporal.WINDOW_DAYS,
          "%d bits" % edge_in.bit_length())

    a = temporal.encode_datelist([anchor, anchor - timedelta(days=1)], anchor)
    b = temporal.encode_datelist([anchor - timedelta(days=1),
                                  anchor - timedelta(days=5)], anchor)
    check("cohort overlap counts shared days",
          temporal.cohort_overlap(a, b) == 1, "1 shared day")


# -- 8. history gate -------------------------------------------------------

def t_history(tmp):
    """A removed wikilink must survive as an expired edge, not vanish."""
    today = date(2026, 7, 22)
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)

    with Store(os.path.join(tmp, "hist.db"), model="m3") as s:
        note = "/notes/a.md"
        for key in (note, "/notes/b.md", "/notes/c.md"):
            s.upsert_node(key, kind="file", path=key, as_of=last_week.isoformat())

        # Last week and yesterday the note linked to both b and c.
        for day in (last_week, yesterday):
            body = "see [[b]] and [[c]]"
            for target in WIKILINK.findall(body):
                s.upsert_edge(note, "/notes/%s.md" % target, "WIKILINKS_TO",
                              as_of=day.isoformat())
        # Today the link to c is gone. The edge is not re-touched, so its
        # last_seen stops at yesterday. Nothing is deleted.
        body = "see [[b]]"
        for target in WIKILINK.findall(body):
            s.upsert_edge(note, "/notes/%s.md" % target, "WIKILINKS_TO",
                          as_of=today.isoformat())
        s.commit()

        now = {(e["src_key"], e["dst_key"])
               for e in s.edges_as_of(today.isoformat())}
        then = {(e["src_key"], e["dst_key"])
                for e in s.edges_as_of(last_week.isoformat())}

        check("today's graph lost the removed link",
              (note, "/notes/c.md") not in now, "%d edges today" % len(now))
        check("today's graph keeps the surviving link",
              (note, "/notes/b.md") in now, "")
        check("last week's graph still has the removed link",
              (note, "/notes/c.md") in then, "%d edges then" % len(then))

        row = s.db.execute(
            "SELECT e.last_seen FROM edges e JOIN nodes d ON d.id=e.dst "
            "WHERE d.node_key='/notes/c.md'").fetchone()
        check("expired edge's last_seen is yesterday",
              row["last_seen"] == yesterday.isoformat(), row["last_seen"])
        check("nothing was deleted", s.edge_count() == 2,
              "%d edge rows" % s.edge_count())


# -- 9. retention ----------------------------------------------------------

def t_retention(tmp):
    """120 days of history -> anything older than 90 exists only as a rollup."""
    as_of = date(2026, 7, 22)
    with Store(os.path.join(tmp, "ret.db"), model="test") as s:
        nid = s.upsert_node("n", kind="file", path="/n.md")
        for i in range(120):
            day = as_of - timedelta(days=i)
            temporal.record_observation(s, nid, day, content_hash="h%d" % i,
                                        size=100 + i)
        s.commit()
        before = s.db.execute(
            "SELECT COUNT(*) c FROM observations").fetchone()["c"]

        temporal.refresh_all_datelists(s, as_of)
        row = s.get_node("n")
        mask_days = len(temporal.decode_datelist(row["datelist_int"], as_of))
        check("datelist_int covers exactly the 32-day window", mask_days == 32,
              "%d days in mask, %d in activity_datelist"
              % (mask_days, len(json.loads(row["activity_datelist"]))))

        report = temporal.apply_retention(s, as_of)
        after = s.db.execute(
            "SELECT COUNT(*) c FROM observations").fetchone()["c"]
        cutoff = report["cutoff"]
        stragglers = s.db.execute(
            "SELECT COUNT(*) c FROM observations WHERE seen_date < ?",
            (cutoff,)).fetchone()["c"]
        rolled = s.db.execute(
            "SELECT COALESCE(SUM(change_days),0) c FROM observations_monthly"
        ).fetchone()["c"]

        check("daily rows older than 90 days are gone", stragglers == 0,
              "cutoff %s, %d -> %d rows" % (cutoff, before, after))
        check("they survive only as a monthly rollup",
              rolled == before - after and rolled > 0,
              "%d days rolled up" % rolled)


def main():
    tmp = scratch()
    try:
        t_roundtrip(tmp)
        t_fts(tmp)
        t_search_says_no(tmp)
        t_rrf()
        t_incremental(tmp)
        t_no_hash_path(tmp)
        t_datelist()
        t_history(tmp)
        t_retention(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


# -- pytest adapter --------------------------------------------------------
#
# The checks above are written as a script: `t_*` helpers driven by `main()`,
# which prints a readable report and returns an exit code. pytest collects
# `test_*` functions, so without this it collected the file, found nothing, and
# reported success -- a runner that verifies nothing while looking green.
#
# One test per checkpoint rather than one per check, because the phases share
# built state: the corpus is built once and then queried repeatedly, and
# splitting that across independent tests would rebuild it each time.

def test_checkpoint_cp1():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
