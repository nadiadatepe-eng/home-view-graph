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

## 13 · `mypy --strict` on seven modules, not on all twenty-five

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
| `lock` | the identity of a held lock -- nonce, pid and start time decide whether a lock file is ours, someone else's, or a dead writer's orphan |
| `config` | the declared layout; the roles that decide which directory holds what |

Not strict: `models/`, `mesh.py`, `cli.py`, `query.py`, `visualize.py`,
`mcp_server.py`, `tests/`. Their failures are loud -- an extractor that
mishandles a type raises, and a checkpoint goes red.

`lock` and `config` were added when CP-11 and CP-7 landed, on the same test:
would a wrong type here be silent? A lock that mis-identifies its own holder
and a config that mis-declares a role both fail by quietly doing the wrong
thing rather than by raising.

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
layer discards daily observations after 90 days; this is the same policy
applied to a file rather than to a date.

That precedent was cited here before it was true. `apply_retention` existed,
was documented in three places as the reason the observations table has a
ceiling, and was called only from CP-1 — so a reader checking whether deletion
was consistent with the rest of the system got a yes from a mechanism that was
not running, and the table grew without bound on every real installation. It
now runs at the end of `update`, with `commit=False` so it stays inside the
update's single transaction. Wiring it in with the default committing signature
broke CP-8's interrupt gate immediately, which is the same defect that gate's
own comment records having caught once before.

**Cost:** after an update,
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

## 17 · The readability probe races, and the race is bounded rather than closed

`incremental.diff` calls `os.access(path, R_OK)` on files whose size and mtime
both match, because revoking read permission changes neither and `update` would
otherwise keep serving text from a file it can no longer open — while a full
`build` would have dropped it. The two must agree, so the probe stays.

It is gated on `use_hash`, which means it does not run for M2 — the model that
never opens a file and therefore cannot disagree with `build` about whether it
can be read. That is the right scope and it was not stated: the paragraph above
reads as a system-wide invariant, and it holds for M1 and M3.

It is a time-of-check/time-of-use race and cannot be made not to be one. There
is no atomic "will this still be readable when the builder gets there"; only
the open itself answers that, and opening every unchanged file is the rebuild
the fast path exists to avoid. Two smaller lies come with it: `os.access` asks
about the real uid while the open uses the effective one, and as root it
returns True for `chmod 000`, so the branch never fires in a root run. CP-8
prints that rather than skipping it silently.

The decision is to bound the damage instead of claiming it is closed:

- A false `changed` costs one wasted reparse. The builder opens the file and
  lands where `build` lands.
- A false `unchanged` leaves one stale node until the next run.
- CP-8 compares `update` against a full build, so drift that persists is
  caught rather than assumed away.

A race here degrades performance, not the answer. That is the whole reason it
is acceptable, and the reason it is written down instead of fixed: a future
reader who "fixes" it by opening the file has traded the fast path for nothing.

---

## 18 · Every exclusion layer must uniquely own files

CP-0's negative control switches all the rules off at once and shows the image
count exploding. That answers *do the rules do anything*, which is a question
about the pile. It cannot answer *does this layer do anything* — and a layer
whose files are all caught by some other layer as well is dead weight that
every gate reports as green. Deleting it would change no output, so nothing
would notice, least of all a reviewer, because the layer reads as correct.

So each of the seven layers is now switched off alone, and each must uniquely
own at least a declared number of files. This is decision 2 seen from the other
side: there, one rule lived in two places and the control could not move; here,
a layer contributes nothing the rest of the stack was not already doing.

It found one on its first run. `[symlinks]` owned zero. The corpus's only
symlink was `icons/link.svg` — an image outside the image root, so the boundary
excluded it either way — and the fixture's own comment claimed that case tested
"the symlink layer and nothing else". The rules were fine; the corpus could not
see them. A second symlink with no second reason, `notes/mirror-of-plan.md`,
fixed the fixture.

The measured floors are in `test_cp0.py`. Three layers sit at 1 because the
fixture plants exactly one vendored repo, one image outside the boundary and
one solo symlink; raising those would be a claim about the fixture author
rather than about the rules.

---

## 19 · The config write is atomic *and* durable, and only one of those failed loudly

`userconfig.write` renders to a sibling file, fsyncs it, and `os.replace`s it
into position, so no reader ever sees a half-written config — decision 15
explains why a half-written one is worse than a corrupt one. That is atomicity.
It is not durability: the rename lives in the parent directory's metadata, and
until that is flushed the machine can come back with every byte of the new file
safely on disk and the old name still in place.

The parent directory is now fsynced too. Errors from it are swallowed, which is
the deliberate reading and not the lazy one: by that point `os.replace` has
returned, the config exists and is complete, and raising would report a failed
write for a write that succeeded — while the cleanup handler went looking for a
scratch file that has already been renamed away. Durability is what is being
attempted; the honesty of the return value is what is being protected. A file
fsync that fails still propagates, because there the bytes really may not be
there.

CP-7 watches which descriptors get fsynced. That is weaker than the thing it
stands for — the real event is a power cut, and there is no way to stage one —
so the gate is named for the syscall rather than for the disk.

---

## 20 · Unmutated area is where the empty gates are, so it is measured

The adversarial audit's most useful finding was not any single check that
could not fail. It was where they all were: among the checks no mutation had
ever been aimed at. A check that cannot fail cannot have a mutation written
against it, so the two defects co-locate by construction.

`tests/mutation_coverage.py` reports that area. A check counts as covered when
some mutation names it in `expected` — the same match the harnesses use to
attribute a kill. Deliberately strict: a check that happens to go red under an
unrelated mutation is not evidence that anyone chose to test it.

It was 37% (104 of 281) when first measured. Writing mutations for the
load-bearing half took it to 57% (163 of 286), and it stands at **59% (249 of
421)** across twelve checkpoints -- the ratio barely moved because CP-7 through
CP-11, and then the three missing edge types, added checks and mutations
together. Every batch found something the
checkpoint had been reporting as green:

- **CP-3** — `doctype is filterable in the store` counted `DISTINCT subtype`,
  which carries `doctype/status`, so four distinct *strings* was a bar one
  doctype cleared wearing four status suffixes.
- **CP-2** — `RE_INLINE_CODE` is DOTALL and matches a backtick run not followed
  by another backtick, so it was already blanking every ``` fence and deleting
  `RE_FENCE` changed nothing. A `~~~` fence is the only one only `RE_FENCE` can
  blank. Separately, nothing tested that blanking preserves length, which is
  the entire reason it pads rather than deletes.
- **CP-1** — the window gate tested a date 40 days out, where an off-by-one
  does not live; day 32 now has its own check. The raw-score trap used −999,
  which puts the same node first under either scheme, so a fusion that read
  scores passed it.
- **CP-4** — `malformed dates flagged, not raised` counted rows in the answer
  key whose declared kind was `malformed` and compared that to a number in the
  spec: two constants agreeing about a third. It now parses them.
- **CP-6** — `every tool declares a schema` read the `TOOLS` constant, not the
  `tools/list` response, so a server that stripped schemas on the way out was
  green. And `a malformed line does not kill the session` was unwrapped, so the
  mutation that kills the session was scored as a crash rather than a failure.

Coverage is a map, not a score. 100% would mean every check has one mutation
aimed at it — not that every way the code can be wrong has been tried.

---

## 21 · Prose is a specification, and it was reviewed as one

Three external rounds asked what the code does wrong. A fourth asked something
else: **where does the prose claim something the code does not do?** The
codebase is deliberately prose-heavy — 20 decisions, long docstrings, comments
that state invariants outright — and that prose is load-bearing. A future
reader trusts it instead of re-deriving the behaviour, so a docstring that lies
is worse than no docstring: it stops the investigation.

Thirteen major divergences came back. The pattern in the worst of them was one
thing, and it is the same one the per-layer control in §18 found:

**Four documented mechanisms had no caller outside `tests/`.**

- `no_open_guard()` — "the enforcement" for the never-open-an-image rule
- `apply_retention()` — cited three times as why observations are bounded
- `refresh_all_datelists()` — named as why cohort masks share an anchor
- `cohort_overlap()` — "the whole point of the bitmask"

Every checkpoint passed, because the tests called them and they worked. The
product never ran any of them. That is worse than dead code: dead code makes no
promise, while a function with a docstring, a decision entry and a passing test
tells a reader the question is already settled.

Resolved four different ways, and which way matters:

- **`apply_retention` is now wired** into `update`. It was a real defect.
- **`refresh_all_datelists` is now wired**, because `update` was running a
  second copy of its loop inline — a duplicated invariant where the named
  mechanism was the dead one.
- **`no_open_guard` stays test-only, and its prose was corrected.** An audit
  hook cannot be uninstalled, so arming it per build would leave a permanent
  process-global tripwire in anything long-running. It is a verification tool;
  it was only ever described as enforcement.
- **`cohort_overlap` stays uncalled, and `mesh`'s prose was corrected.**
  `_temporal_cohort` groups by equal mask, which is a dict lookup rather than a
  comparison per pair. The docstring claimed a bitwise AND it never did.

`tests/test_cp7.py::t_no_mechanism_lives_only_in_tests` now asserts this, by
parsing for call sites rather than grepping — the first version matched text
and passed `no_open_guard` on the strength of the docstring naming it. A gate
looking for mechanisms that exist only in prose must not be satisfiable by
prose. A mechanism leaves that list by having its claim corrected, not by being
quietly dropped.

Three real bugs came out of the same round, none of which any behavioural check
could see, because each one produced a plausible answer:

- **The MCP server was not read-only.** `Mesh.neighbours` and `Mesh.path`
  constructed a `Store` without checking the file existed, and `Store` creates
  and migrates. With no `--mesh-db` the path became the string `"None"`, so an
  unattended agent asking for neighbours got `count: 0` and left a migrated
  database called `None` in whatever directory the server started in.
- **`_temporal_cohort` compared masks across anchors.** Bit *i* means "anchor
  minus *i* days", and the loop reads across models built by separate commands
  on whatever days they ran. It now keys on `(anchor, mask)`.
- **`m2_build` held a hand-written copy of the image extensions**, in the module
  whose own docstring warns against exactly that, beside a `scan.py` that reads
  the rule file correctly. The sets were identical, so nothing was wrong — the
  failure it invited is one-sided and silent: add an extension to
  `categories.toml` alone and the classifier labels the file `image` while M2
  drops it into `skipped_non_image`, a counter whose comment makes the loss
  read as intended. A third copy turned up in `m3_markdown`, shorter by five
  extensions, so `![[cover.heic]]` was filed as a link and `![[cover.png]]` as
  an embed. Both now read `categories.toml`.

**The lesson to carry:** a claim is not verified by a test that exercises it.
It is verified by something that runs when the product runs. Ask of every
documented mechanism: *what calls this outside the tests?*

Asked of every definition rather than the four named ones, it found one more,
and a bigger one. **`Store.edges_as_of` had no path from the command line.**
That is the versioned-edge time-travel query — the `first_seen <= X AND
last_seen >= X` predicate the entire edge schema exists to support, and the
capability `store.py` names in its own docstring as the reason nothing is
deleted: *"which links did this note have last week"*. `--as-of` existed on
`mesh search`, but that filters NODES by `first_seen`; edges could time-travel
only from a test.

`md backlinks --as-of` now reaches it, through `Store.edges_as_of` rather than
by writing the dates into `backlinks`'s own SQL — a second copy of the
predicate would have given the system a second opinion about what "alive on a
date" means. CP-2 asserts it through the CLI, because a check calling the
helper directly would pass with the flag unwired from argparse, which is this
whole section's failure wearing a smaller hat.

Two things that gate got wrong first, both worth remembering: it let argparse's
`SystemExit` escape, so the unwired-flag mutation registered as a crash rather
than a refusal; and the first predicate mutation dropped a SQL clause and
changed the placeholder count, so it raised instead of answering wrongly. **A
mutation that cannot produce a wrong answer only tests error handling.**

---

## 22 · One writer per store, and it refuses rather than queues

Until CP-11 there was no write barrier at all. `Store.__init__` opened SQLite
with `foreign_keys = ON` and nothing else — no journal mode, no busy timeout,
no lock. Two concurrent `homegraph update` runs against one store were
undefined, and since `update` is a single transaction, a collision damages
more rather than less.

The intent is borrowed from `codebase-memory-mcp`'s admission barrier, which
requires every process to share a build and a cache root and records conflicts
explicitly. **The daemon it hangs off is not borrowed**: homegraph has no
long-running service to coordinate — the MCP server is stdio and read-only,
and there is no watcher. A daemon here would be machinery without a problem.

**Refusing is the design.** A queue would make a second writer wait on a first
that may be rebuilding a 588k-file corpus, and its caller — a cron line, a
shell loop — has no way to tell waiting from working. Exit 2 naming the
holder's pid is a fact the caller can act on.

Two barriers, in order:

1. **`lock.StoreLock`** — a `<store>.lock` file taken with `O_CREAT|O_EXCL`,
   holding pid, boot-relative start time, a nonce and the config fingerprint.
2. **`Store.begin_immediate()`** — `BEGIN IMMEDIATE` so SQLite's own write
   lock is taken before the work rather than at the first `INSERT`, which is
   what Python's implicit DEFERRED transaction would do.

Readers take neither. `status`, `search`, `explain` and the MCP server answer
while a writer holds the lock; WAL exists so they can.

**A pid is not a process.** Pids are reused, and a lock left by a crash whose
number was later handed to something else would read as live forever, making
the store unwritable until someone deleted the file by hand — which is how
people learn to delete lock files by reflex. So liveness compares the recorded
start time too. On a platform without `/proc` only `kill(pid, 0)` is
available; the lock still works there, one guarantee weaker, and the refusal
message says so instead of implying otherwise.

**The residual race is closed by reading back, not by locking harder.** Two
processes can both find a stale lock and both unlink it; only one `O_EXCL`
succeeds, but the loser may have removed the winner's fresh file. So a writer
re-reads the lock it believes it took and checks its own nonce is the one on
disk. Symmetrically, `release()` unlinks only a lock whose nonce is still
ours — otherwise a writer whose lock was cleared as an orphan would delete the
lock of whoever took it next.

**Known limitation, written down rather than assumed away: WAL does not work
over network filesystems.** `journal_mode` is the one PRAGMA that can decline,
so `Store` reads back what it actually got instead of trusting the request.
The user config permits an arbitrary root, so this is reachable. Today it is
detected and recorded (`Store.journal_mode`), not refused — the barrier's
correctness does not depend on WAL, only readers' ability to answer during a
write does.

**Two of CP-11's own gates were empty on the first run, and the mutation
harness is what said so.** One claimed to test unparseable lock files and
never reached the branch it named, because `_read` returned `None` and the
caller wrote `holder is not None and live` — the liveness question decided in
two places, the duplicated invariant this project bans, three days after
writing that rule down. `_read` returns `{}` now and `_liveness` decides
alone. The other claimed to test the nonce and tested the `held` flag that
short-circuits before it; the real scenario — our lock cleared as an orphan,
a later writer holding the file, our release running anyway — is now its own
gate. **CP-11: 26 checks, 12 mutations, 12 killed by a named gate, 0 by
another, 0 crash-kills, 0 survivors.**

---

## 23 · Exclusion is reported, and the cap says when it bit

`build` and `update` reported exclusion as a percentage and nothing else.
With 588,589 files in and 5,333 out, **"99.09% excluded" is 99.09% nobody can
check** — including the author. `census` did better and named directories, but
capped the list at twenty and said nothing about the cap. A silent truncation
reads as full coverage, which is the same shape as `all()` over an empty list
reading as agreement (§20).

`corpus.ExclusionReport` now carries the count, the layer that owned each
exclusion, the directories, and `truncated`. **The flag is the point; the list
is a convenience.** `census` gained `--top` and `--all`; `update` and
`md build` print a line they never had.

`corpus_paths()` returns `(paths, report)` rather than taking an optional
report argument. An optional report is a report nobody passes — the same
failure as a mechanism whose only caller is a test (§21). It calls `explain()`
instead of `classify()` at no cost, since `classify` is `explain(...).label`,
so the owning layer is known for free.

**One class fed by `record()`, not a tally per command.** The walks
legitimately differ — `census` wants every decision, `corpus_paths` wants one
label — but the truncation rule must not, because two copies of "did we cut
the list" is how one of them ends up always saying no. That is decision 1 from
the top of this file applied to reporting.

**Directory symlinks are recorded where they are pruned.** `corpus_paths`
drops them from `os.walk` without classifying, so `[symlinks]` owned nothing
in any report and CP-0's new per-layer gate had a layer it could never see —
the same fixture-shaped blindness §18 found from the other side.

CP-0 gained five checks and five mutations. Two of them are the pair that
matters: `truncated` hardwired to `False` passes any gate that only tests the
capped case, and hardwired to `True` passes any gate that only tests the
uncapped one, so both directions are checked plus a cap wider than the tally.

**Measured on the real home, which is where the value showed up:** 465,471
files excluded across all seven layers, 232 directories, list cut at 8 and
said so. `.local/share` owns 100,952 of them. That number was invisible behind
a percentage for the whole life of the project.

**Not done, deliberately:** the MCP server exposes no build, census or update
tool — it is read-only search over an existing store — so there is nothing
there to carry this structure. Said here rather than left as an unexplained
gap in the plan.

---

## 24 · An edge says how it was derived, and the answer says when it was a guess

`m3_build` counted ambiguous wikilink targets in `report.ambiguous_targets`
and then wrote an edge indistinguishable from one the text stated outright.
**The aggregate was honest and the individual fact was not** — and the
individual fact is what a query returns. `mesh.py` had the same shape three
times over: `_figure_for`, `_mentions_file` and `_temporal_cohort` derive
relations from evidence of very different strength and all landed as identical
rows.

Migration v2 adds `method` and `confidence` to `edges`. Five methods, fixed
values: `exact` 1.0 · `path_prefix` 0.7 · `basename` 0.6 · `mention` 0.5 ·
`cohort` 0.4.

**The number is an ordering, not a probability.** Nothing estimates how often
a basename match is right; the scale says a basename match is worth less than
a resolved path and more than a shared change-day. A continuous scale would
invite arithmetic nobody can defend — multiplying two of these produces a
number with no meaning — so adding a sixth value is a decision, not a tuning
knob.

**`method` is a required keyword argument.** A default would be inherited by
every future edge whose author did not think about it, and the ones worth
marking are exactly the ones added in a hurry. The language refusing to supply
it is a stronger guarantee than a test that checks for it: there is no green
run in which someone forgot.

**Re-assertion updates provenance in both directions, including downwards.** A
link that was unambiguous and now collides with a new file is genuinely less
certain than it was; keeping the old 1.0 because it is higher would freeze a
claim the corpus stopped supporting.

**The honesty rule lives in one function.** `store.provenance_note(rows)`
returns one warning naming every derivation below 1.0, or `None`. Every read
path that hands back edges calls it — `md backlinks`, `mesh neighbors`, and
the MCP `mesh_neighbors`, which reports `status: partial` for the same reason
it does when a model is missing. None of them re-implements "which of these
was a guess", because two copies of that question is how one of them ends up
always answering no.

**This is the borrowed idea inverted.** `codebase-memory-mcp` puts a
`confidence` on its CALLS edges too — 0.17 and 0.28 on the three that measured
wrong — and hands the answer back as clean JSON with no warning. A field
nothing forces you to read is decoration, which is the same category as a gate
that cannot say no (§20).

**Three of this checkpoint's own gates were weak, and the mutation harness
said so each time:**

1. "Unambiguous wikilinks are NOT marked" counted the broken-link stubs, whose
   thirteen `exact` rows kept the check green against a build that marked
   *every* resolved link. Now compared within the population the rule applies
   to.
2. `mention` and `cohort` were produced by nothing. Neither was broken — the
   shared fixture is single-day and its path mentions resolve to files no
   model holds. A fixture that cannot see a rule, exactly as §18 found for
   `[symlinks]`. CP-9 plants its own two-day corpus rather than perturbing the
   shared one's declared totals.
3. The reach corpus passed standalone and failed under pytest, because
   CP-2/3/4/6 `setdefault` `HOMEGRAPH_ROOT` at import time and
   `_pathish(home_root())` decides what counts as a path mention. **A result
   that depends on which other checkpoints were imported first is not a
   result.**

**Measured on the real corpus:** 7 832 `exact`, 23 `path_prefix`, 44
`mention`. Sixty-seven edges that looked like stated facts are inferences —
and nine of the twenty-three cluster on two target names that each exist in
two directories, a collision noted when the markdown model was built and
invisible in the graph until now.

Writing that sentence with the target names in it is what tripped
`test_no_real_paths`. The guard was right and the first draft was wrong: a
measurement on a private corpus is reportable as a shape, not as a list of
its filenames.

**CP-9: 20 checks, 20 mutations, 20 killed by a named gate, 0 by another, 0
crash-kills, 0 survivors, coverage 80%.** No mutation for "the migration ran":
skipping it leaves `upsert_edge` writing to a column that does not exist, so it
is detected by the process dying rather than by a gate — §21, a mutation that
cannot produce a wrong answer only tests error handling.

---

## 25 · The query language is closed, and its grammar is published

Written before the parser, and the parser follows it. The other order produces
a language whose definition is "whatever the code accepts", which is how a
subset ends up with no documented edge — `codebase-memory-mcp` rejected
`WHERE NOT ()-[:CALLS]->(f)` with `expected token type 85, got 67 at pos 30`,
a message about its own tokeniser rather than about what the language does not
have.

```ebnf
query      = match , [ where ] , [ asof ] , return ;

match      = "MATCH" , node , edge , node ;
node       = "(" , [ ident ] , [ ":" , label ] , ")" ;
edge       = "-[" , [ ident ] , [ ":" , reltype ] , "]->" ;

where      = "WHERE" , condition , { "AND" , condition } ;
condition  = ident , "." , property , op , literal
           | ident , "NAMED" , string ;
op         = "=" | "!=" | "<" | "<=" | ">" | ">="
           | "PREFIX" | "CONTAINS" ;

asof       = "AS" , "OF" , date ;

return     = "RETURN" , item , { "," , item } ;
item       = ident , "." , property ;

literal    = string | number ;
date       = string ;            (* ISO 8601, quoted or bare *)
```

**Everything not in that grammar is refused with exit 2 and a message naming
what is missing.** No variable-length paths, no `OR`, no `OPTIONAL MATCH`, no
aggregation, no `DISTINCT`. Each of those is a decision to make when someone
needs it, not a gap to fill in silently. One edge per query: a language that
grows a join planner has stopped being a closed language.

**`AS OF` calls `Store.edges_as_of()`. It does not re-express the predicate.**
The compiler asks that function which edge ids were alive on the date and
constrains the result to them. This is the single most likely place in the
project to end up with the temporal invariant living in two layers — the
mistake §16 records, that CP-8 has already caught once, and that would be
invisible here because both copies would agree until one was edited.

**Ambiguity is refused, not resolved.** `WHERE a NAMED 'x'` looks a node up by
basename, which is how people think about their own notes — the wikilink
syntax works that way. When a basename matches more than one file the answer
is `status: "ambiguous"` with the candidates listed, which is what
`code-review-graph` does and what `codebase-memory-mcp` does not: measured on
this repository, cbm bound a call to the wrong same-named function and stamped
the guess with a confidence nothing forces you to read (§24). This project has
a documented basename collision of its own, so the case is not hypothetical.

**Identifiers never reach SQL as text.** Labels, relation types and property
names are checked against whitelists derived from `PRAGMA table_info` and the
distinct values in the store, and every literal is a bound parameter. A
language that interpolates its own identifiers is one rename away from being
an injection surface.

**Provenance is queryable from the first version**, which is why §24 came
first: `e.method` and `e.confidence` are ordinary properties, and a result
containing derived edges carries the same `provenance_note` warning every
other read path does.

**The picture was the last read path without the rule, and the gap was
introduced by the work that closed the others.** `visualize.collect()`
selected `e.rel` and nothing else, so a relation guessed from a filename
collision was drawn exactly like one the text states -- in the form people
trust a graph most. It now carries `method` and `confidence` through to the
page: derived edges are drawn dashed and dimmed, the header counts them, and
the same `provenance_note` text appears in the warning bar.

**The dashes themselves are not gated.** Verifying them needs a browser and
this package has none. What CP-9 checks is the data reaching the page and the
counts being right, in both directions -- a graph of stated edges must claim
nothing derived. The rendering is listed as a claim rather than counted as a
proven one.

**The page answers "which files", not only "where".** A results list on the
right names every node matching the search, one row per file rather than per
section, each a `file://` link. Model labels are the names -- dokumenter,
bilder, markdown, alt annet -- with the identifier on hover, because `m3` is
what you type in `--model`, in the query language and in the MCP tool, and a
picture that hides it entirely speaks a vocabulary the commands do not.

**Nothing on that page is built from an HTML string.** A filename is
arbitrary bytes off a disk: `<img src=x onerror=...>.md` is a legal name, and
one `innerHTML` would turn a badly-named file in your own home directory into
script running in your browser. The gate is structural -- the string does not
appear in the page's code -- and it found a real one on its first run: the
tooltip escaped `<` in two of the four fields it interpolated and nothing
else. Escaping by hand is a list you have to keep complete forever; not
building HTML is a property.

**The URL construction is gated by running the page's own code**, extracted
and executed under node. The first version of that gate built
`'file://' + encodeURI(...)` in the test and compared it against the page's
identical line -- two implementations agreeing because they are the same
thought written twice, and the mutation that dropped the escaping walked
through it. Its assertion was also too weak: `decodeURI` returns an
unescaped string unchanged, so a href with a raw space round-tripped
cleanly. It now requires the URL to be ASCII and space-free as well. **The
click itself is still not gated** -- that needs a browser.

**Three of the five models had no build command at all.** `homegraph build`
printed "not implemented yet", `md build` covered M3 alone, and M1, M2 and M4
could be built only by importing their modules from a script -- while `update`
refused M4 with the advice *"Rebuild M4"*, an instruction for an action the
product did not have. The four stores in `~/.homegraph` had been made by hand.
No behavioural gate could see it, because everything reachable worked; the
claim has to be about the surface, so CP-11 now asserts that the models
`update` knows about and the models `build` offers are the same set, in both
directions.

`homegraph build --model m1=... --model m2=...` builds any of them, through
one `_build_model` that `md build` also calls -- a second entry point would be
two answers to "what does this model contain", agreeing until one was edited.

**That refactor immediately walked around CP-11's own structural gate.** The
gate scanned functions named `cmd_*`; moving the writing into `_build_model`
put a fourth writer outside its view, and it reported three writers, all
guarded, while green. It scans every top-level function now. **A structural
gate that only looks where the code used to be is a gate that has stopped
looking.**

**`build` warns about files it could not read, and exits 2.** `update` has
since CP-8; the new command printed the count inside a summary and exited 0.
Measured rather than imagined: the first real run hit 56 of them -- transient
cache files, all readable again a minute later -- and a build that reports
that as a number nobody reads is the partial result this project keeps
designing against.

**A store built before migration v2 reports zero derived edges, and that is
correct rather than a bug.** The migration defaults existing rows to
`exact`/1.0 because the code that wrote them recorded nothing else; which of
them were guesses is not recoverable after the fact. Provenance appears when
a model is rebuilt, not when it is migrated.

---

## 26 · The three edge types the plan named and the code never drew

The plan's model sections list the relations each model produces. Three of
them existed only there: `REFERENCES_FILE` (M1), `ARCHIVE_CONTAINS` (M4) and
`CITES_CODE` (M5). Every other relation on those lists was built, tested and
counted, so the lists read as descriptions of the system rather than as three
descriptions and three intentions. **This is the same failure as a mechanism
that lives only in `tests/` — see section 21 — with the documentation playing
the part the test played: a reader has no way to tell a shipped relation from
a planned one.** All three are now built.

**`REFERENCES_FILE` — a document names another file in this store.** The M1
counterpart to M3's `MENTIONS_PATH`, deliberately the same shape and the same
weight (`mention`, 0.5). Prose is scanned for path-shaped tokens whose suffix
is one `categories.toml` names, and a token becomes an edge only when it
resolves to a node that already exists. Three anchors are tried in a fixed
order — home-relative, sibling-relative, root-relative — because prose uses
all three, and the first hit wins so an ambiguous token always means the same
file. **A mention that resolves to nothing is counted, not dropped:** a run
where every mention is unresolved is a broken resolver, and it looks exactly
like a corpus whose documents do not cite each other unless the number is
visible.

The extractor owns no extension list. `corpus.known_extensions()` reads them
from the rules, which is the third time this package has had to remove a
hand-written copy of that list — the shortest copy was the one that filed
`.heic` embeds as ordinary links.

**URLs are excluded structurally, not by pattern.** The regex cannot start a
match after `/`, `.`, `-` or `@`, so no position inside `https://host/a/b.pdf`
is a candidate at all. Spaces are not path characters, which is the deliberate
cost: `Gallery Notes.docx` is not found, and the alternative — admitting
spaces — makes every sentence that ends in a filename a candidate.

**`ARCHIVE_CONTAINS` — one level, and only for zip.** `method="exact"`, and
that is why this relation is allowed to exist: the archive's own central
directory states the names. Nothing about what is *inside* those members is
inferred. Gzip, xz and bzip2 carry no member list that can be read without
decompressing them, so they get no entries rather than guessed ones.

**It is the one place in `m4_misc` that reads past the 512-byte header**, and
the module docstring now says so. Listing a zip seeks to the end of the file;
no member is opened and nothing is decompressed, so a zip bomb is a list of
names here. Entry nodes carry **no `path`** — nothing exists at
`bundle.zip/notes.md`, and giving it a path would put a location into the
graph that no `stat()` can confirm and that the mesh would mirror as a file.

**An unlistable archive is counted; an empty one is a fact.** `None` and `[]`
are different answers all the way to the report, because a build where every
archive is corrupt otherwise looks identical to one where every archive is
empty.

**`CITES_CODE` — prose that names a source file.** The plan deferred this one
with a reason: `code` is a corpus category with no store behind it, since the
code model is `code-review-graph`, a separate tool with its own database. That
reason is still true, so the inventory is **passed in** rather than read from a
model: `mesh build --code-root DIR` walks the corpus for the one category no
model owns, and mirrors those paths as stubs — a path and a name, no body, no
index. The federation's claim about code is exactly one relation wide.

**Not asked and asked-and-found-nothing stay distinct.** Without an inventory
the report says `code_inventory: absent` and the relation is not computed. A
zero would tell a reader the corpus names no source files when in fact nobody
looked. For the same reason, `prune` **refuses** when it holds code stubs and
was given no inventory: those stubs are not stale, they are unlisted — the
identical argument `prune` already makes for a model that is merely
unreadable.

**A bare filename only counts when it is unique in the inventory.** A tree of
any size holds a dozen `index.ts`; a note saying "see utils.py" names none of
them in particular, and an edge to whichever one sorted first would be a coin
flip wearing a confidence of 0.6. A full path gets `mention` (0.5) and a
unique bare name gets `basename` (0.6) — the two forms are never merged, so
the answer says which kind of evidence it rests on. **The two written forms of
a path both count** — absolute, and relative to the corpus root — and nothing
between them: matching any suffix would turn `api/handler.js`, and then
`handler.js`, into "path" mentions that skip the uniqueness condition.

Only M1 and M3 are sources. M2 bodies are filenames by construction and M4
bodies are a basename plus, for a database, its table names; neither is prose,
and this relation is about prose.

**What the mutation harness found in these gates, which is the part worth
keeping:** the fixture named the ambiguous file by its full path as well, so
the edge came from the path and deleting the uniqueness condition changed
nothing — the gate was green and untestable at once. And the extension filter
had no decoy to reject, so removing it outright left the mention gate green.
Both were only visible because the needles were written at the same time as
the gates.

**Code is searchable by name, and only by name.** A `CITES_CODE` edge could
name a file that no search would then find: the stubs sit in `mesh.db`'s FTS
index and `Mesh.search` only ever read the model stores. `_search_code` now
queries them as a fifth source called `code`, through the same `_fts_rows`
helper the models use -- one copy of the `transcript` filter and the `as_of`
predicate, because two copies is how one of them stops being edited.

What a stub can answer is **which file**: basename and path, which is what the
CLI prints under a code hit, since a basename alone is ambiguous by
construction. It cannot answer for contents. Searching for a function name
will not find the file that defines it, and that is the boundary this package
keeps: reading source is `code-review-graph`'s job.

Three states, kept apart. **No `mesh_db`** -- the caller asked for a search
over the models it named and gets exactly that, with no warning, because
nothing was dropped that was ever offered. **A mesh with stubs** -- `code`
joins the ranking. **A mesh with none** -- a warning naming the fix, because
zero hits from an inventory nobody built looks identical to a corpus with no
source files in it.

Refactoring for this rotted a mutation needle: `_fts_rows` moved the `as_of`
predicate out of `search`, and the needle aimed at the old location reported
`needle missing`, which the harness scores as a survivor. That is the second
time in two days a needle has rotted, and the summary line is the only place
it shows -- **a rotted needle and an untested gate are indistinguishable from
the total.**


### What the adversarial audit found in this change

Three findings, all reproduced with probes against the running code rather than
by reading it. The lens was deliberately new: *a new edge is an addition to a
graph that already has invariants -- where does one of these change an existing
answer without anything noticing?*

**1. M1 became corpus-dependent, and `update` still believed it was not.**
`REFERENCES_FILE` only draws an edge when the file named in prose is already a
node, so a document's own graph depends on which OTHER documents exist. That is
exactly the property `_m3_affected` exists for, and `SPECS["m1"]` carried
`affected=None` -- so adding a document never rebuilt the neighbour that names
it, the edge never appeared, and no later run recovered it. `update` reported
success. The comment in `update.py` said "M1 and M2 read one file at a time and
do not", which was true when it was written and was the only place the
assumption was recorded. **CP-8 could not catch it: its equivalence run covers
M3 alone.** Fixed with `_m1_affected`, and CP-8 now runs an M1 equivalence on a
corpus of its own.

**2. A refused prune had already written by the time it refused.** The check
sat after the mirror loop, and `Store.close()` commits by default, so
`build_edges(prune=True)` without an inventory advanced `last_seen` on every
mirrored node, committed that, and *then* raised -- leaving nodes dated later
than the edges they carry, a state neither `build` nor a completed `update` can
produce. Both refusals now run before the first write and close with
`commit=False`. **A refusal that has already written is not a refusal.**

The same call reached the user as an uncaught traceback and exit 1, in a CLI
whose every other refusal is exit 2 with a message. Both `mesh build` and
`update` catch it now.

**3. Containment is not naming.** `CITES_CODE` matched a bare filename as a
plain substring, so `runner.py` matched inside `live_runner.py`: 89 of 1 253
basename edges on the real corpus pointed at a file the text never mentions,
each carrying a confident 0.6. `Mesh._boundary` requires the name to stand on
its own -- and the first version of that rule was too strict in the other
direction, refusing a bare name that follows a slash, which cost 663 of 1 233
edges. Both numbers were measured; neither was visible by reading the regex.

**And one measurement, recorded rather than acted on.** Archive entries are
ordinary nodes, so they enter M4's FTS index and compete for its 20 slots
before fusion. Measured on the real corpus: 78 of 1 308 M4 nodes (6.0%) are
archive entries, and across ten descriptive queries they were 4 of 200 fused
hits (2.0%). They stay searchable, because "which archive holds `notes.md`" is
a question worth being able to ask, and the dilution is stated here rather than
left for someone to discover as a ranking oddity.

---

## 14 · Deferred

Kept current, because a deferral list that is not re-read becomes a list of
things that were done and a list of things that were forgotten, indistinguishable
from each other.

- **Codex review** -- batched to CP-FINAL by the author's decision on 2026-07-22,
  rather than run per module. Still outstanding: codex was out of monthly quota,
  and the five external rounds that did run were a different reviewer.
- **Embeddings** -- off, and off by default. Enabling requires naming both a
  provider and a model, so a build path can never quietly load one.
- **M1's update path** -- no longer deferred. It gained an equivalence gate in
  CP-8 on 2026-07-23, because REFERENCES_FILE made M1 corpus-dependent and the
  absence of that gate is what let the widening bug ship. The gate runs on a
  three-document corpus of its own; the fixture's `evolve()` moves markdown,
  not documents.
- **The visualisation's rendering** -- CP-9 gates the data reaching the page and
  the derived-edge counts, in both directions. Whether the dashes actually draw
  needs a browser, and this package has no runtime dependency that could open
  one. Listed as a claim, not counted as a proven gate.

**No longer deferred**, and recorded here because this list said otherwise for a
day after they shipped: the **visualisation** (`visualize.py`, self-contained
HTML, layout computed in Python -- D3 was dropped deliberately, see the plan)
and the **MCP server** (`mcp_server.py`, hand-written JSON-RPC over stdio).
