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

    bad = [r for r in results if not r[1]]
    print("\nsuite completeness: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_suite_is_complete():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
