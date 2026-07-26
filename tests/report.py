#!/usr/bin/env python3
"""Én `check()` for alle checkpoint-filene. Ikke en test selv.

26 testfiler bar hver sin kopi av det samme: en `results`-liste på modulnivå og en `check`
som legger til i den og skriver én PASS/FAIL-linje. Kopiene skilte seg bare på
kolonnebredden, og på om de returnerte `ok` (11 av 26 gjorde ikke).

To ting dette *ikke* kan være, og begge er bærende:

  * **Ikke én delt liste.** pytest importerer alle 26 filene inn i samme prosess. En
    `results` på modulnivå her ville blandet tellingene deres, og én rød sjekk i `cp0`
    ville felt hver senere `main()` i samme kjøring. `reporter()` deler ut en fersk liste
    per kaller.
  * **Ikke én fast bredde.** `mutate.py:run_suite` henter sjekkenavnet ut av en
    `FAIL`-linje med `rsplit("  ", 1)`, altså på siste dobbeltmellomrom. Det virker bare
    så lenge navnet er padet ut til bredden; et navn som er lengre får ett mellomrom, og
    da leses hele «navn detalj» som navnet. Dommen for den mutasjonen endrer seg uten at
    noe blir rødt. Hver fil beholder derfor bredden sin -- de spenner 34 til 62.

`check` returnerer alltid `ok`, også for de filene som ikke gjorde det før. Det er en
utvidelse, ikke en endring: ingen kaller kan se en returverdi den ikke ba om.
"""
from __future__ import annotations


def reporter(width: int = 52):
    """Returner `(results, check)` for én testmodul."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, ok, detail))
        print("%s  %-*s %s" % ("PASS" if ok else "FAIL", width, name, detail))
        return ok

    return results, check
