#!/usr/bin/env python3
"""CP-H3-PARA -- the labelled paraphrase set, and what it is allowed to claim.

H1 built the instrument and proved it could register a lexical->semantic gap,
but the headroom it proved it on was **two handmade pairs on a mini fixture**.
`docs/harvest-plan.md` says so in as many words: a full semantic eval needs more
handmade pairs on the real corpus, and there was nothing to beat until H3
landed. This is that set -- 30 pairs over the real markdown corpus.

The one number this file exists to make trustworthy is the zero. FTS scores
0.000 on all thirty paraphrases, and a zero is worthless on its own: it reads
identically whether the queries are hard or the search is broken. So K3 asks the
same corpus, through the same search function, with a phrase lifted verbatim out
of each document. If THAT is not found, the zero says nothing and this file goes
red before anyone quotes it.

Two things deliberately not claimed:

* **Nothing here decides whether embeddings become default.** The measurement
  says what the gap is. Moving a default is the owner's decision, and this
  not touch a threshold.
* **The labeller is not independent of the system.** I wrote the paraphrases.
  The guard covers lexical leakage mechanically; it cannot cover that I might
  phrase things a vector model happens to like. Written down rather than
  argued away.

Requires the real corpus and `tests/gold/paraphrase-pairs.json`, which is
git-ignored like the other real-corpus keys. In a fresh clone this fails and
says what is missing, which is the same bargain `test_no_real_paths` makes.

Run:
    python3 tests/test_h3_para.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.corpus import Classifier                       # noqa: E402
from homegraph.models.m3_build import build as m3_build       # noqa: E402
from homegraph.models.m3_build import rules_from_config       # noqa: E402
from homegraph.store import Store                             # noqa: E402
from tests.eval.build_eval import AS_OF, fts_search_fn        # noqa: E402
from tests.eval.paraphrase import (DEFAULT_PATH, content_words,  # noqa: E402
                                   guard, leakage, load, stem_leakage)
from tests.eval.scoreboard import evaluate                    # noqa: E402

WANT_PAIRS = 28
results, check = reporter(60)


def markdown_paths():
    """Every classified markdown file under the real home corpus.

    The root is pinned rather than read from `home_root()`. The pairs are
    labelled against the real corpus by construction, and an ambient root is
    whatever the previously-run test left behind: measured 2026-07-31, this file
    passed alone and died with `KeyError: no node for ...` inside the full suite,
    because another checkpoint had moved `HOMEGRAPH_ROOT` and the walk found a
    different tree. A gate whose answer depends on test order is not a gate.
    """
    clf = Classifier()
    paths = []
    for dirpath, dirnames, filenames in os.walk(os.path.expanduser("~"),
                                                followlinks=False):
        dirnames[:] = [d for d in dirnames
                       if clf.explain(os.path.join(dirpath, d)).label != "excluded"]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if clf.explain(p).label == "markdown" and os.path.exists(p):
                paths.append(p)
    return sorted(paths)


def verbatim_query(body):
    """Six consecutive long words lifted straight out of the document.

    The point is that this CANNOT be hard: it is the document's own text. If the
    search cannot find a file from its own words, the paraphrase zero measures
    the plumbing rather than the language.
    """
    words = [w for w in re.findall(r"[^\W_]+", body or "", re.UNICODE) if len(w) > 4]
    return " ".join(words[10:16]) if len(words) >= 16 else None


def main():
    if not os.path.exists(DEFAULT_PATH):
        print("FAILED\tthe paraphrase set is present")
        print("missing %s -- the labelled set is git-ignored real-corpus "
              "material and is not in a fresh clone" % DEFAULT_PATH)
        return 1

    clf = Classifier()
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "m3.db")
        # Built here rather than read from ~/.homegraph/stores: that store was
        # six days old when this was written, and a stale store makes every
        # number below a statement about a corpus that has moved.
        with Store(db, model="m3") as s:
            m3_build(s, markdown_paths(), AS_OF, rules=rules_from_config(clf.config))
            s.rebuild_fts()

        with Store(db) as s:
            pairs = load(store=s)
            fn = fts_search_fn(s, limit=10)

            leaks = guard(pairs)
            check("K1: ingen spørring deler innholdsord med dokumentet sitt",
                  not leaks, "" if not leaks else "lekkasjer: %s" % leaks[:3])

            check("K2: settet er %d par, og hver sti finnes i lageret" % WANT_PAIRS,
                  len(pairs) == WANT_PAIRS, "fikk %d par" % len(pairs))

            # K3 -- the control that makes the zero mean something.
            lex = [(q, p["node_id"]) for p in pairs
                   if (q := verbatim_query(p["body"]))]
            lexical = evaluate(lex, fn, ks=(1, 5, 10))
            check("K3: ordrett frase fra dokumentet finnes (instrumentet virker her)",
                  lexical.recall[10] == 1.0 and len(lex) == WANT_PAIRS,
                  lexical.line())

            para = [(p["query"], p["node_id"]) for p in pairs]
            baseline = evaluate(para, fn, ks=(1, 5, 10))
            # K4 gates the CLAIM, not a direction. The first version asked only
            # for "below the lexical control", which 0.967 satisfies while the
            # checkpoint reports 0.000 -- the headline number would have lived in
            # prose with no gate under it. Raised by codex, 2026-07-31.
            check("K4: FTS finner INGEN av parafrasene (r@10 og MRR er 0)",
                  baseline.recall[10] == 0.0 and baseline.mrr == 0.0
                  and lexical.recall[10] == 1.0,
                  "parafrase %s · leksikalsk r@10=%.3f"
                  % (baseline.line(), lexical.recall[10]))

            # K5 -- the guard has to be able to say no, or K1 is decoration.
            stolen = " ".join(sorted(content_words(pairs[0]["body"] or ""))[:4])
            check("K5: vakten flagger en spørring løftet ut av dokumentet",
                  bool(leakage(stolen, pairs[0]["body"] or "")),
                  "" if leakage(stolen, pairs[0]["body"] or "")
                  else "vakten så ingen lekkasje i ordrett tyvlånt tekst")

            # K6 -- the scoreboard's NAMED refusal. Accepting any exception is
            # not enough: with the guard deleted, `evaluate([])` still dies, on
            # ZeroDivisionError from the mean, and a check that catches both
            # cannot tell a deliberate refusal from an accidental crash. Measured
            # 2026-07-31 -- the first version accepted either, and the mutation
            # that removes the guard SURVIVED it.
            try:
                evaluate([], fn, ks=(10,))
                refusal = "ingen feil i det hele tatt"
            except ValueError as exc:
                refusal = "" if "empty eval" in str(exc) else "ValueError, men ikke den navngitte: %s" % exc
            except Exception as exc:                          # noqa: BLE001
                refusal = "%s -- et krasj, ikke en nektelse" % type(exc).__name__
            check("K6: et tomt sett nektes ved navn, ikke ved krasj",
                  refusal == "", refusal)

            # K7 -- a pair whose document is gone must stop the run. Probed with
            # a path that cannot exist, because on a healthy corpus the branch
            # is never reached and the mutation that guts it SURVIVED (measured
            # 2026-07-31). A guard on a path nothing takes is not verified.
            import json as _json
            import tempfile as _tf
            with _tf.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8") as fh:
                # One good pair beside the bogus one, so dropping the bogus one
                # leaves a NON-empty set. With only the bogus pair, the empty-set
                # guard fires first and this check never gets to speak -- measured
                # 2026-07-31, the mutation came back CRASH-ONLY.
                _json.dump({"pairs": [
                    {"query": pairs[0]["query"], "path": pairs[0]["path"]},
                    {"query": "x", "path": "/nonexistent/gone.md"}]}, fh)
                probe = fh.name
            try:
                load(path=probe, store=s)
                gone = "lastet et par uten dokument i stedet for å nekte"
            except KeyError:
                gone = ""
            finally:
                os.unlink(probe)
            check("K7: et par uten dokument stopper kjøringen", gone == "", gone)

            # K8 -- "no shared words" does not make a query a paraphrase: an
            # empty or irrelevant one passes the guard too, and thirty copies of
            # one pair would pass as thirty pairs. Structure is checkable even
            # though relevance is not. (codex, 2026-07-31.)
            thin = [p["path"] for p in pairs if len(content_words(p["query"])) < 4]
            dup_q = len({p["query"] for p in pairs}) != len(pairs)
            dup_t = len({p["node_id"] for p in pairs}) != len(pairs)
            check("K8: hver spørring er egen, mot sitt eget dokument, og ikke tynn",
                  not thin and not dup_q and not dup_t,
                  "tynne %s · like spørringer %s · like mål %s"
                  % (thin[:2], dup_q, dup_t))

            # K9 -- the exact-token guard cannot see `katt` in `katter`, and in
            # Norwegian that is the common case. Reported separately because it
            # is the weaker signal, and gated because two pairs failed it when
            # it was first run -- both were dropped rather than reworded.
            stems = [(p["path"], stem_leakage(p["query"], p["body"] or ""))
                     for p in pairs if stem_leakage(p["query"], p["body"] or "")]
            # The probe needs a positive control for the same reason the strict
            # guard does: switched off, it reports "clean" about everything and
            # its own gate goes green. Measured -- the mutation that disables it
            # SURVIVED until this line existed.
            # Both words must clear `floor` (5), or the probe skips them and the
            # control reports a failure that is only about its own example.
            probe_works = bool(stem_leakage("kunnskapsgrafen",
                                            "en kunnskapsgraf står her"))
            check("K9: ingen spørring deler bøyningsstamme, og proben finner en",
                  not stems and probe_works,
                  ("stammer: %s" % stems[:2]) if stems
                  else ("" if probe_works else "proben så ikke stammen i et bøyd ord"))

            print("\nMÅLT  leksikalsk : %s" % lexical.line())
            print("MÅLT  parafrase  : %s" % baseline.line())

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_h3_para():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
