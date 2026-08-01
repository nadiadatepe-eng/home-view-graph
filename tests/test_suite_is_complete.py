#!/usr/bin/env python3
"""Every checkpoint file in `tests/` is one `pytest tests/` actually runs.

This guard exists because the property it checks was already claimed, in a
comment, and was already false. `pyproject.toml` lists collectable test files
by name rather than by pattern -- a deliberate decision, so that a new
checkpoint has to be added on purpose -- and the comment justifying it says a
forgotten entry "is visible as a test that never appears rather than as a test
that quietly passes".

It was forgotten twice. `test_i1.py` and `test_i2.py` were written, run by
hand, cited as green, and never collected by `pytest tests/` at all. Visible
in principle; nobody looked. That is the third time this package has been bitten
by a hardcoded list that silently narrows what gets checked -- CP-11's capped
count, then `mutation_coverage.py` globbing `test_cp*.py` and skipping the
whole H-series, now this -- and the pattern is always the same shape: the
mechanism that decides WHAT gets verified is itself unverified.

So the claim stops being a comment and becomes a check that can go red.

Run:
    python3 tests/test_suite_is_complete.py
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
import tomllib

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Files that look like tests but are not checkpoints. Listed by name, with the
# reason, because "it is not really a test" is a claim that should cost
# something to make.
NOT_CHECKPOINTS: dict[str, str] = {}

results, check = reporter(62)


def collectable_patterns() -> list[str]:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)
    return cfg["tool"]["pytest"]["ini_options"]["python_files"]


def documented_harnesses() -> set[str]:
    """The harness names in the `for h in ...; do` loop in CONTRIBUTING.md.

    The same hardcoded-list failure as everything else in this file, one level
    out: the loop is the only place that says which harnesses a sweep runs, and
    it is maintained by hand. It already narrowed once -- it said `for t in
    0..13`, so it ran the fourteen `mutate_cp*` files and none of the ten
    others, and a reader following the README exercised 14 of 24 without a way
    to notice. `mutate_review_findings` was added to it by hand on 2026-08-01,
    which is the same edit that could have been forgotten.
    """
    with open(os.path.join(ROOT, "CONTRIBUTING.md"), encoding="utf-8") as fh:
        text = fh.read().replace("\\\n", " ")
    found = re.search(r"for h in (.+?); do", text, re.S)
    return set(found.group(1).split()) if found else set()


def main() -> int:
    patterns = collectable_patterns()
    on_disk = sorted(f for f in os.listdir(HERE)
                     if f.startswith("test_") and f.endswith(".py"))
    check("there are test files to check at all", bool(on_disk),
          "%d found" % len(on_disk))

    uncollected = [f for f in on_disk
                   if f not in NOT_CHECKPOINTS
                   and not any(fnmatch.fnmatch(f, p) for p in patterns)]
    check("every test file in tests/ is one pytest will collect",
          not uncollected,
          "never collected: %s" % ", ".join(uncollected) if uncollected else "")

    # The other direction. A pattern naming a file that no longer exists is
    # not harmful, but it is the residue of a rename, and a list of names is
    # only trustworthy while every name still means something.
    literal = [p for p in patterns if not any(ch in p for ch in "*?[")]
    stale = [p for p in literal if not os.path.exists(os.path.join(HERE, p))]
    check("and every file named in pyproject.toml still exists",
          not stale, "stale entries: %s" % ", ".join(stale) if stale else "")

    # The mutation harnesses, and the loop that is the only thing saying which
    # of them a sweep runs. `mutate.py` is the shared driver, not a harness --
    # it has no MUTATIONS list of its own.
    harnesses = {f[len("mutate_"):-len(".py")] for f in os.listdir(HERE)
                 if f.startswith("mutate_") and f.endswith(".py")}
    documented = documented_harnesses()
    check("there are mutation harnesses to check at all", bool(harnesses),
          "%d found" % len(harnesses))
    missing = sorted(harnesses - documented)
    check("every mutation harness is named in the CONTRIBUTING loop",
          not missing, "never run by the loop: %s" % ", ".join(missing))
    # The other direction, for the same reason as the pyproject one above: a
    # name that no longer resolves is the residue of a rename, and the loop
    # would exit non-zero on it rather than skip it.
    phantom = sorted(documented - harnesses)
    check("and the loop names no harness that does not exist",
          not phantom, "no such harness: %s" % ", ".join(phantom))

    bad = [r for r in results if not r[1]]
    print("\nsuite completeness: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_suite_is_complete():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
