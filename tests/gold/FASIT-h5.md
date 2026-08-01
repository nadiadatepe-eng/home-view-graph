# CP-H5 answer key — `CO_CHANGED_WITH` from git history

Written **2026-08-01, before a line of H5 code existed.** Same order and same
rule as the other keys in this directory: **a value here is never changed to
make a test pass.** A disagreement is resolved by changing the code, or by
arguing for the change here, with a date.

## What H5 is, and where it goes

The plan says: "`CO_CHANGED_WITH` from `git log --numstat`, strength = shared
commits >= 3, churn as a node property. Mechanical cohort provenance without
embeddings."

Three facts, measured before the design was chosen:

1. **`~/wiki` has 4 commits.** At a threshold of 3 shared commits, co-change
   there is very nearly empty. The demonstration corpus is `~/homegraph`
   itself: 114 commits, 130 tracked files, **323 of 2 547 pairs over the
   threshold**.
2. **The pairs cross model boundaries.** The strongest is `DECISIONS.md` with
   `homegraph/cli.py` at 16 shared commits -- markdown against code, and code
   is a category no model owns.
3. **`mesh` already carries `TEMPORAL_COHORT`**, and the plan calls H5
   mechanical cohort provenance. The edge belongs beside it, not in m3.

So: built in `mesh`, repository named by the caller the way `--code-root`
already works for `CITES_CODE`. **Churn is deferred** by decision on
2026-08-01 -- it is a number nothing reads yet, and it would need a migration.
Written down as deferred rather than dropped; what has to be answered first is
whether churn counts lines or commits, what a file present in two repositories
gets, and whether a file with no history gets NULL or 0.

## The four rules, stated before the code

**R1 -- a pair's strength is the number of commits that touched both.** Counted
over commits, not over lines: `--numstat` gives line counts, and a one-character
fix committed together is the same evidence of coupling as a rewrite.

**R2 -- the threshold is 3 shared commits, and it is `>= 3`.** The only input
that can tell `>= 3` from `>= 2` is a pair with exactly 2, so the key contains
one.

**R3 -- an edge is drawn only when BOTH endpoints are already nodes.** The same
rule `REFERENCES_FILE` and `CITES_CODE` follow. A file in git that is not in
the graph produces no edge, however strongly it co-changes. This is the rule
most likely to be skipped, because skipping it produces *more* edges and every
count still looks healthy.

**R4 -- the edge is symmetric and stored once**, with endpoints in sorted
order. Two rows for one fact would double the pair's weight in any traversal
that counts edges.

**Correction, 2026-08-01, same day, argued rather than edited away.** This rule
originally said `confidence` is the Jaccard ratio -- shared commits over the
commits that touched either file -- so that sharing 3 of 3 would outrank
sharing 3 of 100. **The store forbids it, deliberately.** `upsert_edge` takes
`method` and looks the confidence up from `EDGE_METHODS`; the value is never
passed by a caller, so it cannot drift from the method it describes. That
invariant is older and wider than H5, and one edge type is not a reason to
weaken it for all of them.

So: **`method="co-change"`, confidence 0.45, one value for every edge of this
kind.** Placed above `cohort` (0.40) and below `mention` (0.50) with the reason
in `store.py`: a commit is a statement that two changes belong together, which
is more than "they moved on the same day" and less than "one of them names the
other". The Jaccard ratio is *computed* -- `co_change_pairs` returns it -- and
is used for the threshold and for reporting, not stored on the edge. The cost
is real and is stated rather than hidden: **two pairs at the same threshold are
indistinguishable in the graph**, however different their ratios. The table
below keeps the ratios because they are facts about the fixture; the `EDGE`
row's stored confidence is 0.45.

## The worked example

A git repository built by five commits, each line naming the files that commit
touched:

    c0   alpha.md  beta.md  notes.txt
    c1   alpha.md  beta.md  notes.txt
    c2   alpha.md  beta.md  gamma.md  notes.txt
    c3   alpha.md  gamma.md
    c4   beta.md   delta.md

`alpha.md`, `beta.md`, `gamma.md` and `delta.md` are nodes. **`notes.txt` is
not** -- it is in the repository and not in the graph.

| a | b | shared | union | Jaccard | verdict |
|---|---|---|---|---|---|
| `alpha.md` | `beta.md` | **3** | 5 | **0.600** | **EDGE** |
| `alpha.md` | `gamma.md` | 2 | 4 | 0.500 | no -- under the threshold |
| `alpha.md` | `notes.txt` | 3 | 4 | 0.750 | no -- `notes.txt` is not a node |
| `beta.md` | `notes.txt` | 3 | 4 | 0.750 | no -- `notes.txt` is not a node |
| `beta.md` | `delta.md` | 1 | 4 | 0.250 | no |
| `beta.md` | `gamma.md` | 1 | 5 | 0.200 | no |
| `gamma.md` | `notes.txt` | 1 | 4 | 0.250 | no |
| `gamma.md` | `delta.md` | — | — | — | never in a commit together |

**Exactly one edge:**

```
CO_CHANGED_WITH   alpha.md -> beta.md   method="co-change"   confidence=0.45
```

### What each row is here to pin

* **`alpha.md`/`gamma.md` at exactly 2** is the only row that separates `>= 3`
  from `>= 2`. Without it both spellings pass.
* **`notes.txt` is over the threshold twice, at a HIGHER ratio than the one
  real edge.** An implementation that skips R3 produces three edges instead of
  one, and the two wrong ones outrank the right one. A test that only counted
  edges, or only checked that the expected edge exists, would pass.
* **`gamma.md`/`delta.md` never co-occur**, so they must not appear even as a
  zero-strength pair. An implementation that enumerates all pairs and filters
  afterwards is indistinguishable from one that enumerates co-occurrences --
  until it is asked for a pair that has none.
* **`beta.md`/`delta.md` at 1** is the floor: a single shared commit is not
  coupling, and it is what a naive "they were both in a commit" rule gives.

## Predictions locked before implementing

1. On `~/homegraph`'s own history at threshold 3: **323 pairs qualify**, out of
   2 547 pairs that share at least one commit. The count of edges actually
   written will be **lower**, because R3 drops every pair with an endpoint that
   is not a node -- and most of this repository is code, which no model owns.
   The gap between 323 and the number written is the measurement, not a
   discrepancy.
2. **`~/wiki` produces no edges at all** at threshold 3 with 4 commits. Stated
   so that an empty result there is recognised as correct rather than as a
   broken build.

   **FALSIFIED, 2026-08-01. It produces one.** `index.md` and `log.md` share
   3 of their 3 commits -- a perfect ratio -- because every ingest run into
   that vault updates the index and the log together. Four commits is enough
   for a pair to reach three; the prediction came from reading "4 commits" as
   "too few to reach the threshold" without doing the arithmetic, which is a
   guess written in the voice of a measurement. The rule is not wrong and the
   code is not wrong. **The prediction was wrong, and it is corrected here
   rather than deleted**, because a key that quietly loses its failed
   predictions is a key that has only ever been right.

   What it actually shows is the mechanism working on the smallest possible
   history: two files an author never touches separately.
3. The strongest pair by shared count in `~/homegraph` is `DECISIONS.md` with
   `homegraph/cli.py` at 16. It will **not** be the strongest by `confidence`,
   because both files are committed often on their own.

## The predictions, measured 2026-08-01

| | predicted | measured |
|---|---|---|
| `~/homegraph` pairs sharing >= 1 commit | 2 547 | **2 547** |
| ... of those, over the threshold | 323 | **323** |
| ... edges actually written | "lower" | **4** |
| `~/wiki` edges | 0 | **1** — see the correction above |
| strongest by shared count is also strongest by ratio | no | **no** |

**Prediction 1 holds, and the gap is larger than "lower" suggests: 4 of 323.**
Only 10 of this repository's tracked files are markdown, and markdown is the
only category with a model behind it, so 319 qualifying pairs have at least one
endpoint that is not a node. That is R3 doing exactly what it is for, and it is
the number to quote when someone asks why a repository with 114 commits
produced four edges.

**Prediction 3 holds.** By shared count the strongest pair is
`DECISIONS.md`/`homegraph/cli.py` at 16 shared commits -- and its ratio is
**0.348**, because both files are also committed constantly on their own. The
strongest by ratio is `homegraph/gui.py`/`tests/test_gui.py` at 12 shared and
**0.800**: a module and its test, which is the pair co-change exists to find.

That contrast is also the clearest statement of what the confidence correction
costs. Both pairs are stored at 0.45, and the graph cannot tell "a module and
its test" from "two files that are both edited constantly".

## Addendum, 2026-08-01 — three inputs the worked example cannot contain

Added the same day, with the reason. Adversarial review found that four
realistic mistakes passed the whole gate, and all four for the same underlying
cause: **every file in the worked example sits at the repository root, has an
ASCII name, and is never renamed.** The example cannot tell a correct path join
from `os.path.basename`, cannot see `core.quotepath`, and has no rename to read.

A second repository, kept separate so the table above stays exactly what was
locked before the code:

    c0   sub/note.md  høst.md
    c1   sub/note.md  høst.md
    c2   sub/note.md  høst.md  old.md
    c3   old.md renamed to new.md, høst.md touched

| a | b | shared | union |
|---|---|---|---|
| `høst.md` | `sub/note.md` | **3** | 4 |
| `høst.md` | `new.md` | 1 | 4 |
| `høst.md` | `old.md` | 1 | 4 |
| `old.md` | `sub/note.md` | 1 | 3 |

**One edge**, `høst.md` -- `sub/note.md`, and it pins three things at once:

* **A file in a subdirectory is reachable.** Joining `basename(a)` onto the
  root instead of `a` gives `note.md`, which is not a node, and the edge
  vanishes. The worked example cannot see the difference because `basename(a)`
  and `a` are the same string for every row in it.
* **A non-ASCII name survives.** Without `-z`, `core.quotepath` returns
  `"h\303\270st.md"` -- quoted and octal-escaped -- and every Norwegian
  filename leaves the graph silently. A wiki in Norwegian is the ordinary case
  here, not an exotic one.
* **A rename yields the NEW name and no third name.** `old.md` and `new.md`
  appear; `old.md => new.md` in braces does not. Four rows exactly, so an
  implementation that invents the brace form as an eighth path is caught by
  the count rather than by anyone noticing the string.

**The prose this corrects.** The "Not covered" section below said a rename
"reads as one file ending and another beginning. Both halves keep their own
co-change history." Without `-z` that was not what happened: git printed a
single brace-form field, so the commit contributed **nothing** for that file --
neither half kept anything. The caveat described a reasonable design that the
code did not implement, which is worse than an unmentioned gap, because a
written caveat reads as a case that was considered.

## Not covered by this key

* **Renamed files.** `--follow` works on one path at a time and cannot be run
  for a whole tree in one pass. With `-z` a rename arrives as the old and the
  new name in separate fields and the NEW one is counted; commits before the
  rename already counted the old name under its own. So the two halves keep
  separate histories and neither inherits the other's -- measured in the
  addendum above, after the version of this paragraph that only claimed it.
* **Merge commits.** `--numstat` prints nothing for them without `-m`, so a
  merge contributes no co-change. This is the right default -- a merge touches
  files because two branches did, not because an author changed them together
  -- but it is a choice and not an accident.
* **Whether co-change is USEFUL.** This key fixes what the edge is, not whether
  it improves retrieval. That needs H1's scoreboard and belongs to whatever
  checkpoint consumes the edge.
* **Churn**, deferred by decision above.
