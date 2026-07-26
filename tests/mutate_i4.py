#!/usr/bin/env python3
"""Mutation test for CP-I4 -- the Graphify export.

Two of these are worth reading before the rest. Mutation 2 flips `multigraph`
to `false`, which is the failure that would ship data loss looking like a
cleaner file. Mutation 13 does not touch the package at all: it makes the
checkpoint unable to find Graphify, and the gates that ask Graphify must then
go RED. A gate that quietly passes when the thing it verifies against is
missing is the one failure this checkpoint could not tolerate, because it
would make every other gate here decorative on any machine without Graphify.

Run:
    python3 tests/mutate_i4.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # 1. An unmapped kind falls back instead of stopping. The decision was
    # the refusal: a node class nobody has thought about must not arrive in a
    # federated graph labelled `concept` and be cited from there.
    ("an unmapped kind quietly becomes a concept",
     "homegraph/graphify.py",
     "    try:\n        return KIND_TO_FILE_TYPE[kind]\n    except KeyError:",
     '    return KIND_TO_FILE_TYPE.get(kind, "concept")  # mutated\n'
     "    try:\n        return KIND_TO_FILE_TYPE[kind]\n    except KeyError:",
     "a kind with no Graphify file_type stops the export"),

    # 2. THE one. `false` loads as a simple graph and silently collapses every
    # pair of nodes joined by two relations -- 23 pairs on the real corpus.
    ("the graph declares itself simple, collapsing parallel edges",
     "homegraph/graphify.py",
     '        "multigraph": True,',
     '        "multigraph": False,  # mutated',
     "and loses no edge, including the two on one node pair"),

    # 3. Direction is dropped, so an edge that means "this document cites that
    # one" becomes a symmetric association.
    ("the graph stops being directed",
     "homegraph/graphify.py",
     '        "directed": True,',
     '        "directed": False,  # mutated',
     "Graphify's networkx loads the file as a multigraph"),

    # 4. A guess is promoted to EXTRACTED, which states that the corpus said
    # something it only suggested.
    ("a mention is exported as though the data stated it",
     "homegraph/graphify.py",
     '    "mention": "INFERRED",',
     '    "mention": "EXTRACTED",  # mutated',
     "and no method below 1.0 is promoted to EXTRACTED"),

    # 5. The mapping loses a method the store still defines. `check_tables`
    # exists precisely so this cannot reach an export as a default.
    ("the confidence mapping drifts from the store's own table",
     "homegraph/graphify.py",
     '    "cohort": "INFERRED",',
     "    # mutated: cohort dropped",
     "and the mapping covers exactly those methods, no more"),

    # 6. The number the three-valued enum had to round off is dropped, and the
    # mapping stops being lossless -- 0.7 and 0.6 become indistinguishable.
    ("the numeric confidence is dropped, leaving only the bucket",
     "homegraph/graphify.py",
     '        "confidence_score": score,',
     '        "confidence_score": None,  # mutated',
     "the numeric confidence survives beside the enum"),

    # 7. A required field goes missing for nodes that are not files, which is
    # exactly the case a homegraph graph has and a code graph does not.
    ("source_file is omitted for a node with no path",
     "homegraph/graphify.py",
     '        "source_file": row.get("path") or "",',
     '        "source_file": row.get("path"),  # mutated',
     "and carries source_file as the empty string, not a missing key"),

    # 8. The writer enumerates fields again, so `full` silently stops carrying
    # the text it promises. This is the defect the gate caught while the
    # checkpoint was being written.
    ("full stops carrying the file text it promises",
     "homegraph/graphify.py",
     '_MAPPED = ("t", "model", "node_key", "path", "title")',
     '_MAPPED = ("t", "model", "node_key", "path", "title", "body")  # mutated',
     "full does, so the check above discriminates"),

    # 9. Redaction is skipped entirely.
    ("the writer skips redaction and prints the raw row",
     "homegraph/graphify.py",
     "                row = redact(row, redaction)",
     "                pass  # mutated: no redaction",
     "structure carries no file text into the graph"),

    # 10. A bibliographic reference is filed as a document, losing the one
    # distinction Graphify's `paper` type exists to make.
    ("a citation is filed as a document",
     "homegraph/graphify.py",
     '    "reference": "paper",',
     '    "reference": "document",  # mutated',
     "a bibliographic reference maps to paper"),

    # 11. The copied vocabulary drifts from the installed original. The copy
    # exists because graphify is not a dependency; this is what stops it
    # rotting unnoticed.
    ("the copied file_type vocabulary drifts from Graphify's own",
     "homegraph/graphify.py",
     'VALID_FILE_TYPES = ("code", "document", "paper", "image", "rationale",'
     ' "concept")',
     'VALID_FILE_TYPES = ("code", "document", "paper", "image",'
     ' "concept")  # mutated',
     "and its closed vocabularies still match the copies here"),

    # 12. A missing store is opened rather than refused; `Store()` creates the
    # file, so the export succeeds over an empty graph.
    ("a missing store is opened rather than refused",
     "homegraph/graphify.py",
     '            raise ExportError("no store for %s at %s" % (model, path))',
     "            pass  # mutated: missing store accepted",
     "a missing store is refused by name, not by traceback"),

    # 13. Not a mutation of the product: a mutation of the CHECKPOINT. With
    # Graphify unfindable, every gate that asks Graphify must go red. If they
    # pass, this whole file is theatre on any machine that lacks it.
    ("the checkpoint cannot find Graphify and says nothing",
     "tests/test_i4.py",
     '    exe = shutil.which("graphify")\n    if not exe:\n        return None',
     "    return None  # mutated: graphify unfindable\n"
     '    exe = shutil.which("graphify")\n    if not exe:\n        return None',
     "Graphify's own validate_extraction reports no errors"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_i4.py", prefix="muti4-", timeout=300))
