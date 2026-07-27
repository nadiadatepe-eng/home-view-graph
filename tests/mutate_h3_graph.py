#!/usr/bin/env python3
"""Mutation test for CP-H3 (graph) -- the in-page semantic search.

The gate's whole value is that the JavaScript port cannot drift from the Python
embedder without going red. Each mutation breaks the port or the packing in a
way that would otherwise ship silently -- a dropped per-row scale, a tokenizer
that stops splitting identifiers -- and names the check that must fail.

Run:
    python3 tests/mutate_h3_graph.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # The per-row scale is dropped: int8 becomes round(x) with no scale, so the
    # small components round to zero and the direction shifts. Faithful
    # quantisation cannot be told from garbage without this.
    ("the int8 quantiser drops its per-row scale",
     "homegraph/visualize.py",
     "        scale = peak / 127.0 if peak > 0 else 1.0",
     "        scale = 1.0  # mutated: no per-row scale",
     "emEmbed matches static_embed.embed within quantisation"),

    # The JS tokenizer stops splitting acronyms, so it diverges from the Python
    # splitter on HTMLParser -- a query tokenised differently silently misses
    # the vocabulary.
    ("the ported tokenizer stops splitting acronym boundaries",
     "homegraph/visualize.py",
     r".replace(/([A-Z]+)([A-Z][a-z])/g,'$1 $2')",
     r".replace(/([A-Z]+)([A-Z][a-z])/g,'$1$2')",
     "emSplit matches split_identifiers"),

    # The mean-pool loses its L2 normalisation in the JS port, so cosine is no
    # longer comparable and drifts from the Python embedder.
    ("the ported embedder skips L2 normalisation",
     "homegraph/visualize.py",
     "  nn=Math.sqrt(nn); if(nn>0) for(var d=0;d<EM.dim;d++) acc[d]/=nn;",
     "  nn=Math.sqrt(nn); // mutated: no L2 normalisation",
     "emEmbed returns L2-normalised (unit) vectors"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h3_graph.py", prefix="muth3g-", timeout=180))
