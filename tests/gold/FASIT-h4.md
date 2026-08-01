# CP-H4 answer key — heading tree, breadcrumb, leaf flag, per-section hash

Written **2026-08-01, before a line of H4 code existed.** Same order as CP-0's
key and for the same reason: a specification written after the implementation
only proves the implementation agrees with itself. **A value here is never
changed to make a test pass** — a disagreement is resolved by changing the code,
or by arguing for the change here, with a date.

## What H4 is, after reading the code

`docs/harvest-plan.md` says "node per leaf heading". The nodes already exist:
`m3_build.py:184-189` writes one per heading — `path#i`, `kind="section"`,
`subtype="h<level>"`, with a `CONTAINS` edge from the file. H4 is therefore
three fields, not a node type:

| Field | Where | Today |
|---|---|---|
| `heading_path` | new column, JSON list | absent |
| `section_leaf` | new column, 0/1 | absent |
| `content_hash` | column exists on `nodes` | NULL for sections |

Two decisions taken 2026-08-01, both departures from the plan text:

1. **`body` is the breadcrumb, not the section text.** Section text in `body`
   would turn one FTS hit per file into N — the same class of behaviour change
   as `static_embed`, and it gets measured against H1 before it is taken, not
   as a side effect of H4.
2. **Leaf is a field, not a selection.** "Node per leaf heading" would mean
   deleting the h1/h2 nodes now in the graph. They stay.

## The four rules, stated before the code

**R1 — `heading_path` is the ancestor chain including the heading itself**, as a
JSON list. Ancestors are the nearest preceding headings of strictly shallower
level, outermost first.

**R2 — `body` is that list joined with `" > "`,** and it is built *from* the
list at write time, in one place. Two fields holding one fact drift; this one
cannot, because the second is derived from the first at the only point either
is written. The list is the stored form and the string is the FTS form —
**a heading may itself contain `>`**, so the joined string is not always
splittable back, which is why the list exists and is not merely convenience.

**R3 — a heading is a leaf when the next heading is not deeper.** The last
heading in a document is a leaf. Level is compared, not indentation or blank
lines.

**R4 — `content_hash` is `sha256(own text).hexdigest()`,** full digest, same
shape as `hash_file` uses for files. *Own* text: from the end of the heading
line to the start of the next heading **of any level**, not to the end of the
subtree. A parent's hash therefore covers only its own prose.

Two consequences of R4, both deliberate:

* **A change deep in a subtree does not restamp its ancestors.** The alternative
  (hash the whole subtree) marks every ancestor stale for one edit far below,
  and the unit being re-indexed is the section node, not the subtree.
* **The text is sliced from the raw body, not the code-blanked text.** Offsets
  are computed on `blank_code` output — they have to be, or a `#` inside a fence
  becomes a heading — and `blank_code` replaces code with spaces *of the same
  length*, so the offsets are valid in both strings. Hashing the blanked text
  would make an edit inside a fenced code block invisible to the incremental
  path: content changed, hash unchanged, section reported clean.

An empty section hashes the empty string
(`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) rather
than storing NULL. NULL on these columns means **"not a section node"** —
honest absence, the same distinction migration 3 argues for with
`title_method`. "This section has no text" is a fact, not an absence.

## The worked example

The document, verbatim, frontmatter included:

    ---
    title: Fasit H4
    ---
    # Alpha
    intro under alpha

    ## Beta

    ### Gamma
    gamma text with `inline code` here

    ## Delta > Epsilon
    delta text

    ```python
    # not a heading
    ```

    # Zeta

**Five headings, not six.** `# not a heading` sits inside a fence, so it is
blanked before `RE_HEADING` runs and is not a heading at all. Any implementation
that finds six has stopped using `blank_code`'s output for offsets.

| # | lvl | title | `heading_path` | leaf | own text | `content_hash` |
|---|---|---|---|---|---|---|
| 0 | 1 | `Alpha` | `["Alpha"]` | no | `"intro under alpha\n\n"` | `9343b1e136079d8b…` |
| 1 | 2 | `Beta` | `["Alpha","Beta"]` | no | `"\n"` | `01ba4719c80b6fe9…` |
| 2 | 3 | `Gamma` | `["Alpha","Beta","Gamma"]` | **yes** | ``"gamma text with `inline code` here\n\n"`` | `3076b1a92ecc5f64…` |
| 3 | 2 | `Delta > Epsilon` | `["Alpha","Delta > Epsilon"]` | **yes** | `"delta text\n\n```python\n# not a heading\n```\n\n"` | `5c1a67df39300844…` |
| 4 | 1 | `Zeta` | `["Zeta"]` | **yes** | `""` | `e3b0c44298fc1c14…` |

Full digests, since a truncated hash is not a key:

```
0 Alpha            9343b1e136079d8b2085b2dc01b1b19c14c7b92db8213252a7f67e0bb90158a6
1 Beta             01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b
2 Gamma            3076b1a92ecc5f6484e29cc395e5b33d6ce7a4bf933c5a7f2c9ee5273597b87d
3 Delta > Epsilon  5c1a67df39300844c2c21e5540c43f425c69d931eab437c7ed5f2c5f60911ab7
4 Zeta             e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### What each row is here to pin

* **0 vs 1** — a non-leaf's hash covers its own prose only. `Alpha`'s text stops
  at `## Beta`; it does not include Beta's or Gamma's.
* **1** — a heading with nothing but a blank line under it hashes `"\n"`, not
  the empty string. The blank line is text.
* **2** — inline code is *not* blanked in the hashed text. `` `inline code` ``
  appears verbatim, because the slice comes from the raw body.
* **3** — the title contains `" > "`. `body` is `"Alpha > Delta > Epsilon"`, and
  splitting that on `" > "` yields three parts where the truth is two. This row
  is the whole argument for R2's JSON list, and a test that only checks `body`
  cannot see the difference.
* **3 again** — the fenced block is inside the hashed text. Edit the code in it
  and this digest changes. That is R4's second consequence, measured.
* **4** — last heading, no text: leaf, and the empty-string digest.

## Predictions locked before implementing

1. On `~/wiki` (38 pages), **every section node gets a non-NULL `heading_path`
   and `section_leaf`**, and the count of section nodes is unchanged from the
   current build — H4 adds fields, it does not add or remove nodes. A changed
   count means the heading scan changed, which is a regression, not a feature.
2. **Leaf sections outnumber non-leaf ones** in that corpus. Stated as a
   falsifiable expectation, not a requirement: if it comes out the other way the
   number gets recorded and this line gets an argued correction, not a quiet
   edit.
3. `body` for a section changes from the bare heading to the breadcrumb, so
   **FTS gains parent-heading matches on child sections**. A search for a
   parent heading that previously returned only the file will now also return
   its child sections. This is the one measurable behaviour change in H4 and it
   is expected, not incidental.

## Addendum, 2026-08-01 — the sibling case the worked example could not see

Added the same day, with the reason, rather than edited in silently. The five
headings above contain **no two adjacent headings of the same level**: every
successor is either deeper or shallower. R3 says a heading is a leaf when the
next heading "is not deeper" — but on this document, comparing `level <=` and
comparing `level <` give identical answers for all five rows. The rule as
written is right; the example cannot tell it from a wrong one.

A second document, kept separate so the table above stays exactly what was
locked before the code:

    ## Sibling One
    first

    ## Sibling Two
    second

| # | lvl | title | `heading_path` | leaf | `content_hash` |
|---|---|---|---|---|---|
| 0 | 2 | `Sibling One` | `["Sibling One"]` | **yes** | `b5fe6e40dbcd834e…` |
| 1 | 2 | `Sibling Two` | `["Sibling Two"]` | **yes** | `480c2336b410f1ad…` |

```
0 Sibling One  b5fe6e40dbcd834e3a0af00a2b11992955d45954220bb444b18c7c7757fddca6
1 Sibling Two  480c2336b410f1ad5f8bf1b28944490255804b65350c527787e74ebdd511e3a4
```

Two things this pins that the first document cannot. **`Sibling One` is a
leaf** even though a heading follows it — under `level <` it would be reported
as a parent of its own sibling. And **`Sibling Two` is not a descendant of
`Sibling One`**: an ancestor stack that pops on `>` instead of `>=` leaves the
first sibling in place and yields `["Sibling One","Sibling Two"]`, which reads
as a subsection that does not exist.

## The three predictions, measured 2026-08-01 on `~/wiki`

43 markdown files, built twice into throwaway stores: once from the tree at
`b0697b6` (before H4 existed) and once from the working tree. Same file list,
same `as_of`, neither run touched the live store.

| | before | after |
|---|---|---|
| nodes | 225 | **225** |
| section nodes | 124 | **124** |
| sections with NULL `heading_path` / `section_leaf` / `content_hash` | — | **0 / 0 / 0** |
| non-section nodes carrying either column | — | **0** |

**Prediction 1 holds.** H4 added fields and not one node. Deepest ancestor
chain on this corpus is 3.

**Prediction 2 holds**, and by more than the wording claimed: **94 of 124
sections are leaves** (76 %), 30 are not.

**Prediction 3 holds, and the first attempt to measure it was worthless.** The
first probe counted FTS hits for one hardcoded word and returned 0 in *both*
trees — a number that cannot distinguish the two states is not evidence, and it
was recorded as unmeasured rather than as a refutation. The honest measurement
takes every section that has ancestors, picks a word from its **root** ancestor
that does **not** occur in its own heading, and asks whether FTS matches that
section on that word. **72 probes: 72 of 72 match after H4, 0 of 72 before.**
Same probe list fed to both trees, so the comparison is of the stores and not
of two different questions.

One case in this key does **not** occur in `~/wiki`: no heading there contains
the separator, so `with_separator_in_a_title` is 0. `Delta > Epsilon` is a
fixture-only case. It stays in the key — the rule it pins is about what the
stored form must be able to represent, not about how often the corpus needs it.

## Not covered by this key

* Whether section-level retrieval is *useful* — that needs H1's scoreboard and
  the section text in `body`, which decision 1 above defers.
* Setext headings (`===` / `---` underlines). `RE_HEADING` is ATX-only today,
  and H4 does not change it. A setext heading is invisible to the whole model,
  before and after.
* Duplicate heading titles at the same level in one file. They produce equal
  `heading_path` values and are distinguished only by the existing `path#i`
  key. Named here rather than discovered later.
