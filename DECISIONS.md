# Decisions

Each entry records what was chosen, what it cost, and what evidence forced it.
The costs are the point: an architecture document that only lists benefits is
a sales brochure.

---

## 1 · The corpus layer is one function, and it never opens a file

`classify(path)` is the only classifier in the system. Two classifiers that
drift apart produce a graph whose partition quietly stops holding, and nothing
raises.

Classification is a decision about a *path*. That keeps it cheap across 588 589
of them, and it is what makes M2's no-open guarantee enforceable at all -- a
classifier that read image files would have broken the invariant before M2 ever
ran.

**Cost:** extensionless files are typed by name and location alone at this
layer. `.face` is a JPEG with no extension and is caught by an explicit name
rule, not by its content. M4 does the content sniffing later.

---

## 2 · Never duplicate an invariant across two layers

The first draft enforced the image-directory boundary twice: once in the
exclusion layer, once in the category step. Every gate was green. But when
CP-0's negative control switched all exclusion rules off, the image count did
not move -- it sat at 135, because the second copy still enforced the boundary.

A redundant rule reads as defence in depth and behaves as a gate that cannot
say no. The boundary now lives only in `[image_boundary]`, and the control
moves 135 → 52 661.

This is the single most transferable lesson in the project, and it got harder
rather than easier when the boundary became configurable (§15). A value that
several places need is a value several places are tempted to re-derive. What
holds it in one place now:

- `[image_boundary].roots` expands `{image_roots}` from the user's config, and
  is the only rule that mentions an image directory.
- `models/m2_build.py` *reads* that list to name collections. Naming is not
  enforcing, and the module docstring says so, because the next person to touch
  it will be tempted.
- CP-0's structural check asserts the boundary appears in exactly one layer.
  The negative control cannot see a re-duplication -- the null config disables
  both copies through the same knob -- so the weaker kind of test is the only
  kind that works here.
- CP-7 asserts that emptying the `image` role takes the image count to zero.
  A second enforcer would keep it non-zero.

---

## 3 · Mutation-test every checkpoint, not just the last one

The plan put mutation testing in CP-FINAL. It belongs at every checkpoint, and
the evidence is unambiguous: **all seven checkpoints passed green on their
first run, and every one of them contained gates that tested nothing.** Fourteen
false gates in total -- CP-1 three, CP-2 three, CP-3 one, CP-4 two, CP-5 three,
CP-6 two.

Recurring shapes, worth recognising on sight:

- **Vacuous truth.** `all(...)` over an empty list is `True`. The large-file
  gate passed cleanly with the size cap removed entirely, because the list it
  quantified over was then empty.
- **Comparisons that hold when the feature is absent.** `len(then) <= len(now)`
  is satisfied when the `as_of` filter is ignored and both sides are equal.
- **Cleaning up before checking.** The orphaned-FTS-row test called
  `rebuild_fts()` before looking for orphans, which erased the evidence.
- **Test data where both branches agree.** The RRF ranking case put the same
  document first under any weighting, so the weighting was untested.

Cost: roughly twenty minutes per checkpoint.

**And it is not sufficient.** An adversarial audit of the checkpoints
afterwards found five more gates that could not fail -- including CP-5's
cross-validation, described in its own docstring as "the arithmetic that
catches" a model silently losing files, which summed one dictionary and
compared it to the same dictionary summed again. True for random noise.

All five sat among the 104 checks no mutation targeted. That is the
generalisable part: **mutation coverage is not merely a quality metric, its
absence marks where the empty gates are.** A check that cannot fail cannot have
a mutation written against it, so the two defects co-locate by construction.

The harness itself was also wrong: it counted a crashed suite as a kill, which
made the expected-gate field decorative. The audit proved it by injecting a
mutation that only broke an import and watching it reported as killed. Kills,
misattributed kills and crash-only detections are now counted separately:
**42 / 2 / 4 of 48**, not 48/48.

---

## 4 · M2 never opens an image, and two mechanisms prove it

`stat()` only. No decoding, no EXIF, no perceptual hash, no OCR.

Verified twice, because the two detectors fail differently: a `sys.addaudithook`
tripwire inside the process, and `strace -e trace=openat` outside it. Both were
confirmed independently -- with the audit hook disabled and the build modified
to read files, strace caught all 135 opens.

**Cost, and it is real:** the content of the plots is out of reach. A figure
with unreadable axes stays unreadable to the graph. M5 cannot deduplicate
images by content, so two identical files in two directories remain two nodes
joined by `LIKELY_COPY` rather than merged. That relation is named for a guess
because it is one.

**Benefit:** the 208 MB PNG is never loaded, decompression bombs are not a
threat model, and a full build takes 0.04 s.

---

## 5 · Document extraction uses the standard library

The plan named six libraries and rated "six dependencies, one missing" a medium
risk. On this machine all six are missing.

docx, pptx, xlsx and odt are ZIP archives of XML, which `zipfile` and
`xml.etree` read natively -- 30 of 42 documents with no dependency at all. PDF
got a stdlib reader for metadata, page count, encryption and text-layer
detection.

**Cost:** PDF body text is best-effort. Proper extraction means glyph
positioning and font encoding tables; this recovers text-showing operators,
which suffices for search and citation mining on well-behaved files and returns
nothing on the rest. A PDF with fonts but no recoverable text reports
`needs_ocr` rather than an empty string, because an empty string is
indistinguishable from a document that says nothing.

---

## 6 · Broken wikilinks are nodes, not errors

`[[trading-agent-plan]]` points at nothing. Both the wiki and the memory system
use an unresolved link deliberately, to mark something worth writing later.
Dropping them would erase an intention.

**But they must be counted honestly.** 1 365 of 1 465 unresolved links come from
`GRAPH_REPORT.md` files -- graphify's `[[_COMMUNITY_…]]` cluster labels, which
were never pages. Mixed into `note`, machine output drowns human intent about
12 to 1. Hence the `generated` subtype.

---

## 7 · Code spans are blanked before links are extracted

    Link between pages with `[[wikilinks]]` (Obsidian-style, no path)

Every one of the four apparently-broken link targets in `wiki/` is a line like
that one: documentation of the syntax, inside backticks. Blanking preserves
length rather than deleting, so offsets stay valid -- the same trick M2 uses
when consuming filename tokens, and the reason its series stems can be sliced
from the original string.

---

## 8 · Fusion is RRF over ranks; scores are never compared

BM25 is index-relative. A score of -99 in one model and -0.5 in another say
nothing about each other, so ordering the union by raw score ranks by which
index produces larger magnitudes. It looks like a relevance ordering. It is not
one. This was flagged in the plan as the most likely silent failure in the
project, and it survived contact with reality.

**A real bug was found here.** Fusion keyed on `model:node_key`, so a document
two models both returned became two entries with one contribution each --
cross-model agreement, the entire reason to federate, never accumulated. It
keys on content hash, else path, now.

---

## 9 · Identity: content hash, except for images

M1, M3, M4 and code match on content hash. M2 matches on normalised path,
because it has no hash and cannot have one.

**Cost:** two identical images in two directories stay two nodes. This is the
right trade, and it is written in three places -- here, in `mesh.py`, and in
`m2_build.py` -- because in six months it will look like a deduplication bug.

---

## 10 · Partial results are labelled, loudly

A federated search that quietly drops a model returns fewer results and looks
exactly like a smaller corpus. `MeshResult.status` is `partial` whenever a model
did not answer, the missing models are named, and the warning leads with
`PARTIAL RESULT`.

Same principle throughout: `search()` reports `_out_mode` so a lexical-only
answer cannot be mistaken for a hybrid one; `build`/`update`/`embed` exit 2 with
an explanation rather than succeeding at nothing; `fts_is_stale()` exists so an
under-populated index announces itself instead of returning silence.

---

## 11 · Secrets are excluded by name, and the gap is documented

Layer 5 matches filenames -- `.env`, `id_rsa*`, `*.pem`, `.netrc`, `auth.json`.
Verified with five planted fakes on a synthetic corpus, never against the real
`~/.ssh`.

**Known gap, deliberately not hidden:** `.bash_history` is classified `misc`,
which is what these rules actually do, and shell history routinely contains
tokens. It is flagged in `FASIT.md` rather than silently reclassified. An answer
key that quietly patches over a gap is worse than no answer key.

---

## 12 · Thresholds are remeasured, not inherited

The plan's numbers came from a survey taken before the exclusion layers existed.
Several of its gates became meaningless:

- `count(image) == 102` → **135** (137 files under the image root minus two `.docx`)
- markdown 4 113 → **801**
- documents 77 across 6 types → **42 across 4**
- M4 "under 15 000 nodes after rollup, against ~87 000" → the corpus is **1 686**

The last one was replaced rather than dropped: the rollup must still prove it
reduces, and its sums must reconcile against the raw counts.

---

## 13 · `mypy --strict` on five modules, not on all sixteen

The plan asked for `mypy --strict` clean on new modules. Run over everything it
reports 370 errors -- and the decisive measurement is that **plain mypy reports
zero**. Every one of those 370 is a missing annotation, not a type bug. Full
strict would have documented `str -> str` several hundred times and found
nothing.

So strict applies where a wrong type would be **silent and load-bearing**:

| module | what a wrong type would cost |
|---|---|
| `corpus` | the category label; one function decides the whole partition |
| `store` | node ids used as edge endpoints |
| `temporal` | an int that means a set of dates, with a bit convention M5 relies on |
| `search` | fused hits, where a wrong key halves a rank invisibly |
| `incremental` | the changed/touched distinction that decides re-extraction |

Not strict: `models/`, `mesh.py`, `cli.py`, `tests/`. Their failures are loud --
an extractor that mishandles a type raises, and a checkpoint goes red.

Annotating found one real defect rather than none: `upsert_node` returned
`cur.lastrowid`, which sqlite3 types as `int | None`. Every caller uses that id
as an edge endpoint, so a None would have surfaced far away as a foreign-key
error with nothing pointing back at the cause. It now raises where it happens.

The policy lives in `pyproject.toml`, not in this document, so it is enforced
rather than asserted. Two things went wrong while writing it and are recorded
because both were invisible:

- `strict = true` inside a per-module override is a **global** flag. mypy
  applied it everywhere and returned 231 errors in the very files the override
  was meant to exempt. It is expanded into its constituent per-module flags.
- The config was then verified by breaking it on purpose: removing one
  annotation from `temporal.py` produced exactly one error, restoring it went
  clean. A lint configuration that has never been seen to fail is in the same
  category as a gate that cannot say no.

---

## 15 · The layout is the user's, and it is declared once

The package knew a directory name. On a machine whose desktop language differs
that directory does not exist, and the failure is **silent**: the boundary
matches nothing, every image is excluded, and M2 builds an empty model while
reporting success.

So `homegraph init` scans whatever root you point it at, proposes roles from
the **extension mix inside each directory** rather than from a list of names,
and writes `~/.homegraph/config.toml`. Without that file every other command
exits 2 and says what to run -- the same idiom `build`/`embed` already use.

**Cost, and it is real:** one more step before anything works, and a file that
can be wrong. Guessing was the alternative, and guessing wrong is invisible.

Two constraints that shaped it more than the feature did:

- **The scanner must not become a second classifier.** It runs once, at `init`,
  writes its answer down, and is never consulted again. CP-7 asserts
  structurally that nothing but `cmd_init` imports it. A scanner that kept
  running would be a second opinion about what files are, which is §1 and §2
  together.
- **`init` never opens a file.** It walks the image directory before anything
  has promised not to read it, so a scanner that read files would break M2's
  guarantee upstream of the model that makes it, where no gate in M2 could see
  it. Verified with an audit hook and with strace, the same two ways the build
  is.

The evidence that no layout is imposed is CP-7's language experiment: the
synthetic corpus is built with Norwegian directory names, rebuilt with English
ones at a second root under a second config, and the two partitions are
asserted identical label for label. Its negative control matters as much --
pointing a role at a directory that is not there must break the partition, or
"no label changed" would also be true of a classifier that ignored the config.

Personal *values* moved out of the shipped rules at the same time and for the
same reason: `own_owners` (GitHub accounts) and the directory names of
machine-written markdown. Both are empty in the package and come from the
config. `own_owners` empty does not disable the vendored-repo layer, it inverts
it, which is why that layer has a separate `enabled` switch (§12's neighbour,
recorded in `exclusions.toml` itself).

---

## 16 · `update` is an equivalence, or it is nothing

    a store built on corpus A and updated to corpus B must be
    indistinguishable from a store built on corpus B from scratch.

Anything weaker gives a graph whose contents depend on how you got there.
CP-8 checks it as a **set** comparison over nodes and edges -- counts are equal
when one node has been swapped for another, which is exactly what a broken
incremental path produces -- against a corpus that differs on all five diff
axes, because an equivalence over two identical corpora passes for any update
path at all.

**Removal deletes, and it deletes history.** A tombstone, or a node whose
`last_seen` stops advancing the way edges do, cannot coexist with the claim
above: a rebuild of B has no node for a file that is not in B. The temporal
layer already discards daily observations after 90 days; this is the same
policy applied to a file rather than to a date. **Cost:** after an update,
`edges_as_of()` cannot show a relation only a deleted file took part in.

**A changed file keeps its own node.** Deleting and recreating it would reset
`first_seen` and drop every observation -- history the file did not lose, since
it is still the same file. Its derived nodes and its *outbound* edges go;
inbound edges belong to whichever file asserted them.

**M4 is refused, not approximated.** Its rollup aggregates the whole corpus
into single nodes, so a per-file diff breaks the reconciliation CP-5 checks.

**A layout change is not a file change.** Changing a role moves the boundary
between models while every path stays put, so no diff can see it. The config
fingerprint is stored in the model and `update` refuses when it differs.

Writing this found three things that were already broken and silent:

- M3 resolved `[[wikilinks]]` against whatever batch it was given, so a partial
  rebuild declared every other target broken.
- Files that did not change still needed rebuilding when a link target appeared
  or vanished. That expansion is a heuristic and is labelled one; the
  equivalence gate is what makes it shippable.
- **`touched` could never fire.** No model stored a `content_hash`, so the
  prior hash was always NULL and every rewrite came back `changed`. The
  two-stage design in `incremental.py` -- documented, commented, reasoned about
  -- had been decorative since the day it was written. It took a checkpoint
  that *used* the distinction to notice.

---

## 14 · Deferred

- **Codex review** -- batched to CP-FINAL by the author's decision on 2026-07-22,
  rather than run per module.
- **D3 visualisation** and the **MCP server** -- specified in the plan, not yet
  built. The CLI covers the same queries.
- **Embeddings** -- off, and off by default. Enabling requires naming both a
  provider and a model, so a build path can never quietly load one.
