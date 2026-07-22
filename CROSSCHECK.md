# Cross-check brief — homegraph, 2026-07-22

Frozen for external review before the first commit. Nothing here has been
committed to version control yet.

## What this is

A five-model knowledge graph over a directory the user chooses. SQLite + FTS5,
reciprocal rank fusion across models, incremental updates, an MCP server and a
self-contained HTML visualisation. No runtime dependencies outside the standard
library.

Models: M1 documents · M2 images · M3 markdown · M4 everything else · M5 mesh.

## State as measured, not as claimed

| | |
|---|---|
| `pytest tests/ -q` | 10 passed (CP-0..CP-8 plus the privacy guard) |
| Mutation harnesses, nine of them | 99 checks · 99 killed by a named gate · 0 by a different gate · 0 detected only by a crash · 0 survived |
| `ruff check .` | clean |
| `mypy homegraph/` | clean; strict on five modules |

Reproduce: `uvx --with pytest --from pytest pytest tests/ -q`, then
`python3 tests/mutate_cp{0..8}.py`. The synthetic corpus is the default and
generates identically on any machine. `HOMEGRAPH_REAL_CORPUS=1` needs
`tests/gold/real_corpus.py`, which is gitignored and not distributed.

## Where to look hardest

These are the weak points as understood from the inside. An external reviewer
who only confirms them has not added much; the value is in what is not listed.

1. **Mutation coverage is a minority.** 99 mutations against roughly 240
   checks. This project's own history says empty gates cluster exactly where no
   mutation reaches — fourteen were found that way, every one in a checkpoint
   that had been green on the first run. The ~140 unmutated checks are the
   place to start.

2. **The incremental update path.** `update.py`. The load-bearing claim is
   "same result as a full rebuild, but cheaper." The equivalence gate compares
   nodes and edges as normalised sets against a full rebuild on a corpus that
   differs on all five axes (added, changed, touched, unchanged, removed), and
   reports 0 divergences. The neighbour-expansion step is exact for wikilinks
   and best-effort for path mentions; it is marked as a heuristic in the code,
   and nothing but that one gate supports it. M1's update path is implemented
   but not gated.

3. **The declared-not-derived rule.** `tests/fixtures/synthetic.py` states what
   each planted file is at the moment it is created and never calls
   `classify()`. If any answer key anywhere turns out to be derived from the
   implementation, the gate built on it is a photograph, not a check. This is
   the single assumption the whole test suite rests on.

4. **One invariant, one place.** The image boundary lives in `[image_boundary]`
   and nowhere else. An earlier draft enforced it in both the exclusion layer
   and the category step; every gate stayed green while the negative control sat
   frozen, because the second copy kept enforcing. Making the image root
   configurable was a fresh opportunity to reintroduce exactly that.

5. **The scanner must not be a second classifier.** `scan.py` proposes roles
   once, during `homegraph init`, and writes them down. It does not run during
   `build`. A structural check asserts only `cmd_init` imports it.

6. **`homegraph` never opens an image file.** Filename and `stat()` only.
   Verified two independent ways — `sys.addaudithook` inside the process and
   `strace` outside it — and extended to cover `init`.

7. **No external model review has happened.** Static analysis plus mutation
   testing is a weaker claim than a second model reading the code, and is
   recorded as such. This document exists because that gap is being closed.

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
