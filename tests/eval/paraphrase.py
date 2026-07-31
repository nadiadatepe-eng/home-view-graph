#!/usr/bin/env python3
"""The labelled paraphrase set: loading, and the guard that makes it one.

A pair is a paraphrase only if the query shares **no content word** with the
document it points at. Without that check "paraphrase" is a claim about how the
labeller felt, and the eval measures lexical retrieval while reporting a
semantic number -- which is exactly the leak sim-auditor found in H1 (a heading
that was verbatim in the body made r@10=1.0 a structural constant).

So the guard is not a review step. It runs before the numbers do, it can fail,
and a pair that fails is not in the set.

The set itself lives OUTSIDE the repository, at `~/.homegraph/eval-paraphrase.json`,
because every pair names a real file in a real home directory. `test_no_real_paths`
is the gate that exists for that, and this stays on the right side of it.
"""
from __future__ import annotations

import json
import os
import re

#: Beside the other real-corpus answer keys, and git-ignored like them. That is
#: where `test_no_real_paths` already expects locally-held corpus material to
#: live, so this stays inside the arrangement rather than inventing a second
#: private location that the privacy gate does not know about.
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "gold", "paraphrase-pairs.json")

#: Words carrying no topic. Overlap on these says nothing about leakage, and
#: demanding zero overlap including them would make a natural query impossible
#: -- every Norwegian sentence contains "og". Kept short and explicit rather
#: than pulled from a package: a stopword list nobody can read is a rule nobody
#: can check.
#: Chosen by WORD CLASS -- articles, pronouns, auxiliaries, prepositions,
#: conjunctions, particles -- and never by which pair it would rescue. That
#: distinction is the whole integrity of the guard: a stopword list edited until
#: the set passes is not a guard, it is a rubber stamp. The list was extended
#: once, on 2026-07-31, when the first draft leaked on `ble`, `skal`, `hvert`,
#: `ut`, `mot` and `uten` -- all function words the English half already had
#: equivalents for and the Norwegian half simply lacked. The pairs that leaked
#: on real topic words (`system`, `formatert`, `målt`, `bilder`) were REWRITTEN
#: instead. Which is which is a judgement, so both lists are here to be argued
#: with rather than hidden behind a package import.
STOPWORDS = {
    # English -- articles, pronouns, auxiliaries, prepositions, particles
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "both", "but",
    "by", "can", "could", "do", "does", "each", "either", "every", "for",
    "from", "further", "has", "have", "here", "how", "i", "if", "in", "into",
    "is", "it", "its", "itself", "just", "may", "might", "more", "most", "must",
    "no", "none", "not", "now", "of", "on", "only", "or", "other", "our", "out",
    "over", "own", "same", "should", "since", "so", "some", "such", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "through", "to", "too", "under", "up", "very", "was", "were", "what",
    "when", "where", "which", "while", "who", "why", "will", "with", "without",
    "would", "you", "your",
    # Norwegian -- same classes
    "alle", "alt", "andre", "annen", "annet", "at", "av", "bare", "begge",
    "bli", "blir", "blitt", "ble", "bør", "burde", "da", "de", "dem", "den",
    "denne", "der", "dere", "deres", "det", "dette", "din", "disse", "eller",
    "en", "enn", "er", "et", "etter", "fordi", "for", "fra", "fram", "frem",
    "første", "gjennom", "ha", "han", "har", "hennes", "her", "hun", "hva",
    "hver", "hvert", "hvis", "hvor", "hvordan", "i", "igjen", "ikke", "ingen",
    "inn", "jeg", "kan", "kunne", "med", "mellom", "men", "mens", "min",
    "mine", "mitt", "mot", "må", "måtte", "ned", "noe", "noen", "nå", "når",
    "og", "også", "om", "opp", "over", "på", "samme", "seg", "selv", "sin",
    "sine", "sitt", "siden", "skal", "skulle", "slik", "som", "så", "til",
    "under", "uten", "ut", "var", "ved", "vi", "videre", "vil", "ville",
    "vår", "våre", "vårt", "å",
}

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def content_words(text):
    """Lowercased topic-bearing words. Same tokenizer both sides, on purpose:
    a guard that splits the query differently from the document can report a
    clean pair that is not one."""
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(text))
            if w not in STOPWORDS and len(w) > 1}


def leakage(query, body):
    """The content words a query shares with its document. Empty means the pair
    is a paraphrase; anything else is a lexical hit waiting to happen."""
    return sorted(content_words(query) & content_words(body))


def load(path=None, store=None):
    """Read the set, and resolve each path to the node id in `store`.

    Ids are not stable across rebuilds; paths are. Storing the id would make the
    set silently point at whatever occupies that row after the next build --
    the same class of stale reference as an embedding that outlives its text.
    """
    path = path or DEFAULT_PATH
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    pairs = []
    for item in raw["pairs"]:
        row = store.db.execute(
            "SELECT id, body FROM nodes WHERE node_key = ?",
            (os.path.expanduser(item["path"]),)).fetchone()
        if row is None:
            raise KeyError("no node for %s -- rebuild the store or drop the pair"
                           % item["path"])
        pairs.append({"query": item["query"], "path": item["path"],
                      "node_id": row["id"], "body": row["body"]})
    if not pairs:
        raise ValueError("empty paraphrase set: %s" % path)
    return pairs


def stem_leakage(query, body, floor=5):
    """Overlap the exact-token guard cannot see: inflection and compounding.

    `katt` and `katter` share no token, and in Norwegian that happens
    constantly. This reports a query word that is a prefix of some document word
    (or the reverse) once both are at least `floor` characters -- long enough
    that the shared prefix is a stem rather than a coincidence. Deliberately
    separate from `leakage`: it is a weaker, fuzzier signal, and folding it into
    the strict guard would make one number mean two things. Raised by codex,
    2026-07-31.
    """
    doc = content_words(body)
    hits = []
    for q in content_words(query):
        if len(q) < floor:
            continue
        for d in doc:
            if len(d) >= floor and (d.startswith(q) or q.startswith(d)):
                hits.append((q, d))
                break
    return sorted(hits)


def guard(pairs):
    """Every pair that leaks, with what it leaks. Empty list means the set is
    what it says it is."""
    return [(p["path"], leakage(p["query"], p["body"] or ""))
            for p in pairs if leakage(p["query"], p["body"] or "")]
