#!/usr/bin/env python3
"""Mutation test for CP-12.

An artifact fails by looking complete. A dropped date, a key that kept its
root, a digest that covers less than it claims -- none of them raise, and each
produces a graph that answers confidently about the wrong machine. Every
mutation below manufactures one.

Run:
    python3 tests/mutate_cp12.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # -- what the audit found, one needle each ----------------------------
    ("`..` resolves instead of being refused on import",
     "homegraph/portable.py",
     "    local = os.path.normpath(os.path.join(root, portable[len(MARKER) + 1:]))\n"
     "    _under(local, root)          # raises OutsideRoot if it escaped\n"
     "    return local",
     "    return os.path.normpath(os.path.join(root, portable[len(MARKER) + 1:]))"
     "  # mutated: escapes the reader's root",
     "`..` cannot escape the root on the way in"),

    ("the boundary test becomes a string prefix",
     "homegraph/portable.py",
     "    prefix = root if root.endswith(os.sep) else root + os.sep\n"
     "    if not norm.startswith(prefix):",
     "    if not norm.startswith(root):  # mutated: /rootless is under /root",
     "the adversarial rows behave as the key declares"),

    ("the digest line stops terminating the artifact",
     "homegraph/importer.py",
     "                trailing = [ln for ln in fh if ln.strip()]\n"
     "                if trailing:",
     "                trailing = []\n"
     "                if trailing:  # mutated: content past the end imports",
     "nothing may follow the digest line"),

    ("the manifest's counts are read and not compared",
     "homegraph/importer.py",
     "    if counts and (read[\"nodes\"], read[\"edges\"]) != (want[\"nodes\"],",
     "    if False and (read[\"nodes\"], read[\"edges\"]) != (want[\"nodes\"],",
     "a manifest that overstates its own contents is refused"),

    ("a row may belong to a model the manifest never declared",
     "homegraph/importer.py",
     "            if model not in declared_models:",
     "            if False:  # mutated: smuggled rows import silently",
     "a row outside the declared models is refused"),

    ("a missing column reaches the user as a traceback",
     "homegraph/importer.py",
     "    missing = [f for f in fields if row.get(f) is None]\n"
     "    if missing:",
     "    missing = []\n"
     "    if missing:  # mutated: KeyError at the user",
     "a missing column is named, not reported as a missing endpoint"),

    ("shape ships the content hash after all",
     "homegraph/export.py",
     'SHAPE_DROPS = ("body", "mtime", "content_hash", "size",\n'
     '               "activity_datelist", "datelist_int", "datelist_anchor")',
     'SHAPE_DROPS = ("body", "mtime")  # mutated: fingerprints travel',
     "shape carries no fingerprint of the files themselves"),

    ("inspect prints the manifest without checking it",
     "homegraph/importer.py",
     "    if claimed != digest.hexdigest():\n"
     '        return "digest mismatch"',
     "    if False:  # mutated: inspect cannot say no\n"
     '        return "digest mismatch"',
     "inspect catches a digest that does not match"),

    ("history is restored field by field, keeping local values",
     "homegraph/store.py",
     "                                activity_datelist=COALESCE(?, '[]'),\n"
     "                                datelist_int=COALESCE(?, 0),\n"
     "                                datelist_anchor=?",
     "                                activity_datelist=COALESCE(?, activity_datelist),\n"
     "                                datelist_int=COALESCE(?, datelist_int),\n"
     "                                datelist_anchor=COALESCE(?, datelist_anchor)",
     "an imported node takes its history whole, not field by field"),

    ("the import root is accepted without looking",
     "homegraph/cli.py",
     "    if not os.path.exists(root):",
     "    if False:  # mutated: a root that is not there is fine",
     "a root that does not exist is refused"),

    # -- the conversion itself --------------------------------------------
    ("keys keep their absolute root",
     "homegraph/portable.py",
     "    rel = _under(path, root)\n    return MARKER if not rel else \"%s/%s\" % (MARKER, rel)",
     "    return path  # mutated: the artifact carries the export machine",
     "no structural field carries a root or an absolute path"),

    ("the archive wrapper is not looked into",
     "homegraph/portable.py",
     "    if key.startswith(ARCHIVE_PREFIX):\n        rest = key[len(ARCHIVE_PREFIX):]\n        inner, sep, entry = rest.partition(\"!\")\n        if inner.startswith(\"/\"):",
     "    if key.startswith(ARCHIVE_PREFIX):\n        rest = key[len(ARCHIVE_PREFIX):]\n        inner, sep, entry = rest.partition(\"!\")\n        if False:  # mutated: archive keys keep the old root",
     "no structural field carries a root or an absolute path"),

    ("a path outside the root is emitted instead of refused",
     "homegraph/portable.py",
     "        raise OutsideRoot(\"%s is not under %s\" % (path, root))",
     "        return os.path.relpath(norm, root)  # mutated",
     # Re-aimed: the gate that owns this claim is the planted stray path, not
     # the equivalence. The equivalence corpus has no path outside its root,
     # so it could only ever have caught this by accident.
     "a path outside the root stops the export"),

    # -- what the export carries ------------------------------------------
    ("edges lose their provenance",
     "homegraph/export.py",
     "               \"rel\": r[\"rel\"], \"method\": r[\"method\"],",
     "               \"rel\": r[\"rel\"], \"method\": \"exact\",  # mutated",
     "its edges equal them too, with provenance and dates"),

    ("the datelist anchor is left behind",
     "homegraph/export.py",
     "\"activity_datelist\", \"datelist_int\", \"datelist_anchor\")",
     "\"activity_datelist\", \"datelist_int\")  # mutated: anchor left behind",
     "a round trip under the same root changes nothing"),

    ("structure keeps the file text after all",
     "homegraph/export.py",
     "    if level == \"structure\":\n        return {k: v for k, v in row.items() if k != \"body\"}",
     "    if level == \"structure\":\n        return row  # mutated: labelled structure, shaped full",
     "structure carries no file text, and full carries some"),

    ("an unknown level is accepted",
     "homegraph/export.py",
     "    if level not in LEVELS:",
     "    if False:  # mutated: any string is a redaction level",
     "an unknown redaction level is refused, not approximated"),

    ("a level is declared without being implemented",
     "homegraph/export.py",
     'IMPLEMENTED = ("full", "structure", "shape")',
     'IMPLEMENTED = ("full", "structure")  # mutated: shape declared, absent',
     "every declared level is one this build can produce"),

    # -- shape ------------------------------------------------------------
    ("shape leaves the paths readable",
     "homegraph/export.py",
     '        for field in ("node_key", "path", "src", "dst"):\n'
     "            if field in out:\n"
     "                out[field] = shape_key(out[field])",
     "        pass  # mutated: hashed titles, readable paths",
     "shape leaves no readable name behind"),

    ("shape hashes the type away with the name",
     "homegraph/export.py",
     '    prefix, colon, value = key.partition(":")\n'
     "    if colon:\n"
     '        return "%s:%s" % (prefix, _digest(value))',
     '    prefix, colon, value = key.partition(":")\n'
     "    if colon:\n"
     "        return _digest(key)  # mutated: author: and ref: vanish",
     "the type prefix survives the hashing"),

    ("shape keeps the timestamps",
     "homegraph/export.py",
     'SHAPE_DROPS = ("body", "mtime", "content_hash", "size",',
     'SHAPE_DROPS = ("body",  # mutated: mtime fingerprints\n               "content_hash", "size",',
     "shape carries no text and no timestamps"),

    ("edges keep readable keys while the nodes are hashed",
     "homegraph/export.py",
     '                        written["nodes" if row["t"] == "node" else "edges"] += 1\n'
     "                        row = redact(row, redaction)",
     '                        written["nodes" if row["t"] == "node" else "edges"] += 1\n'
     '                        if row["t"] == "node":  # mutated: edges unshaped\n'
     "                            row = redact(row, redaction)",
     "shape leaves no readable name behind"),

    ("the manifest is left out of the digest",
     "homegraph/export.py",
     "            emit(fh, manifest)",
     "            fh.write(json.dumps(manifest, ensure_ascii=False,\n"
     "                                sort_keys=True) + \"\\n\")  # mutated",
     # An export whose manifest is undigested makes EVERY import fail the
     # digest, so the first gate to go red is the CLI round trip rather than
     # the tamper case. Named after what actually says no.
     "import runs from the command line and writes a store"),

    ("the leak counter reports a number of its own",
     "homegraph/export.py",
     "            if root in value or (base and base in value):\n                leaks[field] = leaks.get(field, 0) + 1",
     "            if root in value:  # mutated: counts less than it scans\n                leaks[field] = leaks.get(field, 0) + 1",
     "the reported user-data leak matches an independent recount"),

    # -- what the import trusts -------------------------------------------
    ("the digest is not checked",
     "homegraph/importer.py",
     "    if claimed != digest.hexdigest():\n"
     "        raise ImportError_(",
     "    if False:  # mutated: a tampered artifact imports cleanly\n"
     "        raise ImportError_(",
     "every broken artifact is refused, and says which way"),

    ("a missing digest is treated as no digest needed",
     "homegraph/importer.py",
     "    if claimed is None:\n"
     "        raise ImportError_(",
     "    if False:  # mutated: truncation is not noticed\n"
     "        raise ImportError_(",
     "every broken artifact is refused, and says which way"),

    ("a newer schema is imported rather than refused",
     "homegraph/importer.py",
     "        if schema > store.version:",
     "        if False:  # mutated: guess what a newer schema meant",
     "every broken artifact is refused, and says which way"),

    ("the format version is compared loosely",
     "homegraph/importer.py",
     "    if fmt != FORMAT_VERSION:",
     "    if fmt is None:  # mutated: any version is close enough",
     "every broken artifact is refused, and says which way"),

    ("history is not restored, so the import stamps today",
     "homegraph/importer.py",
     "    store.restore_node_history(",
     "    return  # mutated: first_seen becomes import day\n    store.restore_node_history(",
     "a round trip under the same root changes nothing"),

    ("edge dates are not restored",
     "homegraph/importer.py",
     "    store.restore_edge_history(src, dst, row[\"rel\"],",
     "    return  # mutated: every edge was first seen today\n"
     "    store.restore_edge_history(src, dst, row[\"rel\"],",
     "its edges equal them too, with provenance and dates"),

    ("the index is not rebuilt after importing",
     "homegraph/importer.py",
     "        store.rebuild_fts()",
     "        pass  # mutated: an imported store answers no query",
     "the artifact ships no index, and the import rebuilds one"),

    # -- the command line the user actually walks -------------------------
    ("a refused import leaves its half-made store behind",
     "homegraph/cli.py",
     "    for path in paths:\n        with contextlib.suppress(OSError):\n            os.remove(path)",
     "    pass  # mutated: an empty store is left looking built",
     "a refused import leaves no store looking built"),

    ("importing over an existing store just overwrites it",
     "homegraph/cli.py",
     "        if occupied and not args.force:",
     "        if False:  # mutated: --force is decorative",
     "importing over an existing store is refused without --force"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_cp12.py", prefix="mut12-", timeout=900))
