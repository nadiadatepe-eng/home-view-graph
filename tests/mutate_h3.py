#!/usr/bin/env python3
"""Mutation test for CP-H3 -- static embeddings and hybrid semantic search.

Each gate in test_h3 claims to catch a specific way the vector path can go
quietly wrong. A gate that cannot actually fail is worse than none, so each
mutation below manufactures exactly that failure -- a strict shortlist that
gives semantic search no room, a corpus scan wearing a shortlist's name, a
ran-empty result collapsing into did-not-run, a namespace filter that stops
filtering, an unnormalised vector, a tokenizer that keeps identifiers whole --
and names the check that must go red.

Run:
    python3 tests/mutate_h3.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # Vector mode secretly falls through to fusion, so the "pure cosine, no
    # lexical noise" promise is a lie and out_mode is 'hybrid', not 'vector'.
    ("mode=vector falls through to RRF fusion instead of pure cosine",
     "homegraph/search.py",
     '    if mode == "vector":',
     '    if False:  # mutated: vector mode fuses',
     "mode=vector reports out_mode 'vector'"),

    # FTS mode stops short-circuiting, so it runs the vector path it promised to
    # ignore.
    ("mode=fts no longer ignores the embedder",
     "homegraph/search.py",
     '    if mode == "fts":',
     '    if False:  # mutated: fts mode runs vectors anyway',
     "mode=fts reports out_mode 'fts' even with an embedder passed"),

    # Cosine is disabled -- it returns a constant, so the stable sort leaves the
    # shortlist in its BM25 order and the decoy (more term matches) wins. This is
    # the mutation the first cut of the gate could NOT catch: the fixture was
    # rebuilt so BM25 order and cosine order disagree, and now neutralising
    # cosine flips the result. (sim-auditor finding 1/4.)
    ("cosine is neutralised, so ranking collapses to lexical BM25 order",
     "homegraph/search.py",
     "    return sum(x * y for x, y in zip(a, b))",
     "    return 0.0  # mutated: cosine disabled",
     "cosine, not term overlap, sets the order (target before decoy)"),

    # The shortlist goes strict: the same AND that ranks the shown results now
    # also gathers the candidates, so a paraphrase sharing only some terms is
    # never a candidate and cosine has nothing to lift. This is the whole reason
    # the OR pool exists.
    ("the vector shortlist uses AND, leaving semantic search no room",
     "homegraph/search.py",
     '    expr = fts_query(query, op="OR")',
     '    expr = fts_query(query, op="AND")  # mutated: strict shortlist',
     "cosine reranking lifts the target to first"),

    # The shortlist becomes a corpus scan. Now the zero-overlap document -- the
    # one whose vector equals the query's -- is a candidate and wins on cosine,
    # which is exactly the cost the shortlist refuses to pay.
    ("the shortlist scans the whole corpus instead of FTS candidates",
     "homegraph/search.py",
     '    sql = ("SELECT n.id node_id FROM nodes_fts JOIN nodes n "\n'
     '           "ON n.id = nodes_fts.rowid WHERE nodes_fts MATCH ?")\n'
     "    args: list[object] = [expr]\n"
     "    if hidden_subtypes:\n"
     '        sql += " AND (n.subtype IS NULL OR n.subtype NOT IN (%s))" % (\n'
     '            ",".join("?" * len(hidden_subtypes)))\n'
     "        args.extend(hidden_subtypes)\n"
     '    sql += " ORDER BY bm25(nodes_fts) LIMIT ?"\n'
     "    args.append(limit)\n"
     '    return [r["node_id"] for r in store.db.execute(sql, args).fetchall()]',
     "    # mutated: scan the whole corpus instead of an FTS shortlist\n"
     '    return [r["node_id"] for r in store.db.execute(\n'
     '        "SELECT id node_id FROM nodes LIMIT ?", [limit]).fetchall()]',
     "the highest-cosine zero-overlap doc is NOT returned (no scan)"),

    # A ran-and-found-nothing result reports as did-not-run. The [] that means
    # "searched, empty" becomes the None that means "never ran", and the two
    # states this module is built to keep apart are one.
    ("a ran-empty vector result collapses into did-not-run (None)",
     "homegraph/search.py",
     "    if not shortlist:\n"
     "        return []                       # ran; nothing lexically overlapping",
     "    if not shortlist:\n"
     "        return None  # mutated: ran-empty looks like did-not-run",
     "a query overlapping nothing -> [] (ran, empty), never None"),

    # The namespace count stops isolating namespaces (AND -> OR), so a model
    # switch finds the previous model's vectors present and serves them instead
    # of reporting None -- the silent cross-model ranking the namespace exists
    # to stop.
    ("the namespace count ignores the namespace (AND -> OR)",
     "homegraph/store.py",
     '            "WHERE provider=? AND model=? AND dim=?",',
     '            "WHERE provider=? OR model=? OR dim=?",  # mutated: leaks namespaces',
     "count is per-namespace, not per-table"),

    # A wrong-length vector is written instead of refused -- a short blob that
    # cosines against a query of the real dimension and silently mis-scores.
    ("a vector whose length disagrees with dim is accepted",
     "homegraph/store.py",
     "        if len(vec) != dim:",
     "        if False:  # mutated: wrong-length vectors accepted",
     "a vector whose length != dim is refused"),

    # The tokenizer stops splitting identifiers, so `getUserById` is one token
    # in a vocabulary of one and the +6% recall the split buys is gone.
    ("the tokenizer keeps camelCase identifiers whole",
     "homegraph/providers/static_embed.py",
     '    spaced = _HUMP.sub(r"\\1 \\2", spaced)',
     "    spaced = spaced  # mutated: no camelCase split",
     "camelCase splits into words"),

    # The embedding is not L2-normalised, so cosine is no longer comparable
    # across documents of different length and the hand-computed unit value is
    # wrong.
    ("the embedding skips L2 normalisation",
     "homegraph/providers/static_embed.py",
     "        norm = math.sqrt(sum(x * x for x in acc))\n"
     "        if norm == 0.0:\n"
     "            return acc                       # tokens cancelled out exactly\n"
     "        return [x / norm for x in acc]",
     "        return acc  # mutated: skip L2 normalisation",
     "a two-token mean is L2-normalised to the hand value"),

    # The count the vector path branches on stops being per-namespace, so
    # another model's leftovers make it look like this one is embedded. The
    # search then runs, finds no vector for any shortlisted node (read is
    # still namespace-filtered), returns [] instead of None -- and the caller
    # reports an ordinary fused answer with no warning. That is
    # code-review-graph #757's failure, reproduced: the index queried under a
    # different identity than it was built under, and nothing said so.
    #
    # It goes red in three places at once, and the `expected` gate below names
    # the reported one on purpose -- that is the layer with no other check.
    # An earlier version of this comment claimed the lower checks still passed;
    # codex measured otherwise. They do not: the per-namespace count and the
    # `None` from `vector_search` both fall to this same mutation.
    ("the vector count stops being per-namespace",
     "homegraph/store.py",
     '            "SELECT COUNT(*) c FROM embeddings "\n'
     '            "WHERE provider=? AND model=? AND dim=?",',
     '            "SELECT COUNT(*) c FROM embeddings "\n'
     '            "WHERE provider=? OR model=? OR dim=?",  # mutated',
     "the mismatch reports out_mode 'fts', not 'hybrid'"),

    # The warning loses the instruction while `out_mode` stays honest. Nothing
    # above catches this: the mutation over the count fails the `out_mode`
    # check first, so the two warning assertions were unmutated until now --
    # and an unmutated assertion is a claim, not a gate. The failure it models
    # is real and small: a report that names the problem and leaves the reader
    # with no way to act is most of the distance back to a silent degradation.
    ("the empty-namespace warning stops saying what to run",
     "homegraph/search.py",
     '                "embeddings are configured but nothing is embedded under the "\n'
     '                "current model\'s namespace; the vector path did not run. Run "\n'
     '                "`homegraph embed` (a model change invalidates old vectors).")',
     '                "no semantic results.")  # mutated: names nothing, '
     'instructs nothing',
     "and says WHY and WHAT TO RUN, not just that it is empty"),

    # Vector mode words its own warning, so the one above leaves it untouched
    # and its assertion unmutated -- codex caught that the first version of
    # this pair claimed otherwise. Two strings, two mutations. Vector mode is
    # the worse place to lose the instruction: there is no lexical fallback,
    # so the warning is the entire answer.
    ("the vector-mode empty-namespace warning stops saying what to run",
     "homegraph/search.py",
     '                    "vector mode: nothing is embedded under the current model\'s "\n'
     '                    "namespace; run `homegraph embed`.")',
     '                    "no semantic results.")  # mutated: instructs nothing',
     "vector mode returns nothing and says why"),

    # Written and removed: `embedding_count` returning a constant 0, aimed at a
    # positive control in t_namespace_invalidation. It came back MISATTRIBUTED
    # -- "cosine reranking lifts the target to first" fails first and stops the
    # suite before the control runs -- and the control itself was a duplicate of
    # t_store_namespace's hybrid check, which that same mutation already kills.
    # Two gates for one claim, and a mutation that could not be attributed to
    # either. Both deleted rather than kept as standing noise in the sweep.
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h3.py", prefix="muth3-", timeout=300))
