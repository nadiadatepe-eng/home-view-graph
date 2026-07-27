#!/usr/bin/env python3
"""Mutation test for CP-H2 -- title provenance.

The one failure this checkpoint prevents is a guess recorded as a fact. Each
mutation manufactures a way for that to happen -- an inferred title tagged
`declared`, a confidence that ignores the method, an unknown method waved
through, an untagged title defaulting to a claim of fact -- and names the check
that must go red.

Run:
    python3 tests/mutate_h2.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # The guess records itself as a fact -- the core failure.
    ("the first-heading fallback claims to be declared",
     "homegraph/models/m3_markdown.py",
     "            title, title_method = sections[0][\"title\"], \"inferred\"",
     "            title, title_method = sections[0][\"title\"], \"declared\"",
     "a first-heading fallback is inferred (0.5), never a fact"),

    # Confidence stops tracking the method.
    ("confidence is hardcoded, so a guess reads as certain",
     "homegraph/store.py",
     "            title_confidence = TITLE_METHODS[title_method]",
     "            title_confidence = 1.0  # mutated: confidence ignores method",
     "an inferred title carries confidence 0.5"),

    # An unknown method is waved through instead of refused.
    ("an unknown title method is accepted, not refused",
     "homegraph/store.py",
     "            if title_method not in TITLE_METHODS:",
     "            if False:  # mutated: unknown methods accepted",
     "an unknown title method is refused"),

    # A frontmatter title is mislabelled -- declared collapses into verbatim.
    ("a declared title is mislabelled verbatim",
     "homegraph/models/m3_markdown.py",
     "            title, title_method = front[\"title\"], \"declared\"",
     "            title, title_method = front[\"title\"], \"verbatim\"  # mutated",
     "a frontmatter title is declared (1.0)"),

    # The read side goes dark: the guess is served without its confidence.
    ("the mesh hit drops title_confidence, so the MCP consumer cannot see the guess",
     "homegraph/mesh.py",
     "                    \"title_confidence\": row.get(\"title_confidence\"),\n",
     "                    # mutated: title_confidence dropped from the fused hit\n",
     "mesh_search surfaces title_confidence"),

    ("fts_search drops title_confidence from its row",
     "homegraph/search.py",
     "n.node_key, n.title, n.title_confidence,",
     "n.node_key, n.title,",
     "fts_search surfaces title_confidence"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h2.py", prefix="muth2-", timeout=180))
