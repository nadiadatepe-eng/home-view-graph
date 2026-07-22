# CP-0 gold set — answer key and method

Written **2026-07-22**, before a single classification rule existed.

The gold set is CP-0's answer key. The order is the whole point: a gold set made
*after* the rules only proves that the rules agree with themselves. So this file
was written first, and **a label here is never changed to make a test pass** — a
disagreement between rule and key is resolved by changing the rule, or by
arguing for the label change here, with a date.

## A note on this document

This is the publishable version. The findings, the numbers and the reasoning are
unchanged; the **examples have been replaced**. Where the original named a file
on the machine the survey was taken on, this version names its counterpart in
the synthetic corpus (`tests/fixtures/synthetic.py`) or describes the case
generically.

That substitution costs something and the cost is stated rather than hidden: a
finding is more convincing when you can go and look at the file that produced
it. What survives is the *shape* of each case, which is what the rules were
written against, and every one of them is planted in the synthetic corpus where
anyone can look. The real material — the inventory snapshot, the four `.tsv`
keys, `real_corpus.py` and the samplers — is retained locally, git-ignored, and
reachable with `HOMEGRAPH_REAL_CORPUS=1`.

## Basis

| | |
|---|---|
| Inventory | `inventory-2026-07-22.tsv` (not distributed) |
| Files | 588 589 (`find -xdev`, non-directories, zero read errors) |
| Seed | 20260722 |

The plan said 591 349. The 2 760 difference is drift over a few hours plus
`-xdev`; the plan itself said the numbers would drift. **Every CP-0 number must
be measured against that snapshot, not against the plan's.**

## How the 60 were drawn

Four rounds, all with a fixed seed, no hand-picking inside a stratum, and **no
exclusion logic anywhere in the sampler**.

| round | what | result |
|---|---|---|
| 1 `sample_gold.py` | stratified on file extension alone + one uniform stratum | negative control |
| 2 `sample_gold_round2.py` | declared corpus roots | positive examples, two errors (below) |
| 3 `sample_gold_round3.py` | named source directories, knowledge base, top level of home | notes and loose files |
| 4 (inline in `candidates-r4.tsv`) | leaf directories inside the user's own projects | own code |

The one move that needs defending is **declared roots**: images are drawn from
the image directory, notes from the wiki and publication directories, and so on.
That is not rule knowledge leaking in — it is decisions 1 and 2, which sit
*above* the rules. Choosing where to look is a different act from deciding what
counts. Inside a root the draw is still uniform, and a file drawn from there
gets `EXCLUDED` if that is the truth. Several did.

`Q3` (the 28 loose files directly under the home directory) is not a sample but
a **complete enumeration** — the stratum is small enough to count in full.

### Two errors in round 2, found in its own output

* One stratum listed the home directory itself as a corpus root, so `startswith`
  matched the entire tree and the stratum collapsed into another uniform draw.
* Another pointed at project roots where `node_modules` and `.venv` dominate by
  count, so all ten draws came back vendor code.

Both were fixed in rounds 3 and 4. They are recorded here because they are
evidence that the sampler was judged on its output rather than on its intent.

## Findings that hit the plan

### 1 · Decision 1 rests on a miscount — 3 208 of 3 247

The plan calls the markdown under the agent tool's state directory "first-class
corpus, 79% of all markdown", with its own `transcript` subtype. The
distribution is:

| path | count |
|---|---|
| `<agent-state>/.tmp/plugins/` | 3 208 |
| `<agent-state>/plugins/cache/` | 21 |
| `<agent-state>/skills/.system/` | 18 |

All 3 247 are **vendored third-party plugin documentation** — cloud platforms,
video-conferencing, note-taking, app-framework vendors. None of it was written
by the user, and 3 208 sit under `.tmp/`, which exclusion layer 3 kills anyway.

The transcripts do exist, but not as markdown: they are in SQLite databases and
a session directory beside them.

Consequence: the `transcript` subtype, the default filter that hides it, and the
justification "42 wiki files drown in 3 247 agent logs" all apply to a corpus
that does not exist in markdown. The subtype may still be needed — but against a
SQLite source, which is a different extractor. **The decision is the user's and
is not overruled here; the number it rests on does not hold.**

### 2 · Markdown does not survive at 100%

The plan carries markdown as 4 113 raw → 4 113 after rules. The raw count of
`.md`/`.markdown`/`.mdx` in this snapshot is **7 987**, and a uniform draw of 12
from that stratum gave **10 excludable out of 12** — dependency trees, virtual
environments, caches, editor state, agent temp directories. Markdown is not a
clean corpus; it is a noisy corpus with a clean core.

### 3 · Dependency trees dominate at every root level

Round 2 `P4`, round 3 `Q1` and round 3 `Q4` all hit 10/10 vendor code, even
though the roots narrowed each round. Only at leaf-directory level did the
user's own code appear. That says the same thing as the plan's 1:530, but
sharper: **no random draw at any realistic root level finds own code.**
Exclusion is not preparation for the work, it is the work.

### 4 · Four holes in the rule layers, found by drawn files

| hole | the shape of file that revealed it | planted as |
|---|---|---|
| Layer 3 globs `*.bak` but not dated backups | a shell-history backup with a `.bak-YYYYMMDD` suffix | `.bash_history.bak-20260620` |
| Layer 2 lists `.config`/`.var`/`.npm`/`.local/share` but not the agent tool's own state directory | a saved file-history snapshot that libmagic reports as **JavaScript source** — a copy of a file that also exists live, so indexing it produces duplicate code nodes | `[app_state].prefixes_scoped` |
| No rule covers machine-generated prose | a graph report written by a wiki-generation tool — labelled `markdown` in the key, since nothing excludes it | `graphify-out/GRAPH_REPORT.md` |
| No symlink policy anywhere in the plan | an SVG inside an unpacked icon theme that is a symlink | `icons/link.svg` |

And one security hole: `.bash_history` is labelled `misc` because that is what
the rules as written produce. Shell history routinely contains tokens. The
secrets layer (layer 5) lists filename patterns and does not catch it.
`.Xauthority` (an X cookie at the top level) had the same problem. **Flagged,
not quietly reclassified — the key must show what the rules actually do.**
`.Xauthority` is now in `[secrets]`; `.bash_history` deliberately is not.

### 5 · The image directory holds 137 files, not 102

And one of them is a `.docx`. "Under the image root" is *necessary* for the
`image` category and never *sufficient*. CP-0's image gate (`count(image) ==
102`) had to be remeasured against this snapshot before it could be used as a
threshold. The synthetic corpus plants the same case twice — a `.docx` and an
`.odt` inside the image directory — because one instance of a trap is an
anecdote.

**Since this was written, the image directory is no longer a name the package
knows.** It comes from `~/.homegraph/config.toml`, proposed by `homegraph init`
from the extension mix on disk. The finding is unchanged; what changed is that
the boundary is now a value rather than a literal. CP-7 covers that, including a
run of the whole corpus under English directory names to show the partition does
not move.

## Composition

60 files, 10 per class. **24 are marked `hard`** — deliberately adversarial
cases, not decoration. The hard ones are spread across all six classes, so a
rule engine cannot reach 95% by acing the easy ones and missing one category
entirely.

The plan's threshold is ≥ 95%, i.e. **3 misses out of 60**.

> A note, not an objection: with 24 hard cases, 3 misses is tight, but the gate
> is still coarse — 60 files against 588 589 is 0.01%. The gold set proves the
> rules are not arbitrary. The partition proof and the noise threshold in CP-0
> are what prove they cover. The gold set should not carry more weight than that.

The synthetic corpus carries **72 declared cases, 57 of them adversarial**, at a
threshold of 100% rather than 95% — a fixture whose every case was planted
deliberately has no excuse for missing one.

## CP-0 run — 2026-07-22, green

`corpus.py` + `rules/*.toml` were written after this file. The gold set scores
60/60 including 24/24 hard — but **that number proves little**, since the rules
were written with the key open. The other gates are what carry.

| gate | result (real corpus) |
|---|---|
| partition, no errors | 0 of 588 589 |
| partition, all counted | 588 589 / 588 589 |
| noise threshold ≥ 70% | **99.1%** excluded |
| image gate, none outside the image root | 0 |
| cache gate, arbitrary depth | 0 wrong |
| negative control | 135 → **52 661** |
| idempotence | 15 908 paths, two identical passes |

### New baseline (the plan's numbers do not apply)

| category | count | the plan said |
|---|---|---|
| EXCLUDED | 583 256 (99.09%) | — |
| code | 2 669 | 7 936 raw |
| misc | 1 686 | ? |
| markdown | 801 | 4 113 |
| image | **135** | 102 |
| document | 42 | 77 |

`count(image) == 102` in the plan is replaced by **135** = 137 files under the
image root minus two `.docx`. Markdown lands at 801, not 4 113 — as finding 2
predicted.

### The negative control caught a real defect in the first draft

The first version enforced the image boundary in **two places**: in the
exclusion layer and again in the category step. Every other gate was green. But
when the exclusion layers were switched off, the image count sat still at 135 —
the control could not fire, because the second copy went on enforcing.

Two copies of one rule read as defence in depth and behave as a gate that cannot
say no. The boundary now lives only in `[image_boundary]`, and the control moves
135 → 52 661.

That is the only reason the gate was worth writing. Worth carrying into CP-1
through CP-7: **never duplicate an invariant across two layers.** It is also the
mistake that becomes easy to make again the moment the boundary turns into a
configurable value, which is why CP-0 keeps a *structural* check that the
boundary appears in exactly one layer — the negative control cannot see a
re-duplication, since the null config disables both copies through the same knob.

### Loose threads into TODO-1

* `misc` is 1 686 files and its subtypes are still only `unknown` — `corpus.py`
  decides membership, M4 refines with libmagic. CP-5's rollup gate is measured
  against 1 686, not the plan's ~87 000.
* The allowlist is empty on purpose. Every entry is a hole in the partition proof.
* `.bash_history` and `.Xauthority`: the latter is now in `[secrets]`, the former
  deliberately not. See finding 4.

## Files

| file | role | distributed? |
|---|---|---|
| `gold-set.tsv` | **the key.** 60 rows, hand-labelled | no |
| `inventory-2026-07-22.tsv` | the snapshot everything is measured against | no |
| `candidates*.tsv` | the four draw rounds, label column empty — provenance | no |
| `sample_gold*.py` | the samplers. No exclusion logic, never open a file | no |
| `real_corpus.py` | each checkpoint's real-corpus thresholds and paths | no |
| `score_gold.py` | scores `classify()` against the key. Fails loudly until it exists | yes |

Everything in the "no" column names a real file, directory or account, and is
git-ignored for that reason. Every checkpoint refuses with a clear message when
they are absent rather than falling back to the synthetic numbers and reporting a
real-corpus run that never happened. The synthetic corpus carries its own keys,
declared in `tests/fixtures/synthetic.py`, which is source.
