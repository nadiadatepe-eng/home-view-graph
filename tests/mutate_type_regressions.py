#!/usr/bin/env python3
"""Mutasjonstest for TYPE-REGRESSIONS -- de to feilene en typesjekker fant.

Begge hadde samme form: en vakt som ikke kunne fyre på en input ingen hadde gitt den ennå.
Ingen av dem var nåbare fra CLI-et, som er nøyaktig derfor de overlevde -- og derfor det ikke
holder at suiten er grønn. Den hadde **0 % mutasjonsdekning** fram til 2026-08-01.

To av de fire mutasjonene setter defekten tilbake. De to andre går på de negative
kontrollene: en vakt som fyrer på alt består begge de positive sjekkene, og et
dispatch-bord som avviser alt svarer -32601 på hvert eneste navn -- inkludert de fire
sjekkene som skal se -32601.

Kjør:
    python3 tests/mutate_typereg.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # Defekten: uten dekodingen beholder vaktlista det `os.fspath` ga, så en bytes-rot gir
    # `str.startswith(bytes)` og en TypeError. Vakten rakk aldri å si at M2 åpnet et bilde.
    ("the watch list keeps bytes instead of decoding to str",
     "homegraph/models/m2_build.py",
     "    watched = [_text(os.path.abspath(os.fspath(r))) for r in roots]",
     "    watched = [os.path.abspath(os.fspath(r)) for r in roots]",
     "bytes root: the guard fires"),

    # Den negative kontrollen. En vakt som fyrer på hver eneste `open` består begge de
    # positive sjekkene over, og ville vært en verre feil enn den som ble rettet.
    ("the guard fires on every open, inside the roots or not",
     "homegraph/models/m2_build.py",
     'if any(full == r or full.startswith(os.path.join(r, "")) for r in watched):',
     'if True:  # mutated: everything trips the guard',
     "bytes root: a file outside the roots stays silent"),

    # Defekten: `dict.get([])` er en TypeError ut av dispatch-bordet, forbi JSON-RPC sin
    # feilvei, så en feilformet forespørsel drepte turen i stedet for å bli besvart.
    ("an unhashable tool name reaches dict.get again",
     "homegraph/mcp_server.py",
     ".get(name) if isinstance(name, str) else None",
     ".get(name)",
     "tools/call name=[] answers -32601"),

    # Den negative kontrollen: et bord som aldri slår opp svarer -32601 på alt, og de fire
    # sjekkene over ville bestått for en server som ikke kan kalle noe verktøy.
    ("no tool name is ever dispatched",
     "homegraph/mcp_server.py",
     ".get(name) if isinstance(name, str) else None",
     '.get("mutated: no name is ever looked up")',
     "a known tool name is dispatched, not rejected"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_type_regressions.py",
                 prefix="muttyp-", timeout=180))
