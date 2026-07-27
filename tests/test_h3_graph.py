#!/usr/bin/env python3
"""CP-H3 (graph) -- semantic search inside the self-contained graph page.

`visualize --embeddings` inlines a title-word sub-matrix and a DOM-free copy of
the embedder (visualize._EMB_JS) so the page can rank nodes by meaning with no
network and no server. The one thing that can go wrong silently is DRIFT: if the
JavaScript port tokenises or pools differently from `providers.static_embed`, a
query embeds to the wrong place and the ranking is quietly wrong. So this gate
runs the SAME ported JS under node and compares it, number for number, to the
Python embedder it claims to mirror:

  * emSplit == split_identifiers, exactly (the tokenizer port);
  * emEmbed over the int8 sub-matrix is within quantisation noise of
    static_embed.embed over the float matrix (cosine > 0.999);
  * a query lands nearer the on-topic title than the off-topic one (the whole
    point survives the round trip).

Skips cleanly if node is not installed. Run:
    python3 tests/test_h3_graph.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import visualize                                  # noqa: E402
from homegraph.models.m3_build import build as m3_build          # noqa: E402
from homegraph.providers import static_embed as se               # noqa: E402
from homegraph.store import Store                                 # noqa: E402

results, check = reporter(56)


# Two topic clusters (dim 0 vs dim 1 dominant), with small multi-component
# vectors on purpose: fractional magnitudes make the per-row int8 SCALE
# load-bearing, so a quantiser that drops it shifts direction enough to fail the
# cosine gate. Integers (0/1) would survive a broken scale and hide the bug.
_VOCAB = {
    "retrieval": [2.5, 0.4, 0.1, 0.0], "ranking": [2.3, 0.5, 0.0, 0.1],
    "search": [2.6, 0.3, 0.2, 0.0], "index": [2.4, 0.2, 0.0, 0.2],
    "memory": [0.3, 2.4, 0.1, 0.0], "agent": [0.5, 2.5, 0.0, 0.1],
    "session": [0.4, 2.3, 0.2, 0.0], "recall": [0.2, 2.6, 0.0, 0.2],
}
_FILES = {
    "a.md": "# Retrieval Ranking Search\nbody about retrieval\n",
    "b.md": "# Memory Agent Session\nbody about memory\n",
}
_HARNESS = """
const fs = require('fs');
__EMB_JS__
const emb = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const EM = emb ? emBuild(emb) : null;
const tests = JSON.parse(process.argv[3]);
const out = {};
for (const t of tests) {
  const v = EM ? emEmbed(EM, t) : null;
  out[t] = {split: emSplit(t), vec: v ? Array.from(v) : null};
}
process.stdout.write(JSON.stringify(out));
"""


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("SKIP  node not installed; cannot exercise the ported JS")
        return 0

    d = tempfile.mkdtemp(prefix="h3g-", dir=os.path.expanduser("~/.homegraph"))
    try:
        # matrix over the topic vocabulary
        toks = list(_VOCAB)
        matrix = os.path.join(d, "m.json")
        json.dump({"provider": "static", "model": "syn-g", "dim": 4,
                   "tokens": toks, "matrix": [_VOCAB[t] for t in toks]},
                  open(matrix, "w"))
        emb = se.load(matrix)

        # a real m3 store whose titles use the vocabulary
        paths = []
        for name, text in _FILES.items():
            p = os.path.join(d, name)
            open(p, "w").write(text)
            paths.append(p)
        db = os.path.join(d, "m3.db")
        with Store(db, model="m3") as s:
            m3_build(s, sorted(paths), "2026-07-22")

        out = os.path.join(d, "graph.html")
        rep = visualize.render({"m3": db}, out, embeddings=matrix, iterations=5)
        page = open(out).read()

        # payload shape
        payload = json.loads(re.search(r"const D = (\{.*?\});\n", page, re.S)
                             .group(1))
        e = payload.get("emb")
        title_words = set()
        for n in payload["nodes"]:
            title_words.update(se.split_identifiers(n[2]))
        expect = {w for w in title_words if w in _VOCAB}
        check("the page carries an emb sub-matrix", e is not None)
        check("emb covers exactly the on-screen title-words in the vocabulary",
              e is not None and set(e["words"]) == expect,
              "got %r want %r" % (sorted(e["words"]) if e else None, sorted(expect)))
        check("the report counts the embedded words", rep.get("embwords") == len(expect),
              "%r" % rep.get("embwords"))
        check("the semantic toggle is in the page", 'id="semtoggle"' in page)

        # quantisation round-trip, in Python: dequantise emb and match the matrix
        q = __import__("base64").b64decode(e["q"])
        scales = struct.unpack("<%df" % len(e["words"]),
                               __import__("base64").b64decode(e["s"]))
        worst = 0.0
        for j, w in enumerate(e["words"]):
            deq = [(_b if _b < 128 else _b - 256) * scales[j]
                   for _b in q[j * 4:(j + 1) * 4]]
            worst = max(worst, max(abs(a - b) for a, b in zip(deq, _VOCAB[w])))
        # < 0.02: an 8-bit per-row scale on a peak-2.6 vector has a quantum of
        # ~0.02, so half that is the honest bound; tighter would be luck.
        check("int8 dequantisation matches the matrix (< 0.02)", worst < 0.02,
              "worst abs err %.5f" % worst)

        # run the ported JS under node
        harness = os.path.join(d, "h.js")
        open(harness, "w").write(_HARNESS.replace("__EMB_JS__", visualize._EMB_JS))
        embfile = os.path.join(d, "emb.json")
        json.dump(e, open(embfile, "w"))
        probes = ["Retrieval Ranking Search", "Memory Agent Session",
                  "getUserById HTMLParser", "retrieval index", "recall session"]
        proc = subprocess.run([node, harness, embfile, json.dumps(probes)],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            check("node runs the ported embedder", False, proc.stderr.strip()[:200])
            return _finish()
        js = json.loads(proc.stdout)

        # tokenizer port == Python, exactly
        tok_ok = all(js[p]["split"] == se.split_identifiers(p) for p in probes)
        check("emSplit matches split_identifiers on every probe (tokenizer port)",
              tok_ok,
              next(("%r: js=%r py=%r" % (p, js[p]["split"], se.split_identifiers(p))
                    for p in probes if js[p]["split"] != se.split_identifiers(p)), ""))

        # The vectors must be unit length -- otherwise the dot products the
        # ranking relies on are not cosines. Checked directly, because a raw dot
        # against a unit Python vector would happily accept an un-normalised one
        # (it just scores higher), which is how a dropped normalisation hides.
        norms = [sum(x * x for x in js[p]["vec"]) ** 0.5
                 for p in probes if js[p]["vec"]]
        check("emEmbed returns L2-normalised (unit) vectors",
              all(abs(nm - 1.0) < 1e-3 for nm in norms),
              "norms %s" % [round(nm, 4) for nm in norms])

        # emEmbed(int8) within quantisation noise of static_embed.embed(float)
        worst_cos = 1.0
        for p in ("Retrieval Ranking Search", "Memory Agent Session",
                  "retrieval index"):
            jsv = js[p]["vec"]
            pyv = emb.embed(p)
            worst_cos = min(worst_cos, _cos(jsv, pyv))
        # > 0.997: honest 8-bit noise on pooled, normalised vectors lands ~0.998;
        # a quantiser that drops the per-row scale drags it to ~0.986, so this
        # threshold passes faithful quantisation and fails a broken one.
        check("emEmbed matches static_embed.embed within quantisation (cos>0.997)",
              worst_cos > 0.997, "worst cosine %.6f" % worst_cos)

        # the ranking survives: a topic-A query is nearer the topic-A title
        qv = js["retrieval index"]["vec"]
        a_title = js["Retrieval Ranking Search"]["vec"]
        b_title = js["Memory Agent Session"]["vec"]
        check("a topic-A query ranks the topic-A title above the topic-B one",
              _cos(qv, a_title) > _cos(qv, b_title),
              "A=%.3f B=%.3f" % (_cos(qv, a_title), _cos(qv, b_title)))
        return _finish()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _finish() -> int:
    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h3_graph():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
