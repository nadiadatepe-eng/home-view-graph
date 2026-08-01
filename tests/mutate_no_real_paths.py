#!/usr/bin/env python3
"""Mutasjonstest for NO-REAL-PATHS -- personvernporten.

Porten er den eneste skriftlige grunnen til at ingenting som publiseres navngir en ekte fil,
katalog, konto eller person. Den hadde **0 % mutasjonsdekning** fram til 2026-08-01.

**Hva mutasjonene treffer, og hvorfor det ikke er sirkulært.** Skanneren bor i testfila
selv -- det finnes ingen produksjonsmodul å svekke. Mutasjonene svekker derfor båndene, og
det som skal velte er *kanarifuglene*: sjekkene som beviser at et bånd kan fyre i det hele
tatt. Det er nettopp jobben deres. Et bånd som ikke matcher noe gjør hver eneste fil ren, og
uten kanarifuglen er det umulig å skille fra en ren fil. To av mutasjonene går utenom testfila
helt: `.gitignore` bestemmer hva som er publiserbart, og er like mye porten som skanneren er.

**Baseline verifisert før harnisset ble skrevet:** suiten er 15/15 i et kopiert tre uten
`.git`, altså på den `.gitignore`-baserte veien mutasjonstrærne bruker. Uten den kontrollen
ville hver mutasjon telt som drept av en port som allerede var rød.

Kjør:
    python3 tests/mutate_noreal.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # Det ekte korpusmaterialet slutter å være ignorert, og ville blitt publisert. Ikke en
    # hypotetisk feil: én linje ut av `.gitignore` er hele lekkasjen, og diffen ser ut som
    # opprydding.
    ("the real-corpus keys stop being ignored",
     ".gitignore",
     "tests/gold/real_corpus.py",
     "tests/gold/real_corpus_RENAMED.py",
     "none of it would be published"),

    # Digest-båndet matcher ingenting. Uten kanarifuglen er et hashsett som aldri treffer
    # umulig å skille fra et rent tre -- og det gjør hver fil under ren.
    ("the digest band can no longer match anything",
     "tests/test_no_real_paths.py",
     '    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]',
     '    return hashlib.sha256((text + "!").encode("utf-8")).hexdigest()[:16]',
     "the digest band can fire"),

    # Det generiske båndet slutter å matche. Denne mutasjonen OVERLEVDE da harnisset ble
    # skrevet: `_hits` som aldri treffer bestod hver sjekk i fila, fordi det generiske
    # båndet var det eneste uten kanarifugl. Kanarifuglen ble lagt til samme dag, og dette
    # er mutasjonen som holder den i live. Mønsteret båndet bærer er IKKE skrevet ut her --
    # denne fila er ikke unntatt slik `test_no_real_paths.py` er, og porten fanget nettopp
    # den kommentaren da den sto her i klartekst.
    ("the generic band stops matching",
     "tests/test_no_real_paths.py",
     "            if re.search(needle, stripped, re.IGNORECASE if\n"
     "                         needle.islower() else 0):",
     "            if False:  # mutated: the generic band matches nothing",
     "the generic band can fire"),

    # Dekningsartefakt-mønstrene slutter å treffe sin egen familie. `.coverage` er en
    # SQLite-base som navngir hver kildefil med absolutt sti, og den lå sporet i tre
    # commits.
    ("the coverage-artifact family stops matching `.coverage`",
     "tests/test_no_real_paths.py",
     r'    r"(^|/)\.coverage($|\.)",',
     r'    r"(^|/)\.coverageXX($|\.)",',
     "the coverage-artifact patterns can match"),

    # «Ingenting av det ville blitt publisert» er sant når det ikke finnes noe å publisere.
    # Sjekken på at materialet faktisk ligger på maskinen er det som skiller de to.
    ("the material checked for is renamed to files that do not exist",
     "tests/test_no_real_paths.py",
     '        "tests/gold/gold-set.tsv", "tests/gold/cp2-links.tsv",\n'
     '        "tests/gold/cp3-documents.tsv", "tests/gold/cp4-filenames.tsv",\n'
     '        "tests/gold/real_corpus.py", "tests/gold/sample_gold.py",\n'
     '        "tests/gold/sample_gold_round2.py", "tests/gold/sample_gold_round3.py",',
     '        "tests/gold/none-a.tsv", "tests/gold/none-b.tsv",\n'
     '        "tests/gold/none-c.tsv", "tests/gold/none-d.tsv",',
     "the real-corpus material exists locally"),

    # Filmengden skrumper til en håndfull. `all()` over nesten ingenting er også `True`,
    # og en port som så på fem filer melder like rent som en som så på alle.
    ("the publishable tree shrinks to five files",
     "tests/test_no_real_paths.py",
     "    return sorted(set(candidates) - ignored)",
     "    return sorted(set(candidates) - ignored)[:5]",
     "the publishable tree is non-empty"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_no_real_paths.py",
                 prefix="mutnrp-", timeout=300))
