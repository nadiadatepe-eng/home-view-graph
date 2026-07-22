#!/usr/bin/env python3
"""M3 -- markdown. Built first because it is the only model with real edges.

`[[wikilink]]` is a relation somebody wrote down on purpose. Every other model
infers its edges from similarity or co-location, which means a bug there
produces edges that look reasonable and cannot be checked. Here the answer key
is the text itself, so M3 validates the graph model while the others would only
validate extraction.

The hard part is not finding `[[...]]`. It is not finding it where it isn't:

    Link between pages with `[[wikilinks]]` (Obsidian-style, no path)

That line documents the syntax. A regex sweep turns it into an edge to a page
named "wikilinks" that nobody wrote and nobody will notice. In the wiki as it
stands, **every single one** of the four apparently-broken link targets is a
case like this. Strip code spans first or the broken-link count is pure noise.

Subtypes, and one correction to the plan: `.codex/` was expected to supply
~3 247 transcript files. It supplies none. Those files are vendored plugin
documentation under `.codex/.tmp/`, excluded by the corpus layer, and the real
transcripts live in `logs_2.sqlite` -- a different extractor for a later day.
The subtype and its search filter are implemented anyway, because the filter
must be able to say no whether or not anything currently trips it.
"""
from __future__ import annotations

import functools
import os
import re

from ..config import home_root

# Fenced blocks first, then inline spans. Order matters: a stray backtick
# inside a fence would otherwise open a phantom inline span across the file.
RE_FENCE = re.compile(r"^(?P<fence>```+|~~~+).*?^(?P=fence)\s*$",
                      re.MULTILINE | re.DOTALL)
RE_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
RE_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

RE_WIKILINK = re.compile(r"!?\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
RE_EMBED_WIKI = re.compile(r"!\[\[([^\]|#]+?)(?:[#|][^\]]*)?\]\]")
RE_MDLINK = re.compile(r"(!?)\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)")
RE_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
RE_INLINE_TAG = re.compile(r"(?:(?<=\s)|\A)#([A-Za-zÆØÅæøå][\w/-]{1,40})")
# The corpus root is injected rather than hardcoded, so a mention of
# `~/Pictures/x.png` is recognised on any machine. Built lazily and cached: the
# root is not known at import time.
@functools.lru_cache(maxsize=8)
def _pathish(root):
    return re.compile(
        r"(?:(?<=\s)|\A|(?<=[`\'\"(]))"
        r"((?:~|" + re.escape(root) + r"|\./|\.\./)[\w./~@+-]*[\w/])")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"}


def blank_code(text):
    """Replace code with spaces of the same length.

    Same length, not removal: offsets stay valid, so a heading's position and a
    link's position still line up with the original document. Blanking is also
    why `[[wikilinks]]` inside backticks simply is not there when the link
    regex runs, rather than being found and then filtered by some second pass
    that has to re-derive whether it was in code.
    """
    def blank(m):
        return re.sub(r"\S", " ", m.group(0))
    return RE_INLINE_CODE.sub(blank, RE_FENCE.sub(blank, text))


def parse_frontmatter(text):
    """Return (mapping, body). Never raises.

    Deliberately a small parser rather than PyYAML. M1 already carries six
    format dependencies and the plan expects one of them to be missing at some
    point; M3 does not need to add a seventh for `key: value` and `[a, b, c]`.
    Anything it cannot parse is reported, not raised -- a malformed header in
    one note must not take down a build over 801 files.
    """
    m = RE_FRONTMATTER.match(text)
    if not m:
        return {}, text, []
    body = text[m.end():]
    data, problems, key = {}, [], None
    for lineno, raw in enumerate(m.group(1).splitlines(), start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith((" ", "\t")) and key:
            continue                      # nested mapping; keep the parent key
        if ":" not in raw:
            problems.append((lineno, raw.strip()))
            continue
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in
                         value[1:-1].split(",") if v.strip()]
        elif value:
            data[key] = value.strip("'\"")
        else:
            data[key] = ""
    return data, body, problems


def page_name(path):
    """Wikilink target name for a file: basename without extension."""
    return os.path.splitext(os.path.basename(path))[0]


def resolve_target(target, src_path, index):
    """Pick which file a `[[target]]` means when several share the name.

    A flat wikilink namespace over a whole home directory collides constantly:
    `shared-note` exists as both `wiki/raw/shared-note.md` (source material)
    and `wiki/wiki/summaries/shared-note.md` (the curated page). Sorting the
    candidates and taking the first picked `raw/` -- alphabetically reasonable,
    semantically wrong, and silent.

    Nearest wins instead: the candidate sharing the longest path prefix with
    the linking file. A note in `wiki/wiki/concepts/` resolves to its sibling in
    `wiki/wiki/summaries/` rather than to the copy two directories away. Ties
    break on shallower path, then lexicographically, so it stays deterministic.
    """
    candidates = index.get(target)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    src_parts = src_path.split(os.sep)

    def shared(path):
        parts = path.split(os.sep)
        n = 0
        for a, b in zip(src_parts, parts):
            if a != b:
                break
            n += 1
        return n

    return min(candidates, key=lambda c: (-shared(c), c.count(os.sep), c))


# Machine-written markdown. Separated from `note` because it behaves nothing
# like hand-written prose: graphify's GRAPH_REPORT.md files alone contribute
# 1 351 of this corpus's 1 465 unresolved wikilinks, all of them `[[_COMMUNITY_
# Market Regime Detection]]`-style cluster labels that were never pages. Mixed
# into `note`, they drown the real broken links -- the ones that mark something
# a human meant to write -- at a ratio of roughly 12 to 1.
#
# Only the report FILENAME ships. The directories such reports are written into
# are named by whoever ran the tool, on one machine, and a shipped directory
# name is a fact about that machine masquerading as a rule. They come from
# ~/.homegraph/config.toml under [markdown].generated_dirs and arrive here as
# `rules["generated_markers"]`, which is why that lookup exists.
GENERATED_MARKERS = ("GRAPH_REPORT.md",)


def subtype_of(path, rules=None):
    rules = rules or {}
    low = path.lower()
    base = os.path.basename(low)
    for prefix in rules.get("transcript_roots", ()):
        if path.startswith(prefix):
            return "transcript"
    # Union, not override: the shipped filename marker and whatever directories
    # this installation names. Making the config replace the default would mean
    # that configuring one directory silently stopped recognising the reports
    # everywhere else.
    for marker in tuple(GENERATED_MARKERS) + tuple(
            rules.get("generated_markers", ())):
        if marker in path:
            return "generated"
    for marker in rules.get("memory_markers", ("/memory/",)):
        if marker in path:
            return "memory"
    if base in ("readme.md", "readme.mdx", "readme.markdown"):
        return "readme"
    return "note"


class MarkdownExtractor:
    def __init__(self, rules=None):
        self.rules = rules or {}

    def extract(self, path, text=None):
        """Everything M3 knows about one file. Pure: no store, no I/O beyond
        reading this one file, so it is trivially testable on strings."""
        if text is None:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()

        front, body, front_problems = parse_frontmatter(text)
        clean = blank_code(body)

        sections = [{"level": len(m.group(1)), "title": m.group(2).strip(),
                     "offset": m.start()}
                    for m in RE_HEADING.finditer(clean)]

        embeds, wikilinks = [], []
        for m in RE_EMBED_WIKI.finditer(clean):
            embeds.append(m.group(1).strip())
        embed_spans = {m.span() for m in RE_EMBED_WIKI.finditer(clean)}
        for m in RE_WIKILINK.finditer(clean):
            if m.span() in embed_spans:
                continue
            wikilinks.append(m.group(1).strip())

        links, path_mentions = [], []
        for m in RE_MDLINK.finditer(clean):
            bang, target = m.group(1), m.group(2).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                links.append(target)
                continue
            if bang or os.path.splitext(target)[1].lower() in IMAGE_EXT:
                embeds.append(target)
            else:
                links.append(target)

        for m in _pathish(home_root()).finditer(clean):
            path_mentions.append(m.group(1))

        tags = []
        raw_tags = front.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        tags.extend(raw_tags)
        tags.extend(m.group(1) for m in RE_INLINE_TAG.finditer(clean))

        title = front.get("title") or (sections[0]["title"] if sections
                                       else page_name(path))

        return {
            "path": path,
            "name": page_name(path),
            "subtype": subtype_of(path, self.rules),
            "title": title,
            "body": body,
            "frontmatter": front,
            "frontmatter_problems": front_problems,
            "sections": sections,
            "wikilinks": _dedupe(wikilinks),
            "links": _dedupe(links),
            "embeds": _dedupe(embeds),
            "tags": _dedupe(tags),
            "path_mentions": _dedupe(path_mentions),
        }


def _dedupe(items):
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
