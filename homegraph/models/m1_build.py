#!/usr/bin/env python3
"""Build the document graph. 42 files, high value each.

`doctype` is a first-class dimension, not a detail: it filters search, it gets
its own census line, and it gets its own error rate. A single overall success
number would hide a format that fails every time, which is the failure the
per-type gate in CP-3 exists to catch.

A file that cannot be read still becomes a node. Status travels with it --
`needs_ocr`, `encrypted`, `corrupt`, `missing_extractor` -- because a document
the system cannot read is a fact worth knowing, and dropping it makes the
library look smaller than it is.
"""
from __future__ import annotations

import collections
import os
from typing import Any, DefaultDict

from ..config import home_root
from ..corpus import known_extensions
from ..incremental import hash_file
from ..temporal import record_observation, refresh_datelist
from .m1_extractors import extract, file_mentions

# Type for extract return value
ExtractResult: type[dict[str, Any]] = dict[str, Any]

MAX_REFS_PER_DOC = 40
MAX_FILE_REFS_PER_DOC = 40


class DocBuildReport:
    def __init__(self):
        self.documents = 0
        self.sections = 0
        self.by_doctype: collections.Counter[str] = collections.Counter()
        self.status: collections.Counter[str] = collections.Counter()
        self.status_by_doctype: DefaultDict[str, collections.Counter[str]] = (
            collections.defaultdict(collections.Counter)
        )
        self.edges: collections.Counter[str] = collections.Counter()
        self.authors: set[str] = set()
        self.empty_text: list[tuple[str, str]] = []
        self.problems: list[tuple[str, list[str]]] = []
        # Named a file, and no file of that name is in this store. Counted
        # rather than dropped: a run where every mention is unresolved is a
        # resolver that stopped working, and it looks identical to a corpus
        # whose documents happen not to cite each other unless the number is
        # somewhere a reader can see it.
        self.unresolved_refs = 0

    def summary(self) -> dict[str, int | dict[str, int]]:
        return {
            "documents": self.documents,
            "sections": self.sections,
            "by_doctype": dict(self.by_doctype),
            "status": dict(self.status),
            "authors": len(self.authors),
            "empty_text": len(self.empty_text),
            "unresolved_refs": self.unresolved_refs,
            "edges": dict(self.edges),
        }

    def error_rate(self, doctype: str | None = None):
        counts = (self.status_by_doctype[doctype] if doctype
                  else self.status)
        total = sum(counts.values())
        if not total:
            return 0.0
        bad = counts["corrupt"] + counts["missing_extractor"]
        return bad / total


def _safe_hash(path: str) -> str | None:
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


def build(store, paths, as_of, report: DocBuildReport | None = None) -> DocBuildReport:
    report = report or DocBuildReport()

    for path in paths:
        data = extract(path)
        try:
            st = os.stat(path)
        except OSError:
            continue

        meta = data["metadata"]
        title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
        nid = store.upsert_node(
            path, kind="document", subtype=data["doctype"], path=path,
            title=title, body=data["text"], size=st.st_size,
            # Stored for the same reason M3 stores it: without it
            # incremental.diff can never report `touched`, because the prior
            # hash is always NULL and every rewrite comes back `changed`.
            mtime=st.st_mtime, content_hash=_safe_hash(path), as_of=as_of)
        record_observation(store, nid, as_of, size=st.st_size)
        refresh_datelist(store, nid, as_of)

        report.documents += 1
        report.by_doctype[data["doctype"]] += 1
        report.status[data["status"]] += 1
        report.status_by_doctype[data["doctype"]][data["status"]] += 1
        if data["problems"]:
            report.problems.append((path, data["problems"][:2]))
        if not data["text"].strip():
            report.empty_text.append((path, data["status"]))

        store.db.execute(
            "UPDATE nodes SET subtype=? WHERE id=?",
            ("%s/%s" % (data["doctype"], data["status"])
             if data["status"] != "ok" else data["doctype"], nid))

        for i, sec in enumerate(data["sections"]):
            # The offset is kept because "where in the document" is half the
            # value of finding it at all.
            store.upsert_node(
                "%s#%d" % (path, i), kind="section", subtype=data["doctype"],
                path=path, title=sec["title"],
                body="%s (offset %d)" % (sec["title"], sec.get("offset", 0)),
                as_of=as_of)
            report.sections += 1

        author = (meta.get("author") or "").strip()
        if author:
            report.authors.add(author)
            store.upsert_node("author:%s" % author, kind="author",
                              subtype="author", title=author, body=author,
                              as_of=as_of)

        for ref in data["outbound_refs"][:MAX_REFS_PER_DOC]:
            store.upsert_node("ref:%s:%s" % (ref["kind"], ref["value"]),
                              kind="reference", subtype=ref["kind"],
                              title=ref["value"], body=ref["value"],
                              as_of=as_of)

    for path in paths:
        if store.node_id(path) is None:
            continue
        data = extract(path)
        for i, _ in enumerate(data["sections"]):
            store.upsert_edge(path, "%s#%d" % (path, i), "CONTAINS", as_of, method="exact")
            report.edges["CONTAINS"] += 1
        author = (data["metadata"].get("author") or "").strip()
        if author:
            store.upsert_edge(path, "author:%s" % author, "AUTHORED_BY", as_of, method="exact")
            report.edges["AUTHORED_BY"] += 1
        for ref in data["outbound_refs"][:MAX_REFS_PER_DOC]:
            key = "ref:%s:%s" % (ref["kind"], ref["value"])
            if store.node_id(key):
                store.upsert_edge(path, key, "CITES", as_of, method="exact")
                report.edges["CITES"] += 1

        _references_file(store, path, data["text"], as_of, report)

    _same_author(store, as_of, report)
    return report


def _resolve_mention(src, token, root):
    """Where a path written in prose could point, in order of confidence.

    Three anchors, because prose uses all three and picking one would make the
    other two silently unresolvable: `~/Documents/review.odt` is written from
    the home directory, `figures/plot.pdf` from the document's own directory,
    and `Documents/review.odt` from the corpus root. The order is fixed and
    the first hit wins, so a token that could mean two files always means the
    same one of them.

    Returns candidates, not a decision. Whether any of them is a node is the
    caller's question -- this function never touches the filesystem, which is
    also why it cannot be fooled by a file that exists on disk but was
    excluded from the corpus.
    """
    if token.startswith("~"):
        return [os.path.expanduser(token)]
    if os.path.isabs(token):
        return [os.path.normpath(token)]
    return [os.path.normpath(os.path.join(os.path.dirname(src), token)),
            os.path.normpath(os.path.join(root, token))]


def _references_file(store, path, text, as_of, report):
    """REFERENCES_FILE: a document names another file, and that file is here.

    The M1 counterpart to M3's MENTIONS_PATH, and deliberately the same shape:
    an edge only when the mention resolves to a node that already exists in
    this store. `upsert_edge` would refuse an endpoint that does not exist, so
    the alternative is not a weaker edge but a crash -- and the interesting
    case is the one where nothing is written at all. A document naming
    `appendix-b.pdf` that was never written must produce no edge and no node;
    inventing a nearest match is worse than the gap it fills.

    `method="mention"` (0.5), the same weight M3 gives the same evidence. The
    document says the name; nothing checked that the file it names is the file
    that was meant.
    """
    root = home_root()
    for token in file_mentions(text, known_extensions(),
                               limit=MAX_FILE_REFS_PER_DOC):
        for candidate in _resolve_mention(path, token, root):
            if candidate != path and store.node_id(candidate):
                store.upsert_edge(path, candidate, "REFERENCES_FILE", as_of,
                                  method="mention")
                report.edges["REFERENCES_FILE"] += 1
                break
        else:
            report.unresolved_refs += 1




def _same_author(store, as_of, report):
    """SAME_AUTHOR is derived from shared author nodes, not guessed from names.

    No fuzzy matching: `C. Ortega` and `Clara Ortega-de San Luis` stay separate.
    Merging them would be a judgement this model has no evidence for, and a
    wrong merge is invisible once it is in the graph.
    """
    rows = store.db.execute(
        "SELECT d.node_key author, s.node_key doc FROM edges e "
        "JOIN nodes s ON s.id = e.src JOIN nodes d ON d.id = e.dst "
        "WHERE e.rel = 'AUTHORED_BY' ORDER BY d.node_key, s.node_key")
    by_author = collections.defaultdict(list)
    for r in rows:
        by_author[r["author"]].append(r["doc"])
    for docs in by_author.values():
        for a, b in zip(docs, docs[1:]):
            store.upsert_edge(a, b, "SAME_AUTHOR", as_of, method="exact")
            report.edges["SAME_AUTHOR"] += 1
