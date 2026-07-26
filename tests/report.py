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
  * **Ikke én fast bredde.** Bredden er ren kolonnejustering, tilpasset det lengste navnet
    i hver fil -- de spenner 34 til 62. Den var *ikke* kosmetikk før `FAILED`-linja under
    fantes; se neste avsnitt.

## En rød sjekk melder seg maskinlesbart, i tillegg til i rapporten

`mutate.py:run_suite` avgjør hvilken gate som sa nei ved å lese testfilas stdout. Fram til
2026-07-26 gjorde den det ved å parse den formaterte rapportlinja:
`line[4:].strip().rsplit("  ", 1)[0].strip()`, altså «alt før siste dobbeltmellomrom». Den
heuristikken er feil på tre uavhengige måter, og alle tre var i bruk i dette repoet:

  1. **Navn lengre enn bredden.** `%-*s` pader ikke da, det står ett mellomrom igjen, og
     hele «navn detalj» leses som navnet -- med tempdir-stier og pid-er i seg, så dommen
     blir ulik mellom to sveip av identisk kode. Traff bl.a. `cp0`, `cp7`, `cp8`.
  2. **Detalj med dobbeltmellomrom**, f.eks. `cp10` sin `"1 row(s)  complete"`. Da faller
     splittet inni detaljen, uansett hvor kort navnet er.
  3. **Navn med dobbeltmellomrom.** Åtte finnes: `cp9` justerer metodenavn i en kolonne
     (`"method basename     is produced by a build"`), `test_review_findings` det samme.
     Å splitte på *første* dobbeltmellomrom i stedet ville kuttet disse på midten.

Ingen splitting på tomrom kan være riktig samtidig for alle tre. Så rapportlinja er til
mennesker, og en rød sjekk skriver **i tillegg** én tabulert linje `FAILED\\t<navn>` som
`run_suite` leser ordrett. Den koster ingenting i grønne kjøringer, og fjerner hele klassen
-- inkludert de navnene som ennå ikke er skrevet.

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
        if not ok:
            # For `mutate.py:run_suite`. Linja over er formatert for lesing og lar seg
            # ikke parse tilbake -- se modulens docstring. Denne er ordrett navnet.
            print("FAILED\t%s" % name)
        return ok

    return results, check
