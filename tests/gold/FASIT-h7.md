# CP-H7 answer key — connect-time catch-up and per-hit staleness

Written **2026-08-02, before a line of H7 code existed** -- and amended
seven times that same day, each amendment dated in place. Two of them came
after running code, not before it: R1's stub rule and R2a's. The rule this key
keeps is that a value is never changed to make a test pass, not that nothing is
ever learned; where a measurement contradicted the key, the key says so and
says which measurement. **A value is never changed to make a test pass** -- a
disagreement is resolved by changing the code, or by arguing for the change
here, with a date.

## What H7 is

The plan says: "reconcile `(size, mtime, hash)` at MCP/watch startup -- covers
the window the daemon was off; per-file staleness banner + `embedding_status`."

The window is real and it is not small. Measured 2026-08-02 on the stores in
`~/.homegraph`, all last written **2026-07-24**:

```
m1   691 paths, 320 no longer on disk
m2   183 paths,  23 no longer on disk
m3 6 637 paths, 2 628 no longer on disk
m4 1 053 paths, 236 no longer on disk
```

A search answers from those rows today with nothing to say that a third of them
describe files that are gone.

## Four measurements that shaped this, taken before the key was written

| measurement | value | consequence |
|---|---|---|
| `stat` over 8 564 stored paths | **0.01 s** | No cost ceiling. Reconcile everything, every connect. No sampling, no cache. |
| `section` nodes carrying a path but no `size`/`mtime` | m1 **618 of 618**, m3 **6 035 of 6 035** | Reconciliation is a **file-level** question. Sections inherit. |
| file-level nodes carrying both `size` and `mtime` | 1 911, **all of them** | Where the comparison applies, it always has something to compare against. |
| `content_hash` column on the `embeddings` table | **does not exist** | "Does this vector match the current content" is not answerable. See R5. |

## The rules, stated before the code

**R1 -- four states, and `absent` is not `current`.**

| state | meaning |
|---|---|
| `current` | the path exists and `size` + `mtime` match what the store holds |
| `stale` | the path exists and one of them differs |
| `missing` | the store holds a path that is no longer on disk |
| `absent` | the path is there, but the node carries no `size`/`mtime`, so drift was never asked of it |

**A node without a stat is still asked whether its path exists.** Two questions
hide in one here: *is the file still there* can be answered for any path, while
*did the content drift* needs a stored `size` and `mtime`. Amended 2026-08-02,
before the code was written, after the first pass reported `absent` for all
9 125 mesh stubs -- which are exactly the nodes whose whole purpose is to name
a file the user can go and open. `_search_code`'s own docstring worries about
an edge naming a file no search can find; reporting `absent` for a file that is
demonstrably gone would be that worry made permanent. Sections are the
exception and keep inheriting (R2): they are not files, so asking the
filesystem about them directly is the wrong question, not a cheaper one.

`absent` exists for the same reason it does in H6 and `code_inventory`: "we did
not look" and "we looked and found nothing wrong" are different answers, and
collapsing them is how a reconciliation reports a clean corpus it never checked.

**R2 -- a section inherits its file's state and is never stated on its own.**
A section carries its parent's path and no stat of its own, so asked directly it
would answer `absent` -- which would be true and useless, and would make 87 % of
m3 unanswerable. It is `stale` because its file is, and that is the only way it
is ever `stale`.

**R2a -- `search` and `reconcile` must give one node one answer.** Amended
2026-08-02 after codex found they did not: the annotation path asked the
filesystem about section hits directly and answered `absent`, while
`reconcile` answered `stale` about the same row. The fixture could not see it,
because a section shares its file's `path` and the fusion key merges the two --
so the section only ever surfaced as its file. It surfaces alone when the
section matches a query and the file does not, and that is now a gate.

**R2b -- when two stored nodes claim the same path, the WORSE state wins.**
Added 2026-08-02.
Two stat-bearing rows can share a path with different stored stats -- one
indexed before an edit and one after. Whichever SQLite returned last used to
decide, which made the answer depend on row order. Worst-first is deterministic
and cannot flatter the corpus: `missing` over `stale` over `absent` over
`current`.

**R2c -- an orphan section is asked whether its path exists.** Added
2026-08-02. A section whose
file node the store does not hold has nothing to inherit. It reports `absent`
when the path is there and `missing` when it is not -- the same split R1's
amendment makes for stubs, and for the same reason.

**R3a -- the corpus view counts NODES, sections included.** A stale file with
three sections is four stale nodes, not one. Nodes are what a search returns,
so a file count in `mesh_explain` would not add up against the window the
warning reports, and the two numbers a reader is comparing must be in the same
unit. Stated 2026-08-02 when the gate expected one stale and got two.

**R3 -- the warning counts RETURNED HITS, not the corpus.** One line in
`MeshResult.warnings`, and only when at least one hit in the returned window is
`stale` or `missing`. A corpus-wide banner would fire on every query forever --
2 628 missing paths in m3 alone -- and a warning that always fires is one nobody
reads. **Zero affected hits must produce zero warnings**, which is what makes
this gate able to say no. The corpus-wide numbers belong in `mesh_explain`,
where someone has asked.

**R4 -- search may stat, but only the hits it returns, and never raise.**
`mesh_search` reads SQLite only, today. Per-hit staleness makes it touch the
filesystem, which on an unmounted network path would block a search that used to
be pure. The bound is the returned window (at most `limit`, tens of calls at
0.01 ms), and a `stat` that fails is `absent` -- never an exception out of
search.

**R5 -- `embedding_status` is derived from the file, not from the vector.**
The `embeddings` table is `(node_id, provider, model, dim, vec)`. There is no
content hash on it, so a vector written before an edit is byte-identical in
shape to one written after, and the table cannot tell them apart. What IS
knowable:

| status | meaning |
|---|---|
| `off` | the store holds no vectors at all |
| `none` | the store holds vectors; this node has none |
| `unknown` | this node has a vector, and its file state is `absent`, so nothing can be said about it |
| `stale` | a vector exists and the node's file is `stale` or `missing` |
| `current` | a vector exists and the node's file is `current` |

Five values, not four. The first draft of this table had `absent` doing the
work of both `none` and `unknown`, which is the collapse R1 exists to forbid --
"this node was never embedded" and "this node is embedded and we cannot judge
it" are different facts and lead to different repairs. Corrected 2026-08-02,
before the code was written.

Note what `off` is read from: whether the store holds **any** vector, not which
namespace is configured on the handle. `Mesh` opens stores without an embedding
namespace, so a configured-namespace test would answer `off` for every store
that has vectors -- true of the handle, false of the store, and wrong for
everyone reading it.

**Second blind spot, and the more common one: the namespace.** This asks whether
a vector exists, not whether it is usable. Every other vector read in the
package filters on `(provider, model, dim)`; this one does not, so after a model
switch a node reports `current` here while `vector_search` finds nothing for it.
Not closed by guessing a current namespace: `embedding_coverage` refused that
same guess in the same words, because the store is opened without one. The
per-namespace picture is published beside the corpus counts in `mesh_explain`
instead. Found by audit 2026-08-02.

**Third: an identical-bytes rewrite.** A file touched to a new mtime with the
same content is `stale`, and its vector is reported `stale` with it though the
vector is still exactly right -- so the advice is to spend an embedding call
that changes nothing. `staleness` carries the same limitation and says so under
"Not covered"; it applies here too.

**What this cannot see:** a file that did not change while the model behind the
namespace did. Closing that needs a `content_hash` column on `embeddings`, which
is a migration -- and this package spent 2026-08-01 learning what a newly filled
column does to whoever keys on it. Deferred with the reason, not overlooked.

**R6 -- MCP reconciles and never writes; `watch` reconciles and updates.**
`mcp_server.py` states it "only ever issues SELECTs". A catch-up that
re-indexed on connect would spend that property on a report. `watch` runs the
same reconciliation and feeds what it finds to the update path it already has,
because updating is what it is for. **One reconciliation, two answers.**

## The worked example

A store holding four file nodes and one section, and a filesystem that has moved
on:

| node | kind | on disk | stored size/mtime | state |
|---|---|---|---|---|
| `/a.md` | document | yes, same bytes | matches | `current` |
| `/b.md` | document | yes, edited | size differs | `stale` |
| `/c.md` | document | **no** | — | `missing` |
| `/d.md` | document | yes | **stored as NULL** | `absent` |
| `/b.md#2` | section | (its file) | none of its own | `stale`, from `/b.md` |

Searching this store with `limit=10` returns **four** hits, not five:

```
warnings: ["1 stale, 1 missing among 4 hit(s) -- reindex to refresh"]
```

`/b.md#2` does not appear separately. `_fusion_key` merges it into `/b.md` by
path, which is the same merge R2a names as the reason the section bug hid for
as long as it did. An earlier revision of this key said five hits and one
stale, which was wrong twice over -- five hits would have been *two* stale by
R3a, since the section inherits. Corrected 2026-08-02 after an audit built the
table and ran it. `/d.md` is `absent` and is **not** counted: nothing was found
wrong with it, because nothing was asked.

A section surfaces on its own only when it matches a query its file does not,
and that case has its own gate.

### What each row is here to pin

* **`/d.md` is not counted.** An implementation that treats `absent` as a
  problem reports a corpus in trouble the first time a node is written without
  a stat, and the warning becomes permanent furniture.
* **`/b.md#2` is `stale` without a stat of its own.** An implementation that
  asks sections directly reports `absent` for 6 035 of m3's nodes.
* **`/c.md` is `missing`, not `stale`.** A file that is gone and a file that
  changed are different repairs: one is a delete, the other a re-index.
* **The count is 5 hits, not 8 564 paths.** R3 made checkable.

## Predictions, locked before implementing

1. **A search over a corpus with nothing stale produces no warning at all** --
   not an empty string, not "0 stale". The line is absent.
2. **On the real stores, `missing` runs into the hundreds** -- they were
   written 2026-07-24 and the measurement above says 3 207 paths are gone. A
   run reporting near zero would mean the reconciliation is comparing something
   other than what it says.

   **Measured, and the prediction as first written was wrong: 3 of 4, not
   every.** m1 320, m3 2 628, m4 236 -- and m2 **23**, because m2 holds 183
   paths in total and a hundred of them would be more than half the store. The
   sentence said "every model" when what it meant was "the corpus, and not by a
   little". Corrected 2026-08-02 rather than the threshold being lowered to
   meet it; `tools/h7_real_mesh.py` asks for 3 of 4 and prints all four.
3. **`stat` cost stays under a tenth of a second for a full corpus pass**, and
   under a millisecond for a single search's window. Stated so that a later
   implementation that hashes on the search path is caught by its own clock.
4. **The store's file `mtime` is unchanged** after an MCP connect plus a
   search. R6 made checkable: a reconciliation that writes fails this.

## Not covered by this key

* **Whether the content actually differs when `size` and `mtime` do.** That is
  `incremental.diff`'s hash stage and it already exists; H7 reports, it does not
  re-derive.
* **Repairing anything from the MCP side.** R6.
* **A vector whose model changed under a stable namespace.** R5, with the
  migration named.
* **Watched trees that were never indexed at all.** A path the store has never
  seen is not stale; it is unindexed, and that is `build`'s question.
