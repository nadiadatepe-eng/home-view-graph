#!/usr/bin/env python3
"""CP-I4 -- the Graphify export, checked against Graphify rather than against us.

The integration plan made phase 3 conditional: "read Graphify's actual JSON
schema from the repo. The format is not guessed." That was done -- version
0.9.16's `validate.py` and `cli.py`, plus a real 974-node `graph.json` -- and
this checkpoint is where the reading gets tested rather than trusted.

**The strongest gates here do not run our code at all.** They hand our output
to Graphify's own `validate_extraction` and to the `networkx` in Graphify's
environment, and ask those two whether the file is what we say it is. A
round-trip through a parser we wrote would only prove we are self-consistent,
which is the failure mode the whole "read the schema" rule exists to avoid.

When Graphify is not installed, those gates say so and **fail** rather than
passing quietly. A source that was never asked has to say so -- the rule
`mesh search` already follows for an unbuilt code inventory.

Run:
    python3 tests/test_i4.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import graphify                                      # noqa: E402
from homegraph.export import ExportError                            # noqa: E402
from homegraph.store import EDGE_METHODS, Store                     # noqa: E402

results, check = reporter(62)


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="i4-", dir=os.path.expanduser("~/.homegraph"))


_SECRET = "quokka-heliotrope-vestibule"


def _store(d: str, *, unknown_kind: bool = False) -> str:
    """A store covering every file_type the mapping can emit."""
    db = os.path.join(d, "m3.db")
    root = os.path.join(d, "root")
    os.makedirs(root, exist_ok=True)
    p = lambda n: os.path.join(root, n)                        # noqa: E731
    with Store(db, model="m3") as s:
        s.begin_immediate()
        s.upsert_node(p("plan.md"), "file", subtype="note", path=p("plan.md"),
                      title="Plan", body=_SECRET, as_of="2026-07-25")
        s.upsert_node(p("arkiv.md"), "file", subtype="note", path=p("arkiv.md"),
                      title="Arkiv", as_of="2026-07-25")
        s.upsert_node(p("paper.pdf"), "document", subtype="pdf",
                      path=p("paper.pdf"), title="A Paper", as_of="2026-07-25")
        s.upsert_node(p("shot.png"), "image", subtype="image",
                      path=p("shot.png"), as_of="2026-07-25")
        s.upsert_node(p("mod.py"), "code", subtype="python", path=p("mod.py"),
                      as_of="2026-07-25")
        # Node kinds that are not files at all: no `path`, so `source_file`
        # has to come out as the empty string Graphify's own data uses.
        s.upsert_node("author:Ada Lovelace", "author", subtype="author",
                      title="Ada Lovelace", as_of="2026-07-25")
        s.upsert_node("ref:doi:10.1234/abcd", "reference", subtype="doi",
                      as_of="2026-07-25")
        if unknown_kind:
            s.upsert_node(p("odd.bin"), "quokka", subtype="unmapped",
                          as_of="2026-07-25")
        # Two relations between the SAME pair. On the real corpus 23 pairs in
        # m3 look like this, and a simple graph loses one of each.
        s.upsert_edge(p("plan.md"), p("arkiv.md"), "WIKILINKS_TO",
                      "2026-07-25", method="exact")
        s.upsert_edge(p("plan.md"), p("arkiv.md"), "LINKS_TO",
                      "2026-07-25", method="mention")
        s.upsert_edge(p("paper.pdf"), "author:Ada Lovelace", "AUTHORED_BY",
                      "2026-07-25", method="exact")
        s.upsert_edge(p("plan.md"), p("shot.png"), "LIKELY_COPY",
                      "2026-07-25", method="basename")
    return db


def _export(db: str, out: str, root: str, **kw):
    try:
        return graphify.export_graph({"m3": db}, out, root, **kw), ""
    except Exception as exc:      # noqa: BLE001 -- reported, not swallowed
        return None, "%s: %s" % (type(exc).__name__, exc)


def _graphify_python() -> str | None:
    """The interpreter Graphify is installed into, or None.

    Discovered from the `graphify` entry point's shebang rather than from a
    hardcoded path -- a checkpoint that names one machine's directory layout
    is not a checkpoint anyone else can run, and `test_no_real_paths.py`
    would refuse it anyway.
    """
    exe = shutil.which("graphify")
    if not exe:
        return None
    try:
        with open(exe, encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return None
    if first.startswith("#!"):
        candidate = first[2:].strip().split()[0]
        if os.path.exists(candidate):
            return candidate
    return None


def _ask_graphify(script: str, path: str) -> tuple[dict | None, str]:
    """Run `script` in Graphify's own environment. Returns (parsed, error)."""
    python = _graphify_python()
    if python is None:
        return None, "graphify is not installed; this gate cannot run"
    proc = subprocess.run([python, "-c", script, path],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return None, (proc.stderr.strip().splitlines() or ["failed"])[-1]
    try:
        return json.loads(proc.stdout), ""
    except ValueError:
        return None, "unparseable answer: %r" % proc.stdout[:120]


_VALIDATE = """
import json, sys
from graphify.validate import validate_extraction, VALID_FILE_TYPES, VALID_CONFIDENCES
data = json.load(open(sys.argv[1], encoding='utf-8'))
print(json.dumps({"errors": validate_extraction(data)[:5],
                  "n_errors": len(validate_extraction(data)),
                  "file_types": sorted(VALID_FILE_TYPES),
                  "confidences": sorted(VALID_CONFIDENCES)}))
"""

_LOAD = """
import json, sys
from networkx.readwrite import json_graph as jg
data = json.load(open(sys.argv[1], encoding='utf-8'))
G = jg.node_link_graph(data, edges='links')
print(json.dumps({"type": type(G).__name__,
                  "nodes": G.number_of_nodes(),
                  "edges": G.number_of_edges()}))
"""


# -- gates ------------------------------------------------------------------


def t_the_mapping_cannot_drift_from_the_store():
    """A sixth edge method must not default its way into the export."""
    try:
        graphify.check_tables()
        ok, detail = True, ""
    except ExportError as exc:
        ok, detail = False, str(exc)
    check("every edge method the store defines has a Graphify confidence",
          ok, detail)
    check("and the mapping covers exactly those methods, no more",
          set(graphify.METHOD_TO_CONFIDENCE) == set(EDGE_METHODS),
          "store %s, mapping %s" % (sorted(EDGE_METHODS),
                                    sorted(graphify.METHOD_TO_CONFIDENCE)))
    check("every file_type the mapping emits is in Graphify's closed set",
          set(graphify.KIND_TO_FILE_TYPE.values())
          <= set(graphify.VALID_FILE_TYPES),
          "emits %s" % sorted(set(graphify.KIND_TO_FILE_TYPE.values())))


def t_graphify_itself_accepts_the_file():
    d = _tmp()
    try:
        db = _store(d)
        out = os.path.join(d, "graph.json")
        _, err = _export(db, out, os.path.join(d, "root"))
        answer, why = _ask_graphify(_VALIDATE, out)
        check("Graphify's own validate_extraction reports no errors",
              not err and answer is not None and answer["n_errors"] == 0,
              err or why or str(answer["errors"]))
        # The two closed sets are copied into `graphify.py` because Graphify
        # is not a dependency. Copies rot; this compares ours to the
        # installed original every run.
        check("and its closed vocabularies still match the copies here",
              answer is not None
              and answer["file_types"] == sorted(graphify.VALID_FILE_TYPES)
              and answer["confidences"] == sorted(graphify.VALID_CONFIDENCES),
              why or (answer and "theirs %s / %s" % (answer["file_types"],
                                                     answer["confidences"])))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_round_trip_keeps_every_edge():
    d = _tmp()
    try:
        db = _store(d)
        out = os.path.join(d, "graph.json")
        rep, err = _export(db, out, os.path.join(d, "root"))
        answer, why = _ask_graphify(_LOAD, out)
        check("Graphify's networkx loads the file as a multigraph",
              answer is not None and answer["type"] == "MultiDiGraph",
              why or (answer and answer["type"]))
        check("and the round trip loses no node",
              rep is not None and answer is not None
              and answer["nodes"] == rep["nodes"],
              err or why or "wrote %s, loaded %s"
              % (rep and rep["nodes"], answer and answer["nodes"]))
        # The one that `multigraph: false` would break, and break silently.
        check("and loses no edge, including the two on one node pair",
              rep is not None and answer is not None
              and answer["edges"] == rep["links"] == 4,
              err or why or "wrote %s, loaded %s"
              % (rep and rep["links"], answer and answer["edges"]))
        check("the report counts the parallel edge rather than hiding it",
              rep is not None and rep["parallel_edges_kept"] == 1,
              err or "%s" % (rep and rep["parallel_edges_kept"]))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_an_unmapped_kind_is_refused_not_defaulted():
    d = _tmp()
    try:
        db = _store(d, unknown_kind=True)
        out = os.path.join(d, "graph.json")
        rep, err = _export(db, out, os.path.join(d, "root"))
        check("a kind with no Graphify file_type stops the export",
              rep is None and "quokka" in err, err)
        check("and the refusal names the closed set it must fit into",
              "concept" in err and "rationale" in err, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_enum_never_invents_certainty():
    d = _tmp()
    try:
        db = _store(d)
        out = os.path.join(d, "graph.json")
        _export(db, out, os.path.join(d, "root"))
        data = json.load(open(out, encoding="utf-8"))
        bad = [e for e in data["links"]
               if e["confidence"] not in graphify.VALID_CONFIDENCES]
        check("every edge carries one of the three allowed confidences",
              not bad, "%d bad" % len(bad))
        # `exact` is the only method that means "the data says so". A mapping
        # that promoted a guess to EXTRACTED would be the export stating
        # something the corpus never claimed.
        promoted = [e for e in data["links"]
                    if e["confidence"] == "EXTRACTED" and e["method"] != "exact"]
        check("and no method below 1.0 is promoted to EXTRACTED",
              not promoted, "%s" % [e["method"] for e in promoted][:3])
        scores = {e["method"]: e["confidence_score"] for e in data["links"]}
        check("the numeric confidence survives beside the enum",
              all(scores[m] == EDGE_METHODS[m] for m in scores),
              "%s" % scores)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_redaction_is_the_one_export_owns():
    d = _tmp()
    try:
        db = _store(d)
        root = os.path.join(d, "root")
        out = os.path.join(d, "graph.json")
        _export(db, out, root)
        text = open(out, encoding="utf-8").read()
        check("structure carries no file text into the graph",
              _SECRET not in text)
        out2 = os.path.join(d, "full.json")
        _export(db, out2, root, redaction="full")
        check("full does, so the check above discriminates",
              _SECRET in open(out2, encoding="utf-8").read())
        out3 = os.path.join(d, "shape.json")
        rep3, err3 = _export(db, out3, root, redaction="shape")
        shaped = json.load(open(out3, encoding="utf-8"))
        check("shape leaves no readable id or label",
              rep3 is not None
              and not any("plan.md" in str(n["id"]) for n in shaped["nodes"])
              and not any(n["label"] == "Arkiv" for n in shaped["nodes"]),
              err3)
        check("and its edges still point at nodes that are in the file",
              bool(shaped["links"])
              and {e["source"] for e in shaped["links"]}
              <= {n["id"] for n in shaped["nodes"]})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_node_that_is_not_a_file_still_validates():
    d = _tmp()
    try:
        db = _store(d)
        out = os.path.join(d, "graph.json")
        _export(db, out, os.path.join(d, "root"))
        data = json.load(open(out, encoding="utf-8"))
        author = next((n for n in data["nodes"] if n.get("kind") == "author"),
                      None)
        check("an author node is a concept, not a document",
              author is not None and author["file_type"] == "concept",
              str(author and author["file_type"]))
        check("and carries source_file as the empty string, not a missing key",
              author is not None and author.get("source_file") == "",
              repr(author and author.get("source_file")))
        ref = next((n for n in data["nodes"] if n.get("kind") == "reference"),
                   None)
        check("a bibliographic reference maps to paper",
              ref is not None and ref["file_type"] == "paper",
              str(ref and ref["file_type"]))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_missing_store_is_named():
    d = _tmp()
    try:
        rep, err = _export(os.path.join(d, "nope.db"),
                           os.path.join(d, "g.json"), d)
        check("a missing store is refused by name, not by traceback",
              rep is None and "m3" in err, err)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_export_does_not_touch_the_store():
    d = _tmp()
    try:
        db = _store(d)
        before = hashlib.sha256(open(db, "rb").read()).hexdigest()
        _export(db, os.path.join(d, "g.json"), os.path.join(d, "root"))
        check("exporting leaves the store byte-identical",
              hashlib.sha256(open(db, "rb").read()).hexdigest() == before)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    for fn in (t_the_mapping_cannot_drift_from_the_store,
               t_graphify_itself_accepts_the_file,
               t_the_round_trip_keeps_every_edge,
               t_an_unmapped_kind_is_refused_not_defaulted,
               t_the_enum_never_invents_certainty,
               t_redaction_is_the_one_export_owns,
               t_a_node_that_is_not_a_file_still_validates,
               t_a_missing_store_is_named,
               t_the_export_does_not_touch_the_store):
        fn()
    bad = [r for r in results if not r[1]]
    print("\nCP-I4: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_checkpoint_i4():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
