#!/usr/bin/env python3
"""M2 -- images. Nothing in this file ever opens an image.

The invariant is `stat()` only: name, path, size, mtime. No decoding, no EXIF,
no perceptual hash, no OCR, no thumbnails. CP-4 verifies it two ways -- a
Python audit hook inside the process and `strace` outside it -- because an
invariant that is merely intended is an invariant that will be broken by the
first convenience import.

What it costs: the content of the plots is out of reach. A figure whose axis
labels are unreadable stays unreadable to the graph, and M5 cannot deduplicate
images by content hash, so two identical files in two directories remain two
nodes joined by LIKELY_COPY rather than merged.

What it buys: the 208 MB PNG is never loaded, decompression bombs are not a
threat model, and a full build takes about a second instead of hours.

The filenames carry more than expected. `03122025_1.png` is a date and a series
index. `161212025_44_3360x2100.png` is a mistyped date, an index and a
resolution. `Hector_270519_25_1.jpeg` is a date that only parses correctly if
you notice the obvious reading puts it in the future. The grammar lives in
rules/filenames.toml; this module is the machinery that applies it.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta

RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "rules", "filenames.toml")

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
         "VIII": 8, "IX": 9, "X": 10}


@dataclass
class NameInfo:
    path: str
    stem: str
    date: str | None = None
    date_kind: str | None = None
    time: str | None = None
    indices: list = field(default_factory=list)
    resolution: str | None = None
    dpi: int | None = None
    copy: bool = False
    variant: str | None = None
    malformed_date: bool = False
    series_stem: str | None = None

    def as_row(self):
        return {
            "date": self.date or "-",
            "kind": ("malformed" if self.malformed_date
                     else (self.date_kind or "-")),
            "indices": ",".join(str(i) for i in self.indices) or "-",
            "resolution": self.resolution or "-",
            "dpi": str(self.dpi) if self.dpi else "-",
            "copy": "y" if self.copy else "n",
            "variant": self.variant or "-",
        }


class FilenameParser:
    def __init__(self, rules_path=RULES, today=None):
        with open(rules_path, "rb") as fh:
            self.rules = tomllib.load(fh)
        self.today = today or date.today()
        d = self.rules["dates"]
        self.re_iso_dt = re.compile(d["iso_dt"])
        self.re_iso = re.compile(d["iso"])
        self.re_ddmmyyyy = re.compile(d["ddmmyyyy"])
        self.re_yymmdd = re.compile(d["yymmdd"])
        self.re_year = re.compile(d["year"])
        self.re_malformed = re.compile(d["malformed"])
        self.months = {m: i for i, m in enumerate(
            ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"], 1)}
        self.months.update({m: i for i, m in enumerate(
            ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"], 1)})
        self.months.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
                            "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
                            "okt": 10, "nov": 11, "dec": 12, "des": 12})
        m = self.rules["markers"]
        self.re_res = re.compile(m["resolution"])
        self.re_dpi = re.compile(m["dpi"], re.IGNORECASE)
        self.copy_markers = m["copy"]
        self.variants = m["variants"]
        s = self.rules["series"]
        self.re_index = re.compile(s["index"])
        self.re_roman = re.compile(s["roman"])
        self.re_nr = re.compile(s["nr"])
        self.yy_fallback = d["yymmdd_vs_ddmmyy"]

    # -- helpers ----------------------------------------------------------

    def _valid(self, y, mo, dy):
        try:
            return date(y, mo, dy)
        except ValueError:
            return None

    def _blank(self, text, span):
        """Remove a consumed token so later patterns cannot re-read its digits.

        This is the same trick as blanking code spans in M3, and for the same
        reason: `3360x2100` must stop being four digits before the index regex
        looks for trailing numbers.
        """
        return text[:span[0]] + " " * (span[1] - span[0]) + text[span[1]:]

    # -- main -------------------------------------------------------------

    def parse(self, path):
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]
        info = NameInfo(path=path, stem=stem)
        work = stem

        for marker in self.copy_markers:
            if marker.lower() in work.lower():
                info.copy = True
                idx = work.lower().index(marker.lower())
                work = self._blank(work, (idx, idx + len(marker)))
                break

        m = self.re_dpi.search(work)
        if m:
            info.dpi = int(m.group(1))
            work = self._blank(work, m.span())

        m = self.re_res.search(work)
        if m:
            info.resolution = "%sx%s" % (m.group(1), m.group(2))
            work = self._blank(work, m.span())

        m = self.re_malformed.search(work)
        if m:
            info.malformed_date = True
            work = self._blank(work, m.span())
        else:
            work = self._parse_date(work, info)

        for name in self.variants:
            if re.search(r"[_\- ]%s(?=$|[_\-. ])" % re.escape(name),
                         work, re.IGNORECASE):
                info.variant = name
                work = re.sub(r"[_\- ]%s(?=$|[_\-. ])" % re.escape(name),
                              " " * (len(name) + 1), work, flags=re.IGNORECASE)
                break

        m = self.re_nr.search(work)
        if m:
            info.indices.append(int(m.group(1)))
            work = self._blank(work, m.span())
        m = self.re_roman.search(work)
        if m and m.group(1) in ROMAN:
            info.indices.append(ROMAN[m.group(1)])
            work = self._blank(work, m.span())

        for m in self.re_index.finditer(work):
            info.indices.append(int(m.group(1)))
        # Sliced from the ORIGINAL stem, not the blanked one. Offsets match
        # because blanking preserves length -- that is what it is for.
        #
        # Slicing `work` instead was a real bug and a silent one: for
        # `03122025_1` the whole prefix is a date, so the blanked stem is
        # nothing but spaces and the series key came out empty. Every
        # date-named image in a folder collapsed into one series, and the
        # collision was invisible because an empty title matches nothing.
        first = self.re_index.search(work)
        info.series_stem = (stem[:first.start()].strip(" _-#")
                            if first else stem.strip(" _-#"))
        return info

    def _parse_date(self, work, info):
        m = self.re_iso_dt.search(work)
        if m:
            d = self._valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d:
                info.date, info.date_kind = d.isoformat(), "iso_dt"
                info.time = "%s:%s:%s" % (m.group(4), m.group(5), m.group(6))
                return self._blank(work, m.span())

        m = self.re_iso.search(work)
        if m:
            d = self._valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d:
                info.date, info.date_kind = d.isoformat(), "iso"
                return self._blank(work, m.span())

        m = self.re_ddmmyyyy.search(work)
        if m:
            d = self._valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if d:
                info.date, info.date_kind = d.isoformat(), "ddmmyyyy"
                return self._blank(work, m.span())

        m = self.re_yymmdd.search(work)
        if m:
            a, b, c = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            d = self._valid(2000 + a, b, c)
            kind = "yymmdd"
            cutoff = self.today + timedelta(
                days=self.rules["dates"]["future_cutoff_days"])
            if self.yy_fallback and (d is None or d > cutoff):
                # The obvious reading lands in the future; re-read as DDMMYY.
                alt = self._valid(2000 + c if c < 50 else 1900 + c, b, a)
                if alt and alt <= cutoff:
                    d, kind = alt, "ddmmyy"
            if d and d <= cutoff:
                info.date, info.date_kind = d.isoformat(), kind
                return self._blank(work, m.span())

        low = work.lower()
        for name, num in self.months.items():
            m = re.search(r"[_\- ]%s[_\- ]((?:19|20)\d{2})" % name, low)
            if m:
                info.date = "%s-%02d" % (m.group(1), num)
                info.date_kind = "monthname"
                return self._blank(work, m.span())

        m = self.re_year.search(work)
        if m:
            info.date = m.group(1) + m.group(2)
            info.date_kind = "year"
            return self._blank(work, m.span())
        return work


def stat_only(path):
    """The only filesystem call M2 is allowed to make on an image."""
    st = os.stat(path)
    return {"size": st.st_size, "mtime": st.st_mtime}
