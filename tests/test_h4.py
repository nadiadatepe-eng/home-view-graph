#!/usr/bin/env python3
"""CP-H4 -- a section node knows where it sits in the heading tree.

The section nodes were already there: `m3_build` has written one per heading,
keyed `path#i`, since M3 existed. What they did not carry was the tree above
them, whether they were a leaf, or a digest of the text underneath -- so
"which part of the document is this" and "has this part changed" were both
questions the graph could not answer.

**The answer key was written first**, before any of this code existed:
`tests/gold/FASIT-h4.md` fixes the five headings, their ancestor chains, their
leaf flags and their five full digests. The digests below are READ OUT OF THAT
FILE rather than recomputed here. A test that recomputes the expected value
with the same function it is testing agrees with itself and proves nothing.

Every positive check has a negative control, because most of the ways this can
be wrong are silently uniform: an implementation that calls every heading a
leaf, one that returns the flat title as the path, one that hashes the same
thing for every section. Each of those passes half the checks below and fails
the other half by construction.

Run:
    python3 tests/test_h4.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from homegraph.models.m3_markdown import (BREADCRUMB,      # noqa: E402
                                          MarkdownExtractor)
from homegraph.store import Store                          # noqa: E402

results, check = reporter(58)

# Verbatim from the answer key. Kept here as a string rather than parsed out of
# the markdown so an edit to the prose around it cannot change what is parsed.
DOC = """---
title: Fasit H4
---
# Alpha
intro under alpha

## Beta

### Gamma
gamma text with `inline code` here

## Delta > Epsilon
delta text

```python
# not a heading
```

# Zeta
"""

FASIT = os.path.join(HERE, "gold", "FASIT-h4.md")

EXPECTED_PATHS = {
    0: ["Alpha"],
    1: ["Alpha", "Beta"],
    2: ["Alpha", "Beta", "Gamma"],
    3: ["Alpha", "Delta > Epsilon"],
    4: ["Zeta"],
}
EXPECTED_LEAF = {0: False, 1: False, 2: True, 3: True, 4: True}


def expected_hashes():
    """The digests, read out of the answer key, in two groups.

    The key has two documents and both number their rows from 0, so a single
    pass over the file silently lets the addendum overwrite the worked example.
    That is not hypothetical -- it happened on the first run of this gate, and
    the checks went red rather than quietly comparing the wrong five, which is
    the only reason it was visible. Split on the addendum heading, and assert
    the shape of each group before trusting either.
    """
    with open(FASIT, encoding="utf-8") as fh:
        head, _, tail = fh.read().partition("\n## Addendum")

    def digests(text):
        return {int(i): d for i, _t, d
                in re.findall(r"^(\d) (.+?)\s{2,}([0-9a-f]{64})$", text, re.M)}

    return digests(head), digests(tail)


def t_key_is_usable():
    main, siblings = expected_hashes()
    check("key: the answer key names five digests for the worked example",
          len(main) == 5,
          "%d found in %s" % (len(main), os.path.relpath(FASIT, ROOT)))
    check("key: and two for the addendum", len(siblings) == 2,
          "%d found" % len(siblings))
    # Distinct across BOTH groups: the two documents share no text, so a
    # collision anywhere would mean the digests are not of what they claim.
    every = list(main.values()) + list(siblings.values())
    check("key: all seven digests are distinct",
          len(set(every)) == len(every) == 7,
          "%d distinct of %d" % (len(set(every)), len(every)))
    return main, siblings


def t_headings_found(tree, sections):
    # Six would mean `# not a heading` inside the fence was counted, which is
    # the case that decides whether offsets still come from the blanked text.
    check("tree: five headings, not six", len(sections) == 5,
          "%d found: %s" % (len(sections),
                            [s["title"] for s in sections]))
    check("tree: the tree is parallel to the sections",
          len(tree) == len(sections),
          "%d vs %d" % (len(tree), len(sections)))


def t_heading_path(tree):
    wrong = [(i, tree[i]["heading_path"], want)
             for i, want in EXPECTED_PATHS.items()
             if i < len(tree) and tree[i]["heading_path"] != want]
    check("path: every ancestor chain matches the key", not wrong,
          "; ".join("#%d got %r want %r" % w for w in wrong))

    # Negative control for a path that is just the title repeated: `Gamma` is
    # three deep and `Zeta` is one. An implementation returning `[title]` passes
    # Zeta and fails Gamma; one returning every preceding heading fails Zeta.
    check("path: a deep heading has three ancestors",
          len(tree) > 2 and len(tree[2]["heading_path"]) == 3,
          "%r" % (tree[2]["heading_path"] if len(tree) > 2 else None,))
    check("path: a root heading has exactly itself",
          len(tree) > 4 and tree[4]["heading_path"] == ["Zeta"],
          "%r" % (tree[4]["heading_path"] if len(tree) > 4 else None,))


def t_leaf(tree):
    wrong = [(i, tree[i]["leaf"], want) for i, want in EXPECTED_LEAF.items()
             if i < len(tree) and tree[i]["leaf"] != want]
    check("leaf: every flag matches the key", not wrong,
          "; ".join("#%d got %r want %r" % w for w in wrong))

    # The two controls that kill "always True" and "always False". Both must
    # hold; either alone is passed by a constant.
    check("leaf: at least one heading is a leaf",
          any(t["leaf"] for t in tree),
          "%d of %d" % (sum(t["leaf"] for t in tree), len(tree)))
    check("leaf: at least one heading is not",
          any(not t["leaf"] for t in tree),
          "%d of %d" % (sum(not t["leaf"] for t in tree), len(tree)))


def t_hashes(tree, digests):
    wrong = [(i, tree[i]["content_hash"][:16], want[:16])
             for i, want in digests.items()
             if i < len(tree) and tree[i]["content_hash"] != want]
    check("hash: every digest matches the key", not wrong,
          "; ".join("#%d got %s want %s" % w for w in wrong))
    check("hash: the five digests are distinct",
          len({t["content_hash"] for t in tree}) == len(tree),
          "%d distinct of %d" % (len({t["content_hash"] for t in tree}),
                                 len(tree)))


def t_hash_sees_inside_a_code_fence():
    """R4's second consequence, measured rather than asserted in prose.

    Offsets must be computed on the blanked text or a `#` in a fence becomes a
    heading. The digest must be taken from the RAW text or an edit inside that
    fence changes nothing the incremental path can see: content changed, hash
    unchanged, section reported clean. Only this check tells the two apart --
    every other check in this file passes either way.
    """
    # The edit must be the SAME LENGTH as what it replaces. `blank_code`
    # replaces code with spaces one for one, so a longer edit changes the
    # blanked text too -- and this check then passes for an implementation
    # hashing the blanked text, which is the one thing it exists to reject.
    # Measured 2026-08-01: with `", edited"` appended, the mutation that hashes
    # `clean` was reported killed by a different gate, and this check stayed
    # green through it.
    edited = DOC.replace("# not a heading", "# not a heaping")
    assert len(edited) == len(DOC), "the edit changed the document's length"
    before = MarkdownExtractor().extract("k.md", DOC)["section_tree"]
    after = MarkdownExtractor().extract("k.md", edited)["section_tree"]
    check("hash: an edit inside a code fence changes its section's digest",
          len(before) > 3 and len(after) > 3
          and before[3]["content_hash"] != after[3]["content_hash"],
          "%s vs %s" % (before[3]["content_hash"][:16],
                        after[3]["content_hash"][:16]))
    # And it must change ONLY that section. A digest covering the subtree would
    # restamp the ancestors too, which is the definition this key rejects.
    moved = [i for i, (b, a) in enumerate(zip(before, after))
             if b["content_hash"] != a["content_hash"]]
    check("hash: and changes no other section's digest", moved == [3],
          "sections that moved: %s" % moved)


def t_breadcrumb_is_derived_not_stored_twice(tree):
    """`body` is the joined path, and the join is not reversible.

    `Delta > Epsilon` is in the key for this: its breadcrumb has three
    separator-delimited parts and its path has two elements. A test that only
    compared `body` strings could not see the difference, which is the whole
    argument for storing the list.
    """
    body = BREADCRUMB.join(tree[3]["heading_path"])
    check("body: the breadcrumb is the path joined",
          body == "Alpha > Delta > Epsilon", repr(body))
    check("body: splitting it back gives the WRONG arity",
          len(body.split(BREADCRUMB)) == 3
          and len(tree[3]["heading_path"]) == 2,
          "%d parts vs %d elements" % (len(body.split(BREADCRUMB)),
                                       len(tree[3]["heading_path"])))


SIBLINGS = """## Sibling One
first

## Sibling Two
second
"""


def t_siblings(digests):
    """The case the first document cannot see -- see the key's addendum.

    Nothing in the worked example has two adjacent headings of the same level,
    so `level <=` and `level <` agree on all five rows, and so do an ancestor
    stack popping on `>=` and one popping on `>`. Both wrong rules are ordinary
    off-by-one edits and both pass every other check in this file.
    """
    tree = MarkdownExtractor().extract("s.md", SIBLINGS)["section_tree"]
    check("sibling: a heading followed by its own sibling is still a leaf",
          len(tree) == 2 and tree[0]["leaf"] is True,
          "%r" % ([t["leaf"] for t in tree],))
    check("sibling: and the second is not a descendant of the first",
          len(tree) == 2 and tree[1]["heading_path"] == ["Sibling Two"],
          "%r" % (tree[1]["heading_path"] if len(tree) > 1 else None,))
    wrong = [(i, tree[i]["content_hash"][:16], want[:16])
             for i, want in digests.items()
             if i < len(tree) and tree[i]["content_hash"] != want]
    check("sibling: both digests match the addendum", not wrong,
          "; ".join("#%d got %s want %s" % w for w in wrong))


def t_build_writes_the_columns(digests):
    """The wiring, not the arithmetic: a real build must store what it computed.

    Everything above tests `heading_tree` directly. A build that computes the
    tree perfectly and then forgets to pass one of the three to `upsert_node`
    passes all of it -- the fields would be correct everywhere except in the
    graph, which is the only place anyone reads them.
    """
    from homegraph.models import m3_build

    work = tempfile.mkdtemp(prefix="h4-build-")
    page = os.path.join(work, "page.md")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(DOC)
    db = os.path.join(work, "m3.db")
    with Store(db, model="m3") as store:
        m3_build.build(store, [page], "2026-08-01")
        rows = store.db.execute(
            "SELECT node_key, body, heading_path, section_leaf, content_hash "
            "FROM nodes WHERE kind = 'section' ORDER BY node_key").fetchall()

    check("build: the build writes one section node per heading",
          len(rows) == 5, "%d section node(s)" % len(rows))
    if len(rows) != 5:
        return
    by_index = {int(r["node_key"].rsplit("#", 1)[1]): r for r in rows}
    third = by_index[3]
    check("build: the stored body is the breadcrumb, not the bare heading",
          third["body"] == "Alpha > Delta > Epsilon", repr(third["body"]))
    # NULL is the answer a forgotten keyword argument gives, and `json.loads`
    # raises on it. A gate that dies here reports CRASH rather than saying no,
    # which is a worse report of the same fact -- found 2026-08-01 by
    # `mutate_h4.py`, whose "the build forgets to pass heading_path" mutation
    # was detected only by the traceback.
    stored = third["heading_path"]
    check("build: the stored ancestor list keeps the separator inside a title",
          stored is not None
          and json.loads(stored) == ["Alpha", "Delta > Epsilon"],
          "NULL" if stored is None else stored)
    check("build: the stored leaf flag distinguishes leaf from parent",
          third["section_leaf"] == 1 and by_index[0]["section_leaf"] == 0,
          "#3=%r #0=%r" % (third["section_leaf"],
                           by_index[0]["section_leaf"]))
    # `all(truthy)` is true for ANY string, so this alone passes for a build
    # that stamps the FILE's digest on every section -- a one-line copy of the
    # file upsert twenty lines above it, and a mutation that survived twelve
    # harnesses when the sim-auditor tried it on 2026-08-01. The stored values
    # are compared to the key, and to each other.
    check("build: every section node carries a digest",
          all(r["content_hash"] for r in rows),
          "%d of %d" % (sum(1 for r in rows if r["content_hash"]), len(rows)))
    stored = [by_index[i]["content_hash"] for i in sorted(by_index)]
    wrong = [(i, stored[i][:16], want[:16]) for i, want in digests.items()
             if i < len(stored) and stored[i] != want]
    check("build: and the STORED digest is the section's, not the file's",
          not wrong, "; ".join("#%d got %s want %s" % w for w in wrong))
    check("build: and the five stored digests differ from each other",
          len(set(stored)) == len(stored),
          "%d distinct of %d" % (len(set(stored)), len(stored)))


def t_store_round_trip(tree):
    """The columns survive SQLite, and a non-section keeps NULL."""
    db = os.path.join(tempfile.mkdtemp(prefix="h4-"), "t.db")
    with Store(db, model="m3") as store:
        store.upsert_node("k.md#3", kind="section",
                          heading_path=tree[3]["heading_path"],
                          section_leaf=tree[3]["leaf"],
                          content_hash=tree[3]["content_hash"],
                          as_of="2026-08-01")
        store.upsert_node("k.md#4", kind="section", heading_path=[],
                          section_leaf=False, as_of="2026-08-01")
        store.upsert_node("k.md", kind="file", as_of="2026-08-01")

        def row(key):
            return store.db.execute(
                "SELECT heading_path, section_leaf, content_hash "
                "FROM nodes WHERE node_key = ?", (key,)).fetchone()

        got = row("k.md#3")
        check("store: the separator inside a heading survives the round trip",
              json.loads(got["heading_path"]) == ["Alpha", "Delta > Epsilon"],
              got["heading_path"])
        check("store: the leaf flag survives as 1",
              got["section_leaf"] == 1, repr(got["section_leaf"]))

        # Absent and false are different facts. An empty ancestor list is a
        # real answer (a `#` has no ancestors) and a non-leaf is a real 0;
        # only a node that is not a section at all gets NULL.
        empty = row("k.md#4")
        check("store: an empty ancestor list is stored, not nulled",
              empty["heading_path"] == "[]", repr(empty["heading_path"]))
        check("store: a non-leaf section stores 0, not NULL",
              empty["section_leaf"] == 0, repr(empty["section_leaf"]))
        plain = row("k.md")
        check("store: a node that is not a section keeps NULL",
              plain["heading_path"] is None and plain["section_leaf"] is None,
              "%r / %r" % (plain["heading_path"], plain["section_leaf"]))

        # The update path is a separate SQL statement from the insert, and a
        # column added to one and forgotten in the other reads correctly right
        # up until the second build.
        store.upsert_node("k.md#3", kind="section", heading_path=["X"],
                          section_leaf=False, as_of="2026-08-02")
        again = row("k.md#3")
        check("store: a rebuild updates the columns rather than keeping stale "
              "ones",
              again["heading_path"] == '["X"]' and again["section_leaf"] == 0,
              "%r / %r" % (again["heading_path"], again["section_leaf"]))


def t_export_round_trip():
    """A portable export and reimport must not quietly drop the new columns.

    Found 2026-08-01 by external review, not by any test: `NODE_COLUMNS` in
    `export.py` is a hand-maintained list, and a column added to `nodes` and
    forgotten there fails nowhere. The export succeeds, the import succeeds,
    and the graph that comes back has less in it than the one that went in --
    the same hardcoded-list shape as `pyproject.toml`'s collect list and the
    CONTRIBUTING loop, on the third list in this repo to be caught by it.
    """
    from homegraph.export import export
    from homegraph.importer import load

    work = tempfile.mkdtemp(prefix="h4-rt-")
    page = os.path.join(work, "page.md")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(DOC)
    src, dst = os.path.join(work, "a.db"), os.path.join(work, "b.db")
    bundle = os.path.join(work, "bundle")

    from homegraph.models import m3_build
    with Store(src, model="m3") as store:
        m3_build.build(store, [page], "2026-08-01")
    try:
        written = export({"m3": src}, bundle, root=work, redaction="full")
        with Store(dst, model="m3") as into:
            load(written.get("path", bundle), {"m3": into}, root=work)
    except Exception as exc:                                 # noqa: BLE001
        check("export: a bundle round trip keeps heading_path", False,
              "%s: %s" % (type(exc).__name__, exc))
        check("export: and keeps section_leaf", False, "see above")
        return

    with Store(dst, model="m3") as store:
        rows = {r["node_key"].rsplit("#", 1)[1]: r for r in store.db.execute(
            "SELECT node_key, heading_path, section_leaf FROM nodes "
            "WHERE kind = 'section'")}
    got = rows.get("3")
    check("export: a bundle round trip keeps heading_path",
          got is not None and got["heading_path"] is not None
          and json.loads(got["heading_path"]) == ["Alpha", "Delta > Epsilon"],
          "NULL" if got is None else repr(got["heading_path"]))
    # Both directions of the flag, so a reimport that writes a constant is not
    # mistaken for one that carried the value.
    check("export: and keeps section_leaf on both a leaf and a parent",
          got is not None and got["section_leaf"] == 1
          and rows.get("0") is not None and rows["0"]["section_leaf"] == 0,
          "#3=%r #0=%r" % (got and got["section_leaf"],
                           rows.get("0") and rows["0"]["section_leaf"]))


def t_shape_export_does_not_publish_the_outline():
    """`shape` hashes the title; it must not print the same words beside it.

    Found 2026-08-01 by adversarial review. `SHAPE_DROPS` listed `body` and
    `redact()` digested `title`, so the level claimed to publish structure
    without labels -- and `heading_path` carried the labels verbatim in a
    column nobody had added to either list. A note titled after a deal would
    export a hashed title next to the deal's name.
    """
    from homegraph.export import redact

    row = {"node_key": "/x/secret.md#1", "kind": "section",
           "title": "Price 4.2 MNOK", "body": "Acquisition > Price 4.2 MNOK",
           "heading_path": '["Acquisition of NorthCorp", "Price 4.2 MNOK"]',
           "section_leaf": 1, "content_hash": "abc", "path": "/x/secret.md"}
    out = redact(dict(row), "shape")
    check("shape: the heading text is not published beside a hashed title",
          "heading_path" not in out,
          "heading_path survived as %r" % (out.get("heading_path"),))
    # The control: `shape` is meant to keep structure, and the leaf flag is
    # structure. Dropping everything would also pass the check above.
    check("shape: and the leaf flag, which names nobody, is kept",
          out.get("section_leaf") == 1, repr(out.get("section_leaf")))


def t_mesh_does_not_fuse_sections_across_files():
    """A section's digest identifies a fragment, not a document.

    Until CP-H4 sections carried NULL `content_hash` and `_fusion_key` fell
    through to path. The day they gained a digest, two files sharing a
    paragraph became one entry in the fused result and one side vanished --
    measured that day at 90 % of 33 010 section nodes sharing a digest with
    at least one other, most of them the digest of a single newline.
    """
    from homegraph.mesh import Mesh

    def key(node_key, digest):
        return Mesh._fusion_key({"node_key": node_key, "kind": "section",
                                 "path": node_key.rsplit("#", 1)[0],
                                 "content_hash": digest, "model": "m3"})

    same = "3ed18cdb87d33b98"
    check("mesh: two files sharing a paragraph stay two entries",
          key("/x/alpha.md#0", same) != key("/x/beta.md#0", same),
          "%s vs %s" % (key("/x/alpha.md#0", same), key("/x/beta.md#0", same)))
    # The control: fusing on the digest is still RIGHT for whole documents,
    # which is what the key exists for. Removing it entirely would pass above.
    doc = Mesh._fusion_key({"node_key": "/x/a.md", "kind": "file",
                            "path": "/x/a.md", "content_hash": same})
    check("mesh: and a file is still fused on its digest",
          doc == "hash:%s" % same, doc)


def main() -> int:
    digests, sibling_digests = t_key_is_usable()
    data = MarkdownExtractor().extract("k.md", DOC)
    tree, sections = data["section_tree"], data["sections"]

    t_headings_found(tree, sections)
    t_heading_path(tree)
    t_leaf(tree)
    t_hashes(tree, digests)
    t_hash_sees_inside_a_code_fence()
    t_breadcrumb_is_derived_not_stored_twice(tree)
    t_siblings(sibling_digests)
    t_build_writes_the_columns(digests)
    t_store_round_trip(tree)
    t_export_round_trip()
    t_shape_export_does_not_publish_the_outline()
    t_mesh_does_not_fuse_sections_across_files()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h4():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
