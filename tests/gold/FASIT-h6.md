# CP-H6 answer key — centrality as the tie-break in the fusion

Written **2026-08-01, before a line of H6 code existed**, and **revised
2026-08-02 after measuring against the real mesh.** Same rule as the other keys
here: **a value is never changed to make a test pass.** A disagreement is
resolved by changing the code, or by arguing for the change here, with a date.

This key has been argued for once. The revision is § "What the real mesh said",
and it changes what H6 *is* — not a number in it.

## What H6 is, and the measurement that shaped it

The plan says: "deterministic fanIn/fanOut as a node property, fed as a third
list in the fusion."

Read literally, that is a **query-independent** list, and the real mesh says
what that would do. Measured 2026-08-01 on `~/.homegraph/real-mesh.db`:

```
9 125 nodes · 1 342 edges
8 621 nodes (94.5 %) have no edge at all
highest fanOut  86   a memory file of code decisions
highest fanIn   69   one project's entry-point module
```

With `RRF_K = 60`, position 1 in any list contributes `1/61`. A global
centrality list therefore hands that entry-point module exactly as much as a
model's own best match -- **in every search, whatever was asked.** 94.5 % of the
list would be tied at zero behind it. That is not a ranking signal; it is a
fixed result injected into every query.

**Decision, 2026-08-01: centrality touches only the candidates the models
already returned.** It reorders; it cannot introduce. A central document that
does not match the query never enters.

**Second departure from the plan text: centrality is NOT stored as a node
property.** It is counted from `mesh.db`'s edges at query time. A stored degree
is a number that goes stale the moment an edge is written, and this package
spent 2026-08-01 learning what a newly-filled column does to whoever keys on it.
Nothing reads a degree except this fusion, and it needs it for at most `limit`
nodes.

## What the real mesh said — revision 2026-08-02

The first implementation fed centrality as a **fourth RRF list**. It passed its
own gate 23/23 and then failed all three predictions below on real data:

| Prediction | Result over 20 queries |
|---|---|
| most queries do not change order | **failed** — 1 of 20 unchanged |
| the ones that move have a candidate several notes cite | partial — 42 of 115 candidates have degree > 0 |
| nothing absent before appears after | **failed** — new keys in 7 of 20 |

**The reason is arithmetic in the fusion, not a defect in the code.** With
`RRF_K = 60` one list contribution at position 1 is worth `1/61 = 0.016393`,
while the distance between two adjacent positions is
`1/61 − 1/62 = 0.000264`. **One extra contribution outweighs about 63 positions
of separation.** Any non-empty centrality list is therefore decisive for every
candidate it touches. Measured: even a list holding a *single* entry left 5 of
20 queries unchanged and still introduced new hits in 2. Dropping degree-0
candidates, and returning an empty list when no candidate has an edge, both give
3 of 20. No bounded variant reaches the predictions.

**Prediction 1's stated reasoning was wrong, and this is the sentence that was
wrong:** *"a set of candidates that are all tied at zero is reordered by fusion
key -- which is the order they already had."* It is not. A zero-degree candidate
still occupies a **position** in the list, and a position is worth `1/(60+r)`
whatever the degree that earned it. The order those positions are handed out in
is `model::node_key` ascending, which is not the fused order and has nothing to
do with the query.

**Therefore, from 2026-08-02, centrality is the tie-break — not a list.** It is
applied after fusion, and it replaces the alphabetical tie-break `_rrf` already
had. This is the only form that satisfies the 2026-08-01 decision above: it
cannot introduce, cannot remove, and cannot move a candidate past one that
scored differently.

It is not a no-op. Measured on the same 20 queries: **10 of 20 have score ties
inside the top 10, 23 tied positions in total.** Half the queries have something
for it to decide, and today that decision is made alphabetically.

## The three rules, as they now stand

**R1 -- centrality is `fanIn + fanOut`,** counted over `mesh.db`'s `edges`
table: rows where the node is `dst`, plus rows where it is `src`. Deterministic
and non-iterative on purpose -- no PageRank, nothing that needs a convergence
threshold nobody can justify.

**R2 -- it orders hits that the fusion scored EQUALLY, and nothing else.**
The sort key becomes `(-score, -degree, key)`. `key` stays last so that two
candidates of equal score *and* equal degree cannot swap between runs of
identical code. A candidate the models did not return has no score and is not
sorted at all, whatever its degree.

**R3 -- no mesh, no tie-break.** When `mesh.db` is absent the fusion sorts as it
did before and the result reports centrality **absent**, not that every node
scored zero. Same distinction `code_inventory` and `co_change` already make. A
mesh that exists but cannot be read is the same answer: centrality is optional,
and an optional reordering must not turn a working query into a failed one.

**R4 -- a document seen by several models takes the HIGHEST of their degrees,
not their sum.** Added 2026-08-02; the rule lived only in the code until an
audit pointed out that the gate asserted a number the key never fixed. Each
model's copy is its own node in `mesh.db` with its own edges, so summing is
arithmetically available -- and wrong, because the hit already carries one RRF
contribution per model and the sum would count the same popularity twice.
Worked example below: `Q` has two edges under `m1` and four under `m3`, and its
degree is **4**, not 6 and not 2.

**R5 -- the report is the number of positions that MOVED**, counted inside the
returned window, or `absent` when there was no mesh. Also added 2026-08-02 for
the same reason. Not the number of candidates ranked: that number is nonzero on
every query with a mesh, so it would say "centrality did something" when it did
nothing. Worked example below: **2**.

## The worked example

Two models return the same two candidates in opposite orders, which is how a
score tie actually arises. A third candidate sits alone at `m3` rank 3, and a
fourth node `D` exists in the mesh with degree 99 and is **not** a candidate.

| node | m1 rank | m3 rank | degree |
|---|---|---|---|
| `P` | 1 | 2 | 0 |
| `Q` | 2 | 1 | 4 |
| `R` | — | 3 | 99 |
| `D` | — | — | 99 |

Scores, `RRF_K = 60`, full precision:

```
P   1/61 = 0.016393  +  1/62 = 0.016129  =  0.032522
Q   1/62 = 0.016129  +  1/61 = 0.016393  =  0.032522
R                       1/63 = 0.015873  =  0.015873
```

`P` and `Q` are **exactly** equal -- the same two contributions in the other
order -- which is how a tie arises in practice: two models each put a different
document first. `R` is not tied with anything.

| | order |
|---|---|
| before H6 | `P`, `Q`, `R` |
| after H6 | **`Q`, `P`, `R`** |
| `D` | absent from both |

### Values locked here, read by the gate rather than restated in it

| name | value |
|---|---|
| `Q`-degree | 4 |
| positions-moved | 2 |

`Q`-degree is R4 made concrete: two edges under `m1`, four under `m3`, and the
answer is the highest. positions-moved is R5: `P` and `Q` swap, so two
positions in the returned window change occupant. Both were asserted by the
gate before they were written down here, which is the wrong order and was
corrected 2026-08-02.

### What each row is here to pin

* **`Q` overtakes `P`, and only because their scores are equal.** Before H6 the
  tie went to `P` on `path:/P` < `path:/Q` -- alphabet, not evidence. This is
  the whole behaviour change.
* **`R` stays last though its degree is 99.** It scored lower, and a tie-break
  may not cross a score boundary. Under the fourth-list design its position is
  not guaranteed but arithmetic, and the margin depends on which variant:

  | design | `R` total | `P` total | `R` short by |
  |---|---|---|---|
  | full fourth list, all three ranked | 0.032266 | 0.048395 | 0.016130 |
  | list that drops degree-0 candidates | 0.032266 | 0.032522 | 0.000256 |

  Both leave the order `Q, P, R`. An earlier revision of this key gave only
  `0.000256` and attributed it to "the fourth-list design", which is the
  second row, not the first -- corrected 2026-08-02 after an audit recomputed
  all three columns. **Under a list `R`'s position depends on numbers nobody
  stated; under a tie-break it cannot move at all**, and that is the reason to
  prefer the tie-break, not the margin.
* **The scores themselves are what tell the two designs apart.** A tie-break
  leaves `0.032522`, `0.032522`, `0.015873` untouched. Any design that folds
  degree into the score changes all three, whatever the resulting order looks
  like. That is the check to trust here, and the one the mutation aims at.
* **`D` is absent, and it has the highest degree in the store.** An
  implementation that ranks all nodes puts `D` first on this query and on every
  other one.
* **`P` is not dropped for having degree 0.** Degree 0 is a position in the
  order, not a reason to disappear -- 94.5 % of the real store is degree 0.

## Predictions, restated 2026-08-02

The first three were locked before implementing and two of them failed. These
replace them, and they are testable properties of the tie-break design rather
than guesses about data:

1. **No candidate moves past one with a different score.** By construction,
   and it is what "tie-break" means. Checked directly on the scores, not
   inferred from the order.
2. **No result that was absent before appears after** -- *measured, not
   guaranteed.* The tie-break cannot make a candidate out of something no model
   returned, but when a tie **straddles the `limit` cutoff** it decides which
   of the tied hits falls inside the window, and from outside that looks like a
   hit appearing. Found by codex 2026-08-02; "by construction" was written here
   first and was too strong. Measured on the real mesh: 0 of 20 queries. The
   gate pins the straddling case directly instead of trusting the claim.
3. **Something does move -- at least 2 of the 20 queries in
   `tools/h6_real_mesh.py`.** The number is there so the prediction can fail:
   "something moved" is satisfied by one query out of twenty and would not
   notice a regression. Measured 2026-08-02: 3 of 20, two positions each. The
   queries are in the repo because an audit pointed out that every count in
   this key was unreproducible without them.
4. **A query whose candidates are all degree 0 is unchanged**, because the
   tie-break then has nothing to say and `key` decides as before.

## Not covered by this key

* **Whether centrality IMPROVES retrieval.** This fixes what centrality *does*,
  not whether it helps. That needs H1's scoreboard and a labelled set.
* **Degree as a stored property**, deferred with the reason above.
* **Weighting the two directions differently.** fanIn and fanOut are summed;
  whether being cited should count for more than citing is a question with a
  real answer and no evidence here yet.
* **An epsilon for "equal".** The tie-break fires on exact float equality.
  With two lists a permuted rank pair is always bit-identical, because IEEE
  addition is commutative. With three or more it is not, because addition is
  not associative: over rank pairs 1..20, 1 088 of 6 840 mathematically tied
  three-list sums (16 %) differ by an ulp and would not be seen as tied.
  Measured 2026-08-02 on the real mesh: **0 adjacent pairs an ulp apart**,
  because the models partition the corpus and multi-model hits are rare. It
  goes live the day one file is indexed by two models. Found by audit; left as
  exact equality rather than fixed silently, because an epsilon is a new
  constant that merges scores that are genuinely different.
