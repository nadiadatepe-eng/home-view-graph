#!/usr/bin/env python3
"""Mutasjonstest for CP-H4 -- heading-treet, løv-flagget og per-seksjon-hashen.

Alle åtte mutasjonene er off-by-one-feil eller utelatelser en anmelder ikke ser: en `<=`
som blir `<`, en JSON-liste som blir en streng, en UPDATE som glemmer to kolonner. Ingen av
dem krasjer, ingen av dem endrer et eneste antall i byggrapporten, og alle åtte lar grafen se
frisk ut.

To av dem drepes bare av søsken-dokumentet i fasitens tillegg. Det gjennomarbeidede
eksempelet har ingen to overskrifter på samme nivå etter hverandre, og på det dokumentet gir
`<=` og `<` identisk svar på alle fem rader -- regelen er riktig, men eksempelet kan ikke
skille den fra en gal.

Kjør:
    python3 tests/mutate_h4.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # --- treet ---

    # En overskrift fulgt av sin egen søster meldes som forelder til henne. Usynlig på det
    # første fasit-dokumentet; det er derfor tillegget finnes.
    ("a heading followed by its own sibling stops being a leaf",
     "homegraph/models/m3_markdown.py",
     '        leaf = (i + 1 == len(sections)\n'
     '                or sections[i + 1]["level"] <= sec["level"])',
     '        leaf = (i + 1 == len(sections)\n'
     '                or sections[i + 1]["level"] < sec["level"])',
     "sibling: a heading followed by its own sibling is still a leaf"),

    # Forfedre-stakken popper på `>` i stedet for `>=`, så forrige søster blir stående og
    # neste søster leses som en underseksjon av henne.
    ("the ancestor stack keeps the previous sibling",
     "homegraph/models/m3_markdown.py",
     '        while stack and stack[-1]["level"] >= sec["level"]:',
     '        while stack and stack[-1]["level"] > sec["level"]:',
     "sibling: and the second is not a descendant of the first"),

    # --- hashen ---

    # Teksten hentes fra den kodeblankede strengen i stedet for den rå. Alt ser riktig ut
    # helt til noen redigerer inne i en kodefence: innhold endret, hash uendret, seksjon
    # meldt ren av den inkrementelle veien.
    ("the digest is taken from the code-blanked text",
     "homegraph/models/m3_markdown.py",
     '        text = body[start:max(start, end)]',
     '        text = clean[start:max(start, end)]',
     "hash: an edit inside a code fence changes its section's digest"),

    # Seksjonen strekker seg til neste overskrift på samme eller grunnere nivå, altså hele
    # undertreet. Da restempler én redigering langt nede hver forelder som endret.
    ("a section's text runs to the end of its subtree",
     "homegraph/models/m3_markdown.py",
     '        end = sections[i + 1]["offset"] if i + 1 < len(sections) else len(body)',
     '        end = next((s["offset"] for s in sections[i + 1:]\n'
     '                    if s["level"] <= sec["level"]), len(body))',
     "hash: and changes no other section's digest"),

    # --- ledningen fra parser til lager ---

    # `body` faller tilbake til den bare overskriften. Regnestykket over er fortsatt
    # riktig; det er bare ingen som skriver resultatet.
    ("the build writes the bare heading as body again",
     "homegraph/models/m3_build.py",
     '                              body=BREADCRUMB.join(place["heading_path"]),',
     '                              body=sec["title"],',
     "build: the stored body is the breadcrumb, not the bare heading"),

    # Ett nøkkelord glemt i kallet. Kolonnen finnes, migrasjonen kjørte, og hver seksjon
    # står med NULL.
    ("the build forgets to pass heading_path",
     "homegraph/models/m3_build.py",
     '                              heading_path=place["heading_path"],\n',
     '',
     "build: the stored ancestor list keeps the separator inside a title"),

    # --- lageret ---

    # Brødsmula lagres i stedet for lista. Gyldig JSON, leses uten feil, og en overskrift
    # med separator i seg er ikke lenger til å dele opp igjen.
    ("the ancestor list is stored as the joined breadcrumb",
     "homegraph/store.py",
     '        path_json = None if heading_path is None else json.dumps(\n'
     '            list(heading_path), ensure_ascii=False)',
     '        path_json = None if heading_path is None else json.dumps(\n'
     '            " > ".join(list(heading_path)), ensure_ascii=False)',
     "store: the separator inside a heading survives the round trip"),

    # `False or None` er `None`. En seksjon som ikke er løv blir umulig å skille fra en
    # node som ikke er en seksjon i det hele tatt.
    ("a non-leaf section is stored as NULL rather than 0",
     "homegraph/store.py",
     '        leaf_int = None if section_leaf is None else int(section_leaf)',
     '        leaf_int = section_leaf or None',
     "store: a non-leaf section stores 0, not NULL"),

    # UPDATE-setningen glemmer de to nye kolonnene. INSERT skriver dem, så alt er riktig
    # ved første bygg og fryser ved det andre -- den verste formen, siden den er grønn
    # akkurat så lenge noen ser på den.
    ("the update statement forgets the two new columns",
     "homegraph/store.py",
     '                                heading_path=?, section_leaf=?, last_seen=?\n'
     '               WHERE id=?""",\n'
     '            (kind, subtype, path, title, body, size, mtime, content_hash,\n'
     '             title_method, title_confidence, path_json, leaf_int,\n'
     '             as_of, row["id"]))',
     '                                last_seen=?\n'
     '               WHERE id=?""",\n'
     '            (kind, subtype, path, title, body, size, mtime, content_hash,\n'
     '             title_method, title_confidence,\n'
     '             as_of, row["id"]))',
     "store: a rebuild updates the columns rather than keeping stale ones"),

    # --- den bærbare rundturen ---

    # Kolonnelista i eksporten er håndholdt. En kolonne lagt til `nodes` og glemt her
    # feiler ingen steder: eksporten går, importen går, og grafen som kommer tilbake har
    # mindre i seg enn den som gikk inn. Dette var den ekte tilstanden fram til den
    # eksterne gjennomgangen 2026-08-01.
    ("the portable export drops the two new columns",
     "homegraph/export.py",
     '                "content_hash", "heading_path", "section_leaf",',
     '                "content_hash",',
     "export: a bundle round trip keeps heading_path"),

    # Importøren sender strengen rett videre i stedet for å dekode den. `upsert_node` gjør
    # sin egen `json.dumps`, så verdien blir dobbeltkodet: `"[\\"Alpha\\"]"` der originalen
    # hadde `["Alpha"]`. Gyldig JSON, leses uten feil, og er feil type.
    ("the importer double-encodes the ancestor list",
     "homegraph/importer.py",
     '        heading_path=(json.loads(row["heading_path"])\n'
     '                      if row.get("heading_path") is not None else None),',
     '        heading_path=row.get("heading_path"),',
     "export: a bundle round trip keeps heading_path"),

    # --- de tre sim-auditor fant 2026-08-01 ---

    # Filens digest stemplet på hver seksjon. Én linje, kopi av fil-upserten tjue linjer
    # over. Denne OVERLEVDE tolv harnesser da den ble prøvd: alle sjekkene som målte
    # digesten leste treet i minnet, og den ene som leste lageret spurte bare om strengen
    # var sann. `all(truthy)` er sann for hva som helst.
    ("every section is stamped with the file's digest",
     "homegraph/models/m3_build.py",
     '                              content_hash=place["content_hash"],',
     '                              content_hash=_safe_hash(path),',
     "build: and the STORED digest is the section's, not the file's"),

    # `shape`-eksporten hasjer `title` og sendte brødsmula i klartekst ved siden av. En
    # notat-tittel skjult, og dokumentets disposisjon publisert.
    ("the shape export publishes the heading text in the clear",
     "homegraph/export.py",
     'SHAPE_DROPS = ("body", "mtime", "content_hash", "size", "heading_path",',
     'SHAPE_DROPS = ("body", "mtime", "content_hash", "size",',
     "shape: the heading text is not published beside a hashed title"),

    # Fusjonsnøkkelen tar seksjoners digest igjen, og to filer som deler et avsnitt blir
    # én node. Den ene siden forsvinner helt fra resultatet.
    ("sections fuse across files on their digest again",
     "homegraph/mesh.py",
     '        if row.get("content_hash") and row.get("kind") != "section":',
     '        if row.get("content_hash"):',
     "mesh: two files sharing a paragraph stay two entries"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_h4.py", prefix="muth4-", timeout=180))
