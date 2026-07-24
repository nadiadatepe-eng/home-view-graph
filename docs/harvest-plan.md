# homegraph — harvest implementation plan

Lånte ideer fra idé-høstingen 2026-07-24 (fire repoer: `yorukot/superfile`, `Cirilcetra/codegraph`, `colbymchenry/codegraph`, `codegraph-ai/CodeGraph`). Regel: lån idéen, bygg selv, krediter i README §Credits — aldri kopier kode.

**Dette er en levende tracker.** Statusen oppdateres per checkpoint. Hvert checkpoint har samme gate-form som CP-0…13 PLUSS en obligatorisk **sim-auditor-runde** før det regnes ferdig.

## Gate-form per checkpoint (invariant)

Et checkpoint er ikke ferdig før alle seks holder:

1. **Fasit før kode** — forventet svar skrevet før mekanismen, fra koden som *lager* svaret, ikke fra en formaning.
2. **Checks grønne** (`tests/test_hN.py`) — kjørt gjennom produksjonskalleren, ikke bare hjelperen ([[mechanisms-need-production-callers]]).
3. **Mutasjonstest** (`tests/mutate_hN.py`) — hver gate drepes av den *navngitte* gaten, 0 overlevende.
4. **Ekte-data-kjøring** — ni grønne checks erstatter ikke én kjøring på ekte korpus.
5. **sim-auditor-runde** — adversarisk gjennomgang (look-ahead, tom gate, duplisert invariant, ikke-deterministisk gate, om resultatet ser «for bra ut»). Funn lukkes eller dokumenteres før ferdig.
6. **ruff + mypy rene** · `dependencies=[]` urørt · kreditering skrevet.

---

## Statustavle

| CP | Idé | Kilde | Status | sim-auditor |
|----|-----|-------|--------|-------------|
| **H1** | Eval-først retrieval-scoreboard | codegraph-ai | ✅ instrument (baseline på ekte korpus → H3-tid) | 4 funn, alle lukket |
| **H2** | Inferert-etikett bærer konfidens | Cirilcetra · colby | ☐ planlagt | — |
| **H3** | Statiske embeddings + hybrid semantisk søk | codegraph-ai | ☐ planlagt | — |
| H4 | Markdown heading-tre for m3 | Cirilcetra · codegraph-ai | ☐ backlog | — |
| H5 | git co-change-kant + churn | Cirilcetra | ☐ backlog | — |
| H6 | fanIn/fanOut-sentralitet som 3. RRF-liste | Cirilcetra | ☐ backlog | — |
| H7 | Connect-time catch-up + staleness-banner | colby · codegraph-ai | ☐ backlog | — |
| H8 | Kryss-modell konsistens-query | codegraph-ai · Cirilcetra | ☐ backlog | — |
| H9 | Intent-MCP-verktøy m/ budsjett→item-tak | colby · Cirilcetra · codegraph-ai | ☐ backlog | — |
| H10 | Prompt-injeksjons-flagg på dok-chunks | codegraph-ai | ☐ backlog | — |
| H11 | N-Triples-eksport → Graphify-bro | codegraph-ai | ☐ backlog | — |
| H12 | Bi-temporal metadata (supersedér, ikke slett) | codegraph-ai | ☐ backlog | — |
| H13 | Alias→kanonisk-type + innhold-sniffing | superfile | ☐ backlog | — |

Symboler: ☐ planlagt · ⏳ pågår · ◐ kode ferdig, gate gjenstår · ✅ ferdig (alle seks) · ⊘ droppet.

---

## Invarianter fra høstingen (håndheves på tvers)

Anti-mønstrene bekreftet homegraphs valg — disse gjøres til eksplisitte gate-krav:

- **Hash innhold, ikke tellinger.** Enhver cache-/endrings-invalidator hasher innholds-digests (rullende XOR av per-node hash), aldri node/kant-*antall*. (= [[empty-gate-patterns]].)
- **Behold RRF.** Fusjon er rangbasert (`k`-param portabel), aldri vektet-sum med per-query max-normalisering (korpus-magiske tall).
- **Inferert = tagget.** Ingen LLM/gjettet etikett lagres som eksakt fakta; alle bærer `method`/`confidence`. (H2 gjør dette til en typet garanti.)
- **Aldri skann hele korpuset for vektorer.** FTS5 shortlister, cosine kjøres kun over unionen.
- **Ingen nett-nedlasting, ingen tung runtime.** «Modellen» er en datafil i prosjektet; inferens er stdlib-aritmetikk. `dependencies=[]` er hellig.
- **Tilpass verktøyet til agenten, ikke omvendt** — ny MCP-funksjon må la et verktøy agenten *allerede kaller* gjøre mer med input den *allerede gir*.

---

## CP-H1 · Eval-først retrieval-scoreboard  ⏳ PÅGÅR

**Idé (codegraph-ai `static-embeddings-plan.md`):** bygg måleinstrumentet FØR du rører embeddings. En merket retrieval-eval fra *gratis* kilder, med recall@k / MRR og en eksplisitt stopp-regel, slik at «hjalp semantisk søk?» kan besvares med et tall, ikke en magefølelse. Uten dette har H3 ingen fasit.

**Hvorfor først:** H3 (embeddings) er meningsløs uten en baseline å måle mot. Dette checkpointet er selv fasiten for H3.

**Fasit før kode:** en liten håndlaget gullmengde `(query → forventet node)` der svaret er kjent, pluss de auto-genererte parene, slik at recall-tallet kan verifiseres for hånd på minst 5 par.

### TODO
- [x] **`tests/eval/scoreboard.py` — metrikk-kjernen** (`recall_at_k`, `reciprocal_rank`, `evaluate`). Tom eval NEKTER (aldri vacuous 100 %); metrikk kan rapportere 0. Ren, korpus-uavhengig.
- [x] **`tests/test_h1.py` — known-answer** mot håndregnet fasit (16/16). Verifiserer at metrikken kan si NEI: alltid-bom → recall 0, tom eval → raise. Registrert i `pyproject` `python_files`.
- [ ] `tests/eval/build_eval.py` — generer `(query, forventet_node_id)`-par fra gratis-kilder over det syntetiske korpuset (filnavn-stamme→dok, markdown-heading→fil, EXIF→bilde). **Kritisk:** query og forventet-node må komme fra ULIKE signaler (ikke filnavn-query mot filnavn-søk — se sim-auditor).
- [ ] Kjør parene mot `search.hybrid_search`/`Mesh.search`, skriv FTS-baseline (recall@1/5/10 + MRR) — dette blir fasiten H3 skal slå.
- [ ] `tests/mutate_h1.py` — muter recall-telleren (av-for-én, alltid-treff, alltid-bom) → drept av navngitt gate.
- [ ] Ekte-data-kjøring: baseline på ekte hjem-korpus (gitignorert), tall i minne, ikke repo.

### sim-auditor-runde ✅ (07-24) — fire funn, alle lukket
Revisoren bekreftet at aritmetikken er ren, men fant at *parene* og *tallenes betydning* var skjeve:
1. **🔴 Verbatim body-lekkasje** — headingen er ordrett i fila, så r@10=1.0 er gitt og mrr=0.5 en strukturkonstant; ingen målerom for semantisk søk. **Lukket:** baselinen er nå ærlig merket «leksikalsk», og en håndlaget **parafrase-mengde** (`_PARAPHRASE`) beviser at instrumentet kan vise FTS bomme (r@10=0.00) — headroom-et H3 skal fylle.
2. **🔴 0 par på ekte fixture + `main()` returnerte exit 0** rundt tom-eval-vakten. **Lukket:** `main()` feiler nå med exit 2 på 0 par.
3. **🟡 Diskrimineringstest degenererte** ved delt fil. **Lukket:** byttet til kryss-fil-mismatch (r@10=0.00, ingen tilfeldig treff).
4. **🟢 `r@10==1.0`-tautologi.** Lukket (merket som leksikalsk sanity, subsumert av #1).

**Ærlig gjenstående begrensning (ikke en defekt):** heading→fil-kilden gir 0 par på det lille syntetiske fixturet (én heading per fil = tittel), og parafrase-headroom er 2 håndlagde par på mini-korpuset. Et *fullt* semantisk eval trenger flere håndlagde parafrase-par på det ekte korpuset — kjøres når H3 landes (det er ingenting å slå før da).

**Status (07-24): FERDIG som instrument.** `scoreboard.py` (metrikk, aritmetikk sim-auditor-ren) + `build_eval.py` (generator + lekkasje-vakter) + `test_h1.py` (22/22, known-answer + mini-korpus + headroom + diskriminering) + `mutate_h1.py` (6/6 drept av navngitt gate, 0 overlevende). ruff + mypy(pakke) rene. **Ekte-korpus-baseline utsatt til H3-tid.**

---

## CP-H2 · Inferert-etikett bærer konfidens  ☐ planlagt

**Idé (Cirilcetra D, colby #6):** enhver etikett som er *gjettet* — LLM-sammendrag, klyngenavn, en tvetydig-oppløst referanse — må tagges `method`/`confidence` på lik linje med kant-proveniensen, aldri lagres som eksakt fakta. homegraph har allerede `method`/`confidence` på kanter (CP-9); dette utvider garantien til node-etiketter og gjør den *typet*, ikke formanet.

**Fasit før kode:** en liste over hvert sted en etikett/subtype/tittel settes fra noe utledet, og forventet `method` for hver.

### TODO
- [ ] Kartlegg alle skrive-steder for node-`title`/`subtype`/etikett som stammer fra en heuristikk eller (planlagt) LLM.
- [ ] Utvid skjemaet: node-etiketter som ikke er direkte lest fra kilden får `label_method` + `label_confidence` (migrasjonskjede i `store.py`).
- [ ] Håndhev i typen: en skrive-vei for en inferert etikett kan ikke kalles uten `method` (påkrevd nøkkelordargument, som `upsert_edge`s `method`).
- [ ] `tests/test_h2.py` + `tests/mutate_h2.py` — en inferert etikett skrevet uten tag MÅ feile; en direkte-lest etikett MÅ ikke kreve tag.

### sim-auditor-runde
Let etter: **omgåelsesvei** (finnes en skrive-vei der en inferert etikett kan lagres som eksakt?), **tom gate** (sjekker gaten faktisk noe, eller er `method`-defaulten arvet stille?), **duplisert invariant** (samme regel to steder som kan drifte fra kant-proveniensen).

---

## CP-H3 · Statiske embeddings + hybrid semantisk søk  ☐ planlagt

**Idé (codegraph-ai, model2vec-mønsteret):** embedding = `tokeniser → slå opp token-rader i en vokab×dim-matrise → vektet mean-pool → L2-norm`. Ingen nevralt nett, ingen ONNX, ingen nett-nedlasting — «modellen» er en datafil distillert offline én gang. Semantisk søk uten å bryte `dependencies=[]`. Måles mot CP-H1.

**Gated av H1.** Bygges kun etter at scoreboardet gir en baseline å slå.

### TODO (delt i under-steg, hver med egen gate hvis nødvendig)
- [ ] **H3a — namespace + lager.** `embeddings`-tabellen (finnes, store.py:172) får `namespace = provider:model:dim`; ny `upsert_embedding()`; alle KNN filtrerer på namespace; modellbytte → re-embed. (Konvergent lån: Cirilcetra①②, superfile#5.)
- [ ] **H3b — statisk embedder.** `providers/static_embed.py`: last matrise+vokab (datafil, ikke nett), tokeniser med **identifikator-splitting** (`getUserById → get user by id`, camelCase/snake/kebab/punktum; +6 % recall, codegraph-ai#4), vektet mean-pool, L2-norm. Ren stdlib. Distillering er et *byggetids*-skript (Python/model2vec), ikke en runtime-avhengighet.
- [ ] **H3c — vektorsøk.** Fyll `search.vector_search` (search.py:106): **FTS5 shortlister → cosine kun over unionen → RRF**, aldri hele korpuset. Bevar `None` (kjørte ikke) vs `[]` (fant ingenting). `hybrid_search` fusjonerer alt.
- [ ] **H3d — config + CLI.** `[embeddings]`-avsnitt (Fase 0 fra integrasjonsplanen); fyll `embed`-kommandoen (cli.py:1191); nekter med exit 2 om datafila mangler.
- [ ] **Mål mot H1:** recall@k med statisk+FTS+RRF vs FTS-baseline. Stopp-regel: behold kun hvis den slår baseline.

### sim-auditor-runde
Pek revisoren på: **look-ahead / lekkasje** (deler distilleringens trenings-tekst H1-evalens test-par?), **fake-embedder-gate** (gates bruker en stub med faste vektorer — aldri ekte matrise, siden embeddings er ikke-deterministiske på tvers av maskiner), **`None` vs `[]`** bevart, **namespace-invalidering** (bytter man modell, blir gamle vektorer faktisk ignorert — eller returneres de stille?), **ingen hel-korpus-skann** (shortlister FTS faktisk før cosine, eller er unionen tom→full?), **datafil ikke nett** (ingen kodesti henter noe over nettet).

---

## Backlog (H4–H13) — rangert, planlegges når H1–H3 er ferdige

Hvert får full CP-form (fasit→checks→mutasjon→ekte data→sim-auditor) når det tas. Ett-linjes intensjon her:

- **H4 · Markdown heading-tre (m3):** node per løv-overskrift, brødsmule `heading_path` (H1>H2>H3), per-seksjon `content_hash` for inkrementell re-index. (Cirilcetra④, codegraph-ai#5.)
- **H5 · git co-change-kant:** `CO_CHANGED_WITH` fra `git log --numstat`, styrke = felles commits ≥3, churn som node-egenskap. Mekanisk kohort-proveniens uten embeddings. (Cirilcetra⑤.)
- **H6 · Sentralitet i RRF:** deterministisk fanIn/fanOut som node-egenskap, mates som tredje liste i fusjonen. (Cirilcetra⑦.)
- **H7 · Connect-time catch-up:** avstem `(size, mtime, hash)` ved MCP-/watch-oppstart — dekker vinduet daemonen var av; per-fil staleness-banner + `embedding_status`. (colby#1#3, codegraph-ai#12.)
- **H8 · Kryss-modell konsistens:** «notat nevner fil som ikke finnes» / «fil aldri nevnt» som spørring i det lukkede språket. (codegraph-ai#9, Cirilcetra⑩.)
- **H9 · Intent-MCP-verktøy:** et par intent-formede verktøy over de atomære, budsjett `small|medium|large` → hardt item-tak; kant-`confidence` som traverseringsvekt; `graphStats`-transparens. (colby, Cirilcetra⑥, codegraph-ai#8.)
- **H10 · Injeksjons-flagg:** `suspicious`-egenskap på dok-noder (needle-liste), flagg-ikke-blokkér, eksponer til konsument. (codegraph-ai#6.)
- **H11 · N-Triples-eksport:** `<node> <edge> <node> .`-linjer med redaksjonsnivåer; bro til planlagt Graphify-føderasjon. (codegraph-ai#10.)
- **H12 · Bi-temporal:** valid-time + transaction-time, supersedér uten å slette; passer m5 «føderer, aldri merge». (codegraph-ai#7.)
- **H13 · Klassifikator-forfining:** alias→kanonisk-type + «fullt navn slår extension»; innhold-basert tekst/binær-sniffing (flere blokker, ikke bare head). (superfile#1#2.)

---

_Rev. 1 · 2026-07-24. Oppdateres per checkpoint. Kilder krediteres i README §Credits når hver landes._
