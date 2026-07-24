#!/usr/bin/env python3
"""Write a store out as a portable artifact. Never mutates the store.

The artifact is lzma-compressed JSON Lines: a manifest, then nodes, then
edges, then a digest. Text rather than a copy of the SQLite file, for one
reason that outweighs the extra work -- **the importer writes through
`Store.upsert_node`**, the same writer every builder uses, so an imported
graph cannot diverge from a built one. A shipped `.db` would be imported by
raw SQL, which is a second builder, and two builders agree until one is
edited.

`zstd` is not in the standard library before Python 3.14 and this package has
no runtime dependencies, so `lzma` it is. Measured on the real stores:
23.5 MB of SQLite compresses to 4.4 MB in 5.3 s, and that is a ceiling --
this format carries no FTS shadow tables and no page padding.

**Two deviations from the plan, both deliberate:**

1. **The digest is a trailer, not a manifest field.** The plan put it in the
   manifest, which cannot be written first and also cover what follows. A
   trailer keeps the file streamable AND makes truncation detectable: an
   artifact whose last line is missing is refused rather than imported as a
   smaller graph. The manifest says the digest is required, so an artifact
   without one cannot pass as complete.
2. **`shape` is refused, not approximated.** The level exists in the format
   and its rows are declared, but the hashing lands in E3 together with its
   gates. Producing an artifact labelled `shape` that was not actually shaped
   is the one failure this package will not ship -- a label that lies is
   worse than a missing feature.

What is deliberately NOT exported:

  * **The FTS index.** It is derived; the importer rebuilds it. Shipping it
    would be a second opinion about what is searchable.
  * **The mesh.** Also derived. Import the models and run `mesh build`; the
    code stubs need a `--code-root` on the new machine anyway, and shipping
    them means shipping paths to files that machine does not have.
  * **The config fingerprint.** It describes the exporting machine's layout.
    Importing it would make the next `update` compare against a layout that
    was never local.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
from typing import Any

from .portable import (ARCHIVE_PREFIX, MARKER, MODEL_SEP, OutsideRoot,
                       to_portable)
from .store import Store

FORMAT_VERSION = 1
PRESET = 6

# What each level carries. Declared here so the manifest, the CLI help and the
# checkpoint all read one list rather than three that drift.
LEVELS = {
    "full": "paths, titles, every edge, and the full text of every file",
    "structure": "paths, titles, every edge; no file text",
    "shape": "hashed names and paths; topology only",
}
# `shape` is declared above and refused below -- see the module docstring.
IMPLEMENTED = ("full", "structure", "shape")

# 64 bits of sha256, hex. Enough that a collision inside one graph is not a
# practical concern, short enough that an artifact stays readable as a shape.
DIGEST_CHARS = 16

# What `shape` refuses to carry, and why each one is here. The first draft
# dropped `body` and `mtime` and called it done; an audit read the actual rows
# and found the level advertised as shareable still carrying:
#
#   * `content_hash` -- `hash_file()` over the CONTENTS. That is strictly
#     stronger disclosure than the guessable-path weakness the docstring
#     admitted: anyone holding a candidate file does not have to guess a path
#     at all, they hash the file and look it up.
#   * `size` -- confirms such a guess on its own.
#   * `activity_datelist` / `datelist_int` / `datelist_anchor` -- a per-node
#     activity mask is a usage pattern, no less machine-identifying than the
#     `mtime` that was already dropped for exactly that reason.
#
# `first_seen` and `last_seen` stay, because the import needs them and a graph
# without them is not importable. That is a real residue and it is written
# down rather than implied away -- see DECISIONS.md section 27.
SHAPE_DROPS = ("body", "mtime", "content_hash", "size",
               "activity_datelist", "datelist_int", "datelist_anchor")

# Columns copied verbatim. `first_seen`, `last_seen` and the datelist columns
# are here because `upsert_node` cannot set them: it stamps `as_of` and starts
# a fresh mask. The importer restores them explicitly, and CP-12 compares them
# as part of the equivalence -- history that survives a round trip only
# because nobody looked at it is not history.
NODE_COLUMNS = ("kind", "subtype", "title", "title_method", "title_confidence",
                "body", "size", "mtime",
                "content_hash", "first_seen", "last_seen",
                "activity_datelist", "datelist_int", "datelist_anchor")


class ExportError(RuntimeError):
    """Refused. Named, never approximated."""


def check_level(level: str) -> None:
    """Refuse a level this build cannot produce. The one place that decides.

    It was two places: this test and a raise inside `redact`. Both said the
    same thing, so removing either left the other answering, and the mutation
    that deleted the check walked straight through the gate. `redact` trusts
    this now, and this is called before a single byte is written.
    """
    if level not in LEVELS:
        raise ExportError("unknown redaction level %r; known: %s"
                          % (level, ", ".join(sorted(LEVELS))))
    if level not in IMPLEMENTED:
        raise ExportError(
            "redaction level %r is declared but not implemented yet (E3); "
            "available: %s. An artifact labelled %r that was not %r is worse "
            "than no artifact."
            % (level, ", ".join(IMPLEMENTED), level, level))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def shape_key(key: str) -> str:
    """A portable key with its names hashed and its TYPE left readable.

    The rule is the one the decision names: **keep the first prefix segment,
    hash the rest.** `author:` stays `author:`, `ref:` stays `ref:`, a path
    keeps its `~/` marker, an archive keeps its `!` seam, a mesh key keeps its
    model. What each node IS survives; which file it is does not.

    Deterministic and unsalted, and that is a real limit rather than an
    oversight: **sha256 of a short path is guessable.** Anyone who can list
    candidate paths can hash them and check for a match -- "is there a file
    called `~/Documents/CV.pdf`?" is answerable against a `shape` artifact.
    What `shape` prevents is READING the graph; it is not anonymisation, and
    calling it that would be the kind of claim this package exists to avoid.
    A per-artifact salt would close that gap and would also make two exports
    of the same corpus incomparable, which is a trade nobody has asked for
    yet. See DECISIONS.md section 27.
    """
    if key.startswith(ARCHIVE_PREFIX):
        inner, sep, entry = key[len(ARCHIVE_PREFIX):].partition("!")
        return "%s%s%s" % (ARCHIVE_PREFIX, shape_key(inner),
                           ("!" + _digest(entry)) if sep else "")
    head, sep, rest = key.partition(MODEL_SEP)
    if sep and "/" not in head and rest:
        return "%s%s%s" % (head, MODEL_SEP, shape_key(rest))
    if key.startswith(MARKER):
        # `~` alone is the root itself and names nothing.
        return key if key == MARKER else "%s/%s" % (MARKER, _digest(key[2:]))
    prefix, colon, value = key.partition(":")
    if colon:
        return "%s:%s" % (prefix, _digest(value))
    return _digest(key)


def redact(row: dict[str, Any], level: str) -> dict[str, Any]:
    """Apply a redaction level to one node row. The only place it happens.

    `structure` drops `body` -- 4.9 million characters of markdown and 1.0
    million of document text on the measured corpus. That is not a side
    effect of exporting, it IS the export, which is why the default is the
    level that leaves it behind.
    """
    if level == "full":
        return row
    if level == "structure":
        return {k: v for k, v in row.items() if k != "body"}
    if level == "shape":
        out = {k: v for k, v in row.items() if k not in SHAPE_DROPS}
        for field in ("node_key", "path", "src", "dst"):
            if field in out:
                out[field] = shape_key(out[field])
        if out.get("title") is not None:
            out["title"] = _digest(out["title"])
        return out
    # Unreachable by construction: `check_level` runs first and refuses
    # anything not in IMPLEMENTED. Kept as a loud failure rather than a
    # silent pass-through, because "unreachable" is a claim about today's
    # callers and a new one may not read this comment.
    raise ExportError("redact() reached with unvalidated level %r" % level)


def _rows(store: Store, model: str, root: str):
    """Every node, then every edge, with paths made portable.

    Edges come second because the importer needs both endpoints to exist
    before `upsert_edge` will accept them -- it refuses an edge whose
    endpoints are missing rather than creating them, and that refusal is
    worth keeping rather than working around with an ordering the artifact
    does not state.
    """
    for r in store.db.execute(
            "SELECT node_key, path, %s FROM nodes ORDER BY node_key"
            % ", ".join(NODE_COLUMNS)):
        row: dict[str, Any] = {"t": "node", "model": model,
                               "node_key": to_portable(r["node_key"], root)}
        if r["path"] is not None:
            row["path"] = to_portable(r["path"], root)
        for col in NODE_COLUMNS:
            if r[col] is not None:
                row[col] = r[col]
        yield row

    for r in store.db.execute(
            "SELECT s.node_key src, d.node_key dst, e.rel, e.method, "
            "e.confidence, e.first_seen, e.last_seen FROM edges e "
            "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst "
            "ORDER BY s.node_key, d.node_key, e.rel"):
        yield {"t": "edge", "model": model,
               "src": to_portable(r["src"], root),
               "dst": to_portable(r["dst"], root),
               "rel": r["rel"], "method": r["method"],
               "confidence": r["confidence"],
               "first_seen": r["first_seen"], "last_seen": r["last_seen"]}


def export(model_paths: dict[str, str], out_path: str, root: str, *,
           redaction: str = "structure") -> dict[str, Any]:
    """Write `model_paths` to `out_path`. Returns what was written and left out.

    Opens every store read-only in effect: it runs SELECTs and closes with
    `commit=False`, so an export cannot advance `last_seen` on the graph it
    is describing. That is not theoretical carefulness -- a refused prune in
    `mesh.py` did exactly that last week, because `Store.close()` commits by
    default.
    """
    check_level(redaction)
    root = os.path.abspath(os.path.expanduser(root))

    counts: dict[str, dict[str, int]] = {}
    schema = 0
    for model, path in sorted(model_paths.items()):
        if not path or not os.path.exists(path):
            raise ExportError("no store for %s at %s" % (model, path))
        with Store(path) as s:
            counts[model] = {
                "nodes": s.db.execute(
                    "SELECT COUNT(*) c FROM nodes").fetchone()["c"],
                "edges": s.db.execute(
                    "SELECT COUNT(*) c FROM edges").fetchone()["c"]}
            schema = max(schema, s.version)

    manifest = {
        "t": "manifest",
        "format": FORMAT_VERSION,
        "schema": schema,
        "redaction": redaction,
        "redaction_note": LEVELS[redaction],
        "models": sorted(model_paths),
        "counts": counts,
        # A DESCRIPTION of the root, never the root. Writing the path here
        # would undo the entire point of the conversion three lines above --
        # and the plan's own footnote about this named a real directory in
        # its first draft, which is how easily it happens.
        "root_note": "exported from a root holding %d node(s)"
                     % sum(c["nodes"] for c in counts.values()),
        "digest": "sha256 over every line including this one, in the trailer "
                  "-- an artifact without one is incomplete and is refused",
    }

    digest = hashlib.sha256()
    written = {"nodes": 0, "edges": 0}
    outside: list[str] = []
    # Measured, not claimed. The structural fields are made portable above;
    # USER DATA is not, and cannot be. A title, an author, a URL or a line of
    # prose may name the export root, and rewriting the user's own text to
    # hide it would be a lie about what the file says. Two facts the corpus
    # produced on the first real run, both worth reporting rather than
    # discovering later:
    #
    #   * a directory INSIDE the root can be named after the root --
    #     `.claude/projects/-home-<user>-<project>/` encodes the very path it
    #     sits under, so a root-relative key still carries the old root as a
    #     component. No amount of prefix-stripping catches that.
    #   * at `full`, prose names absolute paths: 119 bodies did.
    #
    # So the artifact is root-relative STRUCTURALLY, and this number says how
    # far that goes. It is the honest version of "we scrubbed it".
    leaks: dict[str, int] = {}
    base = os.path.basename(root.rstrip(os.sep))

    def note_leak(row):
        for field, value in row.items():
            # `node_key` belongs here: it is structural and portable, and
            # counting it made the CLI tell the user their own TEXT named the
            # root when what named it was a key the conversion had already
            # handled. A report that overstates a leak is a report nobody
            # calibrates against.
            if (field in ("node_key", "path", "src", "dst")
                    or not isinstance(value, str)):
                continue
            if root in value or (base and base in value):
                leaks[field] = leaks.get(field, 0) + 1
    tmp = out_path + ".partial"

    def emit(fh, obj):
        line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
        digest.update(line.encode("utf-8"))
        fh.write(line)

    try:
        with lzma.open(tmp, "wt", encoding="utf-8", preset=PRESET) as fh:
            # The manifest IS digested. It was not, on the argument that a
            # digest must not cover the line announcing it -- true when the
            # digest lived in the manifest, and false the moment it moved to
            # the trailer. The manifest states the redaction level, the model
            # list and the counts; leaving it uncovered meant an artifact
            # could be relabelled `structure` after the fact and still verify.
            emit(fh, manifest)
            for model, path in sorted(model_paths.items()):
                s = Store(path)
                try:
                    for row in _rows(s, model, root):
                        # `redact` is the ONLY place a level is applied, and
                        # it is applied to EDGES too: an edge carries two
                        # keys, and hashing the nodes while leaving the edges
                        # readable would publish the very paths the level
                        # exists to hide. A second `row.pop("body")` stood
                        # here once and did `redact`'s job, so deleting the
                        # rule inside `redact` changed nothing -- a
                        # duplicated invariant, in the package that forbids
                        # them.
                        written["nodes" if row["t"] == "node" else "edges"] += 1
                        row = redact(row, redaction)
                        note_leak(row)
                        emit(fh, row)
                except OutsideRoot as exc:
                    outside.append("%s: %s" % (model, exc))
                finally:
                    s.close(commit=False)
            if outside:
                raise ExportError(
                    "refusing: %d path(s) are not under %s -- an artifact "
                    "that carried them would import outside the directory "
                    "the reader named. First: %s"
                    % (len(outside), root, outside[0]))
            fh.write(json.dumps({"t": "digest", "sha256": digest.hexdigest()},
                                ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp, out_path)
    finally:
        # A refused export leaves nothing behind. The half-written file would
        # otherwise sit there looking like an artifact.
        if os.path.exists(tmp):
            os.remove(tmp)

    return {
        "path": out_path,
        "bytes": os.path.getsize(out_path),
        "redaction": redaction,
        # Structural fields carry no root -- CP-12 proves that. This says how
        # often the root's NAME survives inside user data anyway.
        "root_in_user_data": dict(sorted(leaks.items())),
        "models": sorted(model_paths),
        "written": written,
        "declared": counts,
        "omitted": ["the FTS index (derived; import rebuilds it)",
                    "the mesh (derived; run `mesh build` after importing)",
                    "the config fingerprint (it describes this machine)"]
                   + (["file text (redaction=structure)"]
                      if redaction == "structure" else []),
    }
