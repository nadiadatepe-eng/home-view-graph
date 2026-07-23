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
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 900

MUTATIONS = [
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
     "NODE_COLUMNS = (\"kind\", \"subtype\", \"title\", \"body\", \"size\", \"mtime\",\n                \"content_hash\", \"first_seen\", \"last_seen\",\n                \"activity_datelist\", \"datelist_int\", \"datelist_anchor\")",
     "NODE_COLUMNS = (\"kind\", \"subtype\", \"title\", \"body\", \"size\", \"mtime\",\n                \"content_hash\", \"first_seen\", \"last_seen\")  # mutated",
     "a round trip under the same root changes nothing"),

    ("structure keeps the file text after all",
     "homegraph/export.py",
     "    if level == \"structure\":\n        return {k: v for k, v in row.items() if k != \"body\"}",
     "    if level == \"structure\":\n        return row  # mutated: labelled structure, shaped full",
     "structure carries no file text, and full carries some"),

    ("an unimplemented level is produced anyway",
     "homegraph/export.py",
     "    if level not in IMPLEMENTED:",
     "    if False:  # mutated: `shape` ships unshaped",
     "a level that is not implemented is refused, not approximated"),

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
     "    if claimed != digest.hexdigest():",
     "    if False:  # mutated: a tampered artifact imports cleanly",
     "every broken artifact is refused, and says which way"),

    ("a missing digest is treated as no digest needed",
     "homegraph/importer.py",
     "    if claimed is None:",
     "    if False:  # mutated: truncation is not noticed",
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
     "    for store in stores.values():\n        store.rebuild_fts()",
     "    pass  # mutated: an imported store answers no query",
     "the artifact ships no index, and the import rebuilds one"),

    # -- the command line the user actually walks -------------------------
    ("a refused import leaves its half-made store behind",
     "homegraph/cli.py",
     "        for path in fresh:\n            with _ctx.suppress(OSError):\n                os.remove(path)",
     "        pass  # mutated: an empty store is left looking built",
     "a refused import leaves no store looking built"),

    ("importing over an existing store just overwrites it",
     "homegraph/cli.py",
     "            if s.node_count() and not args.force:",
     "            if False:  # mutated: --force is decorative",
     "importing over an existing store is refused without --force"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp12.py")],
            capture_output=True, text=True, cwd=tree, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}, None
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAIL"):
            red.add(line[4:].strip().rsplit("  ", 1)[0].strip())
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red, proc


def main():
    survived, killed, misattributed, crashes = [], [], [], []
    for name, rel, needle, repl, expected in MUTATIONS:
        tree = tempfile.mkdtemp(prefix="mut12-",
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            src = open(target).read()
            if needle not in src:
                print("SKIP      %-52s needle missing in %s" % (name, rel))
                survived.append((name, "needle missing"))
                continue
            open(target, "w").write(src.replace(needle, repl, 1))

            red, proc = run_suite(work)
            crashed = any(r.startswith("<crash>") or r == "<timeout>"
                          for r in red)
            gate_red = [r for r in red if not r.startswith("<crash>")
                        and r != "<timeout>"]
            if not red:
                print("SURVIVED  %-52s suite still green" % name)
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-52s -> %s" % (name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-52s -> %s (expected %r)"
                      % (name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-52s -> %s" % (name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-52s unclassified" % name)
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(MUTATIONS)))
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0


if __name__ == "__main__":
    sys.exit(main())
