#!/usr/bin/env python3
"""Én mutasjonsdriver for alle harnessene. Ikke et harnisk selv.

Hvert `mutate_*.py` bar sin egen kopi av denne løkka -- 22 kopier av ~72 linjer, funnet av
`ocr delegate rule`-gjennomgangen 2026-07-26. Kopiene hadde allerede drevet fra hverandre på
tre målte punkter, og alle tre er den samme typen: noe som var sant for noen filer, og som
ingen la merke til sluttet å være sant for resten.

  * **`CRASH-ONLY`-blokka manglet i 8 av 22.** `cp*` skriver den, `h*`/`i*` ikke. Metrikken
    finnes i alle 22 -- det er detaljen ved feil som forsvant.
  * **To oppsummeringsformater.** `cp*`: «killed by a different gate, N detected only by a
    crash». `h*`/`i*`: «by a different gate, N crash-only». Et skript skrevet mot det ene
    rapporterte stille «439 killed, av 0 mutasjoner» -- feil tall, ingen feilmelding.
  * **`.git` kopiert inn i mutasjonstreet i 14 av 22.** Rent sløseri: historikken kopieres
    én gang per mutasjon, 442 ganger per sveip.

`MUTATIONS`-listene ble ikke rørt. De er bevismaterialet; dette er bare løkka som kjører dem.

Akseptansetesten for kollapsen var ikke at totalene stemte -- en driver som slutter å kjøre
halve settet rapporterer også «0 survived». Den var at **hver enkelt mutasjon får samme dom
som før**, sjekket med `tools/sweep_verdicts.py` mot grunnlinja på 442.

Et harnisk bruker den slik, og eier ellers bare sin egen `MUTATIONS`-liste:

    from mutate import run
    if __name__ == "__main__":
        sys.exit(run(MUTATIONS, "test_cp0.py", prefix="mut0-", timeout=600))
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bredden var 44 i `cp*` og 52 i `h*`/`i*`. Én verdi her; `sweep_verdicts.py` leser navnet
# fram til `->` eller `(`, så bredden påvirker ikke sammenligningen med grunnlinja.
WIDTH = 52


def run_suite(tree, test_file, timeout):
    """Kjør ett testskript i et mutert tre. Returner settet av røde sjekker.

    `<timeout>` og `<crash> ...` er med i samme sett med vilje: kalleren skiller dem fra
    ekte gate-avslag på prefikset, og den skillelinjen er hele poenget med harnesset.
    En krasj er ikke en gate som sa nei.

    **Navnet leses fra `report.py` sin `FAILED\\t<navn>`-linje, ikke fra rapporten.** Fram
    til 2026-07-26 sto det `line[4:].strip().rsplit("  ", 1)[0].strip()` her -- «alt før siste
    dobbeltmellomrom» i den formaterte `FAIL`-linja. Det er feil på tre måter som alle
    fantes i repoet: navn lengre enn kolonnebredden mister padingen og sluker detaljen
    (`cp0`, `cp7`, `cp8` -- med tempdir-stier i seg, altså ulik dom mellom to sveip av
    identisk kode); detaljer som selv har dobbeltmellomrom flytter splittet inn i detaljen
    (`cp10`); og åtte navn har dobbeltmellomrom i seg fordi de justerer en kolonne (`cp9`,
    `test_review_findings`), så «første dobbeltmellomrom» er like galt. Ingen splitting på
    tomrom kan være riktig for alle tre. Se `report.py` for garantien denne hviler på.
    """
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(tree, "tests", test_file)],
            capture_output=True, text=True, cwd=tree, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"<timeout>"}
    red = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAILED\t"):
            red.add(line[len("FAILED\t"):])
    if proc.returncode != 0 and not red:
        red.add("<crash> %s" % (proc.stderr.strip().splitlines() or [""])[-1])
    return red


# Kopieres aldri inn i et mutasjonstre, uansett harnesse. `.git` sto i 8 av 22 -- de 14
# andre kopierte historikken 442 ganger per sveip for noe ingen test leser.
ALWAYS_IGNORED = ("__pycache__", ".git")


def run(mutations, test_file, *, prefix, timeout=300, ignore=()):
    """Kjør hver mutasjon mot `test_file`. Returner 1 hvis noen overlevde eller krasjet.

    `ignore` er harnessens EGNE ekstra mønstre, i tillegg til `ALWAYS_IGNORED`. Parameteren
    finnes fordi den første kollapsen slo alle 22 sammen til én hardkodet liste og dermed
    stille droppet det fire harnesser utelot med vilje: `cp1` sine `*.tsv` (de 72 MB
    gullfixturene) og `.homegraph`, og `cp9`/`cp10`/`cp11` sine `.venv` og verktøycacher.
    Akseptansetesten så det ikke -- den sammenligner dommen per mutasjon, og et større
    kopiert tre gir samme dom. Den fant det ikke fordi den ikke måler det.
    """
    survived, killed, misattributed, crashes = [], [], [], []
    patterns = ALWAYS_IGNORED + tuple(ignore)

    for name, rel, needle, repl, expected in mutations:
        tree = tempfile.mkdtemp(prefix=prefix,
                                dir=os.path.expanduser("~/.homegraph"))
        try:
            shutil.copytree(ROOT, os.path.join(tree, "pkg"),
                            ignore=shutil.ignore_patterns(*patterns))
            work = os.path.join(tree, "pkg")
            target = os.path.join(work, rel)
            with open(target, encoding="utf-8") as fh:
                src = fh.read()
            if needle not in src:
                # Teller som overlevende, ikke som hoppet over: en nål som ikke finnes
                # betyr at mutasjonen ikke ble prøvd, og en uprøvd mutasjon er ikke bevis
                # for noe som helst.
                print("SKIP      %-*s needle missing in %s" % (WIDTH, name, rel))
                survived.append((name, "needle missing"))
                continue
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(needle, repl, 1))

            red = run_suite(work, test_file, timeout)
            crashed = any(r.startswith("<crash>") or r == "<timeout>" for r in red)
            gate_red = [r for r in red
                        if not r.startswith("<crash>") and r != "<timeout>"]

            if not red:
                print("SURVIVED  %-*s suite still green" % (WIDTH, name))
                survived.append((name, "suite green"))
            elif any(expected in r for r in gate_red):
                print("killed    %-*s -> %s" % (WIDTH, name, expected))
                killed.append(name)
            elif gate_red:
                print("misattrib %-*s -> %s (expected %r)"
                      % (WIDTH, name, sorted(gate_red)[:1], expected))
                misattributed.append(name)
            elif crashed:
                print("CRASH     %-*s -> %s" % (WIDTH, name, sorted(red)[:1]))
                crashes.append(name)
            else:
                print("SURVIVED  %-*s unclassified" % (WIDTH, name))
                survived.append((name, "unclassified"))
        finally:
            shutil.rmtree(tree, ignore_errors=True)

    print("\n%d killed by a named gate, %d killed by a different gate, "
          "%d detected only by a crash, %d survived  (of %d)"
          % (len(killed), len(misattributed), len(crashes), len(survived),
             len(mutations)))
    # Alle tre blokkene skrives av alle harnessene nå. `CRASH-ONLY` fantes bare i 14 av 22,
    # så åtte kunne rapportere et krasjtall uten å si hvilke mutasjoner det gjaldt.
    if crashes:
        print("CRASH-ONLY -- no gate said no; the suite died before asserting:")
        for name in crashes:
            print("  %s" % name)
    if survived:
        print("SURVIVORS -- these gates do not test what they claim:")
        for name, why in survived:
            print("  %s  (%s)" % (name, why))
    return 1 if (survived or crashes) else 0
