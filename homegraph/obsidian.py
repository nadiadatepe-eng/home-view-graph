#!/usr/bin/env python3
"""Write a store out as an Obsidian vault: one markdown note per node.

This is the second export target, and it deliberately shares its substrate
with the first. `export.py` already streams every node and edge with portable
paths (`_rows`) and already owns the one function that decides what a
redaction level carries (`redact`). Both are imported here rather than
reimplemented, so **an Obsidian export cannot leak more than a `.hgx` at the
same level** -- not because two code paths were written to agree, but because
there is only one path and this is a different printer on the end of it. The
project has been bitten by the other arrangement: an invariant duplicated
across two layers agrees until one of them is edited.

**Wikilinks are read off edges that already exist.** M3 built `WIKILINKS_TO`,
`LINKS_TO` and `EMBEDS` from real markdown; the mesh built `MENTIONS_FILE`,
`FIGURE_FOR` and `CITES_CODE`; M1 built `REFERENCES_FILE`. None of it is
recomputed here and no link is invented from a title that looks similar to
another title. A vault is markdown and M3 parses `[[wikilinks]]`, so a graph
exported this way and then indexed again should produce the edges it started
with -- which is only true if this writer never adds any.

**What is deliberately NOT written:**

  * **`.canvas`.** The plan lists it as optional and it stays unbuilt, for the
    same reason `shape` shipped refused rather than approximated: the JSON
    Canvas schema is not readable from anything on this machine. Obsidian here
    is a flatpak, and the vault holds no `.canvas` file to read the real shape
    off. Graphify's schema was read out of its installed source before a line
    of that exporter was written; guessing this one because it is "just JSON"
    would be the same mistake with a friendlier excuse.
  * **The FTS index and the mesh.** Derived, exactly as in `export.py`.

The vault layout is one directory per model (`<vault>/m3/...`), with note
names unique across the WHOLE vault rather than per directory, because
Obsidian resolves `[[name]]` vault-wide and a name that repeats in two
directories is a link that lands somewhere the graph did not point.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Iterable

from .export import ExportError, _rows, check_level, redact
from .portable import to_portable
from .store import Store

# Characters a note name may not carry. The union of three separate lists,
# because a name has to survive all three and the intersection would only
# survive one:
#
#   * the filesystem (`/`, and NUL);
#   * Windows, so a vault stays portable (`\ : * ? " < > |`) -- an export that
#     only opens on the machine that wrote it is not much of an export;
#   * Obsidian's own link syntax (`[ ] # ^ |`), because a `#` in a note name
#     is read as a heading anchor and `[[a#b]]` silently resolves to a
#     SECTION of `a` rather than to the note actually named `a#b`.
FORBIDDEN = set('/\\:*?"<>|[]#^\x00')

# Leaving room inside the usual 255-byte limit for the `.md` suffix and for
# the disambiguating tail added on collision. Counted in bytes, not
# characters: the corpus is Norwegian and `ø` is two bytes, so a name that
# fits by character count can still be refused by the filesystem.
MAX_STEM_BYTES = 180
DISAMBIGUATOR_CHARS = 8

# Edge relations that become a `[[wikilink]]` in the note body, and the
# heading each is written under. Relations NOT listed here still reach the
# note -- as frontmatter counts -- but do not become links, because a link is
# a claim the reader will click and `SAME_FORMAT` (1674 edges on the measured
# corpus, every file sharing a magic number) is noise at that strength.
#
# Read off the builders rather than off the data: `m3_build` writes the first
# three, `mesh` the next three, `m1_build` the last.
LINK_RELATIONS = {
    "WIKILINKS_TO": "Links to",
    "LINKS_TO": "Links to",
    "EMBEDS": "Embeds",
    "MENTIONS_FILE": "Mentions",
    "MENTIONS_PATH": "Mentions",
    "FIGURE_FOR": "Figure for",
    "CITES_CODE": "Cites code",
    "REFERENCES_FILE": "References",
    "CONTAINS": "Contains",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:DISAMBIGUATOR_CHARS]


def _truncate_bytes(stem: str, limit: int) -> str:
    """Cut `stem` to `limit` bytes without splitting a character in half."""
    encoded = stem.encode("utf-8")
    if len(encoded) <= limit:
        return stem
    return encoded[:limit].decode("utf-8", "ignore")


def _candidate(key: str) -> str:
    """A readable note name for one node key. Not yet guaranteed unique.

    The key keeps its shape -- `~/Dokumenter/plan.md` becomes
    `Dokumenter - plan.md` -- so a reader can still see where a note came
    from, which is the whole reason the vault is worth having over a JSON
    dump. The leading `~/` goes because every note has it.
    """
    stem = key[2:] if key.startswith("~/") else key
    stem = "".join(" " if ch in FORBIDDEN else ch for ch in stem)
    stem = " ".join(stem.split())
    # A name that is only dots, or that starts with one, is either a reserved
    # directory entry or hidden. Both are refusals from the filesystem or a
    # note the user cannot see, so neither is left to chance.
    stem = stem.strip(". ")
    return _truncate_bytes(stem, MAX_STEM_BYTES) or "untitled"


def vault_names(keys: Iterable[str]) -> dict[str, str]:
    """Map every node key to a note name unique across the whole vault.

    Two keys can flatten onto one name (`a/b.md` and `a:b.md` both become
    `a b.md`), and on a case-insensitive filesystem `Plan.md` and `plan.md`
    collide as well even though neither the keys nor the flattened names are
    equal. **Collisions are detected case-folded for that reason** -- an
    exFAT drive or a macOS vault would otherwise have one note silently
    overwrite the other, and the graph would be missing a node that every
    count in the report says is there.

    A colliding name takes a digest of its own key as a tail, so the result
    depends only on the key: two exports of the same store produce the same
    vault, and an export of a store with one node added does not rename the
    notes around it.
    """
    keys = sorted(set(keys))
    groups: dict[str, list[str]] = {}
    for key in keys:
        groups.setdefault(_candidate(key).casefold(), []).append(key)

    names: dict[str, str] = {}
    for key in keys:
        base = _candidate(key)
        if len(groups[base.casefold()]) > 1:
            base = "%s (%s)" % (_truncate_bytes(
                base, MAX_STEM_BYTES - DISAMBIGUATOR_CHARS - 3), _digest(key))
        names[key] = base
    return names


def _yaml_scalar(value: Any) -> str:
    """One frontmatter value, quoted so no input can end the block early.

    Everything that is not a number or a bool is emitted double-quoted with
    the two characters that matter inside such a string escaped. Titles come
    from the user's own files and contain colons, quotes, `#`, and -- in PDF
    text -- newlines; any one of them unquoted turns the frontmatter into
    either a different key or a parse error, and Obsidian's failure mode for
    a broken block is to render it as body text, which is how a redaction
    level's promise would quietly stop being kept.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"%s"' % text


def _frontmatter(row: dict[str, Any], model: str, name: str) -> list[str]:
    """The YAML block. Carries only what the redacted row still holds."""
    out = ["---", "homegraph_model: %s" % _yaml_scalar(model),
           "homegraph_key: %s" % _yaml_scalar(row["node_key"])]
    for field in ("kind", "subtype", "path", "title_method", "title_confidence",
                  "size", "mtime", "content_hash", "first_seen", "last_seen"):
        if row.get(field) is not None:
            out.append("%s: %s" % (field, _yaml_scalar(row[field])))
    if row.get("title") is not None and row["title"] != name:
        out.append("title: %s" % _yaml_scalar(row["title"]))
    out.append("---")
    return out


def _node_keys(store: Store, root: str, level: str) -> list[str]:
    """Portable keys as the chosen level will write them, for the naming pass.

    A keys-only query rather than a second trip through `_rows`, because at
    `--redaction full` that would read 5.9 million characters of body text a
    second time to learn nothing but the names. `to_portable` is the same
    function `_rows` calls, so the two passes cannot disagree about what a
    key is.

    The level matters here and it cost a gate to learn: `shape` hashes
    `node_key`, so a name map built from unhashed keys does not contain the
    key the writing pass then looks up. The fix is not to hash here too --
    that would be the rule written down twice -- but to run the bare key
    through `redact`, which is the one function that decides what a level
    does to a key.
    """
    return [redact({"node_key": to_portable(r["node_key"], root)},
                   level)["node_key"]
            for r in store.db.execute(
                "SELECT node_key FROM nodes ORDER BY node_key")]


def _inside(folder: str, target: str) -> bool:
    """Is `target` really under `folder`, after every link is resolved?

    Note names are generated here from a charset with no separator in it, so
    by construction nothing can escape -- and that is exactly the reasoning
    that let `..` out through the importer once, where the promise was
    enforced on the side that WROTE the keys and trusted on the side that
    used them. This is checked against the resolved path, immediately before
    each write, on the side that opens the file.

    **The bound is the model's own directory, not the vault.** Checking
    against the vault was the first version and the gate walked through it: a
    single `..` climbs out of `<vault>/m3/` into `<vault>/`, which is still
    inside the vault and so was allowed. Nothing had left the vault, but a
    note had left the model it belongs to -- and two models whose notes share
    a directory can collide on a name that `vault_names` proved unique per
    vault, silently dropping a node. A guard that only catches the dramatic
    failure is not catching the one that happens.
    """
    folder = os.path.realpath(folder)
    resolved = os.path.realpath(target)
    return resolved.startswith(folder + os.sep)


def _links(edges: dict[str, list[tuple[str, str]]], key: str,
           names: dict[str, str]) -> list[str]:
    """The link section of one note, grouped by heading.

    A target missing from `names` raises rather than being skipped. The first
    draft dropped it silently and reported a `links_dropped_unresolved` count
    beside the others -- a number that **cannot** be anything but zero, because
    `_rows` joins both endpoints against the same store's `nodes` table, so an
    edge that reaches this function always has both ends exported. A metric
    that can never move is worse than no metric: it reads as evidence that
    something was checked.

    The condition stays, as a refusal, because it stops being unreachable the
    moment someone exports edges from one store against names from another --
    which is exactly what phase 4's federation-in would do. A dangling
    `[[link]]` in a vault is not cosmetic: clicking one offers to CREATE the
    note, and a graph that grows a node because a reader clicked it is no
    longer the graph that was exported.
    """
    by_heading: dict[str, list[str]] = {}
    for rel, dst in edges.get(key, ()):
        heading = LINK_RELATIONS.get(rel)
        if heading is None:
            continue
        if dst not in names:
            raise ExportError(
                "edge %s -> %s has no exported note for its target; a link "
                "Obsidian would offer to create is not written" % (key, dst))
        link = "[[%s]]" % names[dst]
        if link not in by_heading.setdefault(heading, []):
            by_heading[heading].append(link)

    out: list[str] = []
    for heading in sorted(by_heading):
        out.append("")
        out.append("## %s" % heading)
        out.append("")
        out.extend("- %s" % link for link in sorted(by_heading[heading]))
    return out


def export_vault(model_paths: dict[str, str], vault: str, root: str, *,
                 redaction: str = "structure",
                 force: bool = False) -> dict[str, Any]:
    """Write every model to `vault` as markdown. Never mutates a store.

    Refuses a vault directory that already holds files unless `force`. That
    guard is not tidiness: the natural mistake is to point this at a vault
    you already keep notes in, and thousands of generated notes mixed into
    hand-written ones is not an operation with an undo.
    """
    check_level(redaction)
    root = os.path.abspath(os.path.expanduser(root))
    vault = os.path.abspath(os.path.expanduser(vault))

    for model, path in sorted(model_paths.items()):
        if not path or not os.path.exists(path):
            raise ExportError("no store for %s at %s" % (model, path))
    if os.path.exists(vault) and not os.path.isdir(vault):
        raise ExportError("vault path %s exists and is not a directory" % vault)
    if os.path.isdir(vault) and os.listdir(vault) and not force:
        raise ExportError(
            "vault %s is not empty. Point this at a new directory, or pass "
            "--force if you meant to write into an existing vault -- this "
            "writes one note per node and does not remove what is there."
            % vault)

    # Pass one: every key in every model, so a link can be written before its
    # target's note exists. Names are unique vault-wide, so the map is built
    # over all models at once rather than per model.
    keys: list[str] = []
    for model, path in sorted(model_paths.items()):
        store = Store(path)
        try:
            keys.extend(_node_keys(store, root, redaction))
        finally:
            store.close(commit=False)
    names = vault_names(keys)

    written = 0
    edges_read = 0
    links_written = 0
    per_model: dict[str, int] = {}

    for model, path in sorted(model_paths.items()):
        store = Store(path)
        try:
            nodes: list[dict[str, Any]] = []
            edges: dict[str, list[tuple[str, str]]] = {}
            for row in _rows(store, model, root):
                # Edge rows go through `redact` too, not only node rows. At
                # `shape` it rewrites `src` and `dst`, and an edge left
                # unredacted would carry readable keys into a vault whose
                # notes are hashed -- the level defeated by the half of the
                # stream nobody thought of as carrying names.
                row = redact(row, redaction)
                if row["t"] == "node":
                    nodes.append(row)
                else:
                    edges.setdefault(row["src"], []).append(
                        (row["rel"], row["dst"]))
                    edges_read += 1
        finally:
            store.close(commit=False)

        folder = os.path.join(vault, model)
        os.makedirs(folder, exist_ok=True)
        for row in nodes:
            key = row["node_key"]
            name = names[key]
            target = os.path.join(folder, name + ".md")
            if not _inside(folder, target):
                raise ExportError(
                    "refusing to write outside %s: note name %r resolves to %s"
                    % (folder, name, os.path.realpath(target)))
            lines = _frontmatter(row, model, name)
            if row.get("title") is not None:
                lines += ["", "# %s" % row["title"]]
            if row.get("body"):
                lines += ["", row["body"]]
            link_lines = _links(edges, key, names)
            lines += link_lines
            links_written += sum(1 for ln in link_lines if ln.startswith("- "))
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines).rstrip() + "\n")
            written += 1
        per_model[model] = len(nodes)

    return {"vault": vault, "redaction": redaction, "notes": written,
            "edges_read": edges_read, "links_written": links_written,
            "per_model": per_model, "canvas": "not written (schema unread)"}
