#!/usr/bin/env python3
"""Mutasjonstest for CP-H6 -- sentralitet som uavgjort-bryter i fusjonen.

Ni mutasjoner. Ingen av dem krasjer, og de fleste endrer rekkefølgen på en måte som ser
like plausibel ut som den riktige -- en rerangering har ingen fasit i seg selv, så «Q, P, R»
og «P, Q, R» er begge troverdige svar med mindre noe holder dem opp mot et tall som ble
skrevet før koden.

To av dem er viktigere enn resten:

* **Den som gjør bryteren til en fjerde RRF-liste igjen.** Det er designet fasiten forkastet
  2026-08-02, og det er ikke en stråmann -- det var koden som lå her i går, grønn på sin egen
  port med 23 av 23. Den dør nå på `R`, som har grad 99, scorer lavere enn uavgjortet og
  derfor må bli liggende sist.
* **Den som lar bryteren rangere alle noder.** Planens bokstavelige lesning: en node med
  høyeste grad i lageret kommer da inn på hver eneste spørring, uansett hva det ble spurt om.

Kjør:
    python3 tests/mutate_h6.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # --- hvilket design dette er ---

    # Tilbake til fjerde-liste-designet: graden legges i scoren i stedet for å bryte
    # uavgjort. Rekkefølgen ser fortsatt riktig ut på dette eksempelet -- `Q, P, R` -- så
    # ordenssjekkene merker ingenting. Det som avslører den er tallene: en uavgjort-bryter
    # rører ikke scoren, og dette designet endrer alle tre. Målt 2026-08-02: `R` klatrer
    # ikke her, den mangler 0,000256 på det, og en sjekk på `R` sin plass ville altså
    # sluppet mutasjonen gjennom på et hår.
    ("the tie-break is added into the score instead, as a fourth list was",
     "homegraph/mesh.py",
     '        out = sorted(fused.values(),\n'
     '                     key=lambda h: (-h["score"],\n'
     '                                    -(h["degree"] or 0), h["key"]))',
     '        for _s in fused.values():\n'
     '            _s["score"] += 1.0 / (RRF_K + 1) if _s["degree"] else 0.0\n'
     '        out = sorted(fused.values(),\n'
     '                     key=lambda h: (-h["score"], h["key"]))',
     "order: and the fused scores are untouched by the tie-break"),

    # --- hva bryteren dekker ---

    # Planens bokstav: gi hver node i meshen en grad, ikke bare kandidatene. `D` har grad 99
    # og er ikke kandidat -- den får nå en oppføring, her og på hver annen spørring.
    ("every node in the mesh gets a degree, not just the candidates",
     "homegraph/mesh.py",
     '        wanted: dict[str, str] = {}\n'
     '        for model, rows in rankings.items():\n'
     '            for row in rows:\n'
     '                wanted.setdefault(self._mesh_node_key(model, row["node_key"]),\n'
     '                                  self._fusion_key(row))',
     '        wanted: dict[str, str] = {}\n'
     '        for model, rows in rankings.items():\n'
     '            for row in rows:\n'
     '                wanted.setdefault(self._mesh_node_key(model, row["node_key"]),\n'
     '                                  self._fusion_key(row))\n'
     '        with Store(self.mesh_db) as _all:\n'
     '            for _r in _all.db.execute("SELECT node_key FROM nodes"):\n'
     '                wanted.setdefault(_r["node_key"],\n'
     '                                  "path:/%s" % _r["node_key"].split("::")[-1])',
     "candidates: the most central node in the store is not scored"),

    # --- hvilken nøkkel som slås opp ---

    # Kvalifiser kode-nøkkelen én gang til, slik koden gjorde til codex fant det
    # 2026-08-02. `code::code::/sti` har aldri eksistert, og en manglende rad er en lovlig
    # null -- så hver eneste kodekandidat fikk grad 0 i stillhet, `main.py` med grad 69
    # inkludert.
    ("a code key is qualified a second time",
     "homegraph/mesh.py",
     "        if model == Mesh.CODE_MODEL:\n            return node_key\n"
     '        return "%s::%s" % (model, node_key)',
     '        return "%s::%s" % (model, node_key)',
     "code: an already-qualified code key finds its own degree"),

    # Motsatt retning: slutt å kvalifisere i det hele tatt. Kodekandidatene blir riktige,
    # og modellkandidatene faller stille til null i stedet.
    ("no key is qualified at all",
     "homegraph/mesh.py",
     "        if model == Mesh.CODE_MODEL:\n            return node_key\n"
     '        return "%s::%s" % (model, node_key)',
     "        return node_key",
     "code: and a model key is still qualified"),

    # --- når meshen ikke lar seg lese ---

    # Åpningen tilbake utenfor `try`, slik den lå før codex-runden. En ødelagt mesh tar da
    # hele det fødererte søket ned -- for en rerangering som er valgfri.
    ("an unreadable mesh raises instead of reporting absent",
     "homegraph/mesh.py",
     "        try:\n            mesh = Store(self.mesh_db)\n"
     "        except (sqlite3.Error, OSError):",
     "        mesh = Store(self.mesh_db)\n"
     "        if False:",
     "broken: a corrupt mesh reports centrality absent"),

    # --- hva som telles ---

    # Bare fanIn. En fil som siterer mange og siteres av ingen faller til null, og
    # rekkefølgen endres uten at noe antall ser galt ut.
    ("centrality counts fanIn only",
     "homegraph/mesh.py",
     '                    "SELECT (SELECT COUNT(*) FROM edges WHERE dst = n.id) "\n'
     '                    "     + (SELECT COUNT(*) FROM edges WHERE src = n.id) d "',
     '                    "SELECT (SELECT COUNT(*) FROM edges WHERE dst = n.id) d "',
     "degree: a candidate ranks by fanIn + fanOut"),

    # Summen i stedet for den høyeste når et dokument er funnet av flere modeller. Ser
    # rimeligere ut enn den er: treffet har allerede ett RRF-bidrag per modell, så summen
    # teller den samme populariteten to ganger.
    ("a document seen by two models collects the sum of its degrees",
     "homegraph/mesh.py",
     '                if degree > degrees.get(fusion_key, -1):\n'
     '                    degrees[fusion_key] = degree',
     '                degrees[fusion_key] = degrees.get(fusion_key, 0) + degree',
     "degree: a document seen by two models takes the highest, not the sum"),

    # En kandidat uten rad i meshen utelates fra kartet i stedet for å få 0. Usynlig i
    # sorteringen, siden `_rrf` leser et manglende oppslag som 0 uansett -- men kartet er
    # også svaret `explain` teller, og «vi vet ingenting om denne» og «denne har ingen
    # kanter» er da blitt samme rapport.
    ("a candidate with no mesh row is left out of the map",
     "homegraph/mesh.py",
     '                degree = row["d"] if row else 0\n'
     '                if degree > degrees.get(fusion_key, -1):',
     '                if row is None:\n'
     '                    continue\n'
     '                degree = row["d"]\n'
     '                if degree > degrees.get(fusion_key, -1):',
     "degree: a candidate with no mesh row is zero, not missing"),

    # --- grad som størrelse, ikke som ja/nei ---
    # De fire under ble skrevet av en revisjon 2026-08-02 og OVERLEVDE den grønne suiten,
    # fordi hvert eneste uavgjort i fiksturet hadde én part på grad 0. Gaten testet
    # «tilkoblet mot ikke tilkoblet», ikke «mer tilkoblet mot mindre». Paret `E` (4) og
    # `F` (5) finnes nå, og alle fire dør på det.

    ("degree is used as a boolean, not as a magnitude",
     "homegraph/mesh.py",
     '                                    -(h["degree"] or 0), h["key"]))',
     '                                    -(1 if h["degree"] else 0), h["key"]))',
     "magnitude: the higher degree leads, though both are nonzero"),

    ("degree is clamped at four",
     "homegraph/mesh.py",
     '                                    -(h["degree"] or 0), h["key"]))',
     '                                    -min(h["degree"] or 0, 4), h["key"]))',
     "magnitude: the higher degree leads, though both are nonzero"),

    ("degree is bucketed in fours",
     "homegraph/mesh.py",
     '                                    -(h["degree"] or 0), h["key"]))',
     '                                    -((h["degree"] or 0) // 4), h["key"]))',
     "magnitude: the higher degree leads, though both are nonzero"),

    # Distinkte naboer i stedet for kanter. To noder kan hevde flere relasjoner om
    # hverandre -- CP-H5s co-change-kanter er den nære kilden -- og da er dette et annet
    # tall. Hvilket som er riktig er et åpent designspørsmål; at det ikke skal kunne
    # endres i stillhet er ikke det.
    ("centrality counts distinct neighbours instead of edges",
     "homegraph/mesh.py",
     '                    "SELECT (SELECT COUNT(*) FROM edges WHERE dst = n.id) "\n'
     '                    "     + (SELECT COUNT(*) FROM edges WHERE src = n.id) d "',
     '                    "SELECT (SELECT COUNT(DISTINCT src) FROM edges WHERE dst = n.id) "\n'
     '                    "     + (SELECT COUNT(DISTINCT dst) FROM edges WHERE src = n.id) d "',
     "magnitude: both candidates in the second tie have edges"),

    # --- rekkefølgen ---

    # Stigende i stedet for synkende. Den minst sentrale av de uavgjorte kommer først, og
    # resultatet ser fortsatt ut som en rangering.
    ("the tie-break sorts ascending",
     "homegraph/mesh.py",
     '                                    -(h["degree"] or 0), h["key"]))',
     '                                    (h["degree"] or 0), h["key"]))',
     "order: with it, the key's order"),

    # Nøkkelen faller ut av sorteringen. To kandidater med lik score OG lik grad kan da
    # bytte plass mellom kjøringer av identisk kode -- samme familie som `mutate_gui` sitt
    # tilfeldige frø.
    ("equal score and equal degree fall back to dict order",
     "homegraph/mesh.py",
     '        out = sorted(fused.values(),\n'
     '                     key=lambda h: (-h["score"],\n'
     '                                    -(h["degree"] or 0), h["key"]))',
     '        out = sorted(fused.values(),\n'
     '                     key=lambda h: (-h["score"], -(h["degree"] or 0)))',
     "ties: and it is by key, not by arrival"),

    # --- om bryteren i det hele tatt når sorteringen ---

    # Gradene regnes ut og kastes. Alt annet er riktig, og rekkefølgen er uendret -- men
    # bare `search()` merker det, siden hver annen sjekk gir gradene til `_rrf` selv.
    ("the degrees are computed and never passed to the sort",
     "homegraph/mesh.py",
     "        hits = self._rrf(rankings, limit, degrees)",
     "        hits = self._rrf(rankings, limit)",
     "search: the more connected of the tied pair is first"),

    # --- fravær mot null ---

    # Ingen mesh gir et tomt kart i stedet for None. «Ingen mesh» og «alt fikk null» blir
    # da det samme svaret, som er feilen `code_inventory` finnes for å ikke gjenta.
    ("no mesh returns an empty map instead of absent",
     "homegraph/mesh.py",
     "        if not self.mesh_db or not os.path.exists(self.mesh_db):\n"
     "            return None",
     "        if not self.mesh_db or not os.path.exists(self.mesh_db):\n"
     "            return {}",
     "absent: a mesh path that does not exist yields no degrees"),

    # --- hva rapporten sier ---

    # Antallet flyttede plasser blir antallet kandidater i stedet. Tallet er fortsatt et
    # tall, og på fikstureksempelet er det til og med nær: 3 mot 2.
    ("the report counts candidates instead of positions moved",
     "homegraph/mesh.py",
     '            moved = sum(1 for a, b in zip(plain, hits) if a["key"] != b["key"])',
     '            moved = len(degrees)',
     "search: it reports how many positions it moved"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h6.py", prefix="muth6-", timeout=180))
