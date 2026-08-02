#!/usr/bin/env python3
"""Mutasjonstest for CP-H7 -- catch-up og per-treff-staleness.

Ti mutasjoner. Ingen av dem krasjer, og de fleste gjør rapporten *mer*
imøtekommende -- flere filer merket foreldet, et banner som alltid står, en
vektor som alltid er fersk. Det er den farlige retningen: en staleness-rapport
som overdriver ser grundig ut, og en som alltid fyrer blir møbel.

De to viktigste:

* **«tell `absent` som et problem».** Da rapporterer korpuset trøbbel første
  gang en node skrives uten stat, og advarselen står for alltid.
* **«la vektoren svare for seg selv».** `embeddings` har ingen `content_hash`,
  så en vektor kan ikke vite om den er utdatert. En implementasjon som spør den
  likevel svarer alltid `current`, og det ser riktig ut.

Kjør:
    python3 tests/mutate_h7.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # --- de fire tilstandene ---

    # `absent` blir `current`. «Vi så ikke etter» og «vi så og fant ingenting
    # galt» blir samme svar, som er nøyaktig sammenblandingen R1 finnes for.
    ("a node with no stored stat is reported as current",
     "homegraph/incremental.py",
     "    if size is None or mtime is None:\n        return ABSENT",
     "    if size is None or mtime is None:\n        return CURRENT",
     "R1: current, stale, missing and absent are four different answers"),

    # En fil som er borte rapporteres som endret. Begge er «ikke fersk», men de
    # er ulike reparasjoner: den ene er en sletting, den andre en re-indeksering.
    ("a path that is gone is reported as stale, not missing",
     "homegraph/incremental.py",
     "    except FileNotFoundError:\n",
     "    except FileNotFoundError:\n        return STALE\n",
     "R1: current, stale, missing and absent are four different answers"),

    # --- seksjoner ---

    # Seksjonen spør filsystemet selv i stedet for å arve. Den har ingen egen
    # stat, så svaret blir `absent` -- for 6 035 av m3s noder på ekte korpus.
    ("a section is asked about directly instead of inheriting",
     "homegraph/incremental.py",
     "            inherited = by_path.get(row[\"path\"])",
     "            inherited = None",
     "R2: a section is stale because its file is"),

    # --- stubber ---

    # Stubben slutter å bli spurt om stien finnes. Alle 9 125 mesh-noder blir
    # `absent`, og de finnes nettopp for å peke på en fil man kan åpne.
    ("a stub with no stat is never asked whether its path exists",
     "homegraph/incremental.py",
     '            states[row["node_key"]] = node_state(row["path"], None, None)\n'
     "    return states",
     '            states[row["node_key"]] = ABSENT\n'
     "    return states",
     # Peker på reconcile-sjekken, ikke `node_state`-sjekken: mutasjonen endrer
     # `reconcile` sin deferred-gren, og et direkte kall på `node_state` går
     # utenom den. Målt 2026-08-02 -- den ble feiltilskrevet til korpustellingen.
     "stub: and reconcile says so too, not only node_state"),

    # En node uten sti får en tilstand i stedet for `absent`. Ikke et krasj --
    # et selvsikkert svar om noe filsystemet ikke har en mening om.
    ("a node with no path at all is reported as current",
     "homegraph/incremental.py",
     "    if not path:\n",
     "    if not path:\n        return CURRENT\n",
     "stub: and a node with no path at all is absent, not an exception"),

    # Søket slutter å arve for seksjoner og spør filsystemet direkte. `reconcile`
    # sier fortsatt `stale`; søket sier `absent` om samme node. To halvdeler av én
    # funksjon som er uenige om én node er verre enn begge svarene hver for seg --
    # og fiksturet så det ikke før en seksjon kunne bli treff alene.
    ("search asks the filesystem about a section instead of inheriting",
     "homegraph/mesh.py",
     '            if row["kind"] == "section" and row["size"] is None:\n'
     "                state = parents.get(\n"
     '                    row["path"],\n'
     '                    incremental.node_state(row["path"], None, None))',
     '            if False:\n'
     "                state = parents.get(\n"
     '                    row["path"],\n'
     '                    incremental.node_state(row["path"], None, None))',
     "agree: the section is the hit, and it is stale, not absent"),

    # --- banneret ---

    # `absent` telles som berørt. Banneret står da på hver spørring mot et
    # korpus som har én node uten stat, altså alltid.
    ("the warning counts absent as trouble too",
     "homegraph/incremental.py",
     "AFFECTED = (STALE, MISSING)",
     "AFFECTED = (STALE, MISSING, ABSENT)",
     "prediction 1: a clean corpus produces no staleness warning at all"),

    # Banneret teller korpuset i stedet for vinduet. Tallet blir stort, ser
    # alvorlig ut, og svarer ikke på spørringen som ble stilt.
    ("the warning counts the corpus instead of the returned window",
     "homegraph/mesh.py",
     '                   len(hits)))',
     '                   sum(sum(v.values())\n'
     '                       for v in self.corpus_staleness().values())))',
     "R3: and it counts the returned window, not the corpus"),

    # Hele korpuset stat-es på hver spørring. Svaret er det samme; kostnaden er
    # 8 564 syscalls i stedet for ti, på en sti som før bare leste SQLite.
    ("every search stats the whole corpus, not just its hits",
     "homegraph/mesh.py",
     "        self._annotate_status(hits)",
     "        self._annotate_status(hits)\n"
     "        for _s in self.corpus_staleness().values():\n"
     "            pass",
     "R4: one stat per returned hit, and no more"),

    # --- embedding_status ---

    # «Ingen vektor» og «ingen vektorer i det hele tatt» blir samme svar. En
    # bruker som lurer på om noe er embeddet får `off` for en node i et lager
    # som er fullt av vektorer.
    ("a node without a vector is reported as off, not none",
     "homegraph/mesh.py",
     '        if not has_vector:\n            return "none"',
     '        if not has_vector:\n            return "off"',
     "R5: an unembedded node reports none, not off and not absent"),

    # Vektoren får svare for seg selv: den finnes, altså er den fersk. Dette er
    # den mutasjonen som ser mest riktig ut, og `embeddings` har ingen
    # `content_hash` å hente svaret fra.
    ("a vector that exists is reported as current, whatever its file says",
     "homegraph/mesh.py",
     '        if state == incremental.ABSENT:\n            return "unknown"\n'
     "        return incremental.CURRENT if state == incremental.CURRENT \\\n"
     "            else incremental.STALE",
     "        return incremental.CURRENT",
     "R5: a vector on a stale file is stale, derived not stored"),

    # --- catch-up ---

    # Catch-up rapporterer noe uansett. «Ingenting drev» blir umulig å si, og
    # watcheren kjører en full update ved hver oppstart.
    ("catch-up reports something even when nothing drifted",
     "homegraph/cli.py",
     "    hurt = [(state, counts[state]) for state in inc.AFFECTED if counts[state]]",
     "    hurt = [(state, counts[state]) for state in inc.AFFECTED]",
     "R6: and says nothing at all when nothing drifted"),

    # Medlemskapstesten som avgjor om en node HAR en vektor, invertert. Funnet av
    # `condition_coverage.py` 2026-08-02: den var den eneste sammensatte betingelsen
    # H7 la til som ingen nal pekte pa.
    ("a node counts as embedded when it has no vector",
     "homegraph/mesh.py",
     '                state, any_vectors, row["id"] in embedded)',
     '                state, any_vectors, row["id"] not in embedded)',
     "R5: the embedded node whose file is current reports current"),

    # --- funnene fra revisjonen 2026-08-02 ---
    # Alle elleve under OVERLEVDE den grønne 32-sjekks-gaten. Ti av dem fordi
    # fiksturet ikke kunne skille; den første fordi instrumentet var feil.

    # Søket helbreder seg selv: det skriver til lageret mens det rapporterer.
    # `os.stat(db).st_mtime_ns` ser det ikke, fordi WAL legger commiten i
    # `db-wal`. Det var det eneste som håndhevet R6.
    ("search writes to the store while reporting on it",
     "homegraph/mesh.py",
     '            hit["staleness"] = state\n'
     '            hit["embedding_status"] = self._embedding_status(',
     '            hit["staleness"] = state\n'
     '            if state == incremental.STALE:\n'
     '                store.db.execute("UPDATE nodes SET last_seen = ? "\n'
     '                                 "WHERE id = ?",\n'
     '                                 ("2026-08-03", row["id"]))\n'
     '                store.db.commit()\n'
     '            hit["embedding_status"] = self._embedding_status(',
     "R6: an MCP connect and search commit nothing, WAL included"),

    # Banneret teller bare den ene halvdelen. Et korpus av slettede filer blir
    # da helt stille -- 2 628 stier i m3 alene.
    ("the banner counts stale but not missing",
     "homegraph/mesh.py",
     '            if h["staleness"] in incremental.AFFECTED)',
     '            if h["staleness"] == incremental.STALE)',
     "banner: a corpus of only missing hits still warns"),

    ("the banner counts missing but not stale",
     "homegraph/mesh.py",
     '            if h["staleness"] in incremental.AFFECTED)',
     '            if h["staleness"] == incremental.MISSING)',
     "banner: a corpus of only stale hits still warns"),

    # Bare halve stat-sammenlikningen. En fil redigert til samme lengde -- en
    # formatterer, et synkeverktoy -- rapporteres fersk.
    ("only the size is compared, not the mtime",
     "homegraph/incremental.py",
     "    if st.st_size == size and abs(st.st_mtime - mtime) <= mtime_tolerance:",
     "    if st.st_size == size:",
     "stat: a same-length edit with a new mtime is stale"),

    ("only the mtime is compared, not the size",
     "homegraph/incremental.py",
     "    if st.st_size == size and abs(st.st_mtime - mtime) <= mtime_tolerance:",
     "    if abs(st.st_mtime - mtime) <= mtime_tolerance:",
     "stat: a longer file with its mtime restored is stale"),

    # Toleransen sluker et helt ar. Enhver mtime-drift blir usynlig.
    ("the mtime tolerance swallows a year",
     "homegraph/incremental.py",
     "def reconcile(store: \"Store\", *, mtime_tolerance: float = 1e-6)",
     "def reconcile(store: \"Store\", *, mtime_tolerance: float = 1e9)",
     "stat: a same-length edit with a new mtime is stale"),

    # Den mildeste tilstanden vinner i stedet for den verste. En seksjon arver
    # `current` fra en fersk soskennode mens lageret serverer den foreldede.
    ("the milder of two states wins on a shared path",
     "homegraph/incremental.py",
     "    return a if _SEVERITY[a] >= _SEVERITY[b] else b",
     "    return a if _SEVERITY[a] <= _SEVERITY[b] else b",
     "R2b: the section inherits the WORSE of two states on its path"),

    # En vektor pa en slettet fil blir `unknown` i stedet for `stale`. Radet
    # blir «vi vet ikke» der det riktige er «re-indekser».
    ("a vector on a missing file is unknown instead of stale",
     "homegraph/mesh.py",
     "        if state == incremental.ABSENT:\n            return \"unknown\"",
     "        if state in (incremental.ABSENT, incremental.MISSING):\n"
     "            return \"unknown\"",
     "R5: a vector whose file is gone is stale, not unknown"),

    # Korpustallene dropper `absent`. «Rapporterer et rent korpus den aldri
    # sjekket» -- pa det ene stedet korpustallene publiseres.
    ("the corpus counts drop absent",
     "homegraph/mesh.py",
     "            counts = collections.Counter(\n"
     "                incremental.reconcile(store).values())",
     "            counts = collections.Counter(\n"
     "                v for v in incremental.reconcile(store).values()\n"
     "                if v != incremental.ABSENT)",
     "R3: including absent, which is not the same as nothing wrong"),

    # Uleselige lagre nevnes bare nar ALLE er uleselige. Ett godt og ett
    # manglende gir da samme svar som det gode alene.
    ("an unreadable store is only named when every store failed",
     "homegraph/cli.py",
     "    if not skipped:\n        return line",
     "    if not skipped or asked:\n        return line",
     "R6: and an unreadable model is named even when another answered"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h7.py", prefix="muth7-", timeout=180))
