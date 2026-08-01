#!/usr/bin/env python3
"""Mutasjonstest for SUITE-IS-COMPLETE -- listene som bestemmer HVA som kjøres.

Denne suiten finnes fordi egenskapen den sjekker allerede var påstått i en kommentar og
allerede var usann: `test_i1.py` og `test_i2.py` ble skrevet, kjørt for hånd, sitert som
grønne, og aldri samlet opp av `pytest tests/`. Den hadde selv **0 % mutasjonsdekning** fram
til 2026-08-01 -- mekanismen som bestemmer hva som verifiseres, uverifisert, én gang til.

Mutasjonene rører ikke testfila. De rører de to listene den vokter -- `pyproject.toml` og
`for h in ...`-løkka i `CONTRIBUTING.md` -- i begge retninger: en oppføring som mangler, og
en oppføring som peker på noe som ikke finnes. Løkke-sjekken ble lagt til 2026-08-01, etter
at `mutate_review_findings` måtte føres inn i løkka for hånd.

Kjør:
    python3 tests/mutate_suite.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # En testfil finnes, men `pytest tests/` samler den aldri opp. Dette er den ekte feilen:
    # den er grønn ved at den ikke kjører.
    ("a test file is dropped from the pytest collect list",
     "pyproject.toml",
     '"test_review_findings.py", ',
     '',
     "every test file in tests/ is one pytest will collect"),

    # Andre retning: et navn som ikke lenger løser opp. Ufarlig i seg selv, men det er
    # restene etter en omdøping, og en navneliste er bare til å stole på så lenge hvert navn
    # betyr noe.
    ("the collect list names a test file that does not exist",
     "pyproject.toml",
     '"test_review_findings.py", ',
     '"test_review_findings.py", "test_renamed_away.py", ',
     "and every file named in pyproject.toml still exists"),

    # Et harnisk finnes på disk, men løkka nevner det ikke -- altså kjører ingen sveip det.
    # Dette er «14 av 24» i sin reneste form.
    ("a harness on disk is dropped from the CONTRIBUTING loop",
     "CONTRIBUTING.md",
     " idx h3_para",
     " h3_para",
     "every mutation harness is named in the CONTRIBUTING loop"),

    # Andre retning: løkka navngir et harnisk som ikke finnes. Løkka avslutter da med
    # feilkode på et manglende filnavn i stedet for å hoppe over det.
    ("the loop names a harness that is not on disk",
     "CONTRIBUTING.md",
     "         h4 h5; do",
     "         h4 h5 renamed_away; do",
     "and the loop names no harness that does not exist"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_suite_is_complete.py",
                 prefix="mutsui-", timeout=180))
