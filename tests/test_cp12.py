#!/usr/bin/env python3
"""CP-12 -- the portable artifact.

The load-bearing claim, and the only one worth making:

    a store built on a corpus under root A, exported, and imported under a
    DIFFERENT root B, is indistinguishable from a store built on the same
    corpus under B from scratch.

Compared as SETS over nodes and edges, the way CP-8 compares an update against
a rebuild. Counts are equal when one node has been swapped for another, which
is exactly what a wrong path conversion produces.

**The negative control is stated narrowly, because the wide version cannot
pass and not because of a bug.** The plan said: search the decompressed
artifact for the export root, one hit is red. Measured on the real corpus:
zero absolute paths survive, and the root's NAME survives anyway -- a
directory inside the root can be named after the root, and at `full` a user's
own prose names absolute paths. Rewriting someone's text to hide a path would
be a lie about what the file says. So the gate covers the STRUCTURAL fields,
which are provable, and `root_in_user_data` is measured beside it and checked
against an independent recount.

Run:
    python3 tests/test_cp12.py
"""
from __future__ import annotations

import json
import lzma
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures.synthetic import ROOT as SYNTH_ROOT       # noqa: E402

os.environ.setdefault("HOMEGRAPH_ROOT", SYNTH_ROOT)

from homegraph.export import export                           # noqa: E402
from homegraph.importer import ImportError_, load             # noqa: E402
from homegraph.store import Store                             # noqa: E402

AS_OF = "2026-07-22"
LATER = "2026-07-30"
ROOTDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def safe_load(*args, **kwargs):
    """`load` that returns an error instead of ending the run.

    A mutation that makes the import raise would otherwise be scored as
    "detected by a crash", the weakest detection there is -- and the harness
    reported exactly that for the mutation that left the manifest out of the
    digest.
    """
    try:
        return load(*args, **kwargs), None
    except Exception as exc:                                    # noqa: BLE001
        return None, "raised:%s: %s" % (type(exc).__name__, exc)


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-52s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def nodes_of(db):
    """Everything an equivalence must cover, including the history.

    `first_seen`, the datelist and its anchor are in here on purpose: a round
    trip that quietly restamps them looks identical on any check that only
    compares keys, and the versioned schema exists for exactly those columns.
    """
    with Store(db) as s:
        return {(r["node_key"], r["kind"], r["subtype"], r["path"],
                 r["title"], r["body"], r["size"], r["content_hash"],
                 r["first_seen"], r["last_seen"], r["datelist_int"],
                 r["datelist_anchor"])
                for r in s.db.execute("SELECT * FROM nodes")}


def edges_of(db):
    with Store(db) as s:
        return {(r["a"], r["rel"], r["b"], r["method"], r["confidence"],
                 r["first_seen"], r["last_seen"])
                for r in s.db.execute(
                    "SELECT s.node_key a, d.node_key b, e.rel, e.method, "
                    "e.confidence, e.first_seen, e.last_seen FROM edges e "
                    "JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst")}


def strip_root(rows, root):
    """Node/edge tuples with `root` removed, so two roots can be compared."""
    def fix(v):
        return v.replace(root, "<ROOT>") if isinstance(v, str) else v
    return {tuple(fix(v) for v in row) for row in rows}


def build_corpus(root, syn):
    """The fixture at a root of our choosing, and the M4 store over it.

    M4 rather than M3: it is the model with the widest key ZOO -- plain paths,
    `archive:...!entry` wrappers, `app:`, `format:` and `rollup:` keys with no
    path at all -- so a converter that handles only the easy shape fails here
    rather than in production.
    """
    from homegraph.corpus import Classifier
    from homegraph.models.m4_misc import build as m4_build

    syn.build(root)
    cfg_path = syn._config_for(root)
    syn.use_config(cfg_path)
    clf = Classifier(home=root)
    paths = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                if clf.classify(p) == "misc":
                    paths.append(p)
            except Exception:                                   # noqa: BLE001
                continue
    db = os.path.join(os.path.dirname(root), "%s.db" % os.path.basename(root))
    with Store(db, model="m4") as s:
        m4_build(s, sorted(paths), AS_OF)
        _seed(s, root)
        s.rebuild_fts()
    return db


def _seed(store, root):
    """Plant what the gates claim to test. Declared, not discovered.

    M4 writes only `exact` edges, all first seen and last seen on the same
    day, and no title contains the corpus root. Six mutations survived
    against that store while claiming to be caught -- provenance, edge dates
    and the leak counter had nothing to bite on. A fixture that cannot
    exhibit the property under test makes its gate vacuous, which is the
    shape this project keeps finding, so the three properties are put here
    deliberately and named:

      * an edge derived by `mention` (0.5), so dropping provenance shows;
      * that same edge re-asserted later, so `last_seen` differs from
        `first_seen` and dropping the date restore shows;
      * a title that names the corpus root, so the leak counter has
        something to count.
    """
    a = store.db.execute(
        "SELECT node_key FROM nodes WHERE path IS NOT NULL "
        "ORDER BY node_key LIMIT 1").fetchone()["node_key"]
    b = store.db.execute(
        "SELECT node_key FROM nodes WHERE path IS NOT NULL "
        "ORDER BY node_key DESC LIMIT 1").fetchone()["node_key"]
    store.upsert_edge(a, b, "MENTIONS_PATH", AS_OF, method="mention")
    store.restore_edge_history(a, b, "MENTIONS_PATH",
                               first_seen=AS_OF, last_seen=LATER)
    store.upsert_node("seeded:title-names-the-root", kind="file",
                      subtype="seeded",
                      title="notes about %s and its layout" % root,
                      body="a title that names the corpus root",
                      as_of=AS_OF)
    # The BASENAME alone, without the rest of the path. The leak counter
    # looks for both, and a fixture carrying only the full root lets the
    # basename half be deleted with nothing to show for it.
    store.upsert_node("seeded:title-names-the-basename", kind="file",
                      subtype="seeded",
                      title="a note filed under %s" % os.path.basename(root),
                      body="a title that names only the root's last segment",
                      as_of=AS_OF)


# -- the checks ------------------------------------------------------------

def t_round_trip(tmp, db_a, root_a):
    """Same root, and nothing may change."""
    art = os.path.join(tmp, "same.hgx")
    report = export({"m4": db_a}, art, root_a, redaction="full")
    back = os.path.join(tmp, "same.db")
    with Store(back, model="m4") as s:
        _, failure = safe_load(art, {"m4": s}, root_a)
    check("the round trip completed without raising", failure is None,
          failure or "no exception")

    n_a, n_b = nodes_of(db_a), nodes_of(back)
    e_a, e_b = edges_of(db_a), edges_of(back)
    check("a round trip under the same root changes nothing",
          n_a == n_b and e_a == e_b and n_a,
          "%d node(s) %s, %d edge(s) %s"
          % (len(n_a), "equal" if n_a == n_b else "DIFFER",
             len(e_a), "equal" if e_a == e_b else "DIFFER"))
    # The claim above is only worth something if the store had history to
    # lose. Without this, a corpus where every date is today would pass while
    # the restore path did nothing at all.
    with Store(db_a) as s:
        anchors = {r["datelist_anchor"] for r in s.db.execute(
            "SELECT datelist_anchor FROM nodes WHERE datelist_anchor IS NOT NULL")}
    check("the source store had history to lose", bool(anchors),
          "%d distinct datelist anchor(s)" % len(anchors))
    return art, report


def t_moved_root(tmp, db_a, root_a, syn):
    """The whole point: a different root, and the same graph."""
    root_b = os.path.join(tmp, "en-helt-annen", "rot-2027")
    db_b = build_corpus(root_b, syn)

    art = os.path.join(tmp, "moved.hgx")
    export({"m4": db_a}, art, root_a, redaction="full")
    imported = os.path.join(tmp, "moved.db")
    with Store(imported, model="m4") as s:
        safe_load(art, {"m4": s}, root_b)

    # The seeded node is excluded from the equivalence, and the exclusion is
    # the finding: its TITLE names the corpus root, and user text is never
    # rewritten -- so after importing under root B it still names root A,
    # while a build under B names B. Both are correct. Asserted below rather
    # than hidden here.
    def real(rows):
        return {r for r in rows if not str(r[0]).startswith("seeded:")}

    got = strip_root(real(nodes_of(imported)), root_b)
    want = strip_root(real(nodes_of(db_b)), root_b)
    only_i = sorted(r[0] for r in got - want)[:2]
    only_b = sorted(r[0] for r in want - got)[:2]
    check("an imported graph equals one built under the new root",
          got == want and got,
          "%d node(s); %d only imported, %d only built%s"
          % (len(got), len(got - want), len(want - got),
             "" if got == want else "  e.g. %s" % (only_i or only_b)))

    got_e = strip_root(edges_of(imported), root_b)
    want_e = strip_root(edges_of(db_b), root_b)
    check("its edges equal them too, with provenance and dates",
          got_e == want_e and got_e,
          "%d edge(s); %d only imported, %d only built"
          % (len(got_e), len(got_e - want_e), len(want_e - got_e)))

    # Nothing may still point at the machine it came from.
    with Store(imported) as s:
        stale = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE node_key LIKE ? OR path LIKE ?",
            (root_a + "%", root_a + "%")).fetchone()["c"]
        under = s.db.execute(
            "SELECT COUNT(*) c FROM nodes WHERE path LIKE ?",
            (root_b + "%",)).fetchone()["c"]
    with Store(imported) as s:
        seeded = s.get_node("seeded:title-names-the-root")
    check("user text is carried verbatim, old root and all",
          seeded is not None and root_a in (seeded["title"] or ""),
          "the imported title still names the exporting root: %s"
          % (root_a in (seeded["title"] or "") if seeded else "no node"))

    check("every imported path lives under the new root, none under the old",
          stale == 0 and under > 0,
          "%d under the new root, %d still under the old" % (under, stale))
    return root_b


def t_negative_control(tmp, art, root_a, report):
    """No STRUCTURAL field carries a root. Measured, with its positive control.

    The wide version of this gate -- "the root's name appears nowhere in the
    artifact" -- cannot pass, and the reason is worth keeping in the code
    rather than in a commit message: a directory inside the root may be named
    after the root, and at `full` the user's own prose names absolute paths.
    """
    structural = ("node_key", "path", "src", "dst")
    offenders, user_data = [], {}
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            for field, value in row.items():
                if not isinstance(value, str):
                    continue
                if field in structural:
                    if value.startswith("/") or root_a in value:
                        offenders.append((field, value[:60]))
                elif root_a in value or os.path.basename(root_a) in value:
                    # The same rule `export` applies -- root OR its basename.
                    # A recount with a narrower rule agrees on empty data and
                    # nowhere else, which is a cross-check that cannot fail.
                    user_data[field] = user_data.get(field, 0) + 1
    check("no structural field carries a root or an absolute path",
          not offenders,
          "%d offender(s)%s" % (len(offenders),
                                "" if not offenders else "  %s" % offenders[:2]))

    # The positive control. Without it the check above passes on an artifact
    # that is simply empty, or on a scan that looks at the wrong fields.
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        marked = sum(1 for line in fh
                     if '"node_key": "~' in line or '"src": "~' in line)
    check("and the scan was looking at fields that exist",
          marked > 5, "%d row(s) carry a rooted key" % marked)

    # `root_in_user_data` is a measurement, not a claim, so it is checked
    # against an independent recount rather than trusted.
    check("the reported user-data leak matches an independent recount",
          report["root_in_user_data"] == user_data,
          "reported %s, recounted %s"
          % (report["root_in_user_data"], user_data))


def t_outside_root(tmp, db_a, root_a):
    """A node the root does not contain must stop the export, not travel.

    The corpus has no such path -- every file is under the root by
    construction -- so the refusal had nothing to refuse and its mutation
    survived. Planted here instead: a node whose path points somewhere else
    entirely, which is what a store built under one root and exported under
    another looks like.
    """
    from homegraph.export import ExportError

    stray = os.path.join(tmp, "stray.db")
    shutil.copyfile(db_a, stray)
    with Store(stray, model="m4") as s:
        s.upsert_node("/somewhere/else/entirely.txt", kind="file",
                      path="/somewhere/else/entirely.txt", title="stray",
                      as_of=AS_OF)

    raised = None
    try:
        export({"m4": stray}, os.path.join(tmp, "stray.hgx"), root_a)
    except ExportError as exc:
        raised = str(exc)
    except Exception as exc:                                    # noqa: BLE001
        raised = "wrong error: %s" % type(exc).__name__
    check("a path outside the root stops the export",
          raised is not None and "not under" in raised,
          "%s" % (raised or "exported it anyway")[:70])
    check("and it left no artifact behind",
          not os.path.exists(os.path.join(tmp, "stray.hgx")),
          "no half-written file")


def t_redaction(tmp, db_a, root_a):
    full = os.path.join(tmp, "r-full.hgx")
    struct = os.path.join(tmp, "r-struct.hgx")
    export({"m4": db_a}, full, root_a, redaction="full")
    export({"m4": db_a}, struct, root_a, redaction="structure")

    def bodies(path):
        n = 0
        with lzma.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("t") == "node" and row.get("body"):
                    n += 1
        return n

    # The positive control first: `structure` dropping every body means
    # nothing unless `full` carried some.
    check("structure carries no file text, and full carries some",
          bodies(full) > 0 and bodies(struct) == 0,
          "%d node(s) with text at full, %d at structure"
          % (bodies(full), bodies(struct)))

    from homegraph.export import IMPLEMENTED, LEVELS

    raised = None
    try:
        export({"m4": db_a}, os.path.join(tmp, "bogus.hgx"), root_a,
               redaction="not-a-level")
    except Exception as exc:                                    # noqa: BLE001
        raised = "%s: %s" % (type(exc).__name__, exc)
    # The MESSAGE, not just the type: `redact` keeps an unreachable backstop
    # that raises the same class, so a check on the class alone passed while
    # the guard producing the user-facing refusal was deleted.
    check("an unknown redaction level is refused, not approximated",
          raised is not None and "unknown redaction level" in raised,
          "%s" % (raised or "produced an artifact")[:70])
    # This gate replaced one that asserted `shape` was refused -- true until
    # E3 shipped, and then a gate testing that a finished feature was still
    # missing. What survives the change is the invariant underneath: a level
    # this build DECLARES must be one it can produce, or the label lies.
    check("every declared level is one this build can produce",
          set(LEVELS) == set(IMPLEMENTED),
          "declared %s, implemented %s"
          % (sorted(LEVELS), sorted(IMPLEMENTED)))


def t_shape(tmp, db_a, root_a):
    """`shape` hides which file, keeps what kind, and still connects.

    The level exists to be shareable, so every check here is about what does
    NOT survive -- with a positive control for each, because "no readable
    name in the artifact" passes trivially on an artifact with no names.
    """
    art = os.path.join(tmp, "shape.hgx")
    export({"m4": db_a}, art, root_a, redaction="shape")
    full = os.path.join(tmp, "shape-full.hgx")
    export({"m4": db_a}, full, root_a, redaction="full")

    def rows(path):
        with lzma.open(path, "rt", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    shaped = [r for r in rows(art) if r.get("t") in ("node", "edge")]
    plain = [r for r in rows(full) if r.get("t") in ("node", "edge")]

    # Nothing that looks like a name. The `~/` marker and the archive `!`
    # seam are structure, not names, so they are the only separators allowed.
    leaked = []
    for row in shaped:
        for field in ("node_key", "path", "src", "dst", "title"):
            value = row.get(field)
            if not isinstance(value, str):
                continue
            tail = value.replace("~/", "").replace("!", "").split(":")[-1]
            if "/" in tail or "." in tail:
                leaked.append((field, value[:50]))
    check("shape leaves no readable name behind",
          not leaked, "%d leak(s)%s" % (len(leaked),
                                        "" if not leaked else "  %s" % leaked[:2]))
    readable = sum(1 for r in plain
                   if "/" in str(r.get("node_key", "")).replace("~/", ""))
    check("and the same corpus at full is full of them",
          readable > 5, "%d readable path(s) at full" % readable)

    # The type survives: every prefix the source used is still there.
    def prefixes(items):
        out = set()
        for row in items:
            key = row.get("node_key")
            if isinstance(key, str) and ":" in key and not key.startswith("~"):
                out.add(key.split(":")[0])
        return out
    check("the type prefix survives the hashing",
          prefixes(shaped) == prefixes(plain) and prefixes(shaped),
          "%s" % sorted(prefixes(shaped)))

    # Text and timestamps are gone, and the control shows they were there.
    def has(items, field):
        return sum(1 for r in items if r.get(field) is not None)
    check("shape carries no text and no timestamps",
          has(shaped, "body") == 0 and has(shaped, "mtime") == 0
          and has(plain, "body") > 0 and has(plain, "mtime") > 0,
          "body %d/%d, mtime %d/%d at shape/full"
          % (has(shaped, "body"), has(plain, "body"),
             has(shaped, "mtime"), has(plain, "mtime")))

    # Deterministic: the same corpus hashes the same way twice, or two
    # artifacts of one graph could never be compared.
    again = os.path.join(tmp, "shape2.hgx")
    export({"m4": db_a}, again, root_a, redaction="shape")
    check("hashing is deterministic across runs",
          open(art, "rb").read() == open(again, "rb").read(),
          "two exports are byte-identical")

    # And the graph still IS a graph: the edges connect the hashed nodes.
    imported = os.path.join(tmp, "shape.db")
    with Store(imported, model="m4") as s:
        _, failure = safe_load(art, {"m4": s}, os.path.join(tmp, "shape-root"))
    with Store(imported) as s:
        n = s.node_count()
        e = s.db.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
        degrees = sorted(r["d"] for r in s.db.execute(
            "SELECT COUNT(*) d FROM edges GROUP BY src"))
    with Store(db_a) as s:
        want_n = s.node_count()
        want_e = s.db.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
        want_d = sorted(r["d"] for r in s.db.execute(
            "SELECT COUNT(*) d FROM edges GROUP BY src"))
    check("a shaped graph still has the shape it came from",
          failure is None and (n, e, degrees) == (want_n, want_e, want_d),
          "%d/%d nodes, %d/%d edges, degree sequences %s%s"
          % (n, want_n, e, want_e,
             "equal" if degrees == want_d else "DIFFER", failure or ""))


def t_refusals(tmp, art):
    """Six ways an artifact can be wrong, and six named refusals."""
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    def write(name, rows):
        p = os.path.join(tmp, name)
        with lzma.open(p, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        return p

    manifest = json.loads(lines[0])
    tampered = list(lines)
    body = json.loads(tampered[3])
    body["title"] = "CHANGED"
    tampered[3] = json.dumps(body, sort_keys=True)
    # Relabelled to something the artifact is NOT. The first version of this
    # wrote `full` onto an artifact already exported at `full`, so the line
    # was byte-identical, the digest matched, and the gate reported the code
    # was broken. A tamper case has to actually tamper.
    other = "structure" if manifest.get("redaction") == "full" else "full"
    relabelled = [json.dumps(dict(manifest, redaction=other), sort_keys=True)]
    relabelled += lines[1:]

    not_lzma = os.path.join(tmp, "plain.hgx")
    with open(not_lzma, "wb") as fh:
        fh.write(b"this is not an archive")

    cases = [
        ("a truncated artifact", write("t-trunc.hgx", lines[:-1]), "digest"),
        ("changed content", write("t-tamper.hgx", tampered), "digest"),
        ("a relabelled manifest", write("t-label.hgx", relabelled), "digest"),
        ("an unknown format",
         write("t-fmt.hgx", [json.dumps(dict(manifest, format=99),
                                        sort_keys=True)] + lines[1:]),
         "format"),
        ("a newer schema",
         write("t-schema.hgx", [json.dumps(dict(manifest, schema=99),
                                           sort_keys=True)] + lines[1:]),
         "schema"),
        ("a file that is not an archive", not_lzma, "readable"),
    ]
    refused, wrong = [], []
    for name, path, expect in cases:
        db = os.path.join(tmp, "refuse.db")
        if os.path.exists(db):
            os.remove(db)
        try:
            with Store(db, model="m4") as s:
                load(path, {"m4": s}, "/tmp/nowhere")
            wrong.append(name)
        except ImportError_ as exc:
            if expect in str(exc):
                refused.append(name)
            else:
                wrong.append("%s (said %r)" % (name, str(exc)[:40]))
        except Exception as exc:                                # noqa: BLE001
            wrong.append("%s (raised %s)" % (name, type(exc).__name__))
    check("every broken artifact is refused, and says which way",
          len(refused) == len(cases) and not wrong,
          "%d of %d refused%s" % (len(refused), len(cases),
                                  "" if not wrong else "  %s" % wrong[:2]))

    # A relabelled manifest is the one the digest only catches because it
    # covers the manifest. Kept as its own line because the argument for
    # excluding it was persuasive and wrong.
    check("the digest covers the manifest, not only the rows",
          "a relabelled manifest" in refused,
          "relabelling `structure` to `full` is caught")


def t_rollback(tmp, art):
    """A refused import leaves nothing usable behind."""
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    broken = os.path.join(tmp, "rb.hgx")
    with lzma.open(broken, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines[:-1]) + "\n")

    db = os.path.join(tmp, "rollback.db")
    if os.path.exists(db):
        os.remove(db)
    refused = False
    try:
        with Store(db, model="m4") as s:
            load(broken, {"m4": s}, "/tmp/nowhere")
    except ImportError_:
        refused = True
    with Store(db) as s:
        left = s.node_count()
    check("a refused import commits nothing", refused and left == 0,
          "refused=%s, %d node(s) written" % (refused, left))


def t_through_the_cli(tmp, db_a, root_a):
    """The path a user walks. A gate that calls the helper directly passes
    with the flag disconnected from argparse -- that has happened here."""
    art = os.path.join(tmp, "cli.hgx")
    env = dict(os.environ, PYTHONPATH=ROOTDIR)
    out = os.path.join(tmp, "cli-m4.db")

    def run(*args):
        return subprocess.run([sys.executable, "-m", "homegraph.cli", *args],
                              capture_output=True, text=True, cwd=ROOTDIR,
                              env=env)

    r = run("export", "--model", "m4=%s" % db_a, "--out", art,
            "--root", root_a)
    check("export runs from the command line", r.returncode == 0
          and os.path.exists(art),
          "exit %d%s" % (r.returncode, ("  " + r.stderr.strip()[:60])
                         if r.returncode else ""))

    r = run("inspect", art)
    check("inspect prints the manifest without importing",
          r.returncode == 0 and "redaction" in r.stdout
          and "structure" in r.stdout,
          "exit %d" % r.returncode)

    r = run("import", art, "--model", "m4=%s" % out, "--root",
            os.path.join(tmp, "cli-root"))
    check("import runs from the command line and writes a store",
          r.returncode == 0 and os.path.exists(out),
          "exit %d%s" % (r.returncode, ("  " + r.stderr.strip()[:60])
                         if r.returncode else ""))

    # Importing again over a store that already holds nodes must refuse.
    r = run("import", art, "--model", "m4=%s" % out, "--root",
            os.path.join(tmp, "cli-root"))
    check("importing over an existing store is refused without --force",
          r.returncode == 2 and "force" in r.stderr,
          "exit %d  %s" % (r.returncode, r.stderr.strip()[:50]))

    # And a refusal leaves no database behind for a fresh destination.
    trunc = os.path.join(tmp, "cli-trunc.hgx")
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    with lzma.open(trunc, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines[:-1]) + "\n")
    gone = os.path.join(tmp, "cli-gone.db")
    r = run("import", trunc, "--model", "m4=%s" % gone, "--root",
            os.path.join(tmp, "cli-root"))
    check("a refused import leaves no store looking built",
          r.returncode == 2 and not os.path.exists(gone),
          "exit %d, file left: %s" % (r.returncode, os.path.exists(gone)))


def t_fts(tmp, db_a, root_a):
    """The index is derived: absent from the artifact, rebuilt on import."""
    from homegraph.search import fts_search

    art = os.path.join(tmp, "fts.hgx")
    export({"m4": db_a}, art, root_a, redaction="full")
    with lzma.open(art, "rt", encoding="utf-8") as fh:
        shipped = sum(1 for line in fh if "nodes_fts" in line)
    imported = os.path.join(tmp, "fts.db")
    with Store(imported, model="m4") as s:
        safe_load(art, {"m4": s}, root_a)
    with Store(imported) as s:
        hits = fts_search(s, "bundle", hidden_subtypes=())
    check("the artifact ships no index, and the import rebuilds one",
          shipped == 0 and len(hits) > 0,
          "%d index row(s) shipped, %d hit(s) after import"
          % (shipped, len(hits)))


def main():
    from tests.fixtures import synthetic as syn

    tmp = tempfile.mkdtemp(prefix="cp12-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        root_a = os.path.join(tmp, "korpus-a")
        db_a = build_corpus(root_a, syn)
        print("corpus A: %s\n" % root_a)

        art, report = t_round_trip(tmp, db_a, root_a)
        t_moved_root(tmp, db_a, root_a, syn)
        t_negative_control(tmp, art, root_a, report)
        t_outside_root(tmp, db_a, root_a)
        t_redaction(tmp, db_a, root_a)
        t_shape(tmp, db_a, root_a)
        t_refusals(tmp, art)
        t_rollback(tmp, art)
        t_fts(tmp, db_a, root_a)
        t_through_the_cli(tmp, db_a, root_a)
        print("\nartifact: %d bytes for %d node(s)"
              % (report["bytes"], report["written"]["nodes"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        syn.use_config(syn.CONFIG)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_cp12():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
