#!/usr/bin/env python3
"""Mutation test for CP-8 -- `homegraph update`.

The equivalence gate is the one thing in this checkpoint worth having, and it
is also the one most likely to be an empty gate. It passes trivially if the
corpus does not change, if the comparison counts instead of comparing, or if
nothing is ever built. So most of the mutations below sever one specific wire
between "the diff said X" and "the store now holds X", and check that the
equivalence notices.

The rest attack the refusals, because a refusal that has never been seen to
fire is a comment.

Run:
    python3 tests/mutate_cp8.py
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
    # -- expiry survives the last_seen advance --------------------------
    #
    # The first fix for the last_seen divergence advanced every edge. That
    # revives links the schema had expired, and no set comparison can see it:
    # both stores hold the same edges and differ only in a column.
    ("every edge is advanced, reviving expired links",
     "homegraph/update.py",
     '        "UPDATE edges SET last_seen = ? "\n'
     '        "WHERE last_seen = (SELECT MAX(e2.last_seen) FROM edges e2 "\n'
     '        "                   WHERE e2.src = edges.src)", (as_of,))',
     '        "UPDATE edges SET last_seen = ?", (as_of,))  # mutated',
     "an expired edge is not revived by the update"),

    ("still-asserted edges are left at the old date",
     "homegraph/update.py",
     "    store.db.execute(\n"
     '        "UPDATE edges SET last_seen = ? "',
     "    store.db.execute(\n"
     '        "SELECT ? WHERE 0 "  # mutated: last_seen never advances',
     "updated edges equal a full rebuild's"),

    # -- a file that vanishes mid-build must be reported ----------------
    ("an unreadable file is skipped in silence again",
     "homegraph/models/m3_build.py",
     "        except OSError as exc:\n"
     "            report.unreadable.append((path, repr(exc)))\n"
     "            continue",
     "        except OSError:\n            continue  # mutated: silent skip",
     "the builder reports what it could not read"),

    ("the skip is recorded but never surfaced",
     "homegraph/models/m3_build.py",
     '            "unreadable": len(self.unreadable),',
     "",
     "the count reaches the summary"),

    # -- an interrupted update must commit nothing ----------------------
    ("an interrupted update commits its half",
     "homegraph/store.py",
     "        self.close(commit=exc_type is None)",
     "        self.close(commit=True)  # mutated: commit whatever was done",
     "an interrupted update commits nothing"),

    ("the real model builder commits before update finishes",
     "homegraph/models/m3_build.py",
     "    return report\n\n\ndef _safe_hash",
     "    store.commit()  # mutated: split update's transaction\n"
     "    return report\n\n\ndef _safe_hash",
     "an interrupted update commits nothing"),

    # -- unreadable is not unchanged ------------------------------------
    ("a file that can no longer be read counts as unchanged",
     "homegraph/incremental.py",
     "            if use_hash and not os.access(state.path, os.R_OK):\n"
     "                changes.changed.append(key)\n"
     "                continue",
     "            pass  # mutated: trust size and mtime alone",
     "an unreadable file is reported as changed, not unchanged"),

    # -- the five axes ---------------------------------------------------
    ("removed files are ignored entirely",
     "homegraph/update.py",
     "    for key in changes.removed:\n"
     "        report.forgotten += 1\n"
     "        forget(store, key)",
     "    for key in []:  # mutated: deletions never applied\n"
     "        report.forgotten += 1\n"
     "        forget(store, key)",
     "removed files leave no node"),

    ("added files fall on the floor",
     "homegraph/update.py",
     "    rebuild = sorted(set(changes.added) | set(changes.changed) "
     "| set(extra))",
     "    rebuild = sorted(set(changes.changed) | set(extra))  # mutated",
     "updated nodes equal a full rebuild's"),

    ("changed files are treated as unchanged",
     "homegraph/incremental.py",
     "        if not use_hash:\n"
     "            # M2's path: the cheap check is the only check, by design.\n"
     "            changes.changed.append(key)\n"
     "            continue",
     "        changes.unchanged.append(key)  # mutated: a change is no change",
     "the diff matches the declared change"),

    ("the diff comes back empty",
     "homegraph/incremental.py",
     "    changes = Changes()\n"
     "    sql = (\"SELECT node_key, size, mtime, content_hash FROM nodes \"",
     "    return Changes()  # mutated: nothing ever changed\n"
     "    changes = Changes()\n"
     "    sql = (\"SELECT node_key, size, mtime, content_hash FROM nodes \"",
     "the diff matches the declared change"),

    ("touched files are reparsed like changed ones",
     "homegraph/incremental.py",
     "            changes.touched.append(key)",
     "            changes.changed.append(key)  # mutated: identical bytes, "
     "full reparse",
     "touched files cost a stat and no reparse"),

    # -- what removal has to take with it --------------------------------
    ("a node is removed but its edges are left behind",
     "homegraph/store.py",
     '        self.db.execute("PRAGMA foreign_keys = ON")',
     '        self.db.execute("PRAGMA foreign_keys = OFF")  # mutated',
     "no edge points at a node that is gone"),

    # `delete_node` forgetting its FTS row is CP-1's mutation, and CP-1 kills
    # it. It cannot be killed here: `update` rebuilds the index at the end, so
    # the cleanup runs before the evidence is looked at -- one of the four
    # empty-gate shapes this project keeps finding. What CP-8 owns is whether
    # the rebuild happens at all.
    ("the updated store's index is never rebuilt",
     "homegraph/update.py",
     "    store.rebuild_fts()",
     "    pass  # mutated: new nodes are invisible to search",
     "the FTS index covers the updated store"),

    ("orphaned derived nodes are never pruned",
     "homegraph/update.py",
     "    report.pruned = prune(store)",
     "    report.pruned = 0  # mutated: tags and wikilinks outlive their files",
     "updated nodes equal a full rebuild's"),

    ("a changed file keeps the edges it no longer asserts",
     "homegraph/update.py",
     '            store.db.execute("DELETE FROM edges WHERE src = ?", (nid,))',
     "            pass  # mutated: stale outbound edges survive the rewrite",
     "updated edges equal a full rebuild's"),

    ("datelist masks retain their old anchor",
     "homegraph/update.py",
     '    for row in store.db.execute(\n'
     '            "SELECT id FROM nodes WHERE datelist_anchor IS NOT NULL"):',
     "    for row in []:  # mutated: masks anchored at A are compared at B",
     "updated nodes equal a full rebuild's"),

    ("a changed file keeps the sections it no longer has",
     "homegraph/update.py",
     "    for key in list(changes.changed) + extra:\n"
     "        forget(store, key, keep_self=True)",
     "    for key in []:  # mutated: nothing is forgotten before rebuilding\n"
     "        forget(store, key, keep_self=True)",
     "updated nodes equal a full rebuild's"),

    # -- the parts that are not a per-file diff at all --------------------
    ("neighbours of the change are not rebuilt",
     "homegraph/update.py",
     "    extra = (list(spec.affected(store, changes, list(current)))\n"
     "             if spec.affected else [])",
     "    extra = []  # mutated: only files that changed on disk are rebuilt",
     "neighbours of the change were rebuilt too"),

    ("a partial rebuild resolves links against only its own batch",
     "homegraph/models/m3_build.py",
     "    index = build_index(index_paths if index_paths is not None "
     "else paths)",
     "    index = build_index(paths)  # mutated: the corpus is the batch",
     "updated nodes equal a full rebuild's"),

    ("section nodes are compared as file nodes and deleted",
     "homegraph/update.py",
     "    changes = incremental.diff(store, current, use_hash=spec.use_hash,\n"
     "                               kinds=(spec.kind,))",
     "    changes = incremental.diff(store, current, "
     "use_hash=spec.use_hash)  # mutated",
     "the diff matches the declared change"),

    # -- history ----------------------------------------------------------
    ("update resets the history of every file it touches",
     "homegraph/update.py",
     "    for key in list(changes.changed) + extra:\n"
     "        forget(store, key, keep_self=True)",
     "    for key in list(changes.changed) + extra:\n"
     "        forget(store, key)  # mutated: the file is deleted and recreated",
     "surviving nodes keep their first_seen"),

    ("shared nodes are pruned between forget and rebuild",
     "homegraph/update.py",
     "    rebuild = sorted(set(changes.added) | set(changes.changed) "
     "| set(extra))",
     "    report.pruned = prune(store)  # mutated: resets shared identities\n"
     "    rebuild = sorted(set(changes.added) | set(changes.changed) "
     "| set(extra))",
     "surviving nodes keep their first_seen"),

    ("a touched file's mtime is written into its sections too",
     "homegraph/update.py",
     '            "UPDATE nodes SET mtime = ?, last_seen = ? '
     'WHERE node_key = ?",',
     '            "UPDATE nodes SET mtime = ?, last_seen = ? '
     'WHERE path = ?",  # mutated',
     "updated nodes equal a full rebuild's"),

    # -- refusals ---------------------------------------------------------
    ("update on an empty store quietly succeeds",
     "homegraph/update.py",
     "    if report.nodes_before == 0:",
     "    if False:  # mutated: an update of nothing is a success",
     "update on an unbuilt store refuses"),

    ("a layout change is applied as though it were a file change",
     "homegraph/update.py",
     "    if have is not None and have != want and not allow_config_change:",
     "    if False:  # mutated: two configurations in one store",
     "update refuses after the layout changed"),

    ("the fingerprint ignores the roles",
     "homegraph/update.py",
     '        {"root": config.root,\n'
     '         "roles": {k: sorted(v) for k, v in sorted(config.roles.items())}},',
     '        {"root": config.root},  # mutated: only the root identifies a '
     'layout',
     "update refuses after the layout changed"),

    ("a model with no incremental path gets one anyway",
     "homegraph/update.py",
     "    if model in NO_INCREMENTAL:\n"
     "        raise CannotUpdate",
     "    if False:\n"
     "        raise CannotUpdate",
     "a model with no correct incremental path says so"),

    # -- the federation ---------------------------------------------------
    ("the federation keeps stubs for files that are gone",
     "homegraph/mesh.py",
     "                if row[\"node_key\"] not in mirrored:\n"
     "                    mesh.delete_node(row[\"node_key\"])\n"
     "                    removed += 1",
     "                pass  # mutated: stale stubs answer about a gone world",
     "the federation forgets a file the model dropped"),

    ("the federation empties itself instead of pruning",
     "homegraph/mesh.py",
     "                if row[\"node_key\"] not in mirrored:",
     "                if True:  # mutated: prune everything",
     "it did not simply empty itself"),
]


def run_suite(tree):
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", "test_cp8.py")],
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
        tree = tempfile.mkdtemp(prefix="mut8-",
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
