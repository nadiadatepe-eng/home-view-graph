#!/usr/bin/env python3
"""Trekk ut per-mutasjon-dommen fra et mutasjonssveip, som kanonisk tekst.

Akseptansetesten for å kollapse de 22 driverne til én er IKKE at totalene
stemmer -- en driver som slutter å kjøre halve settet rapporterer også 0
survived. Testen er at HVER mutasjon får samme dom som før.

Bruk:
    sweep_verdicts.py <sveipekatalog>            # skriv kanonisk dom
    sweep_verdicts.py <før> <etter>              # diff to sveip
"""
import os
import re
import sys

# `killed    <navn>  -> <gate>` / `SURVIVED  <navn>  (<grunn>)` / `CRASH ...`
VERDICT = re.compile(
    r"^(killed|misattributed|SURVIVED|CRASH|MISATTRIB\w*)\s+(.*?)\s*$", re.I)
SUMMARY = re.compile(r"^\d+ killed by a named gate")


def verdicts(path):
    """{harnesse: [(dom, mutasjonsnavn), ...]} -- rekkefølge bevart."""
    out = {}
    for fn in sorted(os.listdir(path)):
        if not fn.startswith("mutate_") or not fn.endswith(".txt"):
            continue
        rows = []
        for line in open(os.path.join(path, fn), encoding="utf-8"):
            if SUMMARY.match(line.strip()):
                continue
            m = VERDICT.match(line.rstrip("\n"))
            if not m:
                continue
            kind, rest = m.group(1).lower(), m.group(2)
            # Navnet er alt før "->" (gate) eller "(" (grunn); begge kan mangle.
            name = re.split(r"\s+->\s+|\s+\(", rest)[0].strip()
            if name:
                rows.append((kind, name))
        out[fn[:-4]] = rows
    return out


def canonical(v):
    return "\n".join("%s\t%s\t%s" % (h, k, n)
                     for h in sorted(v) for k, n in v[h])


def main(argv):
    if len(argv) == 2:
        v = verdicts(argv[1])
        print(canonical(v))
        print("\n# %d harnesser, %d mutasjoner"
              % (len(v), sum(len(r) for r in v.values())), file=sys.stderr)
        return 0

    a, b = verdicts(argv[1]), verdicts(argv[2])
    ha, hb = set(a), set(b)
    rc = 0
    if ha - hb:
        print("HARNESSER BORTE: %s" % sorted(ha - hb)); rc = 1
    if hb - ha:
        print("HARNESSER NYE:   %s" % sorted(hb - ha)); rc = 1
    for h in sorted(ha & hb):
        da, db = dict((n, k) for k, n in a[h]), dict((n, k) for k, n in b[h])
        for n in sorted(set(da) | set(db)):
            if n not in db:
                print("%s: MUTASJON BORTE  %s (var %s)" % (h, n, da[n])); rc = 1
            elif n not in da:
                print("%s: MUTASJON NY     %s (%s)" % (h, n, db[n])); rc = 1
            elif da[n] != db[n]:
                print("%s: DOM ENDRET      %s: %s -> %s" % (h, n, da[n], db[n])); rc = 1
    na = sum(len(r) for r in a.values())
    nb = sum(len(r) for r in b.values())
    print("\n%d -> %d mutasjoner, %d -> %d harnesser: %s"
          % (na, nb, len(ha), len(hb), "IDENTISK" if rc == 0 else "AVVIK"))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
