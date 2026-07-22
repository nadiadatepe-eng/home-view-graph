#!/usr/bin/env python3
"""Nothing that ships may name a real file, directory, account or person.

This is a privacy requirement, not tidiness, and it is the kind that decays:
one pasted absolute path in a docstring six months from now and the guarantee
is gone, silently, in a commit whose diff looks like a comment change. So it is
a test rather than a promise.

**What is checked, and over what.** The publishable tree is everything git
would track -- `git ls-files` when this is a repository, and the same thing
computed from `.gitignore` when it is not yet one. That distinction matters:
`tests/gold/` still holds the real-corpus keys and samplers locally, they are
git-ignored, and this check must agree with git about which files those are
rather than keeping a second list that can drift.

Three bands, and they are deliberately not the same strictness:

1. **Personal identifiers, everywhere.** Account names, `/home/`, the project
   directory names from the machine the survey was taken on. Zero occurrences,
   source and documentation alike. The names are stored as digests so this file
   is covered by the band too, rather than exempting itself the way a plaintext
   needle list has to.

2. **Localised directory names, in the package.** `homegraph/` is the tool. A
   directory name in there -- in any language -- is a fact about one machine
   compiled into a rule, and on a machine that spells it differently the rule
   fails silently. Zero occurrences.

3. **Localised directory names, in the fixture.** ALLOWED, and load-bearing.
   `tests/fixtures/synthetic.py` *creates* directories with Norwegian names in
   order to demonstrate that the package does not know them; CP-7 renames the
   same corpus to English at a second root and asserts the partition is
   identical. Banning the names there would delete the evidence. What is not
   allowed is a name the fixture does not create -- see `_created_dirs()`.

The third band is the one to argue with. It is stated here rather than hidden
in a skip list, and the report on this work names it as the one relaxation.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Band 1. Present on the machine the survey was taken on, and nowhere near a
# rule that could need them.
#
# The identifiers are stored as digests, not as text. Listing them in plaintext
# made this file the last thing in the tree still naming the account and the
# project directories it exists to keep out -- a guard that leaks exactly what
# it guards. Digests let the check ship, run on every clone, and still fail,
# without the repository carrying the names.
#
# Matching is by token n-gram: a file's text is reduced to alphanumeric runs and
# every 1-, 2- and 3-gram is hashed the same way the identifiers were. That
# catches `some-project`, `some_project` and `Some Project` alike, and costs
# the ability to match a substring inside a longer word -- which the plaintext
# band below still covers when the real-corpus material is present.
PERSONAL_DIGESTS = {
    "646fe999c9dad33d", "04c94562ac5c72cf", "aa2135451ce931f3",
    "eb118c0342d6a397", "ab5490d9968e03e0", "e1667cc1701b7173",
    "bb35de065328a9b2", "f137ea3ab3010219", "c66b5036a905884e",
    "8cfde6efdfc4ed5a",
}

# Not personal, and safe to write out: `/home/` covers every absolute home path
# at once, which is stronger than listing users, and the handbook is a public
# repository this package credits by name.
GENERIC = [r"/home/", r"data-engineer-handbook"]

TOKEN = re.compile(r"[a-z0-9æøåäöü]+")
NGRAM_MAX = 3

# A three-token phrase that appears nowhere but the canary check, so proving
# the band can fire does not require writing a real identifier down.
CANARY = "quaywise lanternfen brookvale"
CANARY_DIGEST = {
    hashlib.sha256(b"quaywiselanternfenbrookvale").hexdigest()[:16],
}

# Band 2 and 3. Directory names that belong to one desktop language.
LOCALISED = ["Bilder", "Dokumenter", "Nedlastinger", "Skrivebord",
             "Skjermbilder", "graphify-out", "graph-out"]

# Band 1 exemptions. Two, and each is a deliberate authorship or attribution
# statement rather than something leaked:
#
#   * the upstream repository named in the credits. Withholding a borrowed
#     idea's source would be the problem, not the fix.
#   * LICENSE, which has to carry a copyright holder to be a licence at all.
#
# This file used to be the third, because a plaintext needle list cannot check
# for strings it is forbidden to contain. Storing the identifiers as digests
# removed the need for the exemption, which is better than documenting it: the
# guard is now subject to its own rule.
CITATION = "DataExpert-io/data-engineer-handbook"
SELF = os.path.join("tests", "test_no_real_paths.py")
AUTHORSHIP = ("LICENSE",)

TEXT_SUFFIXES = (".py", ".toml", ".md", ".txt", ".cfg", ".ini", ".tsv",
                 ".html", ".json", ".yml", ".yaml", "")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-44s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def publishable_files():
    """Every file git would track, or would track if this were a repository.

    `git check-ignore` is asked rather than `.gitignore` being re-parsed here,
    because a second implementation of ignore semantics is a second thing to be
    wrong. When git is unavailable the check is not skipped -- it fails, since
    "we could not look" and "we looked and it was clean" are different facts.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                         capture_output=True, text=True)
    if out.returncode == 0 and out.stdout.strip("\0"):
        return sorted(p for p in out.stdout.split("\0") if p)

    # Not a repository yet. Enumerate, then let git filter with the same
    # .gitignore it will use on the day this is committed. A scratch git
    # directory outside the tree does that without initialising anything here:
    # git reads ignore rules from the working tree, so the answer is the one
    # the real repository will give.
    candidates = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__")]
        for name in filenames:
            candidates.append(os.path.relpath(
                os.path.join(dirpath, name), REPO))
    scratch = tempfile.mkdtemp(prefix="ignore-probe-")
    try:
        gitdir = os.path.join(scratch, "git")
        base = ["git", "--git-dir=%s" % gitdir, "--work-tree=%s" % REPO]
        init = subprocess.run(base + ["init", "-q"],
                              capture_output=True, text=True)
        if init.returncode != 0:
            raise RuntimeError("git unavailable: %s" % init.stderr)
        probe = subprocess.run(
            base + ["check-ignore", "--no-index", "--stdin"],
            input="\n".join(candidates), capture_output=True, text=True)
        if probe.returncode not in (0, 1):
            raise RuntimeError(
                "git check-ignore unavailable: %s" % probe.stderr)
        ignored = {line.strip() for line in probe.stdout.splitlines()
                   if line.strip()}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return sorted(set(candidates) - ignored)


def _read(rel):
    path = os.path.join(REPO, rel)
    if not rel.endswith(TEXT_SUFFIXES):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _hits(text, needles, rel):
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.replace(CITATION, "")
        for needle in needles:
            if re.search(needle, stripped, re.IGNORECASE if
                         needle.islower() else 0):
                found.append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
                break
    return found


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _digest_hits(text, rel, digests=PERSONAL_DIGESTS):
    """Lines whose token n-grams hash into PERSONAL_DIGESTS.

    Line by line rather than whole-file, so an n-gram cannot be assembled out
    of two unrelated paragraphs and reported as a leak that is not there.
    """
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        toks = TOKEN.findall(line.replace(CITATION, "").lower())
        for n in range(1, NGRAM_MAX + 1):
            for i in range(len(toks) - n + 1):
                if _digest("".join(toks[i:i + n])) in digests:
                    found.append("%s:%d %s" % (rel, lineno, line.strip()[:70]))
                    break
            else:
                continue
            break
    return found


def _plaintext_band():
    """The real-corpus identifiers in the clear, when they are on this machine.

    `tests/gold/real_corpus.py` is gitignored, so this band runs before
    publishing and not after. It is strictly stronger than the digest band --
    it matches substrings -- and strictly optional, which is why the digest
    band is not conditional on it.
    """
    path = os.path.join(REPO, "tests", "gold", "real_corpus.py")
    if not os.path.exists(path):
        return None
    ns = {}
    try:
        exec(compile(open(path, encoding="utf-8").read(), path, "exec"), ns)
    except Exception:                                     # noqa: BLE001
        return None
    pats = ns.get("PERSONAL_PATTERNS")
    return list(pats) if pats else None


def _created_dirs():
    """Directory names the fixture creates for itself, from its own source.

    Read out of the fixture rather than listed here. A hand-maintained list
    would let a name that is NOT created -- a leaked real path -- hide behind
    an exemption written for one that is.
    """
    src = _read(os.path.join("tests", "fixtures", "synthetic.py")) or ""
    names = set()
    for match in re.finditer(r'"([A-Za-zÆØÅæøå][\w \-.#()]*(?:/[^"]*)?)"', src):
        for part in match.group(1).split("/")[:-1]:
            names.add(part)
    # The English variant's targets are declared in the rename mapping.
    for match in re.finditer(r'ENGLISH_DIRS = \{([^}]*)\}', src, re.DOTALL):
        names.update(re.findall(r'"([^"]+)"', match.group(1)))
    return names


def t_no_personal_identifiers(files):
    offenders = []
    plaintext = _plaintext_band()
    for rel in files:
        text = _read(rel)
        if text is None:
            continue
        # The digest band covers this file too. It is the band that carries the
        # personal names, and the reason they are hashed is so that the guard
        # can be subject to its own rule instead of exempting itself.
        if rel not in AUTHORSHIP:
            offenders += _digest_hits(text, rel)
        # The generic band still has to spell `/home/` out to search for it, so
        # this file is exempt from that one and only that one.
        if rel != SELF and rel not in AUTHORSHIP:
            offenders += _hits(text, GENERIC, rel)
            if plaintext:
                offenders += _hits(text, plaintext, rel)

    # The digest band has to be able to fire. A hash set that matches nothing
    # anywhere, including in text known to contain a name, is indistinguishable
    # from an empty set -- and an empty set makes every file below clean.
    canary = _digest_hits("notes from " + CANARY + " today", "<canary>",
                          CANARY_DIGEST)
    check("the digest band can fire", len(canary) == 1,
          "canary produced %d hit(s)" % len(canary))
    check("plaintext band ran" if plaintext else
          "plaintext band absent (real-corpus material not on this machine)",
          True, "%d pattern(s)" % len(plaintext or []))
    # Without this the loop above proves nothing when the file list is empty.
    # `all()` over nothing is True, and so is "no offenders in no files".
    check("the publishable tree is non-empty", len(files) > 20,
          "%d file(s) would be committed" % len(files))
    # The exemptions must name files that are actually there. An exemption for
    # a file that has been renamed away is a hole nobody would notice.
    check("every exemption names a file in the tree",
          all(f in files for f in AUTHORSHIP),
          "missing: %s" % [f for f in AUTHORSHIP if f not in files])
    check("no personal identifier survives anywhere", not offenders,
          "%d hit(s)%s" % (len(offenders),
                           "" if not offenders else "\n      "
                           + "\n      ".join(offenders[:6])))


# Band 2 covers the shipped tool and the prose that ships beside it. README and
# DECISIONS describe how homegraph works; a directory name in either says the
# tool expects that directory, which is the belief this design exists to
# remove. `tests/gold/FASIT.md` is band 3, because it documents the fixture.
SHIPPED_PROSE = ("README.md", "DECISIONS.md")


def t_package_names_no_directory(files):
    """Band 2: the shipped tool must not know any desktop language's folders."""
    pkg = [f for f in files if f.startswith("homegraph" + os.sep)
           or f.startswith("homegraph/") or f in SHIPPED_PROSE]
    offenders = []
    for rel in pkg:
        text = _read(rel)
        if text is None:
            continue
        offenders += _hits(text, [re.escape(n) for n in LOCALISED], rel)
    check("the package itself is non-empty", len(pkg) > 8,
          "%d package file(s)" % len(pkg))
    check("the package names no localised directory", not offenders,
          "%d hit(s)%s" % (len(offenders),
                           "" if not offenders else "\n      "
                           + "\n      ".join(offenders[:6])))


def t_fixture_only_names_what_it_creates(files):
    """Band 3: a localised name in tests must be one the fixture plants."""
    created = _created_dirs()
    unaccounted = [n for n in LOCALISED
                   if n not in created
                   and any(n in (_read(f) or "") for f in files
                           if f.startswith("tests") and f != SELF)]
    # A directory name the fixture never creates has no business being in the
    # tests either: it can only have come from somebody's real disk.
    check("the fixture declares directories to create",
          "Bilder" in created and "Pictures" in created,
          "%d name(s) parsed from the fixture" % len(created))
    check("tests name no localised directory the fixture does not create",
          not unaccounted, "unaccounted: %s" % unaccounted)


def t_real_corpus_material_is_ignored(files):
    """The keys and samplers exist locally and must not be in the tree."""
    must_be_absent = [
        "tests/gold/gold-set.tsv", "tests/gold/cp2-links.tsv",
        "tests/gold/cp3-documents.tsv", "tests/gold/cp4-filenames.tsv",
        "tests/gold/real_corpus.py", "tests/gold/sample_gold.py",
        "tests/gold/sample_gold_round2.py", "tests/gold/sample_gold_round3.py",
    ]
    leaked = [f for f in must_be_absent if f in files]
    # And they have to actually exist, or "not published" is true because
    # there is nothing to publish -- which would make this gate pass on a
    # checkout that never had them.
    present = [f for f in must_be_absent
               if os.path.exists(os.path.join(REPO, f))]
    check("the real-corpus material exists locally", len(present) >= 4,
          "%d of %d present" % (len(present), len(must_be_absent)))
    check("none of it would be published", not leaked, "leaked: %s" % leaked)


def main():
    files = publishable_files()
    print("publishable tree: %d file(s)\n" % len(files))
    t_no_personal_identifiers(files)
    t_package_names_no_directory(files)
    t_fixture_only_names_what_it_creates(files)
    t_real_corpus_material_is_ignored(files)
    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_no_real_paths():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
