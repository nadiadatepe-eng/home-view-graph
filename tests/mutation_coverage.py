#!/usr/bin/env python3
"""Which checks does no mutation target?

The most useful finding from the adversarial audit was not any single empty
gate. It was where they all lived: among the checks no mutation had ever been
aimed at. A check that cannot fail cannot have a mutation written against it,
so the two defects co-locate by construction -- which makes unmutated area the
first place to look rather than the last.

This reports that area. A check counts as covered when some mutation names it
in its `expected` field, which is the same match the harnesses use to decide
whether a kill was attributed to the right gate. That is deliberately strict:
a check that happens to go red under some unrelated mutation is not evidence
that anyone chose to test it.

Coverage is a map, not a score. 100% would mean every check has one mutation
aimed at it, not that every way the code can be wrong has been tried.

Run:
    python3 tests/mutation_coverage.py [--verbose]
"""
from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def check_names(path):
    """Every literal first argument to check(...) in a checkpoint file.

    Names built by % formatting keep their literal prefix and gain a marker:
    the prefix is what a mutation's `expected` has to match anyway, since the
    formatted number is not known until the run.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "check" and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                out.append(arg.value)
            elif isinstance(arg, ast.BinOp):
                try:
                    out.append(ast.literal_eval(arg.left) + " <fmt>")
                except Exception:                               # noqa: BLE001
                    out.append("<computed>")
            else:
                out.append("<computed>")
    return out


def expected_strings(path):
    """The `expected` element of every 5-tuple in MUTATIONS."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "MUTATIONS" for t in node.targets):
            for elt in getattr(node.value, "elts", []):
                if isinstance(elt, ast.Tuple) and len(elt.elts) == 5:
                    last = elt.elts[4]
                    if isinstance(last, ast.Constant):
                        out.append(last.value)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    verbose = "--verbose" in argv or "-v" in argv

    total = covered_total = 0
    rows = []
    for n in range(9):
        test = os.path.join(HERE, "test_cp%d.py" % n)
        mut = os.path.join(HERE, "mutate_cp%d.py" % n)
        if not os.path.exists(test):
            continue
        checks = check_names(test)
        expected = expected_strings(mut) if os.path.exists(mut) else []
        uncovered = [c for c in checks
                     if not any(e in c or c.startswith(e) for e in expected)]
        total += len(checks)
        covered_total += len(checks) - len(uncovered)
        rows.append(("CP-%d" % n, len(checks), len(uncovered),
                     len(expected), uncovered))

    for name, n_checks, n_unc, n_mut, uncovered in rows:
        print("%-6s %3d checks  %3d covered (%3.0f%%)  %2d mutations"
              % (name, n_checks, n_checks - n_unc,
                 100 * (n_checks - n_unc) / max(n_checks, 1), n_mut))
        if verbose:
            for u in uncovered:
                print("        UNCOVERED  %s" % u)
    print("\nTOTAL  %d checks, %d covered (%.0f%%)"
          % (total, covered_total, 100 * covered_total / max(total, 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
