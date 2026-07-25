# Cross-check brief — homegraph, 2026-07-23

Written for external review. The first version of this document was frozen on
2026-07-22, before the first commit and before any external round had run; four
rounds have run since, and the numbers, the weak points and the closing item
all moved. What follows is the state as of `016064b`.

## What this is

A five-model knowledge graph over a directory the user chooses. SQLite + FTS5,
reciprocal rank fusion across models, incremental updates, a closed query
language, an MCP server and a self-contained HTML visualisation. No runtime
dependencies outside the standard library.

Models: M1 documents · M2 images · M3 markdown · M4 everything else · M5 mesh.

## State as measured, not as claimed

| | |
|---|---|
| `pytest tests/ -q` | 21 passed (CP-0..CP-13, H1–H3 and the graph gate, CP-I1, CP-I2, plus the privacy guard) |
| Mutation harnesses, twenty of them | 413 mutations · 410 killed by a named gate · 3 by a different gate · 0 detected only by a crash · 0 survived |
| `tests/mutation_coverage.py` | 666 checks, 349 covered (52%) — see the note below on why this fell |
| `ruff check .` | clean |
| `mypy homegraph/` | clean on 31 files; strict on seven modules |

Reproduce: `uvx --with pytest --from pytest pytest tests/ -q`, then
`for m in tests/mutate_*.py; do python3 "$m"; done` (CP-0..CP-13, CP-I1, CP-I2, H1–H3 and
the graph gate). The synthetic corpus is the default and
generates identically on any machine. `HOMEGRAPH_REAL_CORPUS=1` needs
`tests/gold/real_corpus.py`, which is gitignored and not distributed — so
`test_no_real_paths.py` fails in a fresh clone or worktree, by design: it
refuses to report "nothing leaked" when there was nothing present to leak.

## Where to look hardest

These are the weak points as understood from the inside. An external reviewer
who only confirms them has not added much; the value is in what is not listed.

0. **The coverage map was not covering everything, and the number fell when it
   did.** `mutation_coverage.py` globbed `test_cp*.py`, so H1, H2, H3, the graph
   gate and `test_no_real_paths.py` had been outside the map since 2026-07-24 —
   140 checks, invisible, while this document cited the total as the guide to
   where empty gates hide. It is the CP-11 failure in a second shape: the fix
   then was "glob, do not hardcode a bound", and what capped it this time was a
   hardcoded *pattern*. Every `test_*.py` is enumerated now, and a file with no
   mutation harness reports 0% rather than being omitted. The honest number went
   from 59% to 53%; nothing about the tests changed, only what was counted.

1. **Mutation coverage is still a minority.** 413 mutations against 666 checks;
   317 checks have no mutation aimed at them. This project's own history says
   empty gates cluster exactly where no mutation reaches — fourteen were found
   that way, every one in a checkpoint that had been green on the first run.
   The unmutated checks are the place to start, and `mutation_coverage.py`
   prints them per checkpoint.

2. **A needle can rot, and rot reads as a survivor.** Each mutation locates its
   target by exact source text. When CP-9 made `method` a required keyword
   argument on `upsert_edge`, two needles in CP-1 and CP-2 stopped matching and
   were reported `survived / needle missing` for a day. Counting them as
   survivors is deliberate and correct — a harness that skipped quietly would
   claim a score it had not earned — but nothing forces anyone to read the
   summary line. Both are repaired; the class of failure is not closed.

   It recurred on 2026-07-24. The H3 `fts_query(op=...)` refactor orphaned
   CP-1's AND/OR needle; an earlier mesh-SQL split orphaned CP-6's; H2's
   `NODE_COLUMNS` change orphaned CP-12's; and H3's new `vector_search` guard
   turned CP-1's "embeddings default on" mutation into a `NotImplementedError`
   crash *upstream* of the assertion meant to catch it — a survivor by a fourth
   route, a shifted failure mode. All four were found by a full recount and
   repaired (the assertion moved ahead of the search for the last one). The
   recount is the check; nothing smaller catches a needle that stopped biting.

3. **The incremental update path.** `update.py`. The load-bearing claim is
   "same result as a full rebuild, but cheaper." The equivalence gate compares
   nodes and edges as normalised sets against a full rebuild on a corpus that
   differs on all five axes (added, changed, touched, unchanged, removed), and
   reports 0 divergences. The neighbour-expansion step is exact for wikilinks
   and best-effort for path mentions; it is marked as a heuristic in the code,
   and nothing but that one gate supports it. **M1's update path is implemented
   but has no equivalence gate.** CP-11 asserts only that every model `update`
   knows about can be built from the command line, which is a claim about the
   surface, not about the result.

4. **The declared-not-derived rule.** `tests/fixtures/synthetic.py` states what
   each planted file is at the moment it is created and never calls
   `classify()`. If any answer key anywhere turns out to be derived from the
   implementation, the gate built on it is a photograph, not a check. This is
   the single assumption the whole test suite rests on. It now covers
   `tests/gold/cp10-queries.tsv` too, which was written by hand from a declared
   graph before `query.py` existed.

5. **One invariant, one place.** The image boundary lives in `[image_boundary]`
   and nowhere else. An earlier draft enforced it in both the exclusion layer
   and the category step; every gate stayed green while the negative control sat
   frozen, because the second copy kept enforcing. Making the image root
   configurable was a fresh opportunity to reintroduce exactly that. Round four
   found three further copies of the image-extension set, in `m2_build` and
   `m3_markdown`, where the sets happened to agree and so nothing was wrong yet.

6. **The scanner must not be a second classifier.** `scan.py` proposes roles
   once, during `homegraph init`, and writes them down. It does not run during
   `build`. A structural check asserts only `cmd_init` imports it.

7. **`homegraph` never opens an image file.** Filename and `stat()` only.
   Verified two independent ways — `sys.addaudithook` inside the process and
   `strace` outside it — and extended to cover `init`. Note that
   `no_open_guard()` is a verification tool, not enforcement: an audit hook
   cannot be uninstalled, so arming it per build would leave a permanent
   process-global tripwire. Round four corrected the prose that called it
   enforcement.

8. **A structural gate only looks where it is pointed.** CP-11's writer check
   scanned functions named `cmd_*`; a refactor that moved writing into
   `_build_model` put a fourth writer outside its view, and it reported three
   writers, all guarded, while green. It scans every top-level function now.
   Ask of any structural gate here: what shape of code does it not see?

9. **Rendering is claimed, not gated.** The visualisation draws derived edges
   dashed and dimmed, and CP-9 checks the data reaching the page and the counts
   in both directions — but not the drawing, which would need a browser this
   package does not have. It is listed as a claim and not counted among the
   proven gates.

## What has already been reviewed

Four external rounds have run, and each produced code rather than agreement:

1. Seven conditions that could not fail.
2. Eight defects that produce a plausible but wrong answer.
3. Errors in the round-two fixes, plus one regression they introduced.
4. Prose read as specification: thirteen divergences between what the
   documentation promised and what the code did. Four documented mechanisms had
   no caller outside `tests/`, and `Store.edges_as_of` — the time-travel
   predicate the whole edge schema exists for — had no path from the command
   line at all.

**The gap that remains is codex.** It was batched to CP-FINAL and has not run;
static analysis plus mutation testing plus four prose-and-behaviour rounds is
what stands behind the code today.

## Privacy constraint

The package is built over one person's home directory and must carry no trace
of it. `tests/test_no_real_paths.py` enforces this over everything git would
track, asking `git check-ignore` rather than reimplementing ignore semantics.

Personal identifiers are stored as truncated SHA-256 digests and matched by
token n-gram, so the guard is subject to its own rule instead of being the last
file in the tree spelling out the names it exists to exclude. It carries a
canary to prove the digest band can fire, and it has been tested negatively:
four planted names produced a failure each time.

If a reviewer finds any real filename, account, or directory from the author's
machine surviving in the publishable tree, that is a finding, and it outranks
everything else in this document.
