#!/usr/bin/env python3
"""Write a store out in Graphify's graph format.

Graphify gave this package the shape of its federation layer -- `merge-graphs`
is why the mesh queries the parts and fuses in memory instead of merging the
stores. This exporter goes the other way: homegraph's graph, in the form
Graphify reads.

**The schema was read, not guessed.** That was the plan's one precondition for
this phase, and it was met by reading `graphify/validate.py` and
`graphify/cli.py` out of the installed package (0.9.16) plus a real 974-node
`graph.json` on disk. What that reading settled:

  * There are **two** shapes, not one. The *extraction* shape
    (`{"nodes": [...], "edges": [...]}`) is what `build` validates on the way
    in; the *graph.json* shape is networkx `node_link_data(G, edges="links")`
    on the way out, and it is what `merge-graphs` and `global add` consume.
  * `validate_extraction` accepts `links` as a fallback for `edges`
    (`validate.py:55`). **So one file can be both.** This writer emits the
    node-link shape carrying the extraction shape's required fields, which
    means the same artifact passes `assert_valid` and loads in `merge-graphs`
    -- one printer, two consumers, rather than two printers to keep in step.
  * `file_type` is a **closed** set of six (`validate.py:4`) and `confidence` a
    closed set of three (`validate.py:5`). Neither is a free string, so both
    get an explicit table here and an unknown value is refused rather than
    approximated into the nearest bucket.
  * `merge-graphs` derives its per-repo prefix from the file's
    **grandparent directory** (`build.py:1065`: `graphify-out/..`). Writing to
    `~/graphify.json` would tag every node with the home directory's name, so
    the CLI recommends `<somewhere>/graphify-out/graph.json` and says why.

**`multigraph` is true, and that was measured.** homegraph's edge table is
keyed by `(src, dst, rel)`, so a pair of nodes can carry two relations -- on
the real corpus, 23 pairs in m3 do. Loaded as a simple graph those 23 collapse
into one edge each, silently: networkx 3.6.1 was asked directly, and
`multigraph: false` turned two parallel edges into one while `multigraph: true`
kept both without needing an explicit key. Graphify's own `merge-graphs` then
flattens to `nx.Graph` on its side (`cli.py`, `_to_simple`) -- which is their
decision to make about a merged view, and a different thing from us handing
them a graph that had already lost data.

Redaction is `export.py`'s, for the same reason `obsidian.py` uses it: this is
a printer on the end of `_rows`, not a second exporter with its own idea of
what `structure` means.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .export import ExportError, _rows, check_level, redact
from .store import EDGE_METHODS, Store

# Graphify's closed vocabularies, read from `graphify/validate.py` lines 4-7 of
# version 0.9.16. Copied rather than imported because graphify is not a
# dependency of this package and never will be -- `dependencies = []` is the
# reason homegraph runs anywhere. The gate re-reads the installed package when
# there is one and says so honestly when there is not.
VALID_FILE_TYPES = ("code", "document", "paper", "image", "rationale", "concept")
VALID_CONFIDENCES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")

# homegraph's `kind` mapped into those six. **Keyed on `kind`, not `subtype`,
# and that is a measurement rather than a preference:** the real stores hold 17
# distinct kinds and well over 60 subtypes, and the subtypes are composed at
# runtime (`pdf/needs_ocr`, `docx/corrupt`, `m3/h2`), so a table keyed on
# subtype would refuse a file that merely failed to OCR. `kind` is the axis the
# builders actually fix.
#
# `rationale` is unused, and saying so is the point: homegraph has no node that
# records WHY something is the case. Mapping something into it to fill the set
# would be inventing a claim about the corpus.
KIND_TO_FILE_TYPE = {
    # Things that are files, or parts of files.
    "code": "code",             # the mesh's code stubs
    "document": "document",     # M1's PDFs, office documents
    "section": "document",      # a heading inside one
    "file": "document",         # M3 notes, M4's everything-else
    "archive_entry": "document",  # a name declared by an archive's directory
    "image": "image",
    # Bibliographic: DOIs, arXiv ids, citekeys, URLs cited by a document.
    "reference": "paper",
    # Things that are not files at all. Graphify's `concept` is the only
    # honest home for these -- an author is not a document, and calling one
    # would be a claim the graph does not make.
    "author": "concept",
    "tag": "concept",
    "app": "concept",
    "format": "concept",
    "collection": "concept",
    "series": "concept",
    "datebucket": "concept",
    "resolution": "concept",
    "wikilink": "concept",      # a link target that resolved to nothing
    "rollup": "concept",        # a cold-state aggregate over many files
}

# homegraph's five edge methods mapped onto Graphify's three-valued
# `confidence`. The numeric confidence is NOT thrown away to fit: it travels
# in `confidence_score`, a float field the real graph.json already carries
# beside the enum. That is the whole reason this mapping is lossless enough to
# accept -- the enum is a bucket, the number is the evidence.
#
#   * `exact` (1.0) -- the data states the relation. EXTRACTED.
#   * `path_prefix` (0.7) -- two candidates existed and a rule picked one.
#     store.py calls it "a real ambiguity, resolved by a rule". AMBIGUOUS.
#   * `basename` (0.6) -- the same filename in two directories, no bytes read.
#     Also a choice between candidates. AMBIGUOUS.
#   * `mention` (0.5) -- prose names a file and it resolved. Deduced from text
#     rather than declared as a link. INFERRED.
#   * `cohort` (0.4) -- two nodes changed on the same days. INFERRED.
METHOD_TO_CONFIDENCE = {
    "exact": "EXTRACTED",
    "path_prefix": "AMBIGUOUS",
    "basename": "AMBIGUOUS",
    "mention": "INFERRED",
    "cohort": "INFERRED",
}


def check_tables() -> None:
    """Refuse to run with a mapping that has drifted from its source.

    `EDGE_METHODS` is the store's own table and a sixth method is described
    there as "a decision rather than a tuning knob". This turns that into
    something the decision cannot skip: add a method without deciding what it
    means to Graphify and this exporter stops, rather than quietly emitting an
    edge with no confidence or defaulting it to EXTRACTED -- which would state
    that the data said something it did not.
    """
    missing = sorted(set(EDGE_METHODS) - set(METHOD_TO_CONFIDENCE))
    if missing:
        raise ExportError(
            "edge method(s) %s have no Graphify confidence mapping. Graphify's "
            "`confidence` is a closed set (%s); decide where these belong "
            "rather than letting them default."
            % (", ".join(missing), ", ".join(VALID_CONFIDENCES)))
    unknown = sorted(set(METHOD_TO_CONFIDENCE) - set(EDGE_METHODS))
    if unknown:
        raise ExportError(
            "mapping names edge method(s) %s that the store no longer has"
            % ", ".join(unknown))
    bad = sorted({v for v in KIND_TO_FILE_TYPE.values()
                  if v not in VALID_FILE_TYPES})
    if bad:
        raise ExportError("mapping emits file_type(s) %s outside Graphify's "
                          "closed set" % ", ".join(bad))


def _file_type(kind: str) -> str:
    """Graphify's `file_type` for one homegraph kind, or a refusal.

    Refusing was chosen over falling back to `concept`: a new kind added to
    m1-m4 must be placed deliberately. A fallback would let a
    node class nobody has thought about arrive in a federated graph labelled
    as a concept, and be cited from there -- the shape this project keeps
    meeting, where a guess written down as a fact loses its uncertainty on the
    way to the next reader.
    """
    try:
        return KIND_TO_FILE_TYPE[kind]
    except KeyError:
        raise ExportError(
            "no Graphify file_type for homegraph kind %r. Graphify's "
            "file_type is a closed set (%s); add %r to KIND_TO_FILE_TYPE "
            "deliberately rather than letting it default."
            % (kind, ", ".join(VALID_FILE_TYPES), kind)) from None


def _label(row: dict[str, Any]) -> str:
    """A display name. Never empty -- Graphify requires the field."""
    title = row.get("title")
    if title:
        return str(title)
    key = str(row["node_key"])
    tail = key.rsplit("/", 1)[-1]
    return tail or key


# Row fields the node mapping consumes into a Graphify name. Everything else
# the redacted row still holds is passed through under its own name.
_MAPPED = ("t", "model", "node_key", "path", "title")


def _node(row: dict[str, Any], model: str) -> dict[str, Any]:
    """One Graphify node.

    **Fields are passed through, not enumerated.** The first version listed
    the handful worth carrying, and `--redaction full` then produced a file
    byte-identical to `structure`: the flag promised the full text of every
    file and the writer never looked at `body`. A label that lies is worse
    than a missing feature -- the same reason `shape` shipped refused rather
    than approximated -- and a second list of what-to-carry is also a second
    policy that drifts from `redact`, which is the one thing that decides.
    So the level decides what the row holds, and whatever the row holds is
    what comes out.
    """
    out: dict[str, Any] = {
        "id": row["node_key"],
        "label": _label(row),
        "file_type": _file_type(row["kind"]),
        # Required by `validate_extraction`, and empty when a node is not a
        # file -- an author has no source file. The real graph.json carries
        # `""` for exactly this case, so the empty string is their convention
        # rather than our invention.
        "source_file": row.get("path") or "",
        "model": model,
    }
    for field, value in row.items():
        if field not in _MAPPED and value is not None:
            out[field] = value
    return out


def _link(row: dict[str, Any]) -> dict[str, Any]:
    method = str(row.get("method"))
    confidence = METHOD_TO_CONFIDENCE.get(method)
    if confidence is None:
        raise ExportError(
            "edge %s -> %s uses method %r, which has no Graphify confidence"
            % (row["src"], row["dst"], method))
    score = row.get("confidence")
    return {
        "source": row["src"],
        "target": row["dst"],
        "relation": row["rel"],
        "confidence": confidence,
        "source_file": row["src"],
        # The number the enum had to round off, kept beside it.
        "confidence_score": score,
        "weight": score,
        "method": method,
    }


def export_graph(model_paths: dict[str, str], out_path: str, root: str, *,
                 redaction: str = "structure") -> dict[str, Any]:
    """Write `model_paths` to `out_path` as a Graphify graph. Never mutates."""
    check_level(redaction)
    check_tables()
    root = os.path.abspath(os.path.expanduser(root))

    for model, path in sorted(model_paths.items()):
        if not path or not os.path.exists(path):
            raise ExportError("no store for %s at %s" % (model, path))

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    per_model: dict[str, dict[str, int]] = {}
    kinds: dict[str, int] = {}

    for model, path in sorted(model_paths.items()):
        store = Store(path)
        n_before, e_before = len(nodes), len(links)
        try:
            for row in _rows(store, model, root):
                row = redact(row, redaction)
                if row["t"] == "node":
                    nodes.append(_node(row, model))
                    kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
                else:
                    links.append(_link(row))
        finally:
            store.close(commit=False)
        per_model[model] = {"nodes": len(nodes) - n_before,
                            "edges": len(links) - e_before}

    parallel = len(links) - len({(e["source"], e["target"]) for e in links})
    data = {
        "directed": True,
        # Measured, not chosen: see the module docstring. `false` would drop
        # the parallel edges counted below.
        "multigraph": True,
        "graph": {"producer": "homegraph", "redaction": redaction,
                  "models": sorted(model_paths)},
        "nodes": nodes,
        "links": links,
        # Declared empty rather than omitted. Graphify reads this key; saying
        # "none" is a different statement from saying nothing, and this
        # package has a rule about that.
        "hyperedges": [],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    return {"out": os.path.abspath(out_path), "redaction": redaction,
            "nodes": len(nodes), "links": len(links),
            "parallel_edges_kept": parallel,
            "file_types": {t: sum(1 for n in nodes if n["file_type"] == t)
                           for t in sorted({n["file_type"] for n in nodes})},
            "per_model": per_model,
            "bytes": os.path.getsize(out_path)}
