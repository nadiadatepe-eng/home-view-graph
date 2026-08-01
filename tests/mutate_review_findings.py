#!/usr/bin/env python3
"""Mutasjonstest for REVIEW-FINDINGS -- de fem defektene fire verktøy gikk forbi.

Suiten hadde **0 % mutasjonsdekning** i sveipet 2026-08-01: 30 sjekker, ikke ett harnisk.
Den er ikke en checkpoint-suite, og det er nettopp derfor den trengte et: den er den eneste
skriftlige grunnen til at fem konkrete defekter ikke kan komme tilbake, og ingen hadde
prøvd å sette dem tilbake for å se om sjekkene fortsatt sier nei.

Halvparten av mutasjonene her går på de negative kontrollene, ikke på defektene. En vakt som
fyrer på alt består hver eneste positive sjekk i suiten, og en parser som avviser alt består
alle fire avvisningssjekkene. Kontrollene finnes for det -- så de skal kunne velte, ellers
måler suiten en billigere egenskap enn den påstår.

Kjør:
    python3 tests/mutate_review_findings.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # --- no_open_guard: grensen mellom en rot og en søster med samme prefiks ---

    # Selve defekten: et rent prefiks-treff. En vakt over `.../Pictures` fyrer på
    # `.../Pictures2`, og en falsk positiv i en snubletråd stopper et bygg som ikke gjorde
    # noe galt.
    ("the open guard compares paths without a separator",
     "homegraph/models/m2_build.py",
     'full.startswith(os.path.join(r, ""))',
     'full.startswith(r)',
     "guard: en søsterkatalog med samme prefiks er stille"),

    # Den negative kontrollen. Uten den består suiten for en vakt som aldri fyrer -- og
    # «M2 åpnet aldri et bilde» ville da vært sant fordi ingen så etter.
    ("the open guard stops firing at all",
     "homegraph/models/m2_build.py",
     'if any(full == r or full.startswith(os.path.join(r, "")) for r in watched):',
     'if False:  # mutated: the guard never fires',
     "guard: en fil under rota fyrer fortsatt"),

    # --- kopi-markører: at ingen markør blir liggende igjen ---

    # Ett `re.sub`-pass i stedet for gjentakelse til strengen står stille. `re.sub` skanner
    # ikke sitt eget resultat, så `_copy` ut av `photo_co_copypy2` etterlater `photo_copy2`
    # -- en markør overlever strippingen som skulle fjerne den.
    ("the copy stripper does one pass instead of repeating",
     "homegraph/models/m2_build.py",
     '    while True:\n'
     '        after = _COPY_RE.sub("", stem)\n'
     '        if after == stem:\n'
     '            return after.strip()\n'
     '        stem = after',
     '    return _COPY_RE.sub("", stem).strip()  # mutated: single pass',
     "copy: ingen markør overlever strippingen"),

    # Den negative kontrollen: en markør uten skilletegn tar en stamme som aldri var en
    # kopi. `photocopy_machine` blir `photo_machine`, og to urelaterte bilder lenkes som
    # LIKELY_COPY. Dette er den samme feilen som guard-mutasjonen over, ett lag opp.
    ("a bare `copy` marker is added without its separator",
     "homegraph/models/m2_build.py",
     'COPY_MARKERS = tuple(sorted(("_copy", "-copy", "_kopi", " copy", "_copy2"),',
     'COPY_MARKERS = tuple(sorted(("_copy", "-copy", "_kopi", " copy", "_copy2", "copy"),',
     "copy: en stamme uten markør er urørt"),

    # --- JSON-RPC: gyldig JSON som ikke er et objekt ---

    # Defekten som lot en klient avslutte serveren med fire tegn: `null` er gyldig JSON,
    # `.get()` på den er en AttributeError, og den gikk ut av løkka i `serve()`.
    # `null` er valgt framfor `[]` fordi navnet på sjekken padder til bredde 4 -- en
    # kortere nyttelast ville gjort forventet navn avhengig av paddingen.
    ("the non-object request guard is removed",
     "homegraph/mcp_server.py",
     'if not isinstance(request, dict):',
     'if False:  # mutated: no non-object guard',
     "rpc: null svarer -32600 i stedet for å kaste"),

    # Den negative kontrollen: en vakt som avviser ALT svarer også -32600 på `null`, og
    # ville bestått alle fire nyttelastsjekkene uten å være en server.
    ("every request is answered as an invalid request",
     "homegraph/mcp_server.py",
     'if not isinstance(request, dict):',
     'if True:  # mutated: every request is invalid',
     "rpc: en gyldig forespørsel behandles fortsatt"),

    # --- JSON-RPC: hvem som får skylda for en TypeError ---

    # Defekten: `except TypeError` rundt selve kallet dekker hele verktøykroppen, så en
    # serverfeil dypt inne rapporteres som -32602 «bad arguments» og klandrer klienten.
    ("a TypeError from the tool body is blamed on the client again",
     "homegraph/mcp_server.py",
     '                result = _text(fn(**args))\n'
     '            except Exception as exc:',
     '                result = _text(fn(**args))\n'
     '            except TypeError as exc:\n'
     '                return self._error(rid, -32602, "bad arguments: %s" % exc)\n'
     '            except Exception as exc:',
     "rpc: TypeError fra kroppen er ikke -32602 bad arguments"),

    # Den andre retningen, og grunnen til at fiksen ikke bare kan være «fjern -32602»:
    # uten bindingssjekken blir et ekte argumentavvik en verktøyfeil, og klienten får aldri
    # vite at den kalte feil.
    ("the signature bind check is dropped",
     "homegraph/mcp_server.py",
     '                inspect.signature(fn).bind(**args)',
     '                pass  # mutated: no bind check',
     "rpc: et ekte argumentavvik gir fortsatt -32602"),

    # --- --model NAME=PATH ---

    # Defekten: `"m3".partition("=")` gir tom sti, modellen registreres mot `""`, og
    # feilen dukker opp langt unna som en urelatert lagerfeil. `visualize` skrev en
    # 18 644 byte stor tegning av en tom graf og returnerte 0.
    ("a pathless model spec is accepted again",
     "homegraph/mcp_server.py",
     'if not sep or not name.strip() or not path.strip():',
     'if False:  # mutated: nothing is refused',
     "spec: `m3` uten =PATH avvises"),

    # Den positive kontrollen. Uten den består alle fire avvisningssjekkene for en parser
    # som avviser hver eneste spec, gyldige inkludert.
    ("every model spec is refused, valid ones included",
     "homegraph/mcp_server.py",
     'if not sep or not name.strip() or not path.strip():',
     'if True:  # mutated: everything is refused',
     "spec: en gyldig spec slipper fortsatt gjennom"),

    # `=` er der, men den ene siden er tom. Å bare kreve skilletegnet er den halve fiksen
    # som ser ferdig ut: `m3=` gir fortsatt tom sti.
    ("only the separator is required, not a non-empty path",
     "homegraph/mcp_server.py",
     'if not sep or not name.strip() or not path.strip():',
     'if not sep:  # mutated: an empty side is allowed',
     "spec: `_models_from(['m3='])` avvises"),
]

# Ikke dekket, med vilje skrevet ned framfor stilltiende utelatt: at `visualize` avviser
# FØR den skriver. Den egenskapen kommer av at `_models_from(args.model)` evalueres som
# argument til `render(...)`, og en mutasjon som snur den rekkefølgen må skrive om kallet
# framfor å bytte en linje. Sjekken finnes i suiten, men ingen mutasjon sikter på den her.

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_review_findings.py",
                 prefix="mutrev-", timeout=180))
