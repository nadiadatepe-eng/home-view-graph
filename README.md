# Home-view-graph

A five-model knowledge graph over a directory you choose. Every number below
was measured, not estimated -- but read the date on each one, because a home
directory moves.

The Python package, the CLI and the config directory keep the shorter name
`homegraph`: they are typed, scripted and symlinked, and renaming what people
type to match what the project is called buys nothing and breaks their muscle
memory. **Home-view-graph** is the project; `homegraph` is the command.

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

`init` walks the root and proposes which folders hold images, based on the
**extension mix actually inside each one** rather than on a list of names. It
shows you the evidence, lets you overwrite the line, and writes
`~/.homegraph/config.toml`. That file is the truth about this installation;
edit it whenever you like.

```toml
root = "/srv/archive"

[roles]
image = ["Photos"]
```

One role, and only one. This example used to list five — `document`, `note`,
`code` and `cache` alongside `image` — and a config copied from it is
**rejected**, because those four were retired: they were roles nothing read,
and a role nobody consults is a promise the config cannot keep. `load()` names
each retired role and says what replaced it rather than ignoring it. What
decides whether a file is a document or a note is its extension, in
`categories.toml`; what `cache` used to mean now lives in `[cache]` in
`exclusions.toml`.

Commands that need to know your layout — `init`, `census`, `explain`, `build`,
`update`, `md build` — **refuse with exit 2** until the config exists, and say
what to run. Commands that only read a store already built — `status`,
`search`, `visualize`, `mesh`, `mcp` — do not consult the config and do not
refuse; they take a database path and answer from it. (This paragraph used to
say "every other command", which was broader than the code: those five exit 0
without any config at all.) An empty role is a legitimate answer and not an
error: the matching model is simply absent, and `mesh` labels its answers
`partial` and names it.

Two properties this rests on, both checked in CP-7:

- **The image boundary lives in exactly one place**, `[image_boundary]` in
  `exclusions.toml`, which expands `{image_roots}` from your config.
  `models/m2_build.py` *reads* that list to name collections; it does not
  re-test the boundary. See §2 of `DECISIONS.md` for what happened the first
  time it was in two places.
- **The scanner is not a second classifier.** It runs once, at `init`, writes
  its answer down, and is never consulted again. CP-7 asserts structurally that
  nothing but `cmd_init` imports it.
- **`init` never opens a file in the corpus.** It reads names and `stat()`, the
  same invariant M2 carries, verified the same two ways -- an audit hook and
  strace. It does read its own rule files and write your config; the claim is
  about the tree it is scanning, which is what CP-4 measures.

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
| `store.py` | SQLite schema, migration chain v1→v2, versioned edges with method/confidence, FTS5 |
| `lock.py` | one writer per store: lock file plus `BEGIN IMMEDIATE`, refuses rather than queues |
| `query.py` | the closed query language; grammar in `DECISIONS.md` section 25 |
| `temporal.py` | observations, `datelist_int` bitmask, 90-day retention |
| `search.py` | FTS5, RRF fusion, `_out_mode` |
| `incremental.py` | mtime+size, then hash to confirm |
| `update.py` | applies that diff to a built model |
| `models/m1_*` | documents: pdf, odt, docx, tex |
| `models/m2_*` | images: filenames and `stat()` only |
| `models/m3_*` | markdown: the richest edge set |
| `models/m4_misc.py` | everything else, plus cold-state rollup |
| `mesh.py` | M5: federates the models, never merges them |
| `visualize.py` | force layout in Python, canvas in the browser, one file |
| `mcp_server.py` | MCP over stdio: `mesh_search`/`neighbors`/`path`/`explain` |
| `portable.py` | node keys with the root taken out, and put back |
| `export.py` / `importer.py` | the portable artifact: lzma JSON Lines, digest in a trailer |
| `cli.py` | `init`, `config`, `explain`, `census`, `query`, `status`, `search`, `md …`, `mesh …`, `visualize`, `mcp`, `update`, `build`, `export`, `import`, `inspect` |

## What the edges say

| model | relations |
| --- | --- |
| M1 documents | `CONTAINS` · `AUTHORED_BY` · `CITES` · `SAME_AUTHOR` · `REFERENCES_FILE` |
| M2 images | `IN_COLLECTION` · `NAMED_DATE` · `SAME_RESOLUTION` · `SERIES_MEMBER` · `LIKELY_COPY` |
| M3 markdown | `CONTAINS` · `WIKILINKS_TO` · `LINKS_TO` · `EMBEDS` · `TAGGED` · `MENTIONS_PATH` |
| M4 misc | `BELONGS_TO_APP` · `SAME_FORMAT` · `ARCHIVE_CONTAINS` (zip only; `.xpi` declined by policy) |
| M5 mesh | `FIGURE_FOR` · `MENTIONS_FILE` · `TEMPORAL_COHORT` · `CITES_CODE` |

`mesh search` answers from the four models **and** from the code inventory,
which appears as a fifth source called `code`. A code stub carries a basename
and a path and no contents, so it answers *which file* -- printed under the
hit, since a basename alone is ambiguous -- and never *what is in it*. Without a
`--mesh-db`, or without `--code-root` having been run, the search says that
code was not consulted rather than returning zero hits and calling itself
complete.

Every edge carries the method it was derived by -- `exact` 1.0, `path_prefix`
0.7, `basename` 0.6, `mention` 0.5, `cohort` 0.4 -- and any answer containing
one below 1.0 comes back labelled. See `DECISIONS.md` section 24.

The last three in that table were listed in the plan and were **not built**
until 2026-07-23, which is worth saying out loud: a documented relation nobody
draws is indistinguishable, to a reader, from one that ships. `CITES_CODE`
needs an inventory passed in (`--code-root`) because `code` is a corpus
category with no model behind it; `ARCHIVE_CONTAINS` reads a zip's central
directory and nothing else, and is the only place M4 reads past the 512-byte
header.

`DECISIONS.md` section 26 has the reasoning, the two gates the mutation harness
caught as untestable on their first draft, and the three defects an adversarial
audit found afterwards -- including one that had already shipped the same shape
twice: **a documented assumption (`M1 reads one file at a time`) that stopped
being true the moment a new relation landed, with nothing but a comment holding
it.**

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

# The federation, with the code inventory CITES_CODE needs. Without
# --code-root that relation is not computed, and the report says `absent`
# rather than 0 -- "nobody asked" is not "nothing found".
homegraph mesh build --model m3=/tmp/m3.db --model m1=/tmp/m1.db \
                     --mesh-db /tmp/mesh.db --code-root ~

# One self-contained HTML file -- open it in any browser, offline, forever
homegraph visualize --model m3=/tmp/m3.db --out graph.html

# MCP server on stdio
homegraph mcp --model m3=/tmp/m3.db

# A portable artifact: lzma, root-relative, importable under any root
homegraph export --model m3=/tmp/m3.db --out graph.hgx
homegraph inspect graph.hgx            # what it carries, before taking it in
homegraph import graph.hgx --model m3=/tmp/new.db --root ~/somewhere-else
```

`export` defaults to `--redaction structure`: paths, titles and every edge,
but no file text. On a measured home corpus the text is 86% of the artifact
and all of it is yours. `full` carries it and says so on stderr. `shape`
hashes names and paths with sha256 and keeps the first prefix segment, so
`author:` stays `author:` and the graph keeps its shape while losing which
files it is about.

**`shape` is not anonymisation.** The digests are unsalted, so anyone who can
enumerate candidate paths can hash them and look for a match. It prevents
reading the graph; it does not prevent confirming a guess. `DECISIONS.md`
section 27 says so in full, including what a salt would cost.

The root is whatever you chose -- a home directory, one project, or a disk
full of them -- and an artifact imports under any other one. The mesh is not
in it: import the models and run `mesh build`, because a federation is cheap
to recompute and shipping it would mean shipping code stubs pointing at files
the receiving machine does not have.

## Updating without rebuilding

```sh
homegraph update --model m3=/tmp/m3.db --model m2=/tmp/m2.db \
                 --mesh-db /tmp/mesh.db --code-root ~
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

**Pass `--mesh-db` or the page is four separate graphs on one canvas.** The
code inventory lives in no model, so without it the drawing has never heard of
a source file and its search box cannot find one -- while the CLI finds it.
With it, code appears as a fifth layer and the cross-model edges are drawn.
Only edges with both endpoints on the page: a mesh knows more nodes than a
capped drawing shows, and half an edge is not a relation.

## Tests

```sh
uvx pytest -q tests/                                            # 14 modules
for t in 0 1 2 3 4 5 6 7 8 9 10 11 12; do python3 tests/mutate_cp$t.py; done
python3 tests/mutation_coverage.py         # which checks no mutation aims at
python3 tests/test_cp0.py                  # any checkpoint runs standalone too
```

Neither line needs `uv`: after `pip install .`, `python3 -m pytest -q tests/`
does the same thing, and every checkpoint runs standalone with no test runner
at all.

`test_no_real_paths.py` fails in a fresh clone, and that is deliberate: the
material it proves is not published is gitignored and therefore absent, so the
gate refuses to pass rather than report "nothing leaked" when there was nothing
present to leak.

**Thirteen checkpoints plus a privacy check. 343 mutations, 0 survived, 0
detected only by a crash**, measured 2026-07-23 after the portable artifact
landed. CP-3's count is 27, not the 26 printed here for a day -- recounted
rather than carried forward, which this file has had to do before. The split of *how* they died is the
fragile number and is timestamped for a reason: it has moved twice within an
hour of measurement. **0 survived is the load-bearing claim**; a mutation
moving between *the named gate said no* and *the suite died* changes how much
that kill is worth, so re-run the harnesses after touching a checkpoint rather
than trusting the numbers here.

| harness | mutations | killed by the named gate |
|---|---|---|
| CP-0 corpus | 18 | 18 |
| CP-1 substrate | 22 | 22 |
| CP-2 markdown | 22 | 22 |
| CP-3 documents | 27 | 27 |
| CP-4 images | 17 | 17 |
| CP-5 misc | 22 | 22 |
| CP-6 mesh | 40 | 39 |
| CP-7 config | 33 | 33 |
| CP-8 update | 32 | 32 |
| CP-9 provenance | 31 | 31 |
| CP-10 query | 26 | 26 |
| CP-11 write barrier | 20 | 20 |
| CP-12 portable artifact | 33 | 33 |
| **total** | **343** | **342** |

The one CP-6 mutation not in the right-hand column was killed by a *different*
gate than the one that named it — recorded rather than rounded away, because a
kill by the wrong gate means the named gate still tests nothing.

Two mutations rotted and were repaired on 2026-07-23: each one finds its target
by exact source text, and when CP-9 made `method` a required argument on
`upsert_edge`, the needles in CP-1 and CP-2 stopped matching. The harness
reports an unappliable needle as a **survivor**, which is the right call — a
score you did not earn is worse than a red line — but it only helps if someone
reads the summary.

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
| `uvx pytest -q tests/` | 13 passed |
| `uvx mypy homegraph/` | clean on 25 files, with strict on seven modules (see `pyproject.toml`) |

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

**Ideas borrowed, code not.** Not a slogan: every item below was reimplemented
here from the idea, and where a borrowed design was found to be wrong it was
inverted rather than copied -- which is itself a debt, and is recorded as one.

### `code-review-graph`

The largest debt, and still a live dependency of the design rather than a
historical one.

- The **schema shape**: SQLite plus FTS5, a migration chain, versioned edges,
  the incremental mtime-then-hash diff, and MCP over stdio.
- **RRF fusion over ranks**, and the reason for it -- BM25 scores from separate
  indexes are not comparable.
- The **postprocessing pipeline** shape.
- **It is still the code model.** `CITES_CODE` deliberately does not read
  source: `code` is a corpus category here with no store behind it, because
  reading code is code-review-graph's job and duplicating it would be a second
  opinion about the same files. See `DECISIONS.md` section 26.
- Two of its **bugs, taken as design constraints**: a silently unbuilt vector
  index (issue #711) and a test suite that wrote into the home directory
  (#712). PR #710 was sent back upstream.

### `Graphify`

- **`merge-graphs`** -- the shape the federation layer needed: query the parts,
  fuse in memory, never merge the stores.
- Its **output taught the corpus a rule it did not have.** 1 365 of 1 465
  broken wikilinks turned out to be `[[_COMMUNITY_…]]` cluster labels from
  generated graph reports, which is where the `generated` subtype came from.
  A borrowed lesson rather than a borrowed design, and worth naming as one.

### `DeusData/codebase-memory-mcp`

Measured against `code-review-graph` on 2026-07-23 with five known-answer
cases. It lost on three of them, **silently** -- and five of its ideas were
still worth taking:

- The **admission barrier** behind its write path, which became `lock.py` (the
  daemon it hangs off was not borrowed; this package runs no services).
- **`truncated` in a report** -- a capped list must say it was capped.
- **Provenance and confidence on edges**, taken *inverted*: cbm carries a
  `confidence` and hands the answer back as clean JSON with no warning. Here
  anything below 1.0 makes the answer say `partial`. **A confidence field
  nothing forces you to read is decoration.**
- A **closed query language** over the graph.
- A **portable artifact** (`--persistence`), which is TODO-E.

### `colbymchenry/codegraph`

Measured against `code-review-graph` on 2026-07-23 with the same five
known-answer cases. It lost the same way cbm did -- three silent misses on the
function-local-import homonyms (`scan`, `build`), with no confidence field at
all this time -- so `code-review-graph` stays the code model. One idea was
worth building and one was worth recording as a warning:

- **React to OS filesystem events instead of polling**, which became
  `homegraph watch` (`watch.py`). Taken *without* codegraph's daemon: this
  package runs no long-lived services, so the watch is foreground and opt-in --
  inotify while the command runs, nothing after Ctrl-C. It triggers the same
  `update` a user would run by hand, and reuses the corpus classifier to watch
  only the corpus tree (676 directories on this home, not the 51 661 a naive
  recursive watch would arm).
- **Its single `explore` tool, taken *inverted* as an anti-pattern.** codegraph
  wraps a silently-wrong answer in "this is the verbatim on-disk source, do not
  read the file yourself" -- authority framing that makes a miss harder to
  catch, not easier. The lesson here: never dress an uncertain answer in
  language that discourages verification. It is why `provenance_note` says
  `partial` out loud rather than presenting a guessed edge as fact.

### `DataExpert-io/data-engineer-handbook`

- **Cumulative table design** and the `datelist_int` bitmask behind the
  temporal layer and the 90-day retention window.

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

- **Mutation coverage is a minority of checks.** 343 mutations name 279 of 470
  checks (59%), and the audit's generalisable finding was that the empty gates
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
