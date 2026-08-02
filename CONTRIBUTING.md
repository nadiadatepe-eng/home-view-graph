# Contributing to Home-view-graph

This project is built to prove itself wrong. The checkpoints, the mutation
harnesses, and the *Known weaknesses* list in the README are not a finished
proof — they are the current best attempt to break the thing, and a fresh pair
of eyes is the most useful thing it can get. Checks and improvements are
welcome.

## The fastest ways to help

- **Send a broken gate.** Run the suite and the mutation harnesses (below). A
  mutation that *survives* is a check that does not test what it claims. That is
  a better bug report than any description of a symptom, because it points at
  the exact line that lies.
- **Fail to reproduce a number.** Every measurement in the README carries a
  date and was taken on one machine. If `census`, a mutation count, or the
  watch's directory count comes out differently on yours, open an issue with
  your numbers and the command that produced them. A number that only holds on
  one machine is a bug in the claim.
- **Pick up a known weakness.** The README's *Known weaknesses in the evidence
  chain* section and the low-severity notes in `DECISIONS.md` are an open
  to-do list, not a disclaimer.

## Running the checks

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

python3 -m pytest -q tests/                 # 34 modules, incl. a privacy gate
for h in cp0 cp1 cp2 cp3 cp4 cp5 cp6 cp7 cp8 cp9 cp10 cp11 cp12 cp13 \
         gui h1 h2 h3 h3_crosslingual h3_graph i1 i2 i3 i4 idx h3_para \
         review_findings no_real_paths type_regressions suite_is_complete \
         h4 h5 h6 h7; do
    python3 tests/mutate_$h.py     # every harness: the cp* loop was 14 of 25
done
python3 tests/mutation_coverage.py          # which checks no mutation aims at
python3 tests/condition_coverage.py         # which NEW conditions none aims at
```

Every checkpoint also runs standalone with no test runner:
`python3 tests/test_cp0.py`. `test_no_real_paths.py` is expected to fail in a
fresh clone — the material it proves is unpublished is gitignored and therefore
absent, so the gate refuses to pass rather than report a clean scan of nothing.

### Why every checkpoint file ends in a `test_checkpoint_*` adapter

The checks are written as a script: `t_*` helpers driven by `main()`, which
prints a readable report via `check()` from `tests/report.py` and returns an
exit code. pytest collects `test_*` functions, so without an adapter it
collected the file, found nothing, and reported success — a runner that
verifies nothing while looking green.

The adapter is **one test per checkpoint, not one per check**, because the
phases share built state: the corpus is built once and then queried repeatedly,
and splitting that across independent tests would rebuild it each time.

This paragraph stood as a ten-line comment in `test_cp0.py` through
`test_cp6.py`, byte for byte in all seven. Those seven now carry a one-line
`# -- pytest adapter` marker pointing here; the other nineteen never had the
comment, only the adapter function.

## House rules

These are not style preferences. Each one is a mistake this codebase has
already made at least once, written down so it is not made again.

1. **Fasit before code.** Write a test's expected answer from the specification,
   by hand, *before* the implementation exists. An answer derived from the code
   is a photograph of the code, not a check on it — it passes by construction
   and catches nothing.

2. **Mutation-test every checkpoint, not just the last one.** A gate that no
   mutation reddens is a gate that tests nothing. Add mutations alongside a new
   check that break each property it claims, and confirm the named gate — not a
   crash, not a neighbour — is what says no. Fourteen false gates were found in
   this project exactly this way, each one green on its first run.

3. **Ask what calls this when the product runs.** A mechanism verified only by a
   test that exercises it is not verified. Four documented mechanisms here once
   had no caller outside `tests/`. If you add a capability, add the production
   caller too.

4. **Never duplicate an invariant across two layers.** If a rule lives in one
   place, reuse it; do not re-derive it. The `watch` command borrows the corpus
   classifier for its exclusions rather than reimplementing them, so the two can
   never disagree.

5. **Run it on real data before calling it done.** Nine green checkpoints have
   missed what one run against a real home directory found. The synthetic
   fixture is a floor, not a ceiling.

6. **No new runtime dependencies.** `dependencies = []` in `pyproject.toml` is a
   design decision (`DECISIONS.md` section 5), not an accident. The standard
   library is the whole toolbox; a feature that seems to need more is a feature
   worth reconsidering first.

7. **Recount before you cite a number.** Two of the mutation counts in this
   repo drifted between measurements. After touching a checkpoint, re-run the
   harness rather than carrying a number forward.

8. **Vary one axis at a time in a fixture.** Three checkpoints in a row shipped
   green and then had a reviewer find the same defect: every drifted file in
   the fixture changed size *and* mtime, every tied pair had one member at
   zero, every result set held both kinds of trouble. A compound condition
   whose operands the fixture never separates cannot fail, and no amount of
   care spots that by reading. `tests/condition_coverage.py` names the ones a
   change added that no mutation aims at; run it before the sweep. A condition
   that genuinely cannot be wrong takes a `# condition-coverage: <reason>`
   line, which is a claim someone can disagree with rather than a silence.

`ruff`, `mypy` and `pyright` are expected to stay clean:

```sh
uvx ruff check homegraph/ tests/
uvx mypy homegraph/
pyright
```

Two type checkers, because they disagreed. On 2026-07-26 mypy reported
`Success: no issues found in 34 source files` while pyright reported 14 errors
on the same unchanged tree — almost all of them in the modules
`[tool.mypy.overrides]` deliberately exempts, where mypy does not check the
bodies of untyped functions and so was not looking. Both stay: mypy owns the
strict contract on the seven load-bearing modules, pyright covers the rest.
Their settings are pinned in `pyproject.toml` for the same reason ruff's are.

`pyright` reads its configuration from `pyproject.toml`, so run it without
arguments — passing a path overrides `include` and silently changes the scope.

### Running the gate in a git worktree

`test_no_real_paths.py` will fail in a fresh worktree with
`the real-corpus material exists locally  0 of 8 present`. That is the guard
working, not a regression: the material it proves is not published is gitignored,
and **`git worktree add` does not copy ignored files**. The gate refuses to pass
rather than report "nothing leaked" when there was nothing present to leak.

Copy the fixtures across before running the suite on a branch:

```sh
cd /path/to/main/checkout
for f in $(git ls-files --others --ignored --exclude-standard -- tests/gold); do
    mkdir -p "$WORKTREE/$(dirname "$f")" && cp -n "$f" "$WORKTREE/$f"
done
```

72 MB, and gitignored on the other side too, so it cannot be committed by
accident. Without this the suite is red for a reason that has nothing to do with
the branch — measured 2026-07-26 during the mutation-driver collapse, where it
looked like a regression for several minutes.

## Opening a pull request

Keep a change focused on one checkpoint or one weakness. State what you
measured and on what, the way the README does — a claim without a number to
back it is the one thing this project tries hardest not to ship.
