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
| **H2** | Inferert-etikett bærer konfidens | Cirilcetra · colby | ✅ ferdig (skrive + lese-side) | 5 funn: 3 lukket, 2 backlog |
| **H3** | Statiske embeddings + hybrid semantisk søk | codegraph-ai | ✅ mekanisme bevist + ekte matrise (potion-multilingual) kjørt mot stopp-regel: embeddings forblir opt-in (default-søk regredierer), semantisk verdi bekreftet kvalitativt | 6 funn: F1/F4 lukket, F5/F6 herdet, F2/F3 rammet inn |
| **H4** | Markdown heading-tre for m3 | Cirilcetra · codegraph-ai | ✅ ferdig (`ae1f276`) | kjørt |
| **H5** | git co-change-kant + churn | Cirilcetra | ✅ ferdig (`f3ea1b3`) | kjørt |
| **H6** | fanIn/fanOut-sentralitet som **uavgjort-bryter** | Cirilcetra | ✅ ferdig (`0f9dcb7`) | 4 funn, alle lukket |
| **H7** | Connect-time catch-up + per-treff staleness | colby · codegraph-ai | ✅ ferdig | 11 funn, alle lukket |
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

### sim-auditor-runde ✅ (07-24) — fem funn: skrive-siden ren, lese-siden manglet
Revisoren bekreftet at skrive-garantien er låst (confidence slås opp fra method, ukjent method avvist, m3 klassifiserer korrekt), men fant at **H2 først bare leverte HALVE garantien**:
1. **🔴 Lese-siden fantes ikke** — `title_confidence` ble skrevet, men INGEN konsument leste den; en `inferred` tittel ble servert til en agent identisk med en `declared`. En proveniens ingen leser kan ikke stoppe en sitering ([[mechanisms-need-production-callers]]). **Lukket:** surfacet gjennom `fts_search` OG `mesh_search` (MCP-stien) — konsumenten ser nå `title_confidence`.
2. **🟡 Mesh-mirror laundret den til NULL** — føderasjonen MCP serverer droppet `title_method`. **Lukket:** mirror bærer den nå videre.
3. **🟡 Round-trip tapte den stille** (ikke i `NODE_COLUMNS`, cp12-ekvivalens så ikke tapet). **Lukket:** lagt til `NODE_COLUMNS` + importer bevarer den.
4. **🟢 Dekningshull (backlog):** m4 format-deteksjon (magic-bytes-gjetning) og rollup «mostly %s» er ekte gjetning-som-etikett, ikke dekket. → egen fremtidig CP (utvid `inferred` til m4). Ikke en regresjon; m3-tittelen var den avgrensede første kalleren.
5. **🟢 `provenance_note` er hardkodet til `EDGE_METHODS`** — ikke gjenbrukt naivt for titler (ville gitt confidence 0.0); surfacet `title_confidence` direkte i stedet. Hensyntatt.

**Status: FERDIG (skrive + lese-side).** `store.py` (TITLE_METHODS, migrasjon 3, upsert-validering) · `m3_markdown.py`/`m3_build.py` (declared/verbatim/inferred) · `mesh.py`+`search.py`+`mcp_server.py` (surface) · `export.py`+`importer.py` (round-trip). test_h2 **9/9**, mutate_h2 **6/6 drept 0 overlevende**, full suite grønn (cp6/cp8/cp12 tålte migrasjonen), ruff+mypy(pakke) rene.

---

## CP-H3 · Statiske embeddings + hybrid semantisk søk  ✅ FERDIG (mekanisme, syntetisk)

**Idé (codegraph-ai, model2vec-mønsteret):** embedding = `tokeniser → slå opp token-rader i en vokab×dim-matrise → vektet mean-pool → L2-norm`. Ingen nevralt nett, ingen ONNX, ingen nett-nedlasting — «modellen» er en datafil distillert offline én gang. Semantisk søk uten å bryte `dependencies=[]`.

**Bygget (07-24):**
- **H3a — namespace + lager** (`store.py`, seksjon `-- embeddings --`): `upsert_embedding()`, `embedding_count()`, `read_embeddings()`. Vektorer lagres float32 (`array`), og HVER lesning filtrerer på `namespace = (provider, model, dim)`. Modellbytte → 0 rader i nytt namespace → ærlig `None` («re-embed»), aldri gamle vektorer stille servert.
- **H3b — statisk embedder** (`providers/static_embed.py`, ny pakke): identifikator-splitting (`getUserById → get user by id`, camel/acronym/snake/kebab/punktum), vektet mean-pool, L2-norm. `load()` leser en datafil, `from_config()` nekter ukjent provider og validerer config-modell/dim mot matrisefila. Ren stdlib, ingen nett.
- **H3c — vektorsøk** (`search.py`): `vector_search` = **OR-FTS shortlist → cosine kun over unionen → rangert**, aldri hele korpuset. `hybrid_search` fusjonerer FTS+vektor via RRF (`out_mode` blir `hybrid`). `None` (kjørte ikke) vs `[]` (kjørte, tomt) bevart gjennom hver exit. Embedderen sendes INN (search leser aldri config).
- **H3d — config + CLI** (`userconfig.py` + `cli.py`): `[embeddings]`-avsnitt (av som standard); `embed`-kommandoen (config-drevet, exit 2 om blokk mangler / datafil mangler / store mangler); `search --embeddings <matrise>` (eksplisitt flagg — search leser fortsatt bare en db-sti).

### Gate
Fasit-først `test_h3.py` (32/32: tokenizer + embed-for-hånd + namespace-round-trip + **cosine slår OR-BM25** + ingen-skann + None/[] + namespace-invalidering + datafil-ikke-nett + embed-kommando) → `mutate_h3.py` (**8/8 drept av navngitt gate, 0 overlevende**) → full suite grønn (cp0–13, h1, h2, no_real_paths: 18/18) → mypy(pakke) ren → ruff (F,E9) ren → personvernvakt 12/12. `test_h3.py` lagt eksplisitt i `pyproject` `python_files`.

### sim-auditor-runde ✅ (07-24) — seks funn
**F1 🔴 (HIGH, lukket) — «semantisk slår leksikalsk» testet ikke cosine.** Første fixture hadde target med FLERE delte FTS-termer enn decoy (`lists`≠`list`, ingen stemming), så ren OR-BM25 rangerte target først uansett; `_cosine → 0.0` lot suiten stå grønn. **Gjenbrukbar lærdom:** en gate som PÅSTÅR å teste en komponent må ha komponentens svikt til å velte resultatet. Lukket ved å bygge fixture der BM25-orden og cosine-orden er UENIGE (decoy deler flere termer, men er fortynnet off-topic), måle mot **OR-BM25** (den sterkeste leksikalske motstanderen, ikke AND-FTS), og legge til en `_cosine→konstant`-mutasjon som nå velter gaten. = [[empty-gate-patterns]] (en gate som ikke kan felle den komponenten den navngir).
**F4 (lukket)** — mutasjons-hull, korollar av F1: ingen mutasjon slo av cosine. Nå finnes den, og drepes.
**F2 (rammet inn)** — baselinen var strengt AND-FTS; OR-BM25 er nå den eksplisitte motstanderen, så «headroom» tilskrives cosine, ikke AND→OR-recall.
**F5 (herdet)** — nett-vakten fanget bare import-linjer; nå også `__import__`/`importlib`/`urlopen`/`socket`. Fortsatt kun over provider-fila, ikke hele kallgrafen — en vakt mot endringen som legger til henting, ikke et bevis.
**F6 (kommentert)** — shortlist-node uten vektor i namespace hoppes over; hvis ALLE hoppes (delvis dekning) → `[]` med `out_mode=hybrid`. Ærlig, og uoppnåelig ved full embed. Kommentert i `search.py`.
**F3 (ærlig ramme)** — hva som FAKTISK er bevist: mekanismen er koblet ende-til-ende, cosine-rerank slår OR-BM25 på et syntetisk fixture, og alle ærlighetsgarantiene (None/[], namespace, ingen skann, ingen nett, tom-eval) holder. **Ikke** bevist: at ekte distillerte vektorer på det EKTE korpuset slår baselinen — det er `evaluate` over 1 par på 3 håndlagde filer med en håndlagd matrise.

### Ekte matrise + stopp-regel — KJØRT (2026-07-24)
Byggetids-verktøyet `tools/distill_matrix.py` (model2vec via `uv`, IKKE en runtime-avhengighet) distillerte `minishlab/potion-multilingual-128M` (pre-distillert, MIT, flerspråklig — korpuset er NB+EN) til en **ord-nivå matrise over korpus-vokabularet**: 17 459 ord × 256 dim (lokalt artefakt, ~45 MB, committes ikke). Ekte m3-store bygget over hjemmekatalogen (`$HOME`, 602 filer, 6035 seksjoner, 6928 noder), embed på ~11 s.

**Stopp-regel mot H1-scoreboardet** (`tools/eval_real.py`, 2269 lekkasje-vaktede heading→fil-par, fil-nivå):

| metode | r@1 | r@5 | r@10 | mrr |
|--------|-----|-----|------|-----|
| AND-FTS | **0.709** | 0.887 | 0.928 | **0.787** |
| OR-BM25 | 0.428 | 0.672 | 0.744 | 0.534 |
| vektor | 0.115 | 0.267 | 0.353 | 0.186 |
| hybrid | 0.639 | 0.865 | 0.922 | 0.736 |

**Verdikt:** på et LEKSIKALSK eval (headingen står ordrett i fila) dominerer AND-FTS, og hybrid *regredierer* den (RRF-støy). Stopp-regelen sier derfor: **ikke gjør vektor/hybrid til default-søk.** Det er akkurat slik pakken shipper — embeddings AV som standard, `search --embeddings` opt-in.

**Men verdien finnes, på spørringene den er FOR** (kvalitativ probe, ikke statistikk fordi parafrase-par ikke kan auto-merkes uten sirkularitet): parafrasen «how an agent remembers things between sessions» ga AND-FTS bare *Changelog*, mens vektor-topptreffet var **`living-memory-architecture`** — en blink AND-FTS bommet fullstendig på. NB-spørringer og trading-parafraser som AND-FTS returnerte *ingenting* på, ga vektor topikalt plausible treff. **Begrensning (bevisst):** OR-FTS-shortlisten kan ikke hente ved NULL leksikalsk overlapp (en ren-NB-spørring ga tomt) — prisen for aldri å skanne hele korpuset. Full-ANN ville løst det, men brøt `ingen-hel-korpus-skann`-invarianten.

### Kryss-språk merket og målt — CP-X/CP-X2 (2026-07-27)

Luken under er lukket for kryss-språk-halvdelen. Ti håndmerkede NB→EN-par mot samme
ekte store, med begge egenskaper (null overlapp / nøyaktig ett anker) etterprøvd gjennom FTS-indeksen
selv (som dekker tittel også) før scoring. Alle fire hentere pluss et **tak** (cosinus over hele
korpuset, ikke shippet) er målt.

| sett | AND-FTS | OR-BM25 | shippet | tak | takets rang av 602 |
|---|---|---|---|---|---|
| uten overlapp (n=8) | 0.000 | 0.000 | 0.000 | mrr 0.003 | 47, 358, 313, 53, 264, 60, 196, 477 |
| ett anker (n=2) | 0.000 | mrr 0.042 | mrr 0.125 | mrr 0.100 | 5, 228 |
| kun ord matrisa har (n=3) | 0.000 | 0.000 | 0.000 | mrr 0.028 | 68, 16, 45 |

Tre prober, tre ulike årsaker til null, hver på sin målte styrke: **justeringen viser
signal** (oversettelsespar i matrisa: cosinus 0.724 mot 0.021 for tilfeldige ord, 9 av 12
par, 2 identiske), **dekningen er en stor skranke men ikke isolert** (49 % av spørreordene
mangler fordi matrisa er destillert over korpusets 89 % engelske vokabular; spørringer
bygget kun av ord matrisa har lander på rang 68/16/45 mot median ~230 — men det er andre
spørringer, ikke de samme med OOV kontrollert), og **selv da nås ikke r@1**, uten at det er
målt hvorfor. Den smale konklusjonen som holder: kortlista er ikke den bindende skranken på
ekte data, siden taket som ignorerer den også feiler.
**Neste steg, hvis tråden tas videre:** destillér over et vokabular som inkluderer
spørrespråket. Én endring i `tools/distill_matrix.py`, deretter ny måling.

**Konklusjon:** mekanismen er validert ende-til-ende på ekte data og ekte vektorer, og korrekt gated AV som standard. Den ekte matrisen bekrefter designvalget (opt-in), ikke at hybrid bør være default. Den ærlige gjenstående luken er et *merket* parafrase/kryss-språk-evalsett — samme lærdom som synteten ga. Lærer/dim kan byttes trivielt via `--model` i distill-verktøyet.

---

## Backlog (H4–H13) — rangert, planlegges når H1–H3 er ferdige

Hvert får full CP-form (fasit→checks→mutasjon→ekte data→sim-auditor) når det tas. Ett-linjes intensjon her:

- **H4 · Markdown heading-tre (m3):** node per løv-overskrift, brødsmule `heading_path` (H1>H2>H3), per-seksjon `content_hash` for inkrementell re-index. (Cirilcetra④, codegraph-ai#5.)
- **H5 · git co-change-kant:** `CO_CHANGED_WITH` fra `git log --numstat`, styrke = felles commits ≥3, churn som node-egenskap. Mekanisk kohort-proveniens uten embeddings. (Cirilcetra⑤.)
- **H6 · Sentralitet i RRF:** deterministisk fanIn/fanOut, telt ved spørringstid. **Ikke** som node-egenskap og **ikke** som tredje liste — begge deler ble målt og forkastet 2026-08-02; se `tests/gold/FASIT-h6.md`. Den bryter uavgjort. (Cirilcetra⑦.)
- **H7 · Connect-time catch-up:** avstem `(size, mtime)` ved MCP-/watch-oppstart. Banneret er **per treff, ikke per korpus** — et korpusbanner ville fyrt på hver spørring for alltid (2 628 borte stier i m3 alene). `embedding_status` utledes av fila, fordi `embeddings` ikke har noen `content_hash`; se `tests/gold/FASIT-h7.md` R5 for de tre blindsonene. (colby#1#3, codegraph-ai#12.)
- **H8 · Kryss-modell konsistens:** «notat nevner fil som ikke finnes» / «fil aldri nevnt» som spørring i det lukkede språket. (codegraph-ai#9, Cirilcetra⑩.)
- **H9 · Intent-MCP-verktøy:** et par intent-formede verktøy over de atomære, budsjett `small|medium|large` → hardt item-tak; kant-`confidence` som traverseringsvekt; `graphStats`-transparens. (colby, Cirilcetra⑥, codegraph-ai#8.)
- **H10 · Injeksjons-flagg:** `suspicious`-egenskap på dok-noder (needle-liste), flagg-ikke-blokkér, eksponer til konsument. (codegraph-ai#6.)
- **H11 · N-Triples-eksport:** `<node> <edge> <node> .`-linjer med redaksjonsnivåer; bro til planlagt Graphify-føderasjon. (codegraph-ai#10.)
- **H12 · Bi-temporal:** valid-time + transaction-time, supersedér uten å slette; passer m5 «føderer, aldri merge». (codegraph-ai#7.)
- **H13 · Klassifikator-forfining:** alias→kanonisk-type + «fullt navn slår extension»; innhold-basert tekst/binær-sniffing (flere blokker, ikke bare head). (superfile#1#2.)

---

_Rev. 1 · 2026-07-24. Oppdateres per checkpoint. Kilder krediteres i README §Credits når hver landes._
