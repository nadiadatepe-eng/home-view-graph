#!/usr/bin/env python3
"""CP-I3 -- an Obsidian vault is a second printer, not a second exporter.

The integration plan's phase 2. What makes this checkpoint worth writing is
not that markdown comes out; it is the four promises the markdown has to keep,
each of which has a way of quietly not holding:

  * **The redaction level is the one `export.py` owns.** `obsidian.py` imports
    `redact` rather than deciding for itself what `structure` means, so the
    gate below asserts a property of the OUTPUT (the file's text is absent
    from the vault), not that a particular function was called. A vault that
    leaked body text at `structure` would still pass a test that only checked
    the call.
  * **Links come from edges that already exist.** A writer that inferred a
    link from two similar titles would look better and be a different graph.
    The gate builds two nodes with near-identical titles and no edge, and
    fails if anything links them.
  * **No note overwrites another.** Node keys are unique; note NAMES are a
    lossy function of them, and on a case-insensitive filesystem the loss is
    bigger than it looks. Both collisions are exercised, and the gate counts
    files on disk rather than trusting the report.
  * **Nothing is written outside the vault.** Names are generated from a
    charset with no separator, so by construction nothing escapes -- which is
    exactly the reasoning that let `..` through the importer once (DECISIONS
    section 27). The guard is therefore tested by defeating the construction:
    the name map is poisoned with a traversal and the write must refuse.

Run:
    python3 tests/test_i3.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import obsidian                                      # noqa: E402
from homegraph.export import ExportError                            # noqa: E402
from homegraph.store import Store                                   # noqa: E402

results, check = reporter(62)


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="i3-", dir=os.path.expanduser("~/.homegraph"))


# The body text is a phrase that appears nowhere else, so "did `structure`
# leak it" is answerable by grepping the whole vault rather than by trusting
# which fields the writer thinks it dropped.
_SECRET = "quokka-heliotrope-vestibule"


def _store(d: str, name: str = "m3.db") -> str:
    """A small m3-shaped store with the awkward cases built in."""
    db = os.path.join(d, name)
    root = os.path.join(d, "root")
    os.makedirs(root, exist_ok=True)
    with Store(db, model="m3") as s:
        s.begin_immediate()
        s.upsert_node(os.path.join(root, "plan.md"), "file", subtype="note",
                      path=os.path.join(root, "plan.md"),
                      title="Plan", body=_SECRET, as_of="2026-07-25")
        s.upsert_node(os.path.join(root, "arkiv.md"), "file", subtype="note",
                      path=os.path.join(root, "arkiv.md"),
                      title="Arkiv", body="ordinary text", as_of="2026-07-25")
        # Same name up to case. On a case-insensitive filesystem these two
        # flatten together; on ext4 they do not. The map must handle both, so
        # the vault is the same graph wherever it is opened.
        s.upsert_node(os.path.join(root, "Plan.md"), "file", subtype="note",
                      path=os.path.join(root, "Plan.md"),
                      title="Plan (capital)", as_of="2026-07-25")
        # Two keys that flatten onto one name through the forbidden-character
        # replacement: `a:b` and `a?b` both become `a b`.
        s.upsert_node(os.path.join(root, "a:b.md"), "file", subtype="note",
                      title="colon", as_of="2026-07-25")
        s.upsert_node(os.path.join(root, "a?b.md"), "file", subtype="note",
                      title="question", as_of="2026-07-25")
        # A title carrying every character that would end a YAML block early.
        s.upsert_node(os.path.join(root, "nasty.md"), "file", subtype="note",
                      title='he said: "---" # done\nsecond line',
                      as_of="2026-07-25")
        # Near-identical titles, deliberately NOT joined by an edge.
        s.upsert_node(os.path.join(root, "twin-one.md"), "file", subtype="note",
                      title="Retrieval Notes", as_of="2026-07-25")
        s.upsert_node(os.path.join(root, "twin-two.md"), "file", subtype="note",
                      title="Retrieval Notes", as_of="2026-07-25")
        # A key long enough that the note name must be cut, and Norwegian so
        # the cut is counted in bytes rather than characters -- `ø` is two.
        s.upsert_node(os.path.join(root, "ø" * 300 + ".md"), "file",
                      subtype="note", title="long", as_of="2026-07-25")
        s.upsert_edge(os.path.join(root, "plan.md"),
                      os.path.join(root, "arkiv.md"),
                      "WIKILINKS_TO", "2026-07-25", method="exact")
        # A relation that is real but not a link: it must reach the report
        # without becoming something the reader can click.
        s.upsert_edge(os.path.join(root, "plan.md"),
                      os.path.join(root, "twin-one.md"),
                      "TAGGED", "2026-07-25", method="exact")
    return db


def _digest_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _read_vault(vault: str) -> dict[str, str]:
    out = {}
    for folder, _, files in os.walk(vault):
        for f in files:
            p = os.path.join(folder, f)
            out[os.path.relpath(p, vault)] = open(p, encoding="utf-8").read()
    return out


def _root_of(d: str) -> str:
    return os.path.join(d, "root")


def _by_key(vault: str, key: str) -> str:
    """The note for one node key, found by its frontmatter rather than name.

    Note names are a lossy function of the key -- `plan.md` and `Plan.md`
    both pick up a disambiguating tail -- so a test that located notes by
    filename would be asserting the naming scheme rather than the content,
    and would go green the day the scheme changed for an unrelated reason.

    Returns `""` rather than raising, for the reason in `_export` below.
    """
    want = 'homegraph_key: "%s"' % key
    for name, text in _read_vault(vault).items():
        if want in text.splitlines()[:12]:
            return text
    return ""


def _export(models: dict, vault: str, root: str, **kw):
    """Run an export, turning a blow-up into something a gate can name.

    The mutation run is what forced this. Four mutations were *detected* --
    the suite went red -- but by a traceback rather than by a gate, and the
    harness classifies that as `CRASH`, deliberately: a crash says the code
    changed, a named gate says WHICH promise broke. When the promise has a
    name, a later reader learns what the mechanism was for; when it is a
    `KeyError` in someone else's stack frame, they learn only that something
    used to work.
    """
    try:
        return obsidian.export_vault(models, vault, root, **kw), ""
    except Exception as exc:      # noqa: BLE001 -- reported, not swallowed
        return None, "%s: %s" % (type(exc).__name__, exc)


# -- gates ------------------------------------------------------------------


def t_structure_leaves_the_text_behind():
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        rep, err = _export({"m3": db}, vault, _root_of(d))
        blob = "\n".join(_read_vault(vault).values())
        check("structure writes a note per node",
              rep is not None and rep["notes"] == 9,
              err or "%d notes" % rep["notes"])
        check("and the file's text is nowhere in the vault",
              not err and _SECRET not in blob, err)

        vault2 = os.path.join(d, "vault-full")
        _export({"m3": db}, vault2, _root_of(d), redaction="full")
        blob2 = "\n".join(_read_vault(vault2).values())
        check("full carries it, so the check above discriminates",
              _SECRET in blob2)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_shape_hashes_the_names():
    """`shape` must reach the vault as shape, not as structure with a label."""
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        _, err = _export({"m3": db}, vault, _root_of(d), redaction="shape")
        notes = _read_vault(vault)
        blob = "\n".join(notes.values())
        check("shape leaves no readable title in the vault",
              not err and "Retrieval Notes" not in blob and "Arkiv" not in blob,
              err)
        check("and no readable filename either",
              not err and not any("arkiv" in n.casefold() for n in notes), err)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_an_unimplemented_level_is_refused():
    d = _tmp()
    try:
        db = _store(d)
        try:
            obsidian.export_vault({"m3": db}, os.path.join(d, "v"),
                                  _root_of(d), redaction="skeleton")
            ok = False
        except ExportError:
            ok = True
        check("an unknown redaction level is refused before any write", ok)
        check("and nothing was created for it",
              not os.path.exists(os.path.join(d, "v")))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_links_come_from_edges_only():
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        _export({"m3": db}, vault, _root_of(d))
        notes = _read_vault(vault)
        plan = _by_key(vault, "~/plan.md")
        check("a real WIKILINKS_TO edge becomes a wikilink",
              "[[" in plan and "arkiv" in plan.casefold())
        twins = [v for k, v in notes.items() if "twin" in k]
        check("two nodes with identical titles and no edge are not linked",
              len(twins) == 2 and not any("[[" in v for v in twins),
              "%d twin notes" % len(twins))
        # The plan node has TWO outbound edges: WIKILINKS_TO (a link) and
        # TAGGED (not one). Asserting the absence of the string "TAGGED" was
        # the first version of this gate and a mutation that turned EVERY
        # relation into a link walked straight through it -- the relation's
        # name never appears in the note either way. Count the links instead.
        outbound = _links_in(plan)
        check("a non-link relation does not become a clickable link",
              len(outbound) == 1 and "twin" not in outbound[0].casefold(),
              "plan links to %s" % outbound)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _value_of(line: str) -> str:
    return line.partition(": ")[2]


def _is_plain(value: str) -> bool:
    """A value YAML reads as a number or a bool needs no quotes."""
    if value in ("true", "false"):
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def _links_in(text: str) -> list[str]:
    out = []
    rest = text
    while "[[" in rest:
        _, _, rest = rest.partition("[[")
        target, sep, rest = rest.partition("]]")
        if sep:
            out.append(target)
    return out


def t_every_link_lands_on_a_note_that_exists():
    """No `[[link]]` may point at nothing.

    This replaced a gate that asserted a dropped-link counter was zero. That
    counter could not be anything BUT zero -- `_rows` joins both endpoints
    against the same store, so an edge always has both ends exported -- and a
    number that cannot move reads as evidence that something was checked. The
    property worth asserting is the one a reader would notice: click any link
    in the vault and a note is there, rather than Obsidian offering to create
    one.
    """
    for level in ("structure", "full", "shape"):
        d = _tmp()
        try:
            db = _store(d)
            vault = os.path.join(d, "vault")
            rep, err = _export({"m3": db}, vault, _root_of(d), redaction=level)
            notes = _read_vault(vault)
            stems = {os.path.splitext(os.path.basename(n))[0] for n in notes}
            targets = [t for text in notes.values() for t in _links_in(text)]
            missing = sorted(set(targets) - stems)
            check("%s: every link lands on a note that exists" % level,
                  not err and bool(targets) and not missing,
                  err or "%d links, missing %s" % (len(targets), missing[:3]))
            check("%s: the report's link count matches the vault" % level,
                  rep is not None and rep["links_written"] == len(targets),
                  err or "report %d, vault %d"
                  % (rep["links_written"], len(targets)))
        finally:
            shutil.rmtree(d, ignore_errors=True)


def t_no_note_overwrites_another():
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        rep, err = _export({"m3": db}, vault, _root_of(d))
        notes = _read_vault(vault)
        check("every node got its own file on disk",
              rep is not None and len(notes) == rep["notes"] == 9,
              err or "%d files, report says %d" % (len(notes), rep["notes"]))
        folded = [n.casefold() for n in notes]
        check("and no two names collide even case-folded",
              len(set(folded)) == len(folded))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_names_survive_the_filesystem():
    """Every note name is one the filesystem and Obsidian will both accept."""
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        _, err = _export({"m3": db}, vault, _root_of(d))
        stems = [os.path.splitext(os.path.basename(n))[0]
                 for n in _read_vault(vault)]
        longest = max([len(s.encode("utf-8")) for s in stems] or [0])
        check("no note name exceeds what the filesystem accepts",
              not err and longest <= obsidian.MAX_STEM_BYTES,
              err or "%d bytes" % longest)
        offenders = sorted({ch for s in stems for ch in s
                            if ch in obsidian.FORBIDDEN})
        check("and none carries a character the name may not hold",
              not err and not offenders, err or "found %s" % offenders)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_names_are_deterministic():
    keys = ["~/a:b.md", "~/a?b.md", "~/plan.md", "~/Plan.md"]
    first = obsidian.vault_names(keys)
    again = obsidian.vault_names(list(reversed(keys)))
    check("the same keys always produce the same names", first == again)
    grew = obsidian.vault_names(keys + ["~/unrelated.md"])
    check("adding a node does not rename the notes around it",
          all(grew[k] == first[k] for k in keys))


def t_frontmatter_survives_hostile_titles():
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        _export({"m3": db}, vault, _root_of(d))
        # Located by filename, not by frontmatter key: this gate exists to
        # catch a frontmatter that no longer parses, and a lookup THROUGH the
        # frontmatter would go looking in the very thing under test. `nasty`
        # collides with nothing, so its name is stable.
        nasty = next((v for k, v in _read_vault(vault).items()
                      if "nasty" in k), "")
        lines = nasty.splitlines()
        check("the note opens with a frontmatter block",
              bool(lines) and lines[0] == "---",
              "note not found" if not lines else "")
        closes = [i for i, ln in enumerate(lines) if ln == "---"]
        check("the block is closed exactly once, by the writer's own delimiter",
              len(closes) >= 2 and closes[1] > closes[0],
              "delimiters at %s" % closes[:3])
        block = lines[closes[0] + 1:closes[1]] if len(closes) >= 2 else []
        # `all()` over an empty list is True, and an empty block is exactly
        # what a broken frontmatter produces -- so the emptiness has to be
        # part of the condition or this gate passes hardest when it should
        # fail. The project has met this shape before; see DECISIONS.
        check("a title containing a newline does not become two YAML lines",
              bool(block) and all(":" in ln for ln in block),
              "%d lines" % len(block))
        # Escaping and quoting are two mechanisms, and a mutation run proved
        # it: removing the quotes left the escaping intact, so the gate above
        # stayed green over frontmatter that no YAML reader would accept
        # (`title: he said: "---" # done` is a parse error and a comment).
        # This gate names the other half.
        unquoted = [ln for ln in block
                    if not _value_of(ln).startswith('"')
                    and not _is_plain(_value_of(ln))]
        check("and every value is quoted, so none can be reread as syntax",
              bool(block) and not unquoted, "unquoted: %s" % unquoted[:2])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_traversal_name_is_refused_at_the_write():
    """Defeat the construction, then check the guard still says no.

    Names cannot contain a separator, so this can only happen if someone
    later changes `_candidate`. That is precisely the change this gate exists
    to catch, and the only way to catch it is to simulate it.
    """
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        real = obsidian.vault_names

        def poisoned(keys):
            out = real(keys)
            victim = sorted(out)[0]
            out[victim] = "../escaped"
            return out

        obsidian.vault_names = poisoned
        try:
            obsidian.export_vault({"m3": db}, vault, _root_of(d))
            refused = False
        except ExportError:
            refused = True
        finally:
            obsidian.vault_names = real
        check("a name that escapes its model directory is refused at the write",
              refused)
        # Both destinations, because one `..` lands in the vault root and two
        # land outside it entirely. The first was allowed by the first draft.
        check("and nothing landed outside the model directory",
              not os.path.exists(os.path.join(vault, "escaped.md"))
              and not os.path.exists(os.path.join(d, "escaped.md")))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_populated_vault_is_not_written_into():
    d = _tmp()
    try:
        db = _store(d)
        vault = os.path.join(d, "vault")
        os.makedirs(vault)
        mine = os.path.join(vault, "my own note.md")
        open(mine, "w").write("hand written\n")
        try:
            obsidian.export_vault({"m3": db}, vault, _root_of(d))
            refused = False
        except ExportError:
            refused = True
        check("a vault that already holds files is refused", refused)
        check("and the note that was there is untouched",
              open(mine).read() == "hand written\n")
        rep = obsidian.export_vault({"m3": db}, vault, _root_of(d), force=True)
        check("--force writes into it", rep["notes"] == 9)
        check("and still does not remove what was there",
              os.path.exists(mine))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_a_missing_store_is_named():
    d = _tmp()
    try:
        try:
            obsidian.export_vault({"m3": os.path.join(d, "nope.db")},
                                  os.path.join(d, "v"), _root_of(d))
            refused = False
        except ExportError as exc:
            refused = "m3" in str(exc)
        check("a missing store is refused by name, not by traceback", refused)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_the_export_does_not_touch_the_store():
    d = _tmp()
    try:
        db = _store(d)
        before = _digest_file(db)
        _export({"m3": db}, os.path.join(d, "vault"), _root_of(d))
        check("exporting leaves the store byte-identical",
              _digest_file(db) == before)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    for fn in (t_structure_leaves_the_text_behind,
               t_shape_hashes_the_names,
               t_an_unimplemented_level_is_refused,
               t_links_come_from_edges_only,
               t_every_link_lands_on_a_note_that_exists,
               t_no_note_overwrites_another,
               t_names_survive_the_filesystem,
               t_names_are_deterministic,
               t_frontmatter_survives_hostile_titles,
               t_a_traversal_name_is_refused_at_the_write,
               t_a_populated_vault_is_not_written_into,
               t_a_missing_store_is_named,
               t_the_export_does_not_touch_the_store):
        fn()
    bad = [r for r in results if not r[1]]
    print("\nCP-I3: %d/%d" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def test_checkpoint_i3():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
