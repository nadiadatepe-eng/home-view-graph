#!/usr/bin/env python3
"""Mutasjonstest for CP-H5 -- `CO_CHANGED_WITH` fra git-historikken.

Seks mutasjoner. Ingen av dem krasjer, og fem av seks gir FLERE eller færre kanter uten at
noe antall ser galt ut -- en samkjøringskant har ingen fasit i seg selv, så «det ble 41
kanter» er like troverdig som «det ble 12».

Den viktigste er den som dropper medlemskapskravet. Å hoppe over det gir flere kanter, og i
fikstureksempelet er de to gale sterkere enn den ene riktige: `notes.txt` deler 3 av 4
commits med både `alpha.md` og `beta.md`, mens den ekte kanten deler 3 av 5.

Kjør:
    python3 tests/mutate_h5.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # --- terskelen ---

    # `>= 2` i stedet for `>= 3`. Paret som deler nøyaktig 2 commits er den ENESTE inputen
    # som skiller de to, og fasiten inneholder det av nettopp den grunnen.
    ("the co-change threshold drops to two shared commits",
     "homegraph/mesh.py",
     "    CO_CHANGE_MIN = 3",
     "    CO_CHANGE_MIN = 2",
     "threshold: a pair sharing exactly 2 commits gets no edge"),

    # --- medlemskapet ---

    # Sti-indeksen tar med seksjonsnoder. De BÆRER `path`, så den siste raden vinner og
    # endepunktet blir `alpha.md#2` i stedet for `alpha.md` — en samkjøringskant mellom to
    # avsnitt ingen har committet. Antallet kanter er uendret, så bare navnet avslører det.
    #
    # Dette er mutasjonen som dekker medlemskapsregelen. Den åpenbare — å slippe gjennom et
    # endepunkt som ikke er en node — krasjer på `sorted(None, str)` i stedet for å bli
    # drept av en navngitt gate, og en krasj er ikke en gate som sa nei. Regelen er dekket
    # av `membership:`-sjekken i gaten; den er ikke mutasjonstestet, og det står i TODO.md.
    ("the path index lets section nodes shadow their file",
     "homegraph/mesh.py",
     "                    \"SELECT node_key, path FROM nodes WHERE path IS NOT NULL \"\n"
     "                    \"AND kind IN ('file','document','image')\"):",
     "                    \"SELECT node_key, path FROM nodes \"\n"
     "                    \"WHERE path IS NOT NULL\"):",
     "edge: it is alpha.md -- beta.md"),

    # --- symmetrien ---

    # Begge retninger skrives. Ett faktum blir to rader, og enhver traversering som teller
    # kanter gir paret dobbel vekt.
    ("the symmetric edge is stored in both directions",
     "homegraph/mesh.py",
     '            mesh.upsert_edge(lo, hi, "CO_CHANGED_WITH", as_of,\n'
     '                             method="co-change")',
     '            mesh.upsert_edge(lo, hi, "CO_CHANGED_WITH", as_of,\n'
     '                             method="co-change")\n'
     '            mesh.upsert_edge(hi, lo, "CO_CHANGED_WITH", as_of,\n'
     '                             method="co-change")',
     "symmetry: the pair is stored once, not in both directions"),

    # --- parsingen ---

    # Den siste `flush()` faller bort, så den eldste commiten teller ikke. Alt annet er
    # riktig, og feilen er nøyaktig ett par-tellepunkt -- den typen som gir et tall som ser
    # rimelig ut.
    ("the oldest commit is never counted",
     "homegraph/mesh.py",
     "            i += 1\n        flush()\n        return {p: (n,",
     "            i += 1\n        return {p: (n,",
     "pairs: every shared count and union matches the key"),

    # Par enumereres for alle filer som noen gang er sett, ikke bare de som faktisk deler en
    # commit. Gir en rad med 0 for to filer som aldri har vært sammen.
    ("pairs are enumerated for files that never shared a commit",
     "homegraph/mesh.py",
     "            for a, b in itertools.combinations(sorted(files), 2):\n"
     "                pairs[(a, b)] += 1",
     "            for a, b in itertools.combinations(sorted(touched), 2):\n"
     "                pairs[(a, b)] += 1",
     "pairs: two files never committed together are absent"),

    # --- rapporten ---

    # «Ingen kanter» og «ingen repo ble oppgitt» blir det samme svaret igjen. Det er samme
    # feil `code_inventory` finnes for å ikke gjenta.
    ("an absent repository reports as zero rather than as absent",
     "homegraph/mesh.py",
     '                "co_change": ("absent" if not repo_root\n'
     '                              else self.repo_top(repo_root)),',
     '                "co_change": (0 if not repo_root\n'
     '                              else self.repo_top(repo_root)),',
     "report: with no repository the report says absent, not zero"),

    # --- de fire nålene sim-auditor fant at passerte hele gaten 2026-08-01 ---

    # Unionen måles aldri. Fasitens forholdstall og de målte 0,348 og 0,800 var tall ingen
    # sjekk kunne skille fra vilkårlige før tillegget begynte å lese union-kolonnen.
    ("the union is the first file's commits alone",
     "homegraph/mesh.py",
     "        return {p: (n, len(touched[p[0]] | touched[p[1]]))",
     "        return {p: (n, len(touched[p[0]]))",
     "pairs: every shared count and union matches the key"),

    # Sti-oppslaget bruker basename. Identisk med riktig kode for hver eneste rad i
    # hovedeksempelet, fordi ingen fil der ligger i en underkatalog.
    ("the path lookup joins the basename instead of the path",
     "homegraph/mesh.py",
     "            src = by_path.get(os.path.realpath(os.path.join(root, a)))\n"
     "            dst = by_path.get(os.path.realpath(os.path.join(root, b)))",
     "            src = by_path.get(os.path.realpath(\n"
     "                os.path.join(root, os.path.basename(a))))\n"
     "            dst = by_path.get(os.path.realpath(\n"
     "                os.path.join(root, os.path.basename(b))))",
     "addendum: the subdirectory pair becomes an edge"),

    # `-z` faller bort. `core.quotepath` er på som standard, så hvert norsk filnavn
    # forsvinner ut av grafen uten en eneste feilmelding.
    ("the log is read without -z, so quotepath mangles the names",
     "homegraph/mesh.py",
     '            ["git", "-C", top, "log", "-z", "--numstat", "--format=%H"],',
     '            ["git", "-C", top, "log", "--numstat", "--format=%H"],',
     "addendum: a subdirectory path and a non-ASCII name pair up"),

    # Toppnivået slås ikke opp. En `--repo-root` som peker på en underkatalog gir null
    # kanter, og rapporten sier fortsatt at repoet ble lest.
    ("paths are joined onto the directory named, not the repository top",
     "homegraph/mesh.py",
     "        root = self.repo_top(repo_root)",
     "        root = os.path.realpath(repo_root)",
     "addendum: a subdirectory as --repo-root finds the same edges"),

    # Medlemskapsregelen, endelig mutasjonstestbar. Den åpenbare nåla krasjer på
    # `sorted(None, str)`; denne speiler git-fila som en node først, som er formen en
    # utvikler faktisk skriver.
    ("a git file that is not a node is mirrored so the edge can be drawn",
     "homegraph/mesh.py",
     "            if src is None or dst is None:\n                continue",
     "            if src is None:\n"
     "                src = \"git::\" + a\n"
     "                mesh.upsert_node(src, kind=\"file\", body=a,\n"
     "                                 path=os.path.join(root, a), as_of=as_of)\n"
     "            if dst is None:\n"
     "                dst = \"git::\" + b\n"
     "                mesh.upsert_node(dst, kind=\"file\", body=b,\n"
     "                                 path=os.path.join(root, b), as_of=as_of)",
     "edge: exactly one edge is written"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h5.py", prefix="muth5-", timeout=180))
