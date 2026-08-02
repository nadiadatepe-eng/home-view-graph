#!/usr/bin/env python3
"""Which compound conditions did this change add that no mutation aims at?

`mutation_coverage.py` asks the question from the gate's side: which *checks*
has nobody written a mutation against. This asks it from the code's side, and
about one shape in particular.

**The shape.** Three checkpoints in a row ended with a reviewer finding the
same defect, and it was never in the implementation:

    H6  every tied pair had one member at degree 0, so "degree as a boolean"
        and "degree as a magnitude" produced identical output
    H6  one side of a max-across-models comparison was always absent, so the
        max and the sum were the same number
    H7  every drifted file changed size AND mtime, so
        `size == stored and abs(mtime - stored) <= tol` had no input that
        could tell its two operands apart
    H7  every result set held both a stale and a missing hit, so
        `staleness in AFFECTED` behaved identically with either member removed

Each is a **compound condition** -- an `and`/`or`, or a membership test over a
set -- whose operands the fixture never varied independently. Each was found by
someone writing the mutation by hand, twice by an audit and once by codex.
Writing the mutation is the part that gets skipped, so this reports where it
was skipped, while the change is still in front of you.

**Deliberately not a coverage percentage.** Over the whole package there are
401 compound conditions and 301 of them no mutation names, which is a number
nobody can act on. Scoped to what a commit touched it is a handful, and a
handful is a to-do list. Same reason `mutation_coverage.py` calls itself a map.

**What it cannot see.** A condition whose operands are varied independently by
the fixture but wrongly; a defect that is not a compound condition at all (the
H6 sort-key ordering was neither); and any mutation written but not yet run.
It reports where nobody aimed, not where the code is wrong.

Run:
    python3 tests/condition_coverage.py              # HEAD, plus uncommitted
    python3 tests/condition_coverage.py --since HEAD~3
    python3 tests/condition_coverage.py --all        # the whole package
"""
from __future__ import annotations

import argparse
import ast
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "homegraph")


def compound_conditions(path):
    """(line number, source line) for every `and`/`or` and membership test.

    A plain comparison is not included. `a == b` has one operand pair and one
    way to be wrong; the fixture hole this hunts needs two operands that can
    be exercised independently, and that is what `and`, `or` and `in` have.
    """
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = {}
    for node in ast.walk(tree):
        compound = (isinstance(node, ast.BoolOp) and len(node.values) >= 2) or (
            isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops))
        if not compound or not isinstance(node, (ast.BoolOp, ast.Compare)):
            continue
        text = lines[node.lineno - 1].strip()
        if text:
            out[node.lineno] = text
    return sorted(out.items())


def changed_lines(since):
    """{path: {line numbers}} touched by the diff, committed or not."""
    out = {}
    diff = subprocess.run(
        ["git", "-C", ROOT, "diff", "-U0", since, "--", "homegraph"],
        capture_output=True, text=True, check=False).stdout
    unstaged = subprocess.run(
        ["git", "-C", ROOT, "diff", "-U0", "--", "homegraph"],
        capture_output=True, text=True, check=False).stdout
    path = None
    for line in (diff + "\n" + unstaged).splitlines():
        if line.startswith("+++ b/"):
            path = os.path.join(ROOT, line[6:])
        elif line.startswith("@@") and path:
            # @@ -old,n +new,m @@
            span = line.split("+", 1)[1].split(" ", 1)[0]
            start, _, count = span.partition(",")
            count = int(count) if count else 1
            out.setdefault(path, set()).update(
                range(int(start), int(start) + max(count, 1)))
    return out


def aimed_at():
    """Every needle text any mutation harness carries, as one blob.

    A condition counts as aimed at when its source line appears inside some
    harness -- the same literal-match rule the harnesses themselves use to
    find what to replace. Strict on purpose: a mutation that happens to run
    through a line is not evidence that anyone chose to attack it.
    """
    blob = []
    for path in sorted(glob.glob(os.path.join(HERE, "mutate_*.py"))):
        with open(path, encoding="utf-8") as fh:
            blob.append(fh.read())
    return "\n".join(blob)


WAIVER = "condition-coverage:"


def waived(path, lineno):
    """A line may say why no mutation can be aimed at it.

    `# condition-coverage: <reason>` on the line itself or within two lines
    above it. Two, not one, because a condition inside a comprehension has the
    comprehension's opening line between it and any comment. A longer
    explanation goes above the marker; the marker is the last comment line, so
    it sits as close to the code as the syntax allows. A
    comment rather than a list in this file, because a waiver that lives away
    from the code it excuses outlives the code -- the same reason the mutation
    needles are literal source text rather than line numbers.

    The reason is not parsed. It is there for the person reading the diff, and
    an empty one is refused so that the marker cannot become a silent opt-out.
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for n in (lineno - 1, lineno - 2, lineno - 3):
        if 0 <= n < len(lines) and WAIVER in lines[n]:
            reason = lines[n].split(WAIVER, 1)[1].strip()
            if reason:
                return reason
    return None


def report(scope, needles, verbose=False):
    total = missed = 0
    for path in sorted(scope):
        wanted = scope[path]
        rows = [(n, t) for n, t in compound_conditions(path)
                if wanted is None or n in wanted]
        if not rows:
            continue
        total += len(rows)
        gaps = []
        for n, text in rows:
            if text in needles:
                continue
            reason = waived(path, n)
            if reason:
                if verbose:
                    print("  %4d  waived: %s" % (n, reason))
                continue
            gaps.append((n, text))
        missed += len(gaps)
        if gaps or verbose:
            print("%s" % os.path.relpath(path, ROOT))
            for n, text in gaps:
                print("  %4d  %s" % (n, text[:88]))
    return total, missed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="HEAD~1",
                        help="revision to diff against (default HEAD~1)")
    parser.add_argument("--all", action="store_true",
                        help="every compound condition in the package")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.all:
        scope = {p: None for p in
                 glob.glob(os.path.join(PACKAGE, "**", "*.py"), recursive=True)}
    else:
        scope = changed_lines(args.since)
        if not scope:
            print("no changed lines under homegraph/ since %s" % args.since)
            return 0

    total, missed = report(scope, aimed_at(), verbose=args.verbose)
    print()
    print("%d compound condition(s) in scope, %d with no mutation aimed at them"
          % (total, missed))
    if missed and not args.all:
        print("Each one is a place a fixture can supply the same value along "
              "both axes\nand nothing will notice. Write the mutation, or say "
              "here why it cannot be wrong.")
    return 1 if missed and not args.all else 0


if __name__ == "__main__":
    sys.exit(main())
