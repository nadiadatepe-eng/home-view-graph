#!/usr/bin/env python3
"""Score homegraph.corpus.classify() against the CP-0 gold set.

Exists before classify() does, on purpose. Right now it fails with a clear
message; that failure is the correct output, and the first thing to check once
corpus.py lands is that this file starts passing for the right reason.

CP-0 threshold: >= 95% (at most 3 misses out of 60). The hard-case accuracy is
reported separately and is not part of the gate -- it is there so a run that
scrapes past 95% by acing the easy 36 and failing the adversarial cases is
visible rather than green.

Never opens a gold-set file. classify() takes a path, and paths are all this
hands it.

Usage:
    python3 score_gold.py [gold-set.tsv]
Exit: 0 pass, 1 below threshold, 2 classify() unavailable.
"""
import collections
import os
import sys

THRESHOLD = 0.95
VALID = {"markdown", "document", "image", "code", "misc", "EXCLUDED"}


def load_gold(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5 or parts[0] == "label":
                continue
            label, subtype, hard, fpath, why = parts[:5]
            if label not in VALID:
                raise SystemExit("gold set has unknown label %r for %s"
                                 % (label, fpath))
            rows.append({"label": label, "subtype": subtype,
                         "hard": hard == "y", "path": fpath, "why": why})
    return rows


def main():
    gold_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "gold-set.tsv")
    gold = load_gold(gold_path)

    try:
        from homegraph.corpus import classify
    except ImportError as exc:
        print("cannot score: homegraph.corpus.classify() is not importable")
        print("  (%s)" % exc)
        print("\nThis is the expected result until TODO-0 is implemented.")
        print("%d gold files are loaded and waiting." % len(gold))
        return 2

    misses, by_class = [], collections.Counter()
    hard_total = hard_hit = 0

    for row in gold:
        got = classify(row["path"])
        got = getattr(got, "name", got)  # tolerate an enum
        ok = got == row["label"]
        by_class[(row["label"], "hit" if ok else "miss")] += 1
        if row["hard"]:
            hard_total += 1
            hard_hit += ok
        if not ok:
            misses.append((row, got))

    total = len(gold)
    hits = total - len(misses)
    acc = hits / total

    print("gold set: %s" % gold_path)
    print("accuracy: %d/%d = %.1f%%  (threshold %.0f%%)"
          % (hits, total, acc * 100, THRESHOLD * 100))
    if hard_total:
        print("hard cases: %d/%d = %.1f%%  (reported, not gated)"
              % (hard_hit, hard_total, hard_hit / hard_total * 100))

    print("\nper class:")
    for label in sorted(VALID):
        hit = by_class[(label, "hit")]
        miss = by_class[(label, "miss")]
        if hit + miss:
            print("  %-9s %2d/%2d" % (label, hit, hit + miss))

    if misses:
        print("\nmisses:")
        for row, got in misses:
            print("  expected %-9s got %-9s %s%s"
                  % (row["label"], got, row["path"],
                     "   [HARD]" if row["hard"] else ""))
            print("      why: %s" % row["why"])

    if acc < THRESHOLD:
        print("\nFAIL: below %.0f%%. Fix the rules -- do not edit gold-set.tsv."
              % (THRESHOLD * 100))
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
