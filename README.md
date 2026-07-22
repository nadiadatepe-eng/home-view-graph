# homegraph

A five-model knowledge graph over a directory you choose. Every number below
was measured, not estimated -- but read the date on each one, because a home
directory moves.

## What is here

The numbers come from one survey, taken 2026-07-22, of a lived-in home
directory with 588 589 files:

```
588 589 files scanned
583 256 excluded  (99.09%)
  2 669 code
  1 686 misc      (M4)
    801 markdown  (M3)
    135 images    (M2)
     42 documents (M1)
```

That census is a **snapshot**, frozen into a file that is not distributed (it
is 74 MB of one person's filenames). CP-0 grades the classifier against that
file rather than against the live filesystem, so the checkpoint stays
reproducible -- and cannot notice when the filesystem underneath it changes. It
already has: a live `homegraph census` later the same day returned 448 374
files and 355 code, because a 143 000-file project directory had been deleted
in between. Nothing was wrong with the classifier; the corpus moved. Run
`census` for today's numbers, and treat the block above as the graded baseline.

The exclusion figure is the headline, not a footnote. Signal-to-noise for
images before rules is roughly 1:530, and no random sample at any realistic
directory level returns the user's own code -- dependency trees dominate every
root. Defining the corpus *is* the work; extraction is the easy part.

## No layout is assumed

homegraph does not expect a `Pictures` folder, or any other folder. It cannot:
a directory name is a fact about one machine and one desktop language, and a
rule that names one fails **silently** everywhere else -- the boundary matches
nothing, every image is excluded, and the image model builds zero nodes while
reporting success.

So the layout is declared once, by you:

```sh
homegraph init                      # scans your home directory
homegraph init --root /srv/archive  # or any other directory
```

`init` walks the root and proposes which folders hold images, documents, notes,
code and cache, based on the **extension mix actually inside each one** rather
than on a list of names. It shows you the evidence, lets you overwrite every
line, and writes `~/.homegraph/config.toml`. That file is the truth about this
installation; edit it whenever you like.

```toml
root = "/srv/archive"

[roles]
image    = ["Photos"]
document = ["Papers"]
note     = ["notes", "wiki"]
code     = ["src"]
cache    = [".cache"]
```

Until it exists, every other command **refuses with exit 2** and says what to
run. An empty role is a legitimate answer and not an error: the matching model
is simply absent, and `mesh` labels its answers `partial` and names it.

Two properties this rests on, both checked in CP-7:

- **The image boundary lives in exactly one place**, `[image_boundary]` in
  `exclusions.toml`, which expands `{image_roots}` from your config.
  `models/m2_build.py` *reads* that list to name collections; it does not
  re-test the boundary. See §2 of `DECISIONS.md` for what happened the first
  time it was in two places.
- **The scanner is not a second classifier.** It runs once, at `init`, writes
  its answer down, and is never consulted again. CP-7 asserts structurally that
  nothing but `cmd_init` imports it.
- **`init` never opens a file.** It reads names and `stat()`, the same
  invariant M2 carries, verified the same two ways -- an audit hook and strace.

The synthetic test corpus is built with Norwegian directory names and then
rebuilt with English ones at a second root, under two configs. CP-7 asserts
that the two partitions are label-for-label identical. If they were not, some
layout would still be imposed.

## Layout

| module | what it does |
|---|---|
| `userconfig.py` | `~/.homegraph/config.toml`: the root and what is under it |
| `scan.py` | `init`'s one-shot role proposal, from extension mix |
| `corpus.py` | the single `classify(path)`. Path-only, never opens a file |
| `rules/*.toml` | exclusion layers, category map, image filename grammar |
| `store.py` | SQLite schema v1, migration chain, versioned edges, FTS5 |
| `temporal.py` | observations, `datelist_int` bitmask, 90-day retention |
| `search.py` | FTS5, RRF fusion, `_out_mode` |
| `incremental.py` | mtime+size, then hash to confirm |
| `update.py` | applies that diff to a built model |
| `models/m1_*` | documents: pdf, odt, docx, tex |
| `models/m2_*` | images: filenames and `stat()` only |
| `models/m3_*` | markdown: the only model with real edges |
| `models/m4_misc.py` | everything else, plus cold-state rollup |
| `mesh.py` | M5: federates the models, never merges them |
| `visualize.py` | force layout in Python, canvas in the browser, one file |
| `mcp_server.py` | MCP over stdio: `mesh_search`/`neighbors`/`path`/`explain` |
| `cli.py` | `init`, `config`, `explain`, `census`, `status`, `search`, `md …`, `mesh …`, `update`, `visualize`, `mcp` |

## Install

Python 3.12 or newer, no runtime dependencies.

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install .          # or `pip install -e .` to keep editing the source
homegraph init
```

Without installing, the same commands run as `python3 -m homegraph.cli …` --
but only from inside this repo, since nothing else puts the package on
`sys.path`.

## Try it

```sh
homegraph init --root ~
homegraph config                      # what this run would use

homegraph explain ~/some/file.docx    # which rule decided, and why
homegraph census --root ~/notes

homegraph md build /tmp/m3.db --root ~/notes
homegraph md backlinks /tmp/m3.db ~/notes/some-page.md
homegraph mesh search --model m3=/tmp/m3.db trails

# One self-contained HTML file -- open it in any browser, offline, forever
homegraph visualize --model m3=/tmp/m3.db --out graph.html

# MCP server on stdio
homegraph mcp --model m3=/tmp/m3.db
```

## Updating without rebuilding

```sh
homegraph update --model m3=/tmp/m3.db --model m2=/tmp/m2.db \
                 --mesh-db /tmp/mesh.db
```

`incremental.py` computes the diff -- `added`, `changed`, `touched`,
`unchanged`, `removed`, cheap on mtime and size, confirmed with a hash.
`update.py` applies it: added and changed files are rebuilt, removed files and
their edges are deleted, `touched` (new mtime, identical bytes) costs one
`stat()` and no reparse, and `unchanged` costs nothing.

The claim is an equivalence, and it is the only one worth making:

> a store built on corpus A and updated to corpus B is indistinguishable from
> a store built on corpus B from scratch.

CP-8 checks it by comparing nodes and edges as **sets**, not counts -- counts
are equal when one node has been swapped for another, which is exactly what a
broken incremental path produces. The test corpus differs on all five axes,
because an equivalence gate over two identical corpora passes for any update
path, including one that does nothing.

Four things that fell out of writing it, all of which had been latent:

- **M3's link index is global.** Rebuilding three files resolved their
  `[[wikilinks]]` against those three files, declared every other target
  broken, and wrote `wikilink:` nodes for pages that exist. The equivalence
  gate caught it on its first run.
- **Neighbours have to be rebuilt too.** Deleting a page breaks every link to
  it, and adding one resolves every link to it, in files that did not change
  and that no per-file diff can see. `update` widens the rebuild set to those
  files. It is a heuristic and is labelled one; the equivalence gate is what
  makes it safe to ship.
- **`touched` could never fire.** No model stored a `content_hash`, so the
  stored hash was always NULL and every rewrite came back `changed`. The
  two-stage design in `incremental.py` had been decorative since it was
  written. M1 and M3 store it now.
- **A layout change is not a file change.** Changing the `image` role moves the
  boundary between two models; the paths do not move, so no diff can see it.
  `update` fingerprints the config into the store and refuses with exit 2
  rather than producing a store that mixes two configurations.

**Removal deletes, and it deletes history.** The alternative -- a tombstone --
cannot coexist with the equivalence claim, since a rebuild of B has no node for
a file that is not in B. That is a real loss and is written down in
`update.py`: after an update, `edges_as_of()` cannot show a relation only a
deleted file took part in.

**M4 is refused, not approximated.** Its cold-state rollup aggregates the whole
corpus into single nodes, so a per-file diff cannot be applied without breaking
the reconciliation CP-5 checks. `update` says so and exits 2.

## Viewing the graph

`visualize` writes a single HTML file with the layout precomputed in Python and
the drawing done on a canvas. No D3, no CDN, no `fetch` -- the plan named D3,
but a 280 KB CDN pull would make the page useless offline and would be this
package's first runtime dependency.

Pan by dragging, zoom by scrolling, hover for the node key, filter by model,
search to highlight. If a model's store is missing the page says so in the
panel rather than quietly drawing a smaller graph.

## Tests

```sh
uvx pytest -q tests/                                            # 10 modules
for t in 0 1 2 3 4 5 6 7 8; do python3 tests/mutate_cp$t.py; done
python3 tests/test_cp0.py                  # any checkpoint runs standalone too
```

Neither line needs `uv`: after `pip install .`, `python3 -m pytest -q tests/`
does the same thing, and every checkpoint runs standalone with no test runner
at all.

**Nine checkpoints plus a privacy check. 99 mutations, 0 survived, 0 detected
only by a crash**, measured 2026-07-22. The split of *how* they died is the
fragile number and is timestamped for a reason: it has moved twice within an
hour of measurement. **0 survived is the load-bearing claim**; a mutation
moving between *the named gate said no* and *the suite died* changes how much
that kill is worth, so re-run the harnesses after touching a checkpoint rather
than trusting the numbers here.

| harness | mutations | killed by the named gate |
|---|---|---|
| CP-0 corpus | 10 | 10 |
| CP-1 substrate | 8 | 8 |
| CP-2 markdown | 9 | 9 |
| CP-3 documents | 8 | 8 |
| CP-4 images | 10 | 10 |
| CP-5 misc | 8 | 8 |
| CP-6 mesh | 9 | 9 |
| CP-7 config | 16 | 16 |
| CP-8 update | 21 | 21 |
| **total** | **99** | **99** |

An earlier revision claimed "48/48 killed". That was wrong: the harness counted
a crashed suite as a kill, which made the `expected` field decorative -- an
adversarial audit proved it by injecting a mutation that only broke an import
and watching it reported as killed. Kills, misattributed kills and crash-only
detections are counted separately now, because "the suite died" and "a gate
said no" are different facts and only the second one is evidence.

Each `test_cp*.py` is a script that prints a readable report and also exposes a
one-line pytest adapter. The adapter is not decoration: before it existed,
`pytest tests/` collected all seven files, found no `test_*` function, and
reported success having run **nothing at all**.

**The mutation suites are not optional.** Every single checkpoint passed green
on its first run and every single one still contained gates that tested
nothing. The recurring shape is vacuous truth: `all()` over an empty list is
`True`, so the file-size gate passed cleanly with the size cap removed
entirely; `len(a) <= len(b)` passes when the filter under test is gone and both
sides are equal. CP-7 and CP-8 contributed four more on their first run -- a
"corrupt config" case that was corrupt in the wrong way, a refusal whose
instruction was carried by two independent strings, a threshold nothing in the
fixture could trip, and an FTS-orphan check that ran *after* the cleanup that
erases the evidence.

## Nothing here names a real file

`tests/test_no_real_paths.py` greps everything git would publish for personal
identifiers, absolute home paths and localised directory names, and fails if it
finds any. It is a test rather than a promise because that guarantee decays
silently: one pasted path in a docstring, in a commit whose diff looks like a
comment change.

Three bands, at deliberately different strictness, and the reasoning is in the
file's docstring. The one relaxation: the *fixture* may name directories in any
language, because it creates them itself in order to demonstrate that the
package does not know them -- and the check verifies that every such name is
one the fixture actually plants.

The real-corpus material -- the inventory snapshot, four answer keys, the
per-checkpoint thresholds and the samplers -- stays local and git-ignored.
Every checkpoint refuses with a clear message when it is absent, rather than
falling back to the synthetic numbers and reporting a real-corpus run that
never happened.

## Static analysis

No external model was available for review (codex ran out of monthly quota,
fallow does not read Python), so the substitute stack is static analysis via
`uvx` -- nothing installed, no quota:

| tool | result |
|---|---|
| `uvx ruff check .` | clean |
| `uvx bandit -r homegraph/` | **found a real vulnerability** (below) |
| `uvx radon cc -a homegraph/` | average complexity **A**; two D blocks |
| `uvx vulture --min-confidence 80` | clean; at 60% it found real dead code |
| `uvx pytest -q tests/` | 10 passed |
| `uvx mypy homegraph/` | clean, with strict on five modules (see `pyproject.toml`) |

**Bandit found what a code review had not:** `xml.etree` parsing `.docx` and
`.odt` is open to entity expansion, and these documents come from the internet.
A 1 KB file can expand to gigabytes. Fixed with a DTD guard rather than a
`defusedxml` dependency, turning eight unguarded call sites into one guarded
chokepoint. The two findings that remain are the guard itself, which bandit
cannot see past, and `random.Random(seed)` in the layout -- flagged as
non-cryptographic, which is the point: the seed is fixed so the same graph
draws twice the same.

**Vulture found a self-inflicted duplication:** `Mesh.identity()` computed the
same thing as `Mesh._fusion_key()` and was never called. Two implementations of
identity is precisely what `DECISIONS.md` §2 warns against, committed in the
same codebase that documents the warning.

## Answer keys

Two corpora, and two sets of keys. **The synthetic corpus is the default**: it
is built by `tests/fixtures/synthetic.py`, carries every adversarial case the
real one produced, and generates byte-identically on any machine, so a fresh
clone can run all nine checkpoints with nothing installed and no fixture to
download. The real corpus is opt-in with `HOMEGRAPH_REAL_CORPUS=1`.

For the synthetic corpus every expected answer is **declared** in
`synthetic.py`, at the point the file is planted -- the fifteen link relations,
the eighteen filename readings, the five documents' metadata, the ten
FIGURE_FOR pairs, the installation config, and which files corpus B added,
changed, touched and removed. Nothing in that file ever calls `classify()`, the
filename parser, the markdown extractor or `extract()`. A key computed from the
thing it grades agrees with any implementation, including a broken one.

The order in which the real keys were written matters, but it is not the same
as independence, and the distinction was overstated here before. No row was
ever edited to make a test pass -- but the rules **were written with the answer
key open**, and `FASIT.md` names three exclusion rules that exist because a
gold row exposed the gap. That makes 60/60 a training score, not a held-out
one. There is no holdout set.

## Where this disagrees with the plan

Seven things the survey got wrong, all documented with evidence in
`tests/gold/FASIT.md`:

1. **The agent tool's state directory supplies no transcripts.** All 3 247
   markdown files there are vendored plugin documentation, 3 208 of them under
   `.tmp/`. The real transcripts are in SQLite.
2. **Markdown is 801 files, not 4 113** -- the rest is dependency noise.
3. **Documents are 42 across 4 types**, not 77 across 6. No pptx, no xlsx.
4. **None of the six document libraries is installed.** The degradation path
   was the starting state, so the extractors are stdlib-only.
5. **The image directory holds 137 files, 2 of them `.docx`.** So
   `count(image) == 135`, and "under the image root" is necessary for the
   category and never sufficient.
6. **1 365 of 1 465 broken wikilinks are machine-generated** cluster labels
   from graph reports, not human intent.
7. **CP-5's rollup threshold is unusable** -- it assumed 87 000 M4 nodes; the
   exclusion layers already removed them.

## Credits

Ideas borrowed, code not.

- **`code-review-graph`** -- schema shape, migration chain, RRF fusion, the
  postprocessing pipeline. Also two of its bugs, taken as design constraints:
  a silently unbuilt vector index (#711) and a test suite that wrote into the
  home directory (#712).
- **`graphify merge-graphs`** -- the shape the federation layer needed.
- **`DataExpert-io/data-engineer-handbook`** -- cumulative table design and the
  `datelist_int` bitmask.

## Known weaknesses in the evidence chain

Found by an adversarial audit of the checkpoints themselves, and not all fixed:

**Fixed since the audit:**

- CP-0 has a mutation harness, and CP-7 and CP-8 were written with one from the
  start.
- CP-0's negative control covers **every** category, not just `image`. Writing
  it revealed that emptying `own_owners` does not disable the vendored-repo
  layer, it *inverts* it -- 448 files were still excluded, so "all rules off"
  had been quietly untrue. There is an explicit `enabled` switch now.
- **The duplicated-invariant lesson is enforced structurally.** Mutation
  testing showed the negative control cannot catch a re-duplication of the
  image boundary, because the null config neutralises both copies through the
  same knob. A structural check asserts the boundary appears in exactly one
  layer. The weaker kind of test, and the only kind that can see this.
- CP-0 tests `[secrets]` directly. Switching the whole layer off previously
  left this checkpoint green.
- CP-2 and CP-3's "full corpus builds" query the store instead of comparing the
  builder's count to its own input.
- CP-6's federation gates were replaced. The old pair passed on randomly
  shuffled rankings, because with a partitioned corpus RRF is forced to
  interleave -- they measured the fusion algorithm.

**Still open:**

- **Mutation coverage is a minority of checks.** 95 mutations against roughly
  240 checks, and the audit's generalisable finding was that the empty gates
  all sat among the checks no mutation targeted.
- **CP-4's time and memory limits do not bear the weight once put on them.**
  They are labelled regression guards. The audit hook and strace are the proof.
- **Three of CP-3's 25 answer-key cells are empty** and compare against
  themselves.
- **The gold set has no holdout.**
- **`update`'s neighbour expansion is a heuristic.** It is exact for
  wikilinks and best-effort for path mentions; what backs it is the CP-8
  equivalence gate, not a proof.

## Not done

External review by an independent model. Codex is out of monthly quota and
fallow does not read Python, so the substitute was static analysis plus an
adversarial audit of the checkpoints. That is a weaker claim than a second
model reading the code, and is stated as one: a linter has no opinion about
what the code was meant to do.
