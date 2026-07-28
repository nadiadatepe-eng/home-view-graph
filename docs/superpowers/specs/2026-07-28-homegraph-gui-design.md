# homegraph GUI — designdokument

**Dato:** 2026-07-28
**Status:** godkjent i brainstorming, ikke bygget
**Utgangspunkt:** `main` = `d6a00a8`

---

## Formålet, og hva det utelukker

**GUI-et finnes for å utforske korpuset uten å vite hva du leter etter.**

Det er den ene oppgaven CLI-en ikke dekker: `homegraph search` krever at spørringen
formuleres først. Alt i dette designet er valgt for det formålet, og der et valg kunne gått
begge veier, vant den varianten som gir en inngang framfor den som gir en tom flate.

Tre formål ble **vurdert og forkastet** som hovedformål, og skal ikke smugles inn senere
uten at denne seksjonen skrives om:

- etterprøve hvorfor et treff rangerte (FTS-rang mot cosinus mot RRF side om side)
- se om et treffsett henger sammen — dekkes delvis av skjematikkruta, men er ikke drivende
- være en raskere inngangsdør til det CLI-en alt gjør

Et GUI som bare eksponerer `search`, `query`, `visualize` og `mcp` på nytt er dekorasjon.
Denne specen er skrevet for å unngå det.

## Flaten

Fire ruter. Horisontalt 20–60–20, midtkolonnen delt 50/50 vertikalt.

```
┌────────────┬───────────────────────────────┬────────────┐
│ h1         │ v1  graf (kraftlayout)        │ h3         │
│            │                               │            │
│ søkeboks   ├───────────────────────────────┤ treffliste │
│ + alt.     │ v2  skjematikk                │            │
│            │     (broer mellom treffene)   │            │
│ sammendrag │                               │            │
│ per filtype│                               │            │
└────────────┴───────────────────────────────┴────────────┘
```

**v1 og v2 deler utvalg.** Én klikket node, to visninger. De to rutene kjenner ikke
hverandre; begge leser samme felt.

## Arkitektur

`Server`-metodene i `mcp_server.py` returnerer allerede rene JSON-klare dict-er — `_text()`
pakker dem i MCP-form først i `handle()`. Svarlaget er altså ikke bundet til MCP; det er
bare aldri blitt kalt fra noe annet. GUI-et blir en **andre transport over samme svarlag**,
ikke et andre svarlag.

```
homegraph/gui.py            NY, ~200 linjer. Ingen svarlogikk.
    serve(...)              http.server i forgrunnen, til Ctrl-C
    _Handler                rutetabell → Server-metoder → json.dumps
homegraph/assets/gui.html   NY, pakkedata: `assets/*.html`
homegraph/mcp_server.py     URØRT — Server-instansen gjenbrukes som den er
homegraph/visualize.py      URØRT — blir stående som offline-artefakten
```

Siden ligger i `assets/`, ikke i en `gui/`-katalog: `homegraph/gui.py` og `homegraph/gui/`
kan ikke være søsken i samme pakke. `[tool.setuptools.package-data]` utvides fra
`rules/*.toml` til også `assets/*.html`.

### Rutetabellen

| Rute | Kaller | Fyller |
|---|---|---|
| `POST /search` | `Server.mesh_search` | h3 treffliste + uthevingen i v1 |
| `POST /query` | `Server.query` | det avanserte søkealternativet i h1 |
| `POST /path` | `Server.mesh_path` (løkket, tak = søkegrensen) | v2 skjematikk — broene |
| `POST /neighbors` | `Server.mesh_neighbors` | v2 fallback når ingen sti finnes |
| `GET /graph` | `visualize.collect` | v1, filnivå, **kun ved oppstart** |

`GET /graph` kalles én gang. Filtrene i h1 demper og skjuler noder i siden; de utløser
**aldri** et nytt kall eller en ny layout. Det er samme egenskap som gjør at søk ikke
flytter noder: posisjonen skal bety det samme gjennom hele økten.

Ingen ny avhengighet: `http.server`, `json` og `webbrowser` er stdlib.
**`dependencies = []` overlever.**

### Den bærende regelen

**Siden tegner, Python bestemmer.** Filtrering til filnivå, hvilke noder som er isolerte,
hvilke treff som ligger i båndet, hvilken sti som vant — alt regnes ut i Python og sendes
ferdig. Nettleseren får ingen avgjørelser.

Det er samme regel `visualize.collect` allerede følger for `link`-flagget: *«Decided here,
in Python, so it is under test — the page only turns the flag into a URL.»* Det er også hele
grunnen til at GUI-et kan portes med de vanlige testene.

## Kjøreform

`homegraph gui` starter en **forgrunnsserver** som dør på Ctrl-C. Ingen daemon, ingen
tjeneste som overlever terminalen.

Det er samme form som `watch.py`, der codegraphs daemon bevisst ikke ble lånt. Alternativet
— en statisk HTML-fil — ble forkastet fordi FTS5 og cosinus ikke finnes i nettleseren:
søket måtte da reimplementeres i JavaScript, og to søkeimplementasjoner kan svare forskjellig
på samme spørsmål. README §Credits navngir nettopp den feilen som grunnen til at
`CITES_CODE` ikke leser kildekode.

## Tilstand og dataflyt

Tre felt, i nettleseren:

```
filter     hvilke partisjoner/subtyper er på       (h1)
search     siste spørring + resultatet             (h1 → h3)
selection  én klikket node-key                     (v1 eller h3 → v2)
```

Én vei, ingen sykler:

```
h1 filter ──┐
            ├──► POST /search ──► h3 treffliste
h1 søk  ────┘                └──► v1 uthever treffene (ingen ny layout)

v1 klikk ───┐
            ├──► selection ──► POST /path ──► v2 skjematikk
h3 klikk ───┘
```

`/path` løkkes i `gui.py`, ikke i nettleseren: ett kall inn med `src` og de andre treffenes
node-keys, N kall til `Server.mesh_path` inne i håndtereren, ett svar ut. `mcp_server.py`
forblir urørt og siden slipper å orkestrere.

## Rutene, én for én

### h1 — søk og sammendrag

Inngangen, ikke pynt. Sammendraget per filtype og partisjon er det du starter fra når du
ikke har en spørring. Søkealternativene er de som finnes: hybrid (`mesh_search`) og det
lukkede språket (`query`).

### v1 — grafen

**Filnivå.** Målt: 2 472 filnoder (M1 73 dokumenter, M2 183 bilder, M3 602 markdown,
M4 1 053 filer, 561 kodestubber). Seksjoner, tagger og lenkestubber er **ikke** med — 13 300
seksjonsnoder ville gjort ruta til grøt.

**De isolerte parkeres i et bånd** langs bunnen, sortert, med antall og andel. Kraftlayouten
kjører bare på det sammenhengende. Grunnen: en isolert node har ingen informasjon i
posisjonen sin, så en tilfeldig posisjon tegner støy som ser ut som data. Båndet gjør
`md gaps`-funnet — 315 av 602, 52,3 % — til noe som ses framfor et tall.

**Ved søk: uthev treffene, dim resten, ingen ny layout.** Layouten regnes én gang ved
oppstart og røres aldri. Det er det som gjør at posisjonene betyr det samme før og etter et
søk, og det er den egenskapen `visualize.py` alt er skrevet for: *«the same graph produces
the same picture twice — that matters for a visualisation you are going to compare against
last week's.»* En ny layout per søk kaster den.

### v2 — skjematikken

**Broene mellom treffene.** Stiene fra den klikkede fila til de *andre* søketreffene. Alt
som ikke ligger på en sti mellom to treff, tegnes ikke.

Det er den eneste lesningen der «aktiveres først etter et søk» betyr noe — et naboskaps- eller
innholdstre ville fungert like godt uten søk, og da er begrensningen vilkårlig. Broene svarer
på det en treffliste faktisk reiser: *er disse fem funnene én sak eller fem uavhengige?*

**Fallback:** finnes ingen sti, tegnes naboskapet til den klikkede noden i stedet — valgt
fil i midten, innkommende venstre, utgående høyre, deterministisk sortert.

**Filnivå, ingen kodeinnmat.** Dokumenter og filer, ikke funksjoner og klasser.

**Porten har to betingelser.** `search` ikke-tom **og** `selection` satt. Er bare den ene
oppfylt, står ruta med hva den venter på — «velg et treff» eller «kjør et søk først» — ikke
blank.

### h3 — trefflista

`mesh_search`-resultatet med rang, modell, tittel og sti. `status` og `models_missing` står
over lista, ikke i et verktøytips.

## Feilhåndtering

Svarlaget bærer den allerede; GUI-ets jobb er å ikke svelge den.

- **`partial` vises i flaten.** Mangler en modell, står det hvilke som svarte og hvilke som
  ikke gjorde det. Et konfidensfelt ingen tvinges til å lese er dekorasjon.
- **Ingen sti er et svar.** `mesh_path` returnerer null utenfor `max_depth`; skjematikken
  tegner treffet som frakoblet med taket oppgitt, og utelater det aldri stille.
- **Utledede kanter merkes.** `mesh_neighbors` setter `status: partial` når en kant har
  `method != exact`. Fallbacken tegner den kanten annerledes enn en `WIKILINKS_TO` noen
  faktisk skrev.
- **Bindingen er 127.0.0.1, uten `--host`-flagg.** Serveren eksponerer et helt
  hjemmekatalog-korpus, og en verts-bryter er en måte å publisere det ved uhell. Trengs
  fjerntilgang: `ssh -L`. Åpner ikke nettleseren seg, skrives URL-en i terminalen.

## Porter

Siden har ingen avgjørelser, så testene ligger på HTTP-nivå.

1. **Kjent-svar over hver rute** mot fixturkorpuset — forespørsel inn, eksakt respons ut.
   Samme form som de 26 suitene.
2. **Gaten som må kunne si nei:** en modell fjernes, og responsen *må* bære `status: partial`
   og navngi den. Mutasjonen som fjerner videreføringen skal drepes av den testen ved navn.
3. **Kryssjekk mot et målt faktum:** settet `/graph` kaller isolert må være identisk med
   `isolated_notes(store)` — 315 av 602. Da kan ikke GUI-et og `md gaps` drive fra hverandre
   uten at noen sier fra.
4. `mutate_gui.py` i samme driver som resten.

## Taket, sagt høyt

**Ingenting her tester at siden tegner riktig.** Det ville krevd nettleserautomatisering og
dermed pakkas første runtime-avhengighet. Mitigeringen er at siden ikke har logikk å ta feil
av: tegner den feil, tegner den feil fra korrekte data. Det er en reell begrensning, ikke en
løst.

## Den ene ukjente — skal måles før noe bygges

**20 treff betyr 19 stisøk på inntil 4 hopp over meshen, per klikk.** Kostnaden er ukjent.

Måles først, antas ikke. Faller det ut for dyrt, er utveien lavere `max_depth` eller færre
treff i skjematikken — begge med taket skrevet i svaret, slik at en kappet visning sier at
den ble kappet.

## Beslutninger, med grunn

| Valg | Avvist alternativ | Grunn |
|---|---|---|
| Forgrunnsserver | statisk HTML; statisk øyeblikksbilde | to søkeimplementasjoner kan svare forskjellig |
| Skjematikk = broer | naboskap; innholdstre | eneste lesning der «etter søk» betyr noe |
| Isolerte i bånd | alt i kraftlayout; tom til valg | posisjon uten kant er støy som ser ut som data |
| Uthev ved søk | filtrer + ny layout; ingen reaksjon | deterministisk layout er en egenskap som skal beholdes |
| Transport over MCP-laget | utvid `visualize.py`; eget JSON-API | «a second opinion about the same files» |

## Utenfor omfang

Redigering, skriving til stores, autentisering, flerbruker, fjerntilgang, kodeinnmat i
skjematikken, og alt som ville krevd en runtime-avhengighet.
