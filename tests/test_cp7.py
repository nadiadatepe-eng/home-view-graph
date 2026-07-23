#!/usr/bin/env python3
"""CP-7 -- the installation config, and the claim that no layout is imposed.

Three things are under test, and only the third is interesting.

1. **Refusal.** With no config, the commands exit 2 and say what to run. The
   alternative -- guessing a directory name -- fails silently: the image
   boundary matches nothing, every image is excluded, and M2 builds an empty
   model while reporting success. A tool that produces an empty model on most
   of the machines it runs on is worse than one that refuses on all of them.

2. **The config is load-bearing.** Emptying the `image` role must move the
   image count to zero, and pointing it at an empty directory must produce an
   *absent* model that mesh labels `partial` -- not a silent zero that reads
   like a small corpus. Both directions matter: a config nobody reads and a
   config that cannot be wrong are the same defect.

3. **The layout is not imposed.** The same corpus is built twice, once with
   Norwegian directory names and once with English ones, at two different roots
   and with two different configs. Same rule files, same classifier. If the
   partitions differ, something in the package still knows a directory name.

   The English corpus's labels are NOT re-derived. Each declared case keeps the
   label it was born with; only its path is rewritten by the same mapping that
   renames the directories on disk. Nothing in the fixture calls `classify()`.
   A key obtained by classifying the renamed tree would agree with any
   classifier, including one that had the Norwegian names hardcoded.

Run:
    python3 tests/test_cp7.py
"""
from __future__ import annotations

import ast
import collections
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from homegraph import cli, userconfig                          # noqa: E402
from homegraph.corpus import ALL_LABELS, Classifier            # noqa: E402
from homegraph.models.m3_markdown import subtype_of            # noqa: E402
from homegraph.scan import scan                                # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def _labels(root, config_path):
    """{relative path: label} for every file under root, under one config.

    Every classify() call is wrapped. Without that, a mutation that makes the
    classifier raise kills the process before any gate can say no, and the
    harness reports the weakest kind of detection there is.
    """
    from tests.fixtures.synthetic import inventory
    cfg = userconfig.load(config_path)
    clf = Classifier(home=root, config=cfg)
    out = {}
    for path, is_link in inventory(root):
        try:
            label = clf.classify(path, is_symlink=is_link)
        except Exception as exc:                                # noqa: BLE001
            label = "raised:%s" % type(exc).__name__
        out[os.path.relpath(path, root)] = label
    return out


def _decisions(root, config_path):
    """{relative path: (label, subtype, layer)} for every file under root.

    The label alone is not the partition, only its shadow. Two corpora can
    agree on every label while disagreeing about which rule produced it -- a
    file excluded as `cache` under one layout and as `dependency` under the
    other reads as identical, and the claim CP-7 makes is that renaming the
    directories changes nothing about how the rules apply.

    `detail` is deliberately not compared: it carries the matched directory
    name, which is exactly what the English variant renames. Comparing it
    would fail for the one reason that is not a defect.

    How much stronger this is than the label comparison was measured, not
    assumed. Renaming a third directory in the English corpus -- `.icons`,
    which the shipped [app_state] layer names and the image boundary also
    covers -- left all 857 labels identical and moved 240 paths from
    `L2_app_state` to `image_boundary`. The label gate stayed green through
    it. That rename was then reverted, because renaming a directory the rules
    name by design is a different corpus rather than the same corpus in
    another language, and CP-7's claim is about the role mechanism.

    So this gate is strictly stronger and known to be reachable, and no
    mutation in mutate_cp7.py can currently reach it: the two corpora differ
    only in the image directories, where nothing but the category step can
    produce a label. An honest statement of its coverage is that it is a
    tripwire for future layout changes, not a proven gate today.
    """
    from tests.fixtures.synthetic import inventory
    cfg = userconfig.load(config_path)
    clf = Classifier(home=root, config=cfg)
    out = {}
    for path, is_link in inventory(root):
        try:
            d = clf.explain(path, is_symlink=is_link)
            got = (d.label, d.subtype, d.layer)
        except Exception as exc:                                # noqa: BLE001
            got = ("raised:%s" % type(exc).__name__, "-", "-")
        out[os.path.relpath(path, root)] = got
    return out


# -- 1. refusal ------------------------------------------------------------

def t_refuses_without_config(tmp):
    missing = os.path.join(tmp, "does-not-exist", "config.toml")

    raised = None
    try:
        userconfig.load(missing)
    except userconfig.ConfigMissing as exc:
        raised = str(exc)
    except Exception as exc:                                    # noqa: BLE001
        raised = "wrong exception: %r" % exc
    check("load() refuses when there is no config",
          raised is not None and "init" in (raised or ""),
          (raised or "no exception raised")[:70])

    env = dict(os.environ, HOMEGRAPH_CONFIG=missing)
    proc = subprocess.run(
        [sys.executable, "-m", "homegraph.cli", "census", "--root", tmp],
        capture_output=True, text=True, cwd=REPO, env=env, timeout=120)
    check("census exits 2 without a config", proc.returncode == 2,
          "exit %d" % proc.returncode)
    # Exit code alone is not the claim. A refusal nobody can act on is a
    # crash with a tidy return value. It has to name the command AND say the
    # root is the user's choice -- "run init" alone still leaves someone
    # believing homegraph expects a particular layout, which is the belief
    # this whole design exists to remove.
    check("the refusal names the command to run",
          "homegraph init" in proc.stderr and "--root" in proc.stderr,
          (proc.stderr.strip().splitlines() or ["(no stderr)"])[0][:60])
    check("the refusal produced no output on stdout", not proc.stdout.strip(),
          "%d byte(s) on stdout" % len(proc.stdout))

    # Two ways a config can be wrong, and they are not the same failure.
    #
    # Unparseable TOML must NOT be reported as missing: that would send the
    # user to `init`, which overwrites the file they meant to fix.
    def kind_of(text):
        path = os.path.join(tmp, "broken-%d.toml" % abs(hash(text)))
        with open(path, "w") as fh:
            fh.write(text)
        try:
            cfg = userconfig.load(path)
        except userconfig.ConfigMissing:
            return "ConfigMissing"
        except RuntimeError:
            return "RuntimeError"
        return "loaded root=%s" % cfg.root

    check("unparseable TOML is not reported as a missing config",
          kind_of("root = \n") == "RuntimeError",
          "raised %s" % kind_of("root = \n"))
    # Parseable but rootless is a config that says nothing about the machine.
    # Refused, never defaulted -- a guessed root is the silent failure this
    # whole layer exists to remove, and it would look like a successful load.
    rootless = kind_of('[roles]\nimage = ["x"]\n')
    check("a config with no root is refused, not defaulted",
          rootless == "ConfigMissing", rootless)


# -- 2. init writes what it proposed ---------------------------------------

def t_init_writes_config(tmp, root):
    cfg = os.path.join(tmp, "init-written.toml")
    proc = subprocess.run(
        [sys.executable, "-m", "homegraph.cli", "init", "--root", root,
         "--yes", "--config", cfg],
        capture_output=True, text=True, cwd=REPO, timeout=300,
        env=dict(os.environ, HOMEGRAPH_CONFIG=cfg))
    check("init exits 0", proc.returncode == 0,
          "exit %d%s" % (proc.returncode,
                         "" if proc.returncode == 0
                         else "  " + proc.stderr[-120:]))
    check("init wrote the file", os.path.isfile(cfg), cfg)
    if not os.path.isfile(cfg):
        return None

    written = userconfig.load(cfg)
    proposed = scan(root).roles
    # The file must say what the scan said. Writing a config that disagrees
    # with the proposal the user just approved is the failure nobody would
    # look for.
    same = all(list(written.roles.get(name, ())) == list(proposed.get(name, []))
               for name in userconfig.ROLES)
    check("the written config matches the proposal", same,
          "image=%s" % list(written.roles.get("image", ())))
    check("init recorded the root it was given",
          os.path.realpath(written.root) == os.path.realpath(root),
          written.root)

    # An interactive run with no terminal must refuse rather than accept.
    proc2 = subprocess.run(
        [sys.executable, "-m", "homegraph.cli", "init", "--root", root,
         "--config", os.path.join(tmp, "interactive.toml")],
        capture_output=True, text=True, cwd=REPO, timeout=300,
        stdin=subprocess.DEVNULL, env=dict(os.environ, HOMEGRAPH_CONFIG=cfg))
    check("init without --yes and without a terminal refuses",
          proc2.returncode == 2
          and not os.path.exists(os.path.join(tmp, "interactive.toml")),
          "exit %d, no file written" % proc2.returncode)
    return written


def t_scan_thresholds(tmp):
    """The proposal must be able to say "I do not know".

    Both thresholds have to be able to reject, or `init` labels every folder
    something and `--yes` writes it down. Built on a purpose-made tree because
    the synthetic corpus has no thin-majority directory -- and a threshold that
    nothing in the fixture can trip is a threshold that is not being tested.
    """
    tree = os.path.join(tmp, "thresholds")
    plant = {
        # 5 of 6 are images: a clear photo directory.
        "clear": ["a.png", "b.png", "c.png", "d.png", "e.png", "notes.md"],
        # No category reaches half. The honest answer is no role at all.
        "mixed": ["a.png", "b.png", "c.md", "d.md", "e.py", "f.py"],
        # Unanimous, and far too small to mean anything.
        "few": ["a.png", "b.png"],
    }
    for name, files in plant.items():
        os.makedirs(os.path.join(tree, name), exist_ok=True)
        for f in files:
            open(os.path.join(tree, name, f), "w").close()

    # Two directories that used to earn a confident, wrong role.
    #
    # `swamped` is the download folder shape: three PDFs and a thousand files
    # the rules do not recognise. Measured against classified files only that
    # is 3/3 and reads as a document directory.
    #
    # `tied` is an exact split. `most_common` breaks ties by insertion order,
    # so the old `<` comparison made the answer depend on the order the
    # filesystem happened to return names in.
    os.makedirs(os.path.join(tree, "swamped"), exist_ok=True)
    for f in ("a.pdf", "b.pdf", "c.pdf"):
        open(os.path.join(tree, "swamped", f), "w").close()
    for i in range(1000):
        open(os.path.join(tree, "swamped", "blob%d.zzz" % i), "w").close()
    os.makedirs(os.path.join(tree, "tied"), exist_ok=True)
    for f in ("a.md", "b.md", "c.pdf", "d.pdf"):
        open(os.path.join(tree, "tied", f), "w").close()

    roles = scan(tree).roles
    check("a clear majority earns a role", roles.get("image") == ["clear"],
          "image=%s" % roles.get("image"))
    # Asserted on each directory's verdict, not on the proposal. Only `image`
    # is a role now, so a directory that earns `document` never appears in the
    # proposal whatever the thresholds do -- a gate phrased over `roles` here
    # would be green for a reason that has nothing to do with what it claims.
    verdict = {d.name: d.role for d in scan(tree).dirs}
    check("a handful of known files among many unknown earns nothing",
          verdict.get("swamped") is None, "swamped=%r" % verdict.get("swamped"))
    check("an exact tie earns nothing rather than insertion order",
          verdict.get("tied") is None, "tied=%r" % verdict.get("tied"))
    check("the threshold directories were actually built",
          {"swamped", "tied", "clear"} <= set(verdict),
          "missing %s" % ({"swamped", "tied", "clear"} - set(verdict)))
    check("a thin majority earns nothing",
          all("mixed" not in (roles.get(r) or []) for r in userconfig.ROLES),
          "mixed appears in %s" % [r for r in userconfig.ROLES
                                   if "mixed" in (roles.get(r) or [])])
    check("too few files earns nothing",
          all("few" not in (roles.get(r) or []) for r in userconfig.ROLES),
          "few appears in %s" % [r for r in userconfig.ROLES
                                 if "few" in (roles.get(r) or [])])


def t_yes_does_not_replace_an_existing_config(tmp, root):
    """`--yes` answers the questions. Only `--force` may overwrite.

    The two were one condition, so a scripted `init --yes` replaced a config
    that was already there. That matters more than an ordinary clobber:
    `own_owners` cannot be reconstructed by `init` -- nothing on disk says
    which GitHub accounts are yours -- so the rewrite silently inverts the
    vendored-repo layer and starts excluding the user's own repositories.
    """
    path = os.path.join(tmp, "keepme.toml")
    original = ('root = "%s"\n[roles]\nimage = ["Bilder"]\n'
                '[vendored_repos]\nown_owners = ["acct-must-not-survive"]\n' % root)
    with open(path, "w") as fh:
        fh.write(original)

    def run(*flags):
        argv = ["init", "--root", root, "--config", path, *flags]
        try:
            return cli.main(argv), open(path).read()
        except SystemExit as exc:                         # noqa: PERF203
            return int(exc.code or 0), open(path).read()
        except Exception as exc:                          # noqa: BLE001
            return "raised:%s" % type(exc).__name__, open(path).read()

    code, after = run("--yes")
    check("`init --yes` refuses an existing config", code == 2, "exit %s" % code)
    check("`--yes` left the file byte-identical", after == original,
          "file changed" if after != original else "unchanged")
    # The refusal is only meaningful if --force still works; otherwise this
    # gate would pass just as well on an init that can never write anything.
    code, after = run("--yes", "--force")
    check("`--force` still replaces it", code == 0 and after != original,
          "exit %s, %s" % (code, "rewritten" if after != original else "same"))
    check("the replacement dropped the field init cannot know",
          "acct-must-not-survive" not in after,
          "dropped" if "acct-must-not-survive" not in after
          else "own_owners survived a rewrite it could not have reconstructed")


def t_pruned_ratio_does_not_decide_the_role(tmp):
    """A directory is named by its own files, not by the rubbish beside them.

    Found by running `init` on a real home directory, which the fixture had no
    equivalent of: a wiki holding 42 markdown files and nothing else was
    proposed as `cache`, because a dependency tree next to it outnumbered them
    and the pruned ratio short-circuited the role. Every repository with a
    `node_modules` was labelled by its junk. The ratio is still reported -- it
    is real information -- but it decides nothing.
    """
    tree = os.path.join(tmp, "swamped")
    notes = os.path.join(tree, "notebook")
    os.makedirs(notes, exist_ok=True)
    for i in range(8):
        open(os.path.join(notes, "note%d.md" % i), "w").close()
    # Twenty times as much excluded material as kept material, in a directory
    # the exclusion layer already prunes. Before the fix this alone decided.
    junk = os.path.join(notes, "node_modules", "pkg")
    os.makedirs(junk, exist_ok=True)
    for i in range(160):
        open(os.path.join(junk, "dep%d.js" % i), "w").close()

    prop = scan(tree)
    st = next((d for d in prop.dirs if d.name == "notebook"), None)
    check("the swamped directory was measured", st is not None
          and st.pruned >= 20 * max(st.files, 1),
          "files=%s pruned=%s" % (getattr(st, "files", None),
                                  getattr(st, "pruned", None)))
    check("the pruned ratio is still reported", bool(st and st.mostly_pruned),
          "mostly_pruned=%s" % (st and st.mostly_pruned))
    # Asserted on the directory's own verdict rather than on the proposal.
    # `note` is no longer a role the config carries -- only `image` is -- but
    # the verdict is still what the table shows the user, and it is still the
    # thing the pruned ratio must not override.
    check("its own files decide the verdict",
          st is not None and st.role == "note",
          "role=%r" % (getattr(st, "role", None),))
    check("a role nothing reads is not proposed",
          set(prop.roles) == set(userconfig.ROLES),
          "proposed keys %s vs roles %s"
          % (sorted(prop.roles), sorted(userconfig.ROLES)))


def t_retired_roles_are_named_not_ignored(tmp):
    """`cache` was a role nobody read. Removing it must not fail silently.

    A config written by an older `init` arrives pre-filled with the key. Two
    ways to be wrong: keep accepting it (the line the user believes), or reject
    it as an unrecognised typo (true, and useless). It is named instead.
    """
    path = os.path.join(tmp, "retired.toml")
    with open(path, "w") as fh:
        fh.write('root = "%s"\n[roles]\nimage = ["Pictures"]\n'
                 'cache = ["Documents"]\n' % tmp)
    try:
        userconfig.load(path)
        raised = ""
    except Exception as exc:                              # noqa: BLE001
        raised = str(exc)

    check("a retired role is rejected", bool(raised),
          "raised" if raised else "load() returned without raising")
    check("the message names the role and says what replaced it",
          "cache" in raised and "exclusions.toml" in raised,
          raised[:110] or "(no message)")
    check("`cache` is no longer a known role", "cache" not in userconfig.ROLES,
          "ROLES=%s" % (userconfig.ROLES,))


def t_scan_never_becomes_a_classifier():
    """The scanner proposes once; nothing in a build may call it.

    Structural, and deliberately so: a behavioural check cannot see a second
    classifier that happens to agree with the first today. This is the same
    shape as CP-0's one-place-only check, and for the same reason -- see
    DECISIONS.md §2.
    """
    offenders = []
    for name in ("corpus.py", "cli.py", "mesh.py", "incremental.py",
                 "models/m1_build.py", "models/m2_build.py",
                 "models/m3_build.py", "models/m4_misc.py"):
        path = os.path.join(REPO, "homegraph", name)
        src = open(path, encoding="utf-8").read()
        # cli.py imports it inside cmd_init, which is the one legal caller.
        body = src.split("def cmd_init", 1)[-1].split("\ndef ", 1)[0] \
            if name == "cli.py" else ""
        hits = [ln for ln in src.splitlines()
                if "scan" in ln and "import" in ln and ln not in body]
        offenders += ["%s: %s" % (name, h.strip()) for h in hits]
    check("only `init` imports the scanner", not offenders,
          "%d offending import(s) %s" % (len(offenders), offenders[:2]))


def t_no_mechanism_lives_only_in_tests():
    """Every documented mechanism must have a caller outside tests/.

    An external review found four of these at once: `no_open_guard`, described
    as "the enforcement" for the never-open-an-image rule; `apply_retention`,
    cited in three places as the reason the observations table has a ceiling;
    `refresh_all_datelists`, named as why cohort masks share an anchor; and
    `cohort_overlap`, named as the comparison the bitmask exists for. All four
    were reachable only from tests. The prose described a running system; the
    package shipped a library of unarmed mechanisms.

    That is worse than dead code, because dead code does not make a promise. A
    reader checking "is the observations table bounded?" finds a function, a
    docstring and a checkpoint that exercises it, and stops looking.

    Structural, like `t_scan_never_becomes_a_classifier` above and for the same
    reason: no behavioural check can see this. Every gate passes -- the tests
    call the function, so it works -- and the product never runs it.

    The list is explicit rather than derived from every public name. A derived
    version would have to encode which helpers are legitimately internal, and
    that judgement belongs in a list a reader can argue with.

    Two of the original four are deliberately NOT here, and the distinction is
    the whole point of the gate rather than an exception to it:

    * `no_open_guard` is a verification tool. An audit hook cannot be removed
      once installed, so arming it per build would leave a process-global
      tripwire in anything long-running. Its prose now says verification
      instead of enforcement, which makes it honest rather than armed.
    * `cohort_overlap` is a public helper for querying a store. Nothing in the
      package uses it because `_temporal_cohort` groups by equal mask, which
      is a dict lookup rather than a comparison per pair -- the mesh docstring
      used to claim otherwise and has been corrected.

    So a mechanism leaves this list by having its prose corrected, not by being
    quietly dropped. `refresh_all_datelists` stays: the anchor discipline it
    names is real, `_temporal_cohort` now enforces it by keying on the anchor,
    and if that ever becomes the only enforcement this should fail again.
    """
    must_be_called = {
        "apply_retention": "temporal.py: 'full daily observations for 90 days'",
        "refresh_all_datelists": "mesh.py: 'every node's mask must share one "
                                 "anchor'",
    }
    pkg = os.path.join(REPO, "homegraph")
    sources = {}
    for dirpath, _, files in os.walk(pkg):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                sources[os.path.relpath(p, pkg)] = open(
                    p, encoding="utf-8").read()

    # Parsed, not grepped. The first version matched text and passed
    # `no_open_guard` on the strength of the module docstring that names it:
    # "`no_open_guard()` is the enforcement". A gate looking for a mechanism
    # that exists only in prose cannot itself be satisfied by prose.
    called = collections.defaultdict(list)
    for rel, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else None)
            if name:
                called[name].append("%s:%d" % (rel, node.lineno))

    unarmed = ["%s -- %s" % (name, claim)
               for name, claim in sorted(must_be_called.items())
               if not called.get(name)]

    check("no documented mechanism is reachable only from tests",
          not unarmed,
          "%d unarmed: %s" % (len(unarmed), unarmed[:2]) if unarmed
          else "%d mechanism(s) have a caller in the package"
               % len(must_be_called))


# -- 3. the config is load-bearing -----------------------------------------

def t_empty_image_role(tmp, root):
    """No image role means no image files. Not a smaller count -- none."""
    cfg = os.path.join(tmp, "no-image.toml")
    userconfig.write(cfg, root, {"image": [], "document": ["Documents"]})
    labels = _labels(root, cfg)
    n = sum(1 for v in labels.values() if v == "image")
    check("emptying the image role removes every image", n == 0,
          "%d image(s) classified" % n)
    # And they must be EXCLUDED, not demoted into the junk drawer -- decision 2
    # again, at a new entry point.
    png = [k for k in labels if k.endswith(".png") and "Bilder" in k]
    demoted = [k for k in png if labels[k] != "EXCLUDED"]
    check("images with no root are excluded, never demoted to misc",
          png and not demoted,
          "%d image-extension file(s), %d demoted" % (len(png), len(demoted)))


def t_image_role_points_at_an_empty_directory(tmp, root):
    """An image root that exists and holds nothing: M2 absent, not silent zero.

    The distinction the whole federation layer exists for. Zero results and a
    model that never answered look identical unless something says which.
    """
    empty = os.path.join(tmp, "EmptyPictures")
    os.makedirs(empty, exist_ok=True)
    cfg = os.path.join(tmp, "empty-image.toml")
    userconfig.write(cfg, root, {"image": [empty]})
    labels = _labels(root, cfg)
    n = sum(1 for v in labels.values() if v == "image")
    check("an empty image directory yields zero image files", n == 0,
          "%d image(s)" % n)

    from homegraph.mesh import Mesh
    with Mesh({"m2": os.path.join(tmp, "no-such-m2.db")}) as mesh:
        res = mesh.search("anything", limit=5)
        check("a missing image model is reported as partial, and named",
              res.partial and "m2" in res.models_missing,
              "status=%s missing=%s" % (res.status, res.models_missing))


def t_generated_dirs_come_from_the_config():
    """A machine-specific directory name is config, not a shipped rule."""
    path = "/anywhere/graph-export/wiki/Feature_Extraction.md"
    check("an unconfigured generated directory is an ordinary note",
          subtype_of(path) == "note", subtype_of(path))
    check("a configured generated directory changes the subtype",
          subtype_of(path, {"generated_markers": ("/graph-export/",)})
          == "generated",
          subtype_of(path, {"generated_markers": ("/graph-export/",)}))
    # The shipped filename marker must survive the config being set, or
    # configuring one directory silently stops recognising reports elsewhere.
    report = "/anywhere/else/GRAPH_REPORT.md"
    check("configuring a directory does not disable the shipped marker",
          subtype_of(report, {"generated_markers": ("/graph-export/",)})
          == "generated",
          subtype_of(report, {"generated_markers": ("/graph-export/",)}))


def t_config_write_is_atomic(tmp):
    """A config is written whole or not at all.

    The dangerous half-write is not a corrupt file -- that is refused loudly.
    It is `root = "/h"` with no [roles]: valid TOML, loads cleanly, every role
    empty. The next `update` then finds no images and removes the image corpus
    while reporting success. So the failure mode to exclude is "parses, and is
    wrong", not "does not parse".
    """
    path = os.path.join(tmp, "atomic.toml")
    userconfig.write(path, tmp, {"image": ["Pictures"]})
    before = open(path).read()

    # Interrupt render() midway, exactly where a signal would land.
    real_render = userconfig.render

    def exploding_render(*a, **kw):
        raise KeyboardInterrupt("interrupted while writing")

    userconfig.render = exploding_render
    try:
        userconfig.write(path, tmp, {"image": ["Something-Else"]})
        outcome = "returned normally"
    except KeyboardInterrupt:
        outcome = "raised"
    except Exception as exc:                                  # noqa: BLE001
        outcome = "raised:%s" % type(exc).__name__
    finally:
        userconfig.render = real_render

    check("an interrupted write propagates rather than half-succeeding",
          outcome == "raised", outcome)
    check("the previous config survives byte-for-byte",
          os.path.exists(path) and open(path).read() == before,
          "file %s" % ("changed" if os.path.exists(path) else "gone"))
    check("no scratch file is left in the config directory",
          not [f for f in os.listdir(tmp) if ".tmp-" in f],
          "%s" % [f for f in os.listdir(tmp) if ".tmp-" in f])
    # And the survivor must still be usable, not merely present.
    try:
        reloaded = userconfig.load(path)
        roles = list(reloaded.roles.get("image", ()))
    except Exception as exc:                                  # noqa: BLE001
        roles = "raised:%s" % type(exc).__name__
    check("the survivor still loads with its roles intact",
          roles == ["Pictures"], "%r" % (roles,))


def t_config_write_is_durable(tmp):
    """Atomic is not durable, and the write claims both.

    `t_config_write_is_atomic` proves no reader sees a half-written config.
    It cannot see the other half of the promise: `os.replace` publishes the new
    name inside the parent directory, and until that directory's own metadata
    is flushed the rename lives in the page cache. A power cut there loses the
    rename while keeping every fsync'd byte of the file it renamed -- the
    config reverts, the store built from it does not.

    Checked by watching which file descriptors get fsync'd, because the real
    event is a power cut and there is no way to stage one. So this gate is
    about the syscall being issued, not about the disk. Weaker than the thing
    it stands for, and named accordingly.
    """
    import stat as stat_mod

    target = os.path.join(tmp, "durable", "config.toml")
    synced = []
    real_fsync = os.fsync

    def spy(fd):
        try:
            st = os.fstat(fd)
            synced.append((stat_mod.S_ISDIR(st.st_mode), st.st_ino))
        except OSError:
            pass
        return real_fsync(fd)

    os.fsync = spy
    try:
        userconfig.write(target, tmp, {"image": ["Pictures"]})
    finally:
        os.fsync = real_fsync

    parent = os.path.dirname(target)
    check("the config's bytes are fsynced before the rename",
          any(not is_dir for is_dir, _ in synced),
          "%d fsync(s), %d on a file"
          % (len(synced), sum(1 for d, _ in synced if not d)))
    check("the rename is fsynced too, not only the bytes",
          any(is_dir and ino == os.stat(parent).st_ino
              for is_dir, ino in synced),
          "%d fsync(s) on a directory"
          % sum(1 for d, _ in synced if d))
    # A directory fsync that raises must not turn a completed write into a
    # reported failure: the file is already in place by then.
    def angry(fd):
        # Only the directory. A file fsync that fails means the bytes may not
        # be on disk, and that one SHOULD propagate -- swallowing it is how a
        # write reports success for a config that is not there.
        if stat_mod.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(22, "EINVAL on this filesystem")
        return real_fsync(fd)

    second = os.path.join(tmp, "durable", "config2.toml")
    os.fsync = angry
    try:
        userconfig.write(second, tmp, {"image": ["Pictures"]})
        outcome = "returned"
    except Exception as exc:                                    # noqa: BLE001
        outcome = "raised:%s" % type(exc).__name__
    finally:
        os.fsync = real_fsync
    check("a filesystem that refuses fsync does not fail the write",
          outcome == "returned" and os.path.exists(second), outcome)


def t_generated_dirs_reach_the_build(tmp):
    """The config must reach the extractor through the path a user takes.

    The three checks above prove the MECHANISM works when rules are handed to
    `subtype_of` directly. They passed for as long as the key existed while
    every real caller -- the CLI and `update` -- built M3 with no rules at all,
    so a configured directory still came out as `note`. A gate that exercises
    a code path nobody runs is a new shape of empty gate: the assertion is
    real, the subject is not.

    So this one goes through `build()` the way the callers do, and through
    `rules_from_config`, which is now the single place that turns a config into
    extractor rules.
    """
    from homegraph.models.m3_build import build, rules_from_config
    from homegraph.store import Store

    gen = os.path.join(tmp, "gen-corpus", "reports")
    os.makedirs(gen, exist_ok=True)
    page = os.path.join(gen, "summary.md")
    with open(page, "w") as fh:
        fh.write("# Summary\n\nGenerated prose.\n")

    cfgpath = os.path.join(tmp, "gen.toml")
    userconfig.write(cfgpath, os.path.dirname(gen), {"image": []},
                     generated_dirs=("/reports/",))
    cfg = userconfig.load(cfgpath)

    def subtype_after_build(rules):
        db = os.path.join(tmp, "gen-%s.db" % bool(rules))
        if os.path.exists(db):
            os.remove(db)
        try:
            with Store(db, model="m3") as s:
                build(s, [page], "2026-01-01", rules=rules)
                row = s.db.execute(
                    "SELECT subtype FROM nodes WHERE path = ?", (page,)
                ).fetchone()
                return row["subtype"] if row else "(no node)"
        except Exception as exc:                              # noqa: BLE001
            return "raised:%s" % type(exc).__name__

    check("the config declares the directory",
          cfg.generated_dirs == ("/reports/",), "%r" % (cfg.generated_dirs,))
    check("without the config the page is an ordinary note",
          subtype_after_build(None) == "note", subtype_after_build(None))
    check("rules_from_config carries the directory into the build",
          subtype_after_build(rules_from_config(cfg)) == "generated",
          subtype_after_build(rules_from_config(cfg)))


# -- 4. no layout is imposed -----------------------------------------------

def t_same_rules_same_partition(tmp):
    """Two roots, two languages, two configs, one partition."""
    from tests.fixtures import synthetic as syn

    no_root = os.path.join(tmp, "corpus-no")
    en_root = os.path.join(tmp, "corpus-en")
    _, no_cases = syn.build(no_root)
    _, en_cases, en_roles = syn.build_english(en_root)

    check("the English variant renamed the directories",
          en_roles["image"] == ["Pictures"]
          and os.path.isdir(os.path.join(en_root, "Pictures"))
          and not os.path.exists(os.path.join(en_root, "Bilder")),
          "image role %s" % en_roles["image"])
    # Neither corpus is `~`, and neither config was written by scanning.
    check("neither root is the home directory",
          os.path.realpath(no_root) != os.path.realpath(os.path.expanduser("~"))
          and os.path.realpath(en_root)
          != os.path.realpath(os.path.expanduser("~")),
          "%s / %s" % (os.path.basename(no_root), os.path.basename(en_root)))

    no_labels = _labels(no_root, syn._config_for(no_root))
    en_labels = _labels(en_root, syn._config_for(en_root))

    check("both corpora were classified at all",
          len(no_labels) > 100 and len(no_labels) == len(en_labels),
          "%d vs %d files" % (len(no_labels), len(en_labels)))

    # Sets, not counts. Counts are equal when one node is swapped for another.
    translated = {syn._rename(rel, syn.ENGLISH_DIRS): label
                  for rel, label in no_labels.items()}
    differing = sorted(k for k in set(translated) | set(en_labels)
                       if translated.get(k) != en_labels.get(k))
    check("renaming the directories changes no label", not differing,
          "%d path(s) differ%s" % (len(differing),
                                   "" if not differing else "  %s"
                                   % differing[:2]))

    # Same comparison one level down: not just the label, but the layer that
    # produced it. Labels agreeing while layers diverge is a real outcome --
    # move a directory and an earlier layer can start claiming files a later
    # one used to own, with every count unchanged. The gate above would stay
    # green through that, and so would the census.
    no_dec = _decisions(no_root, syn._config_for(no_root))
    en_dec = _decisions(en_root, syn._config_for(en_root))
    translated_dec = {syn._rename(rel, syn.ENGLISH_DIRS): d
                      for rel, d in no_dec.items()}
    layer_diff = sorted(
        (k, translated_dec.get(k), en_dec.get(k))
        for k in set(translated_dec) | set(en_dec)
        if translated_dec.get(k) != en_dec.get(k))
    check("renaming the directories changes no deciding rule", not layer_diff,
          "%d path(s) decided differently%s"
          % (len(layer_diff),
             "" if not layer_diff else "  %s" % (layer_diff[:2],)))
    # The finer comparison must be strictly harder than the coarse one, or it
    # is decoration. Every label difference is a decision difference; a run
    # where the labels diverge and the decisions do not means `_decisions` is
    # not reading what it claims to read.
    check("the rule comparison subsumes the label comparison",
          len(layer_diff) >= len(differing),
          "%d decision diffs vs %d label diffs"
          % (len(layer_diff), len(differing)))

    # And each still matches its own declared key, so "identical" cannot mean
    # "identically wrong".
    wrong_no = [(rel, want) for want, _s, _h, rel, _w in no_cases
                if no_labels.get(rel) != want]
    wrong_en = [(rel, want) for want, _s, _h, rel, _w in en_cases
                if en_labels.get(rel) != want]
    check("the Norwegian corpus matches its declared key", not wrong_no,
          "%d/%d wrong %s" % (len(wrong_no), len(no_cases), wrong_no[:2]))
    check("the English corpus matches its declared key", not wrong_en,
          "%d/%d wrong %s" % (len(wrong_en), len(en_cases), wrong_en[:2]))

    # A negative control for this gate: with the English config left pointing
    # at the Norwegian directory name, the partition MUST diverge. Without
    # this, "no label changed" would also be true of a classifier that ignored
    # the config entirely.
    bad_cfg = os.path.join(tmp, "en-wrong-role.toml")
    userconfig.write(bad_cfg, en_root, dict(syn.DECLARED_ROLES))
    bad_labels = _labels(en_root, bad_cfg)
    moved = sum(1 for k, v in en_labels.items()
                if v == "image" and bad_labels.get(k) != "image")
    check("pointing the role at the wrong name breaks the partition",
          moved == sum(1 for v in en_labels.values() if v == "image")
          and moved > 0,
          "%d image(s) lost when the role names a directory that is gone"
          % moved)

    # The scanner, independently, must find the English layout too.
    proposed = scan(en_root).roles.get("image")
    check("init proposes the English image directory", proposed == ["Pictures"],
          "%s" % proposed)
    return no_labels, en_labels


def t_labels_are_known(no_labels, en_labels):
    bad = sorted({v for v in list(no_labels.values()) + list(en_labels.values())
                  if v not in ALL_LABELS})
    check("every label from both corpora is a known one", not bad,
          "unknown: %s" % bad)


def main():
    from tests.fixtures import synthetic as syn
    syn.build_once()
    tmp = tempfile.mkdtemp(prefix="cp7-",
                           dir=os.path.expanduser("~/.homegraph"))
    try:
        t_refuses_without_config(tmp)
        t_init_writes_config(tmp, syn.ROOT)
        t_scan_thresholds(tmp)
        t_yes_does_not_replace_an_existing_config(tmp, syn.ROOT)
        t_pruned_ratio_does_not_decide_the_role(tmp)
        t_retired_roles_are_named_not_ignored(tmp)
        t_scan_never_becomes_a_classifier()
        t_no_mechanism_lives_only_in_tests()
        t_empty_image_role(tmp, syn.ROOT)
        t_image_role_points_at_an_empty_directory(tmp, syn.ROOT)
        t_generated_dirs_come_from_the_config()
        t_config_write_is_atomic(tmp)
        t_config_write_is_durable(tmp)
        t_generated_dirs_reach_the_build(tmp)
        no_labels, en_labels = t_same_rules_same_partition(tmp)
        t_labels_are_known(no_labels, en_labels)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # The fixture's env var was repointed by build()/build_english(); put
        # it back so a later checkpoint in the same pytest process does not
        # inherit a config for a corpus that has just been deleted.
        syn.use_config(syn.CONFIG)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_checkpoint_cp7():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
