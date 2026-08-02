#!/usr/bin/env python3
"""CP-H6 -- centrality as the tie-break in the fusion, over the candidates only.

`fanIn + fanOut` from the mesh's own edges, used to order hits that RRF scored
EQUALLY. Deterministic and non-iterative: no PageRank, nothing with a
convergence threshold nobody can justify.

**The answer key went first**, before any of this code:
`tests/gold/FASIT-h6.md` fixes three candidates, their degrees, the
full-precision scores and the one order change they produce.

The key was revised once, on 2026-08-02, and this suite follows the revision.
The first implementation fed centrality as a fourth RRF list; it passed its own
gate and then failed on the real mesh, because with `RRF_K = 60` one list
contribution at position 1 is worth `1/61 = 0.016393` while two adjacent
positions are `0.000264` apart. One extra contribution outweighs some 63
places, so any non-empty list decides everything it touches.

What separates the surviving design from the one it replaced is the SCORES: a
tie-break leaves them untouched, and anything that folds degree into the score
changes all three. `R` -- degree 99, lowest score, still last -- reads like the
discriminating case and is not quite: measured, it misses climbing by 0.000256
under the fourth-list design. It is pinned anyway, because under a tie-break its
position is guaranteed rather than arithmetic.

`D` has the highest degree in the store, is not a candidate, and must never
appear. A test that only asserted "the order changed" would pass for an
implementation that ranks every node.

Run:
    python3 tests/test_h6.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from homegraph.mesh import Mesh                               # noqa: E402
from homegraph.search import RRF_K                            # noqa: E402
from homegraph.store import Store                             # noqa: E402

results, check = reporter(58)

FASIT = os.path.join(HERE, "gold", "FASIT-h6.md")

# Degrees the fixture plants, from the key's table, split across the two
# directions on purpose. A fixture where every edge points the same way cannot
# tell `fanIn + fanOut` from `fanIn`, and the mutation that drops one of them
# survived until this split existed (2026-08-01).
IN_DEGREE = {"Q": 3, "D": 99}
OUT_DEGREE = {"Q": 1, "R": 99}
# A candidate with NO row in the mesh at all. Degree 0, same as a row with no
# edges -- but reached by a different path through the lookup, and the two were
# indistinguishable until this existed.
UNMIRRORED = "U"
CANDIDATES = ("P", "Q", "R")


def row(name):
    return {"node_key": name, "title": name, "path": "/%s" % name}


def rankings(names=CANDIDATES):
    """The key's worked example: two models, opposite orders, one tail.

    `P` and `Q` swap between `m1` and `m3`, which is how an exact score tie
    arises -- the same two contributions in the other order. `R` trails in
    `m3` alone and is tied with nothing.
    """
    p, q, r = names
    return {"m1": [row(p), row(q)],
            "m3": [row(q), row(p), row(r)]}


def degrees_for(mesh_db, base=None) -> dict[str, int]:
    """Degrees from a mesh that exists, narrowed to a dict.

    `centrality_degrees` returns `None` for "no mesh", which is R3 and is
    checked on its own in `t_no_mesh_is_absent_not_zero`. Every other check
    here has built a fixture mesh, so `None` would be a fixture failure
    pretending to be a result.
    """
    got = Mesh({}, mesh_db=mesh_db).centrality_degrees(
        rankings() if base is None else base)
    assert got is not None, "fixture mesh %r did not answer" % (mesh_db,)
    return got


def expected_from_key():
    """The scores and both orders, read out of the key rather than recomputed.

    Recomputing `1/61 + 1/62` here with the same arithmetic the code uses
    proves the arithmetic agrees with itself. The key holds the numbers to six
    places and the two orders; this reads them.
    """
    with open(FASIT, encoding="utf-8") as fh:
        text = fh.read()
    scores = {n: float(s) for n, s in re.findall(
        r"^([A-Z])\s+.*?=\s+([\d.]+)$", text, re.M)}
    before = re.search(r"\| before H6 \| `(\w)`, `(\w)`, `(\w)` \|", text)
    after = re.search(r"\| after H6 \| \*\*`(\w)`, `(\w)`, `(\w)`\*\* \|", text)
    return scores, (list(before.groups()) if before else []), \
        (list(after.groups()) if after else [])


def locked_from_key(name):
    """A value the key fixes, read out of it instead of restated here.

    Two of the gate's numbers were asserted before the key wrote them down --
    the highest-across-models degree and the moved-count -- which is the wrong
    order, and an audit said so on 2026-08-02. They live in the key's
    "Values locked here" table now, and this reads them.
    """
    with open(FASIT, encoding="utf-8") as fh:
        text = fh.read().replace("`", "")
    found = re.search(r"^\| %s \| (\d+) \|$" % re.escape(name), text, re.M)
    assert found, "the key no longer locks %r" % (name,)
    return int(found.group(1))


def build_mesh():
    """A mesh whose nodes carry the key's degrees, built from real edges.

    The degrees are made by writing edges rather than by setting a number, so
    what the code counts is what the fixture planted. Only the `m3::` side is
    mirrored: `m1::Q` has no row, which is what makes the "highest across the
    models" rule in `centrality_degrees` observable.
    """
    work = tempfile.mkdtemp(prefix="h6-")
    mesh_db = os.path.join(work, "mesh.db")
    with Store(mesh_db, model="m5") as mesh:
        for name in ("P", "Q", "R", "D"):
            mesh.upsert_node("m3::%s" % name, kind="file", path="/%s" % name,
                             title=name, body=name, as_of="2026-08-01")
        # `Q` is mirrored under `m1` too, with FEWER edges. Without this the
        # highest and the sum are the same number here and the fixture cannot
        # tell them apart -- the mutation that sums them survived until this
        # existed (2026-08-02).
        mesh.upsert_node("m1::Q", kind="file", path="/Q", title="Q", body="Q",
                         as_of="2026-08-01")
        for i in range(2):
            other = "src-m1Q-%d" % i
            mesh.upsert_node(other, kind="file", path="/%s" % other,
                             as_of="2026-08-01")
            mesh.upsert_edge(other, "m1::Q", "MENTIONS_FILE", "2026-08-01",
                             method="mention")
        for name, degree in IN_DEGREE.items():
            for i in range(degree):
                other = "src-%s-%d" % (name, i)
                mesh.upsert_node(other, kind="file", path="/%s" % other,
                                 as_of="2026-08-01")
                mesh.upsert_edge(other, "m3::%s" % name, "MENTIONS_FILE",
                                 "2026-08-01", method="mention")
        for name, degree in OUT_DEGREE.items():
            for i in range(degree):
                other = "dst-%s-%d" % (name, i)
                mesh.upsert_node(other, kind="file", path="/%s" % other,
                                 as_of="2026-08-01")
                mesh.upsert_edge("m3::%s" % name, other, "MENTIONS_FILE",
                                 "2026-08-01", method="mention")
        _magnitude_nodes(mesh)
    return mesh_db


def _magnitude_nodes(mesh):
    """A second tied pair where BOTH degrees are nonzero and different.

    Every other tie in this suite has one member at degree 0, so the gate was
    testing *connected against unconnected* -- not *more connected against
    less*, which is what R1 is about. Found 2026-08-02 by an audit that wrote
    four mutations surviving the green suite: degree as a boolean, degree
    clamped at 4, degree divided by 4, and `COUNT(DISTINCT src)` instead of
    `COUNT(*)`. All four are indistinguishable from the truth when one side is
    zero. Fourth instance of [[uniform-fixture-cannot-discriminate]].

    `E` has four edges from four sources. `F` has five edges from three, one
    source asserting three relations -- so `COUNT(*)` is 5 and
    `COUNT(DISTINCT src)` is 3. Correct: `F` (5) leads `E` (4). Under any of
    the four mutations they tie and `key` puts `E` first.
    """
    for name in ("E", "F"):
        mesh.upsert_node("m3::%s" % name, kind="file", path="/%s" % name,
                         title=name, body=name, as_of="2026-08-01")
    for i in range(4):
        src = "src-E-%d" % i
        mesh.upsert_node(src, kind="file", path="/%s" % src, as_of="2026-08-01")
        mesh.upsert_edge(src, "m3::E", "MENTIONS_FILE", "2026-08-01",
                         method="mention")
    for i in range(3):
        src = "src-F-%d" % i
        mesh.upsert_node(src, kind="file", path="/%s" % src, as_of="2026-08-01")
        mesh.upsert_edge(src, "m3::F", "MENTIONS_FILE", "2026-08-01",
                         method="mention")
    # The same source, two further relations: five rows, three sources.
    for rel in ("CITES_CODE", "LIKELY_COPY"):
        mesh.upsert_edge("src-F-0", "m3::F", rel, "2026-08-01",
                         method="basename")


def magnitude_rankings():
    return {"m1": [row("E"), row("F")], "m3": [row("F"), row("E")]}


def t_magnitude_not_just_presence(mesh_db):
    """Two connected candidates: the MORE connected one wins, not either one."""
    degrees = degrees_for(mesh_db, magnitude_rankings())
    check("magnitude: both candidates in the second tie have edges",
          degrees == {"path:/E": 4, "path:/F": 5}, "%r" % (degrees,))
    order = [h["node_key"] for h in
             Mesh._rrf(magnitude_rankings(), 10, degrees)]
    check("magnitude: the higher degree leads, though both are nonzero",
          order == ["F", "E"], "%r" % (order,))
    # Without the tie-break the alphabet decides and `E` leads, so the check
    # above is a behaviour difference rather than the fixture's own order.
    plain = [h["node_key"] for h in Mesh._rrf(magnitude_rankings(), 10)]
    check("magnitude: and the alphabet would have said the opposite",
          plain == ["E", "F"], "%r" % (plain,))


def t_degrees_are_counted(mesh_db):
    degrees = degrees_for(mesh_db)
    # `Q` is cited three times and cites once. An implementation counting
    # `fanIn` alone reports 3, one counting `fanOut` alone reports 1.
    check("degree: a candidate ranks by fanIn + fanOut",
          degrees.get("path:/Q") == 4, "%r" % (degrees,))
    check("degree: a candidate with no edges is zero, not missing",
          degrees.get("path:/P") == 0, "%r" % (degrees,))
    check("degree: and one with edges outward only is counted too",
          degrees.get("path:/R") == 99, "%r" % (degrees,))
    # `m1::Q` has two edges, `m3::Q` has four. The document is one hit, so the
    # reported degree is the highest of its models -- not the first one looked
    # up (2), and not their sum (6). The hit already carries one RRF
    # contribution per model; summing would count the same popularity twice.
    check("degree: a document seen by two models takes the highest, not the sum",
          degrees.get("path:/Q") == locked_from_key("Q-degree")
          and len(degrees) == 3, "%r" % (degrees,))
    # A candidate the mesh has never heard of is 0, not absent from the map:
    # `_rrf` would then read it as `None` and the sort would differ from a
    # planted zero for no reason anyone could see.
    extra = degrees_for(mesh_db, {"m3": [row(UNMIRRORED)]})
    check("degree: a candidate with no mesh row is zero, not missing",
          extra == {"path:/U": 0}, "%r" % (extra,))


def t_code_candidates_are_not_qualified_twice(mesh_db):
    """Code rows come FROM the mesh, so their key is already `code::<path>`.

    Qualifying it again asks for `code::code::<path>`, which has never existed
    -- and a missing row is a legitimate zero, so the lookup fails in silence.
    Found by codex 2026-08-02 on the real mesh: every code candidate scored 0,
    including `main.py` at degree 69, the highest in the store.
    """
    work = tempfile.mkdtemp(prefix="h6-code-")
    db = os.path.join(work, "mesh.db")
    with Store(db, model="m5") as mesh:
        mesh.upsert_node("code::/x.py", kind="file", path="/x.py", title="x",
                         body="x", as_of="2026-08-01")
        for i in range(7):
            other = "src-x-%d" % i
            mesh.upsert_node(other, kind="file", path="/%s" % other,
                             as_of="2026-08-01")
            mesh.upsert_edge(other, "code::/x.py", "CITES_CODE", "2026-08-01",
                             method="basename")
    got = Mesh({}, mesh_db=db).centrality_degrees(
        {"code": [{"node_key": "code::/x.py", "title": "x", "path": "/x.py"}]})
    check("code: an already-qualified code key finds its own degree",
          got == {"path:/x.py": 7}, "%r" % (got,))
    # The other direction, so the fix cannot be "never qualify anything".
    model_side = Mesh({}, mesh_db=mesh_db).centrality_degrees(
        {"m3": [row("Q")]})
    check("code: and a model key is still qualified",
          model_side == {"path:/Q": 4}, "%r" % (model_side,))


def t_a_tie_across_the_cutoff_changes_the_window(mesh_db):
    """The one thing the tie-break CAN change about the returned set.

    When a tie straddles `limit`, the tie-break decides which of the tied hits
    falls inside the window -- and from outside that looks like a hit
    appearing. The key says so; this pins it, so nobody has to trust the
    sentence. Found by codex 2026-08-02, after the key claimed "by
    construction" for something that is only true of the candidate set.
    """
    base = rankings()
    degrees = degrees_for(mesh_db)
    narrow_plain = [h["node_key"] for h in Mesh._rrf(dict(base), 1)]
    narrow_broken = [h["node_key"] for h in Mesh._rrf(dict(base), 1, degrees)]
    check("cutoff: with limit 1 the alphabet used to decide",
          narrow_plain == ["P"], "%r" % (narrow_plain,))
    check("cutoff: and the degree decides instead",
          narrow_broken == ["Q"], "%r" % (narrow_broken,))
    check("cutoff: which is a different hit, not a reordering of the same one",
          narrow_plain != narrow_broken, "%r" % (narrow_broken,))


def t_an_unreadable_mesh_does_not_take_search_down():
    """Centrality is optional. A mesh that cannot be read makes it absent."""
    work = tempfile.mkdtemp(prefix="h6-broken-")
    broken = os.path.join(work, "mesh.db")
    with open(broken, "wb") as fh:
        fh.write(b"this is not a database, not even close\n" * 64)
    paths = _search_stores(work)
    # Caught rather than allowed to propagate: an implementation that lets the
    # error out must be a FAILED CHECK, not a dead suite. A harness cannot tell
    # a crash from a gate saying no, and "detected only by a crash" is the
    # weakest verdict this project accepts.
    try:
        got = Mesh(paths, mesh_db=broken).search("quaywise", limit=10)
    except Exception as exc:                                 # noqa: BLE001
        check("broken: a corrupt mesh reports centrality absent", False,
              "search raised %r" % (exc,))
        check("broken: and the models still answer", False,
              "search raised %r" % (exc,))
        return
    check("broken: a corrupt mesh reports centrality absent",
          got.centrality == "absent", repr(got.centrality))
    check("broken: and the models still answer",
          [h["node_key"] for h in got.hits] == ["P", "Q", "R"],
          "%r" % ([h["node_key"] for h in got.hits],))


def t_the_order_changes_as_the_key_says(mesh_db):
    scores, before_key, after_key = expected_from_key()
    check("key: the key names three scores and both orders",
          len(scores) == 3 and len(before_key) == 3 and len(after_key) == 3,
          "%d score(s), before=%r after=%r"
          % (len(scores), before_key, after_key))

    base = rankings()
    degrees = degrees_for(mesh_db)
    before = [h["node_key"] for h in Mesh._rrf(dict(base), 10)]
    fused = Mesh._rrf(dict(base), 10, degrees)
    after = [h["node_key"] for h in fused]

    check("order: without the tie-break the order is the models' own",
          before == before_key, "%r vs key %r" % (before, before_key))
    check("order: with it, the key's order", after == after_key,
          "%r vs key %r" % (after, after_key))
    # The scores, not only their ordering: an implementation that added degree
    # INTO the score produces a different set of numbers here, and that is
    # exactly the design this key replaced.
    got = {h["node_key"]: round(h["score"], 6) for h in fused}
    wrong = [(n, got.get(n), s) for n, s in scores.items()
             if got.get(n) != round(s, 6)]
    check("order: and the fused scores are untouched by the tie-break",
          not wrong, "; ".join("%s got %r want %r" % w for w in wrong))
    check("order: the two tied hits really do have equal scores",
          got.get("P") == got.get("Q"), "%r" % (got,))


def t_the_tiebreak_cannot_cross_a_score(mesh_db):
    """`R` has degree 99 and stays last, because it scored lower.

    This is the check that tells the surviving design from the fourth-list one
    it replaced. Under a list, `R` gains `1/61` and overtakes both tied hits;
    under a tie-break it cannot move at all.
    """
    base = rankings()
    degrees = degrees_for(mesh_db)
    fused = Mesh._rrf(dict(base), 10, degrees)
    order = [h["node_key"] for h in fused]
    check("bounded: the most connected candidate stays behind the tie",
          order[-1] == "R", "%r" % (order,))
    check("bounded: and it is behind because it scored lower",
          round(fused[-1]["score"], 6) < round(fused[0]["score"], 6),
          "%r" % ([round(h["score"], 6) for h in fused],))
    # The set, not only the order. A tie-break adds nothing and drops nothing.
    check("bounded: the result set is unchanged, only its order",
          sorted(order) == sorted(CANDIDATES), "%r" % (order,))


def t_a_central_non_candidate_never_enters(mesh_db):
    """`D` has the highest degree in the store and no model returned it."""
    base = rankings()
    degrees = degrees_for(mesh_db)
    check("candidates: the most central node in the store is not scored",
          "path:/D" not in degrees, "%r" % (sorted(degrees),))
    after = [h["node_key"] for h in Mesh._rrf(dict(base), 10, degrees)]
    check("candidates: and it is not in the result", "D" not in after,
          "%r" % (after,))


def t_no_mesh_is_absent_not_zero():
    """"No mesh" and "everything scored zero" are different answers."""
    mesh = Mesh({}, mesh_db=os.path.join(tempfile.mkdtemp(), "nope.db"))
    check("absent: a mesh path that does not exist yields no degrees",
          mesh.centrality_degrees(rankings()) is None, "not None")
    none_configured = Mesh({}, mesh_db=None)
    check("absent: and the same with no mesh configured at all",
          none_configured.centrality_degrees(rankings()) is None, "not None")
    # With no degrees the sort must fall back to key, not to `None` ordering.
    order = [h["node_key"] for h in Mesh._rrf(rankings(), 10, None)]
    check("absent: and the fusion still sorts, by key",
          order == ["P", "Q", "R"], "%r" % (order,))


def t_ties_are_broken_deterministically(mesh_db):
    """Equal score AND equal degree must not swap between runs.

    Two candidates with no edges tie on both terms, so `key` decides. A verdict
    that varies across runs of identical code is the failure `mutate_gui`'s
    random seed turned out to be.
    """
    # `Z` reaches the fusion FIRST and sorts LAST by key. Built the other way
    # round, a sort that dropped the key term would still produce key order --
    # Python's sort is stable -- and the mutation that drops it survived until
    # this fixture disagreed with itself (2026-08-02).
    base = {"m1": [row("Z"), row("Y")], "m3": [row("Y"), row("Z")]}
    degrees = degrees_for(mesh_db, base)
    check("ties: both candidates are degree 0 here",
          degrees == {"path:/Y": 0, "path:/Z": 0}, "%r" % (degrees,))
    check("ties: and the one that arrives first is not the one key puts first",
          list(Mesh._rrf(dict(base), 10, degrees))[0]["node_key"] == "Y",
          "arrival order was Z, Y")
    runs = {tuple(h["node_key"] for h in Mesh._rrf(dict(base), 10, degrees))
            for _ in range(5)}
    check("ties: five runs give one ordering", len(runs) == 1, "%r" % (runs,))
    check("ties: and it is by key, not by arrival",
          runs.pop() == ("Y", "Z"), "see above")


def t_centrality_is_visible_but_is_not_a_model(mesh_db):
    """It orders; it finds nothing. No hit may claim it as a source."""
    base = rankings()
    degrees = degrees_for(mesh_db)
    fused = Mesh._rrf(dict(base), 10, degrees)
    top = fused[0]
    check("report: no hit names centrality among the models it came from",
          all("centrality" not in h["models"] for h in fused),
          "%r" % (top["models"],))
    check("report: and no fusion source is named centrality",
          all(not s.startswith("centrality")
              for h in fused for s in h["sources"]),
          "%r" % (top["sources"],))
    check("report: the hit keeps its real model", top["model"] in ("m1", "m3"),
          repr(top["model"]))
    # The other half: it must still be visible SOMEWHERE, or a reordering has
    # happened that nobody can see.
    check("report: every hit carries the degree that ordered it",
          [h["degree"] for h in fused] == [4, 0, 99],
          "%r" % ([h["degree"] for h in fused],))
    plain = Mesh._rrf(dict(base), 10)
    check("report: and with no mesh the degree is None, not 0",
          all(h["degree"] is None for h in plain),
          "%r" % ([h["degree"] for h in plain],))


def _search_stores(work):
    """Two stores whose BM25 orders are opposite, so the fusion really ties.

    Shorter body ranks higher for the same term, so `m1` puts `P` first and
    `m3` puts `Q` first. `R` trails in `m3` alone. `S` and `T` are a second
    query whose candidates have no mesh rows at all.
    """
    m1, m3 = os.path.join(work, "m1.db"), os.path.join(work, "m3.db")
    with Store(m1, model="m1") as store:
        store.upsert_node("P", kind="file", path="/P", title="P",
                          body="quaywise", as_of="2026-08-01")
        store.upsert_node("Q", kind="file", path="/Q", title="Q",
                          body="quaywise and more words here", as_of="2026-08-01")
        store.upsert_node("S", kind="file", path="/S", title="S",
                          body="flotsam", as_of="2026-08-01")
        store.upsert_node("T", kind="file", path="/T", title="T",
                          body="flotsam and more words here", as_of="2026-08-01")
        store.rebuild_fts()
    with Store(m3, model="m3") as store:
        store.upsert_node("Q", kind="file", path="/Q", title="Q",
                          body="quaywise", as_of="2026-08-01")
        store.upsert_node("P", kind="file", path="/P", title="P",
                          body="quaywise and more words here", as_of="2026-08-01")
        store.upsert_node("R", kind="file", path="/R", title="R",
                          body="quaywise and even more words here than that",
                          as_of="2026-08-01")
        store.upsert_node("T", kind="file", path="/T", title="T",
                          body="flotsam", as_of="2026-08-01")
        store.upsert_node("S", kind="file", path="/S", title="S",
                          body="flotsam and more words here", as_of="2026-08-01")
        store.rebuild_fts()
    return {"m1": m1, "m3": m3}


def t_search_actually_uses_it(mesh_db):
    """Through `search()`, not through `_rrf` -- the wiring, not the arithmetic.

    Every check above hands the degrees to `_rrf` itself. A build that computes
    them perfectly and then never passes them to the sort passes all of them:
    the parts are right and the path is dead. Found 2026-08-01 by `mutate_h6.py`,
    whose "computed and never used" mutation survived until this existed.
    """
    work = tempfile.mkdtemp(prefix="h6-search-")
    paths = _search_stores(work)

    plain = Mesh(paths, mesh_db=None).search("quaywise", limit=10)
    fused = Mesh(paths, mesh_db=mesh_db).search("quaywise", limit=10)

    check("search: with no mesh the result reports centrality absent",
          plain.centrality == "absent", repr(plain.centrality))
    check("search: the same documents come back either way",
          sorted(h["node_key"] for h in plain.hits)
          == sorted(h["node_key"] for h in fused.hits),
          "%r vs %r" % ([h["node_key"] for h in plain.hits],
                        [h["node_key"] for h in fused.hits]))
    check("search: the fusion really produced a tie to break",
          len({round(h["score"], 9) for h in fused.hits}) < len(fused.hits),
          "%r" % ([round(h["score"], 9) for h in fused.hits],))
    check("search: the more connected of the tied pair is first",
          fused.hits and fused.hits[0]["node_key"] == "Q",
          "%r" % ([h["node_key"] for h in fused.hits],))
    check("search: and it was not first before",
          plain.hits and plain.hits[0]["node_key"] == "P",
          "%r" % ([h["node_key"] for h in plain.hits],))
    check("search: it reports how many positions it moved",
          fused.centrality == locked_from_key("positions-moved"),
          repr(fused.centrality))
    # Prediction 4 in the key: nothing to say, nothing said.
    flat_plain = Mesh(paths, mesh_db=None).search("flotsam", limit=10)
    flat = Mesh(paths, mesh_db=mesh_db).search("flotsam", limit=10)
    check("search: a query whose candidates are all degree 0 is unchanged",
          [h["node_key"] for h in flat.hits]
          == [h["node_key"] for h in flat_plain.hits],
          "%r vs %r" % ([h["node_key"] for h in flat.hits],
                        [h["node_key"] for h in flat_plain.hits]))
    check("search: and it reports that it moved nothing",
          flat.centrality == 0, repr(flat.centrality))
    # Deliberately NOT checked here: "nothing absent before appears after".
    # With limit=10 over three candidates nothing can be truncated, so the two
    # sets are equal whatever the code does. The case that can differ is a tie
    # straddling the cutoff, and it has its own check above.


def t_explain_reports_it_on_its_own_line(mesh_db):
    work = tempfile.mkdtemp(prefix="h6-explain-")
    paths = _search_stores(work)
    out = Mesh(paths, mesh_db=mesh_db).explain("quaywise", limit=10)
    check("explain: centrality has its own line",
          out.get("centrality") == locked_from_key("positions-moved"),
          repr(out.get("centrality")))
    check("explain: and it is not counted as a store that answered",
          "centrality" not in out["hits_per_model"],
          "%r" % (out["hits_per_model"],))
    check("explain: the fusion description says how ties are ordered",
          "fanIn+fanOut" in out["fusion"], repr(out["fusion"]))


def main() -> int:
    check("fixture: RRF_K is the constant the key computed with", RRF_K == 60,
          "RRF_K=%r" % (RRF_K,))
    mesh_db = build_mesh()
    t_degrees_are_counted(mesh_db)
    t_magnitude_not_just_presence(mesh_db)
    t_code_candidates_are_not_qualified_twice(mesh_db)
    t_a_tie_across_the_cutoff_changes_the_window(mesh_db)
    t_an_unreadable_mesh_does_not_take_search_down()
    t_the_order_changes_as_the_key_says(mesh_db)
    t_the_tiebreak_cannot_cross_a_score(mesh_db)
    t_a_central_non_candidate_never_enters(mesh_db)
    t_no_mesh_is_absent_not_zero()
    t_ties_are_broken_deterministically(mesh_db)
    t_centrality_is_visible_but_is_not_a_model(mesh_db)
    t_search_actually_uses_it(mesh_db)
    t_explain_reports_it_on_its_own_line(mesh_db)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h6():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
