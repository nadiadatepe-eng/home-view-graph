#!/usr/bin/env python3
"""Fem defekter et regelsett fant som fire andre verktøy gikk forbi.

`ocr delegate rule` resolverer et Python-regelsett; en gjennomgang mot det fant fem ting i
`mcp_server.py` og `models/m2_build.py` som **pyright, mypy, ruff og codex alle passerte**.
Ingen av dem er typefeil, og det er poenget: en typesjekker og et regelsett overlapper ikke.
Se CP-D i TODO.md.

Alle fem er etterprøvd mot koden før de ble skrevet ned her. To av dem har konsekvenser som
er verre enn de ser ut:

  * `no_open_guard` sammenlignet stier med `startswith` uten skilletegn, så en vakt over
    `.../Pictures` fyrte på `.../Pictures2`. En falsk positiv i en snubletråd stopper et bygg
    som ikke gjorde noe galt -- og `collection_of` i SAMME fil har alltid hatt den riktige
    formen (`parent == root or parent.startswith(root.rstrip("/") + os.sep)`).
  * `serve()` kalte `handle()` i `else`-grenen til en `try` som bare dekker `json.loads`.
    Gyldig JSON som ikke er et objekt -- `[]`, `"x"`, `3`, `null` -- ga `AttributeError` ut av
    løkka og drepte serveren. En klient kan avslutte serveren med fire tegn.

**Hver sjekk har en negativ kontroll.** En vakt som fyrer på alt er ikke en vakt, og en
handler som svarer med feil på alt er verre enn ingen handler.

Kjør:
    python3 tests/test_review_findings.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from homegraph.mcp_server import Server                      # noqa: E402

results, check = reporter(58)


# --- no_open_guard ---------------------------------------------------------
# Egen interpreter per case: `no_open_guard` dokumenterer selv at audit-hooks ikke kan
# fjernes, så to caser i samme prosess kjører under hverandres snubletråder.
_GUARD_PROBE = r'''
import os, sys
sys.path.insert(0, %r)
from homegraph.models.m2_build import ImageOpened, no_open_guard
root, target = sys.argv[1], sys.argv[2]
try:
    with no_open_guard(root):
        with open(target, "rb"):
            pass
except ImageOpened:
    print("ImageOpened")
except Exception as exc:
    print("%%s: %%s" %% (type(exc).__name__, exc))
else:
    print("silent")
'''


def _guard(root: str, target: str) -> str:
    proc = subprocess.run([sys.executable, "-c", _GUARD_PROBE % ROOT, root, target],
                          capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip().splitlines()
    return out[-1] if out else "no output: %s" % proc.stderr.strip()[-100:]


def t_guard_boundary() -> None:
    base = tempfile.mkdtemp()
    watched = os.path.join(base, "Pictures")
    sibling = os.path.join(base, "Pictures2")
    os.makedirs(watched)
    os.makedirs(sibling)
    inside = os.path.join(watched, "a.png")
    outside = os.path.join(sibling, "b.txt")
    for p in (inside, outside):
        with open(p, "wb") as fh:
            fh.write(b"x")

    # Regresjonen: et prefiks-treff uten skilletegn.
    got = _guard(watched, outside)
    check("guard: en søsterkatalog med samme prefiks er stille",
          got == "silent", got)

    # Negativ kontroll -- uten denne består testen for en vakt som aldri fyrer.
    got = _guard(watched, inside)
    check("guard: en fil under rota fyrer fortsatt",
          got == "ImageOpened", got)


# --- kopi-markører ---------------------------------------------------------
def t_copy_markers() -> None:
    from homegraph.models import m2_build

    strip = getattr(m2_build, "strip_copy_markers", None)
    if strip is None:
        check("copy: `strip_copy_markers` finnes", False,
              "markørlogikken er fortsatt inline i link_images()")
        return
    check("copy: `strip_copy_markers` finnes", True)

    cases = [("photo_copy2", "photo"), ("photo_copy", "photo"),
             ("photo-copy", "photo"), ("photo_kopi", "photo"),
             ("photo copy", "photo"), ("photo", "photo")]
    bad = [(s, strip(s), want) for s, want in cases if strip(s) != want]
    check("copy: hver markør strippes helt", not bad,
          "; ".join("%s -> %r, ventet %r" % b for b in bad))

    # Å fjerne én markør kan gjenskape en annen: `_copy` ut av `photo_co_copypy2`
    # etterlater `photo_copy2`. Lengste-først alene holder ikke, og ett regex-pass
    # heller ikke -- `re.sub` skanner ikke resultatet sitt på nytt. Egenskapen som
    # betyr noe er ikke hvilken streng som kommer ut, men at ingen markør blir igjen.
    leftovers = [s for s in ("photo_co_copypy2", "a_co_copypy2_kopi", "x_cop_copyy2")
                 if any(m in strip(s) for m in m2_build.COPY_MARKERS)]
    check("copy: ingen markør overlever strippingen", not leftovers,
          "; ".join("%s -> %r" % (s, strip(s)) for s in leftovers))

    # Negativ kontroll: en stamme uten markør skal ikke røres.
    check("copy: en stamme uten markør er urørt",
          strip("photocopy_machine") == "photocopy_machine",
          repr(strip("photocopy_machine")))


# --- JSON-RPC-laget --------------------------------------------------------
def _server() -> Server:
    return Server.__new__(Server)          # ingen lagre åpnes


def t_non_object_request() -> None:
    srv = _server()
    for payload in ("[]", '"x"', "3", "null"):
        try:
            reply = srv.handle(json.loads(payload))
            code = (reply or {}).get("error", {}).get("code")
            check("rpc: %-4s svarer -32600 i stedet for å kaste" % payload,
                  code == -32600, "code=%r" % (code,))
        except Exception as exc:
            check("rpc: %-4s svarer -32600 i stedet for å kaste" % payload, False,
                  "%s: %s" % (type(exc).__name__, exc))

    # Negativ kontroll: et ekte objekt skal fortsatt behandles normalt.
    reply = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    check("rpc: en gyldig forespørsel behandles fortsatt",
          isinstance(reply, dict) and "error" not in reply,
          str(reply)[:60])


def t_model_spec_needs_path() -> None:
    from homegraph import mcp_server

    parse = getattr(mcp_server, "parse_model_specs", None)
    if parse is None:
        check("spec: `parse_model_specs` finnes", False,
              "parsingen er fortsatt inline i main()")
        return
    check("spec: `parse_model_specs` finnes", True)

    try:
        parse(["m3"])
        check("spec: `m3` uten =PATH avvises", False, "ga ingen feil")
    except ValueError as exc:
        check("spec: `m3` uten =PATH avvises", True, str(exc)[:50])
    except Exception as exc:
        check("spec: `m3` uten =PATH avvises", False,
              "feil unntakstype: %s" % type(exc).__name__)

    # Negativ kontroll: den gyldige formen skal fortsatt gå gjennom.
    try:
        got = parse(["m3=/tmp/x.db"])
        check("spec: `m3=/tmp/x.db` går gjennom", got == {"m3": "/tmp/x.db"}, repr(got))
    except Exception as exc:
        check("spec: `m3=/tmp/x.db` går gjennom", False,
              "%s: %s" % (type(exc).__name__, exc))

    # `homegraph mcp` -- den dokumenterte kommandoen -- går gjennom `cli.cmd_mcp`, ikke
    # gjennom `mcp_server.main`. Første fiks lukket bare den siste, og hadde blitt
    # rapportert som ferdig. Sjekket er på OPPFØRSEL, ikke på kildetekst: første utgave
    # lette etter `partition("=")` i kilden og slo ut på kommentaren som forklarte fiksen.
    from homegraph import cli

    class _Args:
        model = ["m3"]                     # ingen =PATH
        mesh_db = None

    try:
        rc = cli.cmd_mcp(_Args())
        # Hadde parsingen sluppet den gjennom, ville `Server(...).serve()` blokkert på
        # stdin. At vi kom hit med en returkode er i seg selv beviset.
        check("spec: `homegraph mcp` avviser `m3` uten =PATH", rc == 2, "rc=%r" % (rc,))
    except SystemExit as exc:
        check("spec: `homegraph mcp` avviser `m3` uten =PATH", exc.code not in (0, None),
              "SystemExit(%r)" % (exc.code,))
    except Exception as exc:
        check("spec: `homegraph mcp` avviser `m3` uten =PATH", False,
              "%s: %s" % (type(exc).__name__, exc))


def t_tool_error_is_not_argument_error() -> None:
    """En TypeError fra verktøykroppen skal ikke rapporteres som -32602."""
    srv = _server()

    def boom(**_kwargs):
        raise TypeError("noe dypt inne gikk galt")

    srv.mesh_search = boom                                   # type: ignore[method-assign]
    reply = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "mesh_search", "arguments": {}}})
    code = (reply or {}).get("error", {}).get("code")
    check("rpc: TypeError fra kroppen er ikke -32602 bad arguments",
          code != -32602, "code=%r" % (code,))

    # Negativ kontroll: et ekte argumentavvik SKAL fortsatt gi -32602.
    def strict(*, needed):
        return needed

    srv.mesh_path = strict                                   # type: ignore[method-assign]
    reply = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "mesh_path", "arguments": {"wrong": 1}}})
    code = (reply or {}).get("error", {}).get("code")
    check("rpc: et ekte argumentavvik gir fortsatt -32602",
          code == -32602, "code=%r" % (code,))


def main() -> int:
    t_guard_boundary()
    t_copy_markers()
    t_non_object_request()
    t_model_spec_needs_path()
    t_tool_error_is_not_argument_error()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_review_findings():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
