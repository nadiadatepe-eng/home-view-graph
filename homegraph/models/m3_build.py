#!/usr/bin/env python3
"""Turn extracted markdown into nodes and edges.

Kept separate from extraction so the parser can be tested on strings with no
database in sight, and so this file can be read as "what the graph looks like"
without wading through regexes.

Three decisions worth stating:

**Broken links become nodes.** `[[trading-agent-plan]]` points at nothing on
disk. That is not an error to be logged and dropped -- both this wiki and the
memory system use an unresolved link deliberately, to mark something worth
writing later. Dropping them would erase an intention. They get `subtype
'broken'` so they can be counted and listed.

**Backlinks are derived, never stored.** An inbound-link table is a second copy
of the edge table that can disagree with it. `backlinks()` is one query.

**MENTIONS_PATH only links to nodes that exist here.** A transcript mentioning
`~/<images>/Art/03122025_3.png` is a real relation, but the image node lives in
M2, and this model has no business inventing it. Unresolved mentions are
counted and handed to mesh (TODO-6), which is where cross-model identity is
actually decided.
"""
from __future__ import annotations

import collections
import os

from ..incremental import hash_file
from ..temporal import record_observation, refresh_datelist
from .m3_markdown import MarkdownExtractor, page_name, resolve_target



class BuildReport:
    def __init__(self):
        self.files = 0
        self.sections = 0
        self.edges = collections.Counter()
        self.broken_links = collections.Counter()
        self.broken_by_subtype = collections.Counter()
        self.ambiguous_targets = collections.Counter()
        self.unresolved_mentions = 0
        self.frontmatter_problems = []
        self.subtypes = collections.Counter()
        # Files the builder was asked for and could not read. Recorded rather
        # than passed over: during an incremental update the old edges have
        # already been deleted by the time this happens, so a silent skip
        # leaves a node with stale text and no relations while the run reports
        # success. A file deleted between the diff and the build, or one whose
        # permissions changed, lands here.
        self.unreadable = []

    def summary(self):
        return {
            "files": self.files,
            "sections": self.sections,
            "edges": dict(self.edges),
            "broken_links": len(self.broken_links),
            "broken_by_subtype": dict(self.broken_by_subtype),
            "broken_link_total": sum(self.broken_links.values()),
            "ambiguous_targets": len(self.ambiguous_targets),
            "unresolved_mentions": self.unresolved_mentions,
            "frontmatter_problems": len(self.frontmatter_problems),
            "unreadable": len(self.unreadable),
            "subtypes": dict(self.subtypes),
        }


def build_index(paths):
    """page name -> [paths]. Collisions are kept, not silently resolved.

    Two files named `index.md` in different directories are a real ambiguity in
    a flat wikilink namespace. Picking one quietly would create an edge to the
    wrong document that looks perfectly ordinary.
    """
    index = collections.defaultdict(list)
    for p in paths:
        index[page_name(p)].append(p)
    for name in index:
        index[name].sort()
    return index


def rules_from_config(cfg):
    """The extractor rules an installation's config implies.

    One function, called by every caller that builds M3, because the two that
    existed -- the CLI and `update` -- both passed no rules at all. The config
    key was written by `init`, read by `load()`, and then dropped on the floor:
    a directory named in `[markdown].generated_dirs` still produced `note`.
    CP-7's gate passed throughout, because it called `build(..., rules=...)`
    directly and so tested a code path no user takes.
    """
    return {"generated_markers": tuple(getattr(cfg, "generated_dirs", ()) or ())}


def build(store, paths, as_of, rules=None, report=None, index_paths=None):
    """Build nodes and edges for `paths`.

    `index_paths` is the name index's population, defaulting to `paths`. They
    differ only for an incremental update, where a handful of files are rebuilt
    but `[[wikilinks]]` still resolve against the whole corpus. Without it a
    partial build declares every link broken -- the targets are real, they were
    simply not in the batch -- and writes `wikilink:` nodes for pages that
    exist. That is not a hypothetical; it is what the equivalence gate in CP-8
    caught the first time this ran.
    """
    ex = MarkdownExtractor(rules)
    index = build_index(index_paths if index_paths is not None else paths)
    report = report or BuildReport()
    extractions = []

    # Pass 1: nodes. Every endpoint must exist before any edge is inserted.
    for path in paths:
        try:
            data = ex.extract(path)
        except OSError as exc:
            report.unreadable.append((path, repr(exc)))
            continue
        extractions.append(data)
        report.files += 1
        report.subtypes[data["subtype"]] += 1
        if data["frontmatter_problems"]:
            report.frontmatter_problems.append(
                (path, data["frontmatter_problems"]))

        st = os.stat(path)
        # The hash is stored because nothing else can distinguish `touched`
        # from `changed`: a file rewritten with identical bytes has a new mtime
        # and the same content, and re-extracting it produces nothing. Without
        # this column that distinction existed in incremental.py and could
        # never fire -- the stored hash was always NULL, so every rewrite came
        # back `changed`. Reading the file again costs one pass over a markdown
        # file that was just read anyway.
        nid = store.upsert_node(
            path, kind="file", subtype=data["subtype"], path=path,
            title=data["title"], title_method=data["title_method"],
            body=data["body"], size=st.st_size,
            mtime=st.st_mtime, content_hash=_safe_hash(path), as_of=as_of)
        record_observation(store, nid, as_of, size=st.st_size)
        refresh_datelist(store, nid, as_of)

        for i, sec in enumerate(data["sections"]):
            store.upsert_node("%s#%d" % (path, i), kind="section",
                              subtype="h%d" % sec["level"], path=path,
                              title=sec["title"], body=sec["title"],
                              as_of=as_of)
            report.sections += 1

        for tag in data["tags"]:
            store.upsert_node("tag:%s" % tag, kind="tag", subtype="tag",
                              title=tag, body=tag, as_of=as_of)

        for target in data["wikilinks"]:
            if target not in index:
                store.upsert_node("wikilink:%s" % target, kind="wikilink",
                                  subtype="broken", title=target, body=target,
                                  as_of=as_of)

    # Pass 2: edges.
    for data in extractions:
        path = data["path"]
        for i, _ in enumerate(data["sections"]):
            store.upsert_edge(path, "%s#%d" % (path, i), "CONTAINS",
                              as_of, method="exact")
            report.edges["CONTAINS"] += 1

        for target in data["wikilinks"]:
            hits = index.get(target)
            if not hits:
                store.upsert_edge(path, "wikilink:%s" % target,
                                  "WIKILINKS_TO", as_of, method="exact")
                report.broken_links[target] += 1
                report.broken_by_subtype[data["subtype"]] += 1
            else:
                # The edge carries the ambiguity, not just the report. Until
                # migration v2 this counter was the only trace: the tally said
                # "N ambiguous" and the row in the graph was indistinguishable
                # from a link the text resolved outright. Anyone querying the
                # store -- or reading a backlink -- got a guess that looked
                # like a fact.
                ambiguous = len(hits) > 1
                if ambiguous:
                    report.ambiguous_targets[target] = len(hits)
                store.upsert_edge(path, resolve_target(target, path, index),
                                  "WIKILINKS_TO", as_of,
                                  method="path_prefix" if ambiguous
                                  else "exact")
            report.edges["WIKILINKS_TO"] += 1

        for tag in data["tags"]:
            store.upsert_edge(path, "tag:%s" % tag, "TAGGED", as_of, method="exact")
            report.edges["TAGGED"] += 1

        for target in data["links"]:
            resolved = _resolve_relative(path, target)
            if resolved and store.node_id(resolved):
                store.upsert_edge(path, resolved, "LINKS_TO", as_of, method="exact")
                report.edges["LINKS_TO"] += 1

        for target in data["embeds"]:
            resolved = _resolve_relative(path, target)
            if resolved and store.node_id(resolved):
                store.upsert_edge(path, resolved, "EMBEDS", as_of, method="exact")
                report.edges["EMBEDS"] += 1

        for mention in data["path_mentions"]:
            resolved = os.path.expanduser(mention)
            if not os.path.isabs(resolved):
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), resolved))
            if store.node_id(resolved):
                store.upsert_edge(path, resolved, "MENTIONS_PATH", as_of,
                                  method="mention")
                report.edges["MENTIONS_PATH"] += 1
            else:
                report.unresolved_mentions += 1

    return report


def _safe_hash(path):
    """The content hash, or None. Never raises.

    Broad on purpose. A hash is a convenience for the next incremental run --
    without it a rewrite comes back `changed` instead of `touched`, which costs
    a reparse and nothing else. Nothing about this build depends on it, so no
    failure to compute it may take the build down. That is not hypothetical:
    M2's audit-hook tripwire raises on any open under the image root, and a
    document filed inside an image directory is a real case this corpus plants
    twice.
    """
    try:
        return hash_file(path)
    except Exception:                                           # noqa: BLE001
        return None


def _resolve_relative(src, target):
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    target = target.split("#", 1)[0]
    if not target:
        return None
    if target.startswith("~"):
        return os.path.expanduser(target)
    if os.path.isabs(target):
        return target
    return os.path.normpath(os.path.join(os.path.dirname(src), target))


def backlinks(store, node_key, rel="WIKILINKS_TO", as_of=None):
    """Inbound edges, computed on demand. Never a stored second copy.

    `as_of` answers the question the versioned edge schema was built for --
    "which files linked here on that date" -- by going through
    `Store.edges_as_of`, which owns the first_seen/last_seen predicate.

    Not by repeating the predicate here. That query was reachable only from a
    test until this call existed: `--as-of` on `mesh search` filters NODES by
    first_seen, so time travel over edges, the capability store.py's docstring
    names as the whole point, had no path from the command line. Writing the
    dates into this SQL instead would have given the system a second opinion
    about what "alive on a date" means, which is how the boundary rule in
    DECISIONS.md section 2 gets broken by a helpful shortcut.

    Returns `(sources, note)`. The note names any inbound link that was
    inferred rather than stated -- a wikilink whose target name matched two
    files and was resolved by path prefix looks exactly like an unambiguous
    one once it is a row, and a backlink list is precisely where someone
    treats it as a fact.
    """
    from ..store import provenance_note
    nid = store.node_id(node_key)
    if nid is None:
        return [], None
    if as_of:
        rows = [r for r in store.edges_as_of(as_of, rel) if r["dst"] == nid]
        return sorted(r["src_key"] for r in rows), provenance_note(rows)
    rows = store.db.execute(
        "SELECT s.node_key, e.method, e.confidence FROM edges e "
        "JOIN nodes s ON s.id = e.src "
        "WHERE e.dst = ? AND e.rel = ? ORDER BY s.node_key",
        (nid, rel)).fetchall()
    return [r["node_key"] for r in rows], provenance_note(rows)


def broken_links(store):
    return [r["title"] for r in store.db.execute(
        "SELECT title FROM nodes WHERE kind='wikilink' AND subtype='broken' "
        "ORDER BY title")]


#: Relations that count as one note reaching another. `TAGGED` is deliberately
#: absent -- two notes carrying the same tag are not linked to each other --
#: but this list is NOT what keeps a tag out of the count. `m.kind = 'file'`
#: below does, and a mutation adding `TAGGED` here proved it by leaving the
#: suite green. Both guards stay: this one states the rule, that one enforces
#: it. If a file-to-file relation is ever added to the schema, this list is
#: the place the decision has to be made, and the kind filter will not make
#: it for you.
LINKING_RELS = ("WIKILINKS_TO", "LINKS_TO", "MENTIONS_PATH")


def isolated_notes(store):
    """File nodes with no link to or from another file node.

    A note whose every wikilink points at a target that does not exist is the
    case this reports, so an edge into a `broken` stub is not a connection --
    `broken_links` already names those from the other end. The two together
    are the whole gap: links that go nowhere, and notes nothing goes to.

    Returns (paths, total) so the caller can print the share. 315 isolated
    notes is a different corpus at 602 files than at 6 000, and the count on
    its own cannot tell you which one you are looking at.
    """
    placeholders = ",".join("?" * len(LINKING_RELS))
    # `path` is nullable in the schema. For M3 file nodes the key IS the path,
    # so the fallback is not a guess -- but printing `None` for a row that has
    # no path would name no file at all, and a report you cannot act on reads
    # as damage you cannot find.
    paths = [r["path"] for r in store.db.execute(
        "SELECT COALESCE(n.path, n.node_key) path FROM nodes n "
        "WHERE n.kind = 'file' AND NOT EXISTS ("
        "  SELECT 1 FROM edges e JOIN nodes m"
        "    ON m.id = CASE WHEN e.src = n.id THEN e.dst ELSE e.src END"
        "  WHERE (e.src = n.id OR e.dst = n.id) AND e.rel IN (%s)"
        "    AND m.kind = 'file' AND m.id <> n.id) "
        "ORDER BY n.path" % placeholders, LINKING_RELS)]
    total = store.db.execute(
        "SELECT COUNT(*) c FROM nodes WHERE kind = 'file'").fetchone()["c"]
    return paths, total


def neighbours(store, node_key, seen=None, depth=2):
    """Breadth-limited traversal that survives cycles.

    A -> B -> A is normal in a wiki; the visited set is what keeps it from
    being an infinite loop rather than a graph.
    """
    seen = seen if seen is not None else set()
    frontier, out = [node_key], []
    for _ in range(depth):
        nxt = []
        for key in frontier:
            if key in seen:
                continue
            seen.add(key)
            nid = store.node_id(key)
            if nid is None:
                continue
            for row in store.db.execute(
                    "SELECT d.node_key k FROM edges e "
                    "JOIN nodes d ON d.id = e.dst WHERE e.src = ?", (nid,)):
                out.append((key, row["k"]))
                if row["k"] not in seen:
                    nxt.append(row["k"])
        frontier = nxt
    return out
