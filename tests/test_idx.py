#!/usr/bin/env python3
"""CP-IDX -- the category a wiki author wrote in `index.md` becomes an edge.

M3 already reads `index.md`: its `[[wikilinks]]` become `WIKILINKS_TO` like any
other page's. What it threw away was the *structure around them* -- which
heading a link was listed under. That heading is the one classification in the
corpus the author wrote by hand, and it was the only thing worth taking from
`Egonex-AI/Understand-Anything` when that repo was measured against `~/wiki` on
2026-07-31 (borrowed idea, credited, no code copied).

Ten checks. Six of them exist because an obvious implementation is wrong in a
way no count would show, and four of those six were found by codex reviewing
the first version -- every one of them a case where the store still looked
healthy:

* **K2** is the whole point. Taking "the first heading" or "the file's title"
  produces exactly as many edges as the correct rule, and every count in the
  report agrees. Only asking *which* category a specific article landed in can
  tell the two apart.
* **K3** guards the direction that looks like generosity. An article nobody
  listed has no author-written category, and inventing one -- "uncategorized",
  the first heading, the containing directory -- manufactures curation that
  does not exist. That is worse than the gap it fills.
* **K4** is a regression guard, not a feature. The links in `index.md` were
  already edges; this checkpoint ADDS a second reading of the same file. An
  implementation that consumes the links into categories would leave every
  count looking healthy while the graph quietly lost 39 edges on `~/wiki`.
* **K5** pins the mechanism to the corpus it came from, with the numbers
  measured before any of this was written.
* **K6-K9** are the incremental path. `md build` writing the right graph is
  half a feature: `forget` deletes edges by src, and this edge is written by
  the index's build but hangs off the article. Editing an article, deleting the
  index, and a dead name gaining a file each drift a different way, and each
  drifts silently.
* **K10** is the author's second classification. Reusing `wikilinks`' dedupe
  shape -- one heading per target -- kept the first and dropped the rest, with
  every count still agreeing.

Run:
    python3 tests/test_idx.py
"""
from __future__ import annotations

import os
import sys
import tempfile

from report import reporter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph.models.m3_build import build as m3_build        # noqa: E402
from homegraph.store import Store                              # noqa: E402

AS_OF = "2026-07-31"
results, check = reporter(54)


WIKI = {
    "index.md": """# Wiki

## People
- [[ada]]
- [[grace]]

## Concepts
- [[graphs]]

## Tools
- [[ripgrep]]
- [[ghost]]
""",
    "ada.md": "# Ada\n\nSee [[graphs]].\n",
    "grace.md": "# Grace\n",
    "graphs.md": "# Graphs\n",
    "ripgrep.md": "# ripgrep\n",
    # Listed nowhere in index.md. K3 says it stays uncategorised.
    "orphan.md": "# Orphan\n\nLinks out to [[ada]] but nobody lists it.\n",
}


def make_paths(root):
    return sorted(os.path.join(root, name) for name in WIKI)


def make_wiki(root):
    for name, text in WIKI.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    return make_paths(root)


def categories_of(store, path):
    """Category titles this article was filed under, via the new edge."""
    rows = store.db.execute(
        "SELECT n.title FROM edges e "
        "  JOIN nodes s ON s.id = e.src "
        "  JOIN nodes n ON n.id = e.dst "
        " WHERE e.rel = 'CATEGORIZED_UNDER' AND s.node_key = ?", (path,))
    return sorted(r[0] for r in rows)


def edge_count(store, rel):
    return store.db.execute(
        "SELECT COUNT(*) FROM edges WHERE rel = ?", (rel,)).fetchone()[0]


def build_fixture(store, root):
    paths = make_wiki(root)
    return m3_build(store, paths, AS_OF,
                    index_file=os.path.join(root, "index.md"))


def category_pairs(store):
    """(article name, category) for the whole store — the comparable shape."""
    rows = store.db.execute(
        "SELECT s.node_key src, n.title cat FROM edges e "
        "  JOIN nodes s ON s.id = e.src "
        "  JOIN nodes n ON n.id = e.dst "
        " WHERE e.rel = 'CATEGORIZED_UNDER'")
    return sorted((os.path.basename(r["src"]), r["cat"]) for r in rows)


def run_incremental(edit):
    """Build, apply `edit(root)`, update, and compare with a full rebuild.

    Returns `(before, after, rebuilt)`. The comparison is always the same one --
    an incremental store must be indistinguishable from a rebuilt one -- so the
    scenarios below differ only in what `edit` does to the corpus.
    """
    from homegraph import update as up
    from homegraph import userconfig

    with tempfile.TemporaryDirectory() as root:
        old_env = os.environ.get("HOMEGRAPH_ROOT")
        os.environ["HOMEGRAPH_ROOT"] = root
        try:
            paths = make_wiki(root)
            index = os.path.join(root, "index.md")

            with Store(os.path.join(root, "inc.db")) as store:
                m3_build(store, paths, AS_OF, index_file=index)
                before = category_pairs(store)
                paths = edit(root) or paths
                cfg = userconfig.UserConfig(path="", root=root, roles={})
                up.update(store, "m3", paths, AS_OF, cfg,
                          allow_config_change=True)
                after = category_pairs(store)

            # The rebuild reads the corpus as it now stands, index or no index.
            with tempfile.TemporaryDirectory() as fresh:
                with Store(os.path.join(fresh, "full.db")) as store:
                    m3_build(store, paths, AS_OF,
                             index_file=index if os.path.isfile(index) else None)
                    rebuilt = category_pairs(store)
        finally:
            if old_env is None:
                os.environ.pop("HOMEGRAPH_ROOT", None)
            else:
                os.environ["HOMEGRAPH_ROOT"] = old_env
    return before, after, rebuilt


def write(root, name, text):
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def check_two_categories():
    """A page listed under two headings is filed under both.

    `wikilinks` dedupes, and the first version of this reused that shape:
    target -> one heading, first occurrence wins. Every count agreed and one
    of the author's two classifications was gone (found by codex). Its own
    fixture, so the counts the other checks pin stay pinned.
    """
    both = {
        "index.md": "# Wiki\n\n## People\n- [[ada]]\n\n## Founders\n- [[ada]]\n",
        "ada.md": "# Ada\n",
    }
    with tempfile.TemporaryDirectory() as root:
        for name, text in both.items():
            write(root, name, text)
        paths = sorted(os.path.join(root, n) for n in both)
        with Store(os.path.join(root, "two.db")) as store:
            m3_build(store, paths, AS_OF,
                     index_file=os.path.join(root, "index.md"))
            cats = categories_of(store, os.path.join(root, "ada.md"))
    check("K10: side oppført under to overskrifter får begge",
          cats == ["Founders", "People"], "fikk %s" % cats)


def check_incremental():
    # K6 -- a page is re-filed under a different heading.
    def refile(root):
        write(root, "index.md", WIKI["index.md"]
              .replace("## Concepts\n- [[graphs]]\n", "## Concepts\n")
              .replace("- [[grace]]\n", "- [[grace]]\n- [[graphs]]\n"))
    before, after, rebuilt = run_incremental(refile)
    check("K6: omfiling i index.md lander der full ombygging lander",
          after == rebuilt and before != after,
          "etter %s · full %s" % (after, rebuilt))

    # K7 -- an ARTICLE changes and the index does not. The index still has to
    # be read again, because `forget` has just deleted that article's outbound
    # edges, category included, and only the index's build writes them.
    # Without the last branch of `_index_and_its_articles` this is the case
    # that silently loses a category on every ordinary edit (found by codex).
    def touch_article(root):
        write(root, "ada.md", "# Ada\n\nSee [[graphs]]. Edited.\n")
    before, after, rebuilt = run_incremental(touch_article)
    check("K7: redigert artikkel beholder kategorien sin",
          after == rebuilt == before,
          "etter %s · full %s" % (after, rebuilt))

    # K8 -- the index is deleted. Nothing else writes these edges and nothing
    # else deletes them, so without the `filed` return they would outlive the
    # file that assigned them (found by codex).
    def drop_index(root):
        os.remove(os.path.join(root, "index.md"))
        return sorted(p for p in make_paths(root) if os.path.isfile(p))
    before, after, rebuilt = run_incremental(drop_index)
    check("K8: slettet index.md tar kategoriene med seg",
          after == rebuilt == [], "etter %s · full %s" % (after, rebuilt))

    # K9 -- a name the index listed with no file behind it gets one. The index
    # itself did not change, and `filed` cannot know about a page that never
    # had an edge, so only "something arrived" can pull the index back in
    # (found by codex).
    def ghost_arrives(root):
        write(root, "ghost.md", "# Ghost\n")
        return sorted(make_paths(root) + [os.path.join(root, "ghost.md")])
    before, after, rebuilt = run_incremental(ghost_arrives)
    check("K9: død lenke som får en fil, får kategorien sin",
          after == rebuilt and ("ghost.md", "Tools") in after,
          "etter %s · full %s" % (after, rebuilt))


def main():
    with tempfile.TemporaryDirectory() as root:
        with Store(os.path.join(root, "m3.db")) as store:
            report = build_fixture(store, root)

            # K1 -- the edge exists at all, for every listed article.
            listed = {"ada": "People", "grace": "People",
                      "graphs": "Concepts", "ripgrep": "Tools"}
            missing = [name for name in listed
                       if not categories_of(store, os.path.join(root, name + ".md"))]
            check("K1: hver oppført artikkel får en kategorikant",
                  not missing, "" if not missing else "uten kant: %s" % missing)

            # K2 -- the heading the link sits UNDER, not the first heading and
            # not the document title. `ada` and `graphs` differ here; an
            # implementation that always answers "People" passes K1 and fails
            # this one.
            wrong = {name: categories_of(store, os.path.join(root, name + ".md"))
                     for name, want in listed.items()
                     if categories_of(store, os.path.join(root, name + ".md")) != [want]}
            check("K2: kategorien er overskriften lenken står under",
                  not wrong, "" if not wrong else "feil kategori: %s" % wrong)

            # K3 -- two ways to have no category, and the guard only covers
            # one of them. `orphan.md` exists but is unlisted; `[[ghost]]` is
            # listed but has no file. The second is the one the `target not in
            # index` guard is for -- without this half of the check, deleting
            # that guard was a mutation that SURVIVED (measured 2026-07-31).
            orphan = categories_of(store, os.path.join(root, "orphan.md"))
            ghosts = [row["src"] for row in store.db.execute(
                "SELECT s.node_key src FROM edges e "
                "  JOIN nodes s ON s.id = e.src "
                " WHERE e.rel = 'CATEGORIZED_UNDER' "
                "   AND s.node_key LIKE 'wikilink:%'")]
            check("K3: verken uoppført artikkel eller død lenke får kategori",
                  orphan == [] and ghosts == [],
                  "" if not (orphan or ghosts)
                  else "orphan %s · døde lenker %s" % (orphan, ghosts))

            # K4 -- the links in index.md are still ordinary wikilink edges.
            # 5 from index.md (incl. the dead [[ghost]]) + ada->graphs
            # + orphan->ada = 7.
            wikilinks = edge_count(store, "WIKILINKS_TO")
            check("K4: WIKILINKS_TO er uendret av kategoriene",
                  wikilinks == 7, "WIKILINKS_TO = %d, ventet 7" % wikilinks)

            # K5 -- the counts the report claims are the counts in the store.
            # A report that tallies what it meant to write rather than what it
            # wrote is the failure mode `mutation-verdict-name` was about.
            edges = edge_count(store, "CATEGORIZED_UNDER")
            check("K5: rapporten stemmer med lageret (4 kanter, 3 kategorier)",
                  edges == 4
                  and report.edges["CATEGORIZED_UNDER"] == 4
                  and report.categories == 3,
                  "lager %d · rapport %s · kategorier %s"
                  % (edges, report.edges.get("CATEGORIZED_UNDER"),
                     getattr(report, "categories", None)))

    # K6 -- the incremental path. `md build` writing categories is only half a
    # feature: `forget` deletes edges by src, and CATEGORIZED_UNDER runs
    # article -> category while only the index's build writes it. Re-filing a
    # page under a new heading and then running `update` must land where a full
    # rebuild lands, or the store drifts a little further from the truth on
    # every edit and nothing says so.
    check_two_categories()
    check_incremental()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_idx():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
