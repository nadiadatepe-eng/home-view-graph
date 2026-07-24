#!/usr/bin/env python3
"""CP-H1 -- corpus-driven eval pairs, generated leakage-free.

The scoreboard (scoreboard.py) is only honest if its pairs do not encode their
own answers. This module builds `(query, expected_node_id)` pairs from the
markdown corpus with one cheap, defensible source -- a section heading -> the
FILE that contains it -- guarded so the query is not a trivial self-match:

  * UNIQUE heading only. A heading that appears in two files has no single right
    answer, so it is dropped, never guessed at.
  * Not the file's own `title`. The file node's title is FTS-indexed on the file
    itself, so a title query is a self-match that would inflate recall.
  * >= 2 words. A one-word heading ("Notes", "Overview") measures tokenizer
    luck, not retrieval.
  * The expected file's subtype is not one search hides by default -- otherwise
    a real hit is scored as a miss for a reason unrelated to retrieval quality.

The heading text still lives in the file's BODY, so this measures **lexical**
retrieval of the right document: the FTS baseline that CP-H3 (semantic search)
must beat. It deliberately does NOT measure paraphrase/synonym recall -- those
pairs need hand-authoring, and they are exactly the headroom semantic search is
for. That limitation is real and is flagged for the sim-auditor, not hidden.

Run standalone to print the FTS baseline over a fresh synthetic corpus:
    python3 tests/eval/build_eval.py
"""
from __future__ import annotations

import os
from collections.abc import Sequence

from homegraph.search import DEFAULT_HIDDEN_SUBTYPES, fts_search
from tests.eval.scoreboard import Pair, Score, SearchFn, evaluate

AS_OF = "2026-07-22"


def build_m3_corpus(root: str, syn) -> str:
    """Build an m3 (markdown) store over the synthetic corpus rooted at `root`.

    Mirrors the CP-2 build path: classify with the real Classifier, feed only
    the markdown files, rebuild FTS. The store db is written beside `root`.
    """
    from homegraph.corpus import Classifier
    from homegraph.models.m3_build import build as m3_build
    from homegraph.store import Store

    syn.build(root)
    syn.use_config(syn._config_for(root))
    clf = Classifier(home=root)
    paths = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                if clf.classify(p) == "markdown":
                    paths.append(p)
            except Exception:                                   # noqa: BLE001
                continue
    db = os.path.join(os.path.dirname(root), "%s-m3.db" % os.path.basename(root))
    with Store(db, model="m3") as s:
        m3_build(s, sorted(paths), AS_OF)
        s.rebuild_fts()
    return db


def build_pairs(store, min_words: int = 2,
                hidden: Sequence[str] = DEFAULT_HIDDEN_SUBTYPES) -> list[Pair]:
    """(section heading -> parent FILE node id), with the four leakage guards."""
    files: dict[str, dict] = {}
    for r in store.db.execute(
            "SELECT id, node_key, title, subtype FROM nodes WHERE kind='file'"):
        files[r["node_key"]] = {
            "id": r["id"],
            "title": (r["title"] or "").strip(),
            "subtype": r["subtype"],
        }
    heads: dict[str, set[str]] = {}
    for r in store.db.execute(
            "SELECT node_key, title FROM nodes WHERE kind='section'"):
        parent = r["node_key"].rsplit("#", 1)[0]
        heads.setdefault((r["title"] or "").strip(), set()).add(parent)

    hidden_set = set(hidden)
    pairs: list[Pair] = []
    for head, parents in heads.items():
        if len(parents) != 1:                 # ambiguous -> drop, never guess
            continue
        (path,) = tuple(parents)
        f = files.get(path)
        if f is None:
            continue
        if f["subtype"] in hidden_set:         # hidden by default -> not a miss
            continue
        if head == f["title"] or not head:     # title self-match / empty
            continue
        if len(head.split()) < min_words:      # too short to mean anything
            continue
        pairs.append((head, f["id"]))
    return pairs


def fts_search_fn(store, limit: int = 10) -> SearchFn:
    def _fn(query: str) -> list[int]:
        return [h["node_id"] for h in fts_search(store, query, limit=limit)]
    return _fn


def fts_baseline(store, pairs: Sequence[Pair],
                 ks: Sequence[int] = (1, 5, 10)) -> Score:
    return evaluate(pairs, fts_search_fn(store, limit=max(ks)), ks=ks)


def main() -> int:
    import shutil
    import tempfile

    from tests.fixtures import synthetic as syn
    from homegraph.store import Store

    tmp = tempfile.mkdtemp(prefix="h1-eval-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        root = os.path.join(tmp, "korpus")
        db = build_m3_corpus(root, syn)
        rc = 0
        with Store(db) as s:
            pairs = build_pairs(s)
            print("generated %d leakage-guarded pairs" % len(pairs))
            if not pairs:
                # 0 pairs is not success. The empty-eval guard in
                # scoreboard.evaluate would catch this, but only if we CALLED
                # it -- so refuse here rather than exit 0 on a vacuous run. The
                # synthetic fixture yields 0 (its markdown files are one heading
                # each, which becomes the title and is dropped by the self-match
                # guard); a real baseline needs the real corpus or hand-authored
                # pairs. sim-auditor CP-H1 finding 2.
                print("ERROR: 0 pairs -- no measurement produced; the heading "
                      "source is too sparse on this corpus.", file=sys.stderr)
                rc = 2
            else:
                print("FTS baseline:", fts_baseline(s, pairs).line())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        syn.use_config(syn.CONFIG)
    return rc


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    raise SystemExit(main())
