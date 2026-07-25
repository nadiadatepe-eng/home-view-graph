#!/usr/bin/env python3
"""Command line for the whole package.

    homegraph init [--root DIR]          look at a directory, propose roles
    homegraph explain <path>...          which rule decided, and why
    homegraph census [--root DIR]        counts per category and subtype
    homegraph config                     show the config in force
    homegraph build | update | embed     build a model, or move it forward
    homegraph md build|backlinks|broken  the markdown model
    homegraph mesh search|explain|...    the federation across models
    homegraph status | search            read a store that already exists
    homegraph visualize | mcp            a page, or an MCP server over stdio

`init` comes first because everything that reads your disk depends on it. It
scans whatever directory you point it at, proposes which folders hold images,
and writes `~/.homegraph/config.toml`. No structure is imposed: the proposal is
evidence about your disk, and you can overwrite it.

Until that file exists, every command that has to know your layout **refuses
with exit 2** and says so. Guessing a directory name would produce an empty
image model that reports success, which is the worst of the available failures.
The commands that only read a store already built -- `status`, `search`,
`visualize`, `mesh`, `mcp` -- take a database path and never consult the
config, so they neither need it nor refuse without it.

`explain` exists because a classifier nobody can interrogate is a classifier
nobody can argue with. When a file lands somewhere surprising, this prints the
layer and the specific rule that put it there, so the next move is editing a
rule rather than reading source.

**The corpus-layer commands do not open a classified file.** `census`, `init`
and `explain` walk the tree for names and `stat()`, nothing more -- CP-4
verifies that over `init` with an audit hook and with strace, the same two ways
it verifies the image build. That is a claim about those three. `md build` and
`update` do read the files they index, because extracting text is what they are
for; this docstring described a three-command CLI and kept saying "no command
here" long after the other twelve arrived.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import os
import sys

# Running this file directly -- an editor's Run button, or `python cli.py` --
# gives it no package context, and the relative imports below fail with
# "attempted relative import with no known parent package". That message
# describes Python's import machinery rather than anything the reader did
# wrong, so re-enter as a module instead of printing it: put the project root
# on the path and hand off to `homegraph.cli`. The exit code is the child's.
#
# The guard is on __package__ rather than __name__, because `python -m
# homegraph.cli` also sets __name__ to "__main__" and must NOT recurse.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from homegraph.cli import main as _main

    sys.exit(_main())

from . import userconfig
from .config import home_root
from .corpus import ALL_LABELS, EXCLUSION_LAYERS, TOP_DIRS, Classifier


def _walk(root):
    """Yield (path, is_symlink) for every non-directory under root.

    Does not follow symlinks -- following them would double-count targets and
    can leave the corpus root entirely.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            full = os.path.join(dirpath, name)
            yield full, os.path.islink(full)
        # Directory symlinks show up in dirnames; classify and skip them.
        for name in list(dirnames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                dirnames.remove(name)
                yield full, True


def cmd_init(args):
    """Propose roles for a directory, then write the config the rest reads.

    `--root` takes any directory. There is no expected layout and no folder is
    created: the scan reports what is there, and an empty role is a legitimate
    answer that makes the matching model absent rather than wrong.
    """
    from .scan import format_proposal, scan

    root = os.path.abspath(os.path.expanduser(args.root or home_root()))
    if not os.path.isdir(root):
        print("not a directory: %s" % root, file=sys.stderr)
        return 2

    path = userconfig.config_path(args.config)
    # `--force` is the only thing that may replace an existing config. `--yes`
    # used to count too, which conflated two different permissions: "do not ask
    # me the role questions" and "throw away the file I already have". A
    # scripted `init --yes` would then silently overwrite -- and `own_owners`
    # is the one field `init` cannot reconstruct, because no evidence on disk
    # says which accounts are yours. The cheap reading of --yes cost the field
    # that has to be filled in by hand.
    if os.path.exists(path) and not args.force:
        print("%s already exists. Edit it, or re-run with --force to replace "
              "it.\n(--yes answers the role questions; it does not replace an "
              "existing config.)" % path, file=sys.stderr)
        return 2

    prop = scan(root)
    print(format_proposal(prop))
    roles = {name: list(dirs) for name, dirs in prop.roles.items()}

    if not args.yes:
        print("\nAccept, or type a comma-separated replacement for any role.")
        print("Entries are directories under the root, or absolute paths. "
              "Empty line accepts; a single '-' clears the role.")
        for name in userconfig.ROLES:
            current = ", ".join(roles.get(name) or []) or "(none)"
            try:
                answer = input("  %-8s [%s]: " % (name, current)).strip()
            except EOFError:
                # Not an interactive terminal and --yes was not passed. Refuse
                # rather than silently taking the proposal: a script that meant
                # to accept can say so.
                print("\nno terminal to confirm on; re-run with --yes to "
                      "accept the proposal unattended", file=sys.stderr)
                return 2
            if not answer:
                continue
            roles[name] = ([] if answer == "-" else
                           [p.strip() for p in answer.split(",") if p.strip()])

    userconfig.write(path, root, roles)
    print("\nwrote %s" % path)
    for name in userconfig.ROLES:
        print("  %-8s %s" % (name, ", ".join(roles.get(name) or []) or "(none)"))
    if not roles.get("image"):
        print("\nNo image role. M2 will be absent; mesh will report `partial` "
              "and name it.")
    return 0


def cmd_config(args):
    """Print the loaded config, resolved. Refuses if there is none."""
    cfg = userconfig.load(args.config)
    print("path   %s" % cfg.path)
    print("root   %s" % cfg.root)
    for name in userconfig.ROLES:
        print("  %-8s %s" % (name, ", ".join(cfg.role_dirs(name)) or "(none)"))
    print("own_owners     %s" % (", ".join(cfg.own_owners) or "(none)"))
    print("generated_dirs %s" % (", ".join(cfg.generated_dirs) or "(none)"))
    return 0


def cmd_explain(args):
    clf = Classifier()
    for path in args.paths:
        d = clf.explain(path)
        print("%s\n    label   %s\n    subtype %s\n    layer   %s\n    rule    %s"
              % (path, d.label, d.subtype, d.layer, d.detail))
    return 0


def cmd_census(args):
    from .corpus import ExclusionReport
    clf = Classifier()
    by_label = collections.Counter()
    by_subtype = collections.Counter()
    # `--all` means no cap, which is the only way to see the tail. The cap is
    # not the problem; a cap nobody is told about is.
    excluded = ExclusionReport(args.root, cap=None if args.all else args.top)

    for path, is_link in _walk(args.root):
        d = excluded.record(path, clf.explain(path, is_symlink=is_link))
        by_label[d.label] += 1
        by_subtype[(d.label, d.subtype)] += 1

    total = sum(by_label.values())
    print("root:  %s" % args.root)
    print("files: %d\n" % total)

    print("%-10s %9s %7s" % ("category", "count", "share"))
    for label in ALL_LABELS:
        n = by_label[label]
        print("%-10s %9d %6.2f%%" % (label, n, n / total * 100 if total else 0))

    print("\n%-10s %-22s %9s" % ("category", "subtype", "count"))
    for (label, subtype), n in sorted(by_subtype.items(),
                                      key=lambda kv: (kv[0][0], -kv[1])):
        print("%-10s %-22s %9d" % (label, subtype, n))

    print("\n%-18s %9s" % ("layer", "excluded"))
    for layer in EXCLUSION_LAYERS:
        # Every layer is listed, including the ones that owned nothing. A
        # layer missing from the output reads as "no such rule"; a layer
        # showing 0 reads as "this corpus does not reach it", which is the
        # finding CP-0's per-layer check exists to surface.
        print("%-18s %9d" % (layer, excluded.by_layer.get(layer, 0)))

    shown = excluded.top()
    print("\n%d excluded director%s%s"
          % (len(shown), "y" if len(shown) == 1 else "ies",
             (" of %d -- re-run with --all for the rest"
              % excluded.dirs_total) if excluded.truncated else ""))
    for name, n in shown:
        print("  %9d  %s" % (n, name))
    return 0


def cmd_query(args):
    """Run one query. Grammar in DECISIONS.md section 25.

    Refusals exit 2 and name the missing capability, because "syntax error at
    position 31" sends the reader to look for a typo in a query that is
    correctly written in a language this one is not.
    """
    from .query import QueryError, run
    from .store import Store

    text = " ".join(args.query)
    with Store(args.db) as s:
        try:
            res = run(s, text)
        except QueryError as exc:
            print("REFUSED  %s" % exc, file=sys.stderr)
            if exc.missing:
                print("         (not supported: %s)" % exc.missing,
                      file=sys.stderr)
            return 2

    if res.status == "ambiguous":
        # crg's behaviour, and the one cbm does not have: name the candidates
        # and answer nothing, rather than pick one and look certain.
        for w in res.warnings:
            print("AMBIGUOUS  %s" % w, file=sys.stderr)
        for c in res.candidates:
            print("  %s" % c, file=sys.stderr)
        return 2

    print("  ".join(res.columns))
    for row in res.rows:
        print("  ".join("" if v is None else str(v) for v in row))
    print("\n%d row(s)  %s" % (len(res.rows), res.status))
    for w in res.warnings:
        print("PARTIAL -- %s" % w)
    return 0


def cmd_status(args):
    from .store import Store
    with Store(args.db) as s:
        st = s.status()
        for key in ("path", "model", "schema_version", "nodes", "edges",
                    "observations", "observations_monthly", "fts_rows",
                    "fts_stale", "embeddings", "embeddings_enabled"):
            print("%-22s %s" % (key, st[key]))
        for ns in st["embeddings_coverage"]:
            pct = 100.0 * ns["embedded"] / ns["of"] if ns["of"] else 0.0
            print("%-22s %s:%s:%d  %d/%d nodes with text (%.0f%%)%s"
                  % ("embeddings_ns", ns["provider"], ns["model"], ns["dim"],
                     ns["embedded"], ns["of"], pct,
                     "  INCOMPLETE" if ns["stale"] else ""))
        if st["fts_stale"]:
            print("\nWARNING: the FTS index does not cover every node. "
                  "Searches will silently under-return until rebuild_fts().")
        # Printed for the same reason `fts_stale` is, and it was missing for as
        # long as embeddings have existed. An incomplete namespace does not make
        # the vector path refuse -- it makes it run over part of the corpus and
        # report `hybrid`, which reads as a whole answer.
        for ns in st["embeddings_coverage"]:
            if ns["stale"]:
                print("\nWARNING: %d of %d node(s) with text have no vector "
                      "under %s:%s:%d. Semantic search will run and quietly "
                      "leave them out; `homegraph embed` fills the gap and now "
                      "only embeds what is missing."
                      % (ns["of"] - ns["embedded"], ns["of"],
                         ns["provider"], ns["model"], ns["dim"]))
    return 0


def cmd_search(args):
    from . import providers
    from .search import hybrid_search
    from .store import Store

    # `--embeddings <locator>` turns on semantic reranking. Named explicitly
    # rather than read from the config, because `search` reads a database path
    # and what you type -- the invariant this command's help promises, and the
    # reason the ollama form is a locator rather than a flag meaning "go look in
    # config.toml". Both forms self-describe their provider/model/dim (the
    # matrix from its own header, the server from a probe embedding), so the
    # store is opened with that namespace and its vectors must have been written
    # under it by `homegraph embed`; a different one finds zero and says so.
    embedder = None
    embeddings = None
    if getattr(args, "embeddings", None):
        try:
            embedder = providers.from_locator(args.embeddings)
        except providers.PROVIDER_ERRORS as exc:
            print("%s" % exc, file=sys.stderr)
            return 2
        embeddings = {"provider": embedder.provider, "model": embedder.model}

    mode = getattr(args, "mode", "auto")
    if mode == "vector" and embedder is None:
        # Vector mode has no lexical fallback, so without a matrix it can only
        # return nothing. Refuse up front with the fix rather than print an
        # empty result and a warning.
        print("`--mode vector` needs `--embeddings <matrix>`; there is no "
              "lexical fallback in vector mode.", file=sys.stderr)
        return 2

    # The provider is reached AGAIN here -- `hybrid_search` embeds the query --
    # so the handler has to cover the search, not just the construction above.
    # It did not, and a server that died between the probe and the query gave a
    # traceback where every other failure in this command gives exit 2.
    with Store(args.db, embeddings=embeddings) as s:
        try:
            res = hybrid_search(s, " ".join(args.query), limit=args.limit,
                                embedder=embedder, mode=mode)
        except providers.PROVIDER_ERRORS as exc:
            print("%s" % exc, file=sys.stderr)
            return 2
        for w in res.warnings:
            print("WARNING: %s\n" % w)
        # Printed even when empty, and the mode is printed either way: a search
        # that found nothing and a search whose vector half never ran look
        # identical unless you say which happened.
        print("%d hit(s), _out_mode=%s" % (len(res), res._out_mode))
        for hit in res.hits:
            print("  %2d  %-8.5f %-40s %s"
                  % (hit["rank"], hit["score"] if "score" in hit else 0.0,
                     (hit.get("title") or "")[:40], hit.get("node_key")))
    return 0


def cmd_visualize(args):
    from . import providers
    from .visualize import render

    # Static matrices only, and this is a property of the OUTPUT rather than a
    # gap in the wiring. The page inlines a word-level sub-matrix so the browser
    # can embed a typed query with no network; a server-backed provider has no
    # word matrix to inline, and a page that called out to an endpoint would
    # stop being the self-contained file this command exists to produce.
    # Refused by name, because the alternative -- `static_embed.load()` failing
    # on a string that is obviously not a path -- would report "no such file"
    # about a locator that was never meant to be one.
    emb = getattr(args, "embeddings", None)
    if emb and "://" in emb:
        print("visualize --embeddings takes a matrix data file, not %r.\n\n"
              "The page embeds queries in the browser from an inlined word "
              "matrix, so it works offline; a server-backed provider has no "
              "matrix to inline. Use a distilled matrix here, or drop "
              "--embeddings and keep the lexical title search." % emb,
              file=sys.stderr)
        return 2
    # This command had NO handler for a bad matrix at all -- a typo in the path
    # was a traceback, while every sibling command exits 2 with the fix in it.
    # CP-I1 touched this function without closing that, which the audit named.
    try:
        report = render({n: p for n, _, p in
                         ((s.partition("=")[0], None, s.partition("=")[2])
                          for s in args.model)},
                        args.out, limit_per_model=args.limit,
                        min_degree=args.min_degree, title=args.title,
                        mesh_db=getattr(args, "mesh_db", None),
                        embeddings=emb)
    except providers.PROVIDER_ERRORS as exc:
        print("%s" % exc, file=sys.stderr)
        return 2
    for key, value in report.items():
        print("%-12s %s" % (key, value))
    print("\nOpen it in a browser:  file://%s" % os.path.abspath(args.out))
    return 0


def cmd_mcp(args):
    from .mcp_server import Server
    models = {}
    for spec in args.model:
        name, _, path = spec.partition("=")
        models[name] = path
    Server(models, args.mesh_db).serve()
    return 0


def cmd_update(args):
    """Apply a filesystem diff to already-built models.

    One store per model, as everywhere else. `--mesh-db` additionally refreshes
    the federation, because mesh mirrors model nodes and a stub for a file that
    is gone answers confidently about a world that no longer exists.
    """
    from datetime import date

    from . import update as up

    cfg = userconfig.load(getattr(args, "config", None))
    root = os.path.abspath(os.path.expanduser(args.root or cfg.root))
    as_of = args.as_of or date.today().isoformat()

    models = {}
    for spec in args.model:
        name, _, path = spec.partition("=")
        models[name] = path

    failed = False
    for name, path in sorted(models.items()):
        spec = up.SPECS.get(name)
        if spec is None:
            print("%-6s REFUSED  %s"
                  % (name, up.NO_INCREMENTAL.get(
                      name, "unknown model; known: %s"
                            % ", ".join(sorted(up.SPECS)))), file=sys.stderr)
            failed = True
            continue
        if not os.path.exists(path):
            print("%-6s REFUSED  no store at %s -- build it first"
                  % (name, path), file=sys.stderr)
            failed = True
            continue
        paths, excluded = up.corpus_paths(root, spec.label, config=cfg)
        # Printed before the update runs, so a corpus that silently tripled --
        # an exclusion layer switched off -- is visible next to the diff that
        # is about to be applied rather than after it.
        print("%-6s %s" % (name, excluded.line()))
        with _writing(path, model=name,
                      fingerprint=up.fingerprint(cfg)) as store:
            try:
                report = up.update(store, name, paths, as_of, cfg,
                                   allow_config_change=args.allow_config_change)
            except (up.NotBuilt, up.ConfigChanged, up.CannotUpdate) as exc:
                print("%-6s REFUSED  %s" % (name, exc), file=sys.stderr)
                failed = True
                continue
        print("%-6s %s" % (name, report.summary()))
        if report.unreadable:
            # Named files, not just a count, and stderr with a non-zero exit:
            # these nodes now hold text from before the update and none of the
            # relations they used to assert, because the old edges were deleted
            # before the read failed. Reporting success here would be the
            # quietest wrong answer this command can give.
            print("%-6s WARNING  %d file(s) could not be read during the "
                  "rebuild; their nodes are stale. Rebuild this model."
                  % (name, len(report.unreadable)), file=sys.stderr)
            for path, why in report.unreadable[:5]:
                print("         %s  %s" % (path, why), file=sys.stderr)
            failed = True

    if failed:
        # Exit 2, the same refusal code the rest of the CLI uses. A partial
        # update that exits 0 is a partial update nobody will notice.
        return 2

    if args.mesh_db:
        # The code inventory is re-walked here, not remembered: `update` runs
        # because the tree moved, and a remembered list of source files is a
        # claim about a tree that has since changed. Without --code-root the
        # federation still refreshes, and `build_edges` refuses to prune code
        # stubs it was not given the inventory for.
        code_paths = _code_inventory(getattr(args, "code_root", None), cfg)
        from .mesh import ModelUnavailable
        try:
            with _barrier(args.mesh_db):
                result = up.refresh_mesh(models, args.mesh_db, as_of,
                                         code_paths=code_paths)
        except ModelUnavailable as exc:
            print("mesh   REFUSED  %s" % exc, file=sys.stderr)
            return 2
        print("mesh   %s" % result)
    return 0


def cmd_watch(args):
    """Foreground: re-run `update` whenever the corpus changes on disk.

    Opt-in and non-persistent -- inotify while this process lives, nothing
    after Ctrl-C. The borrowed idea (react to OS events, measured against
    codegraph 2026-07-23) without the daemon homegraph has deliberately never
    had. Each change fires the very same `update` the user would run by hand:
    the watch is a trigger, not a second path into the graph.
    """
    from datetime import datetime

    from . import watch as wat

    cfg = userconfig.load(getattr(args, "config", None))
    root = os.path.abspath(os.path.expanduser(args.root or cfg.root))
    if not os.path.isdir(root):
        print("not a directory: %s" % root, file=sys.stderr)
        return 2
    # The debounce is now the irrelevant-burst backoff too (watch_loop sleeps
    # it), so a non-positive value is no longer merely odd: negative raises out
    # of time.sleep and kills the watch, and zero re-opens the CPU spin. Reject
    # both here rather than let either reach the loop.
    if args.debounce <= 0:
        print("watch  REFUSED  --debounce must be > 0 (got %s)" % args.debounce,
              file=sys.stderr)
        return 2

    # The stores an update writes, so their own writes never trigger another
    # update. Absolute, because `relevant` compares absolute paths.
    ignore = set()
    for spec in args.model:
        _name, _, path = spec.partition("=")
        ignore.add(os.path.abspath(os.path.expanduser(path)))
    if args.mesh_db:
        ignore.add(os.path.abspath(os.path.expanduser(args.mesh_db)))

    # Watch only the tree the corpus cares about. The classifier owns the
    # exclusion rule; watch borrows it rather than re-deriving one, so the two
    # can never disagree about what `.cache`/`.venv`/a vendored repo is. Only
    # the directory-structural layers prune -- a per-file reason (a secret
    # name, an image outside its root) still lets its directory be watched.
    from .corpus import EXCLUDED, Classifier
    clf = Classifier(home=root)
    structural = {"L1_dependencies", "L2_app_state", "L3_cache",
                  "L4_vendored_repo"}

    # The stores live inside the watched tree, so every update's own writes come
    # straight back as events. `ignore` already stops them *triggering* an
    # update, but a watched store still arms an inotify watch and its ~40MB write
    # flood spins the loop -- so prune the directories that hold them one layer
    # earlier. `store_prune` owns that rule (the braces to `ignore`'s belt); the
    # classifier owns the corpus-exclusion rule. A directory is unwatched if
    # either says so.
    stores_pruned = wat.store_prune(root, ignore)

    def prune(directory):
        if stores_pruned(directory):
            return True
        dec = clf.explain(os.path.join(directory, "__homegraph_probe__"))
        return dec.label == EXCLUDED and dec.layer in structural

    try:
        source = wat.Inotify()
        source.add_tree(root, prune=prune)
    except wat.InotifyUnavailable as exc:
        print("watch  REFUSED  %s" % exc, file=sys.stderr)
        return 2

    def trigger():
        stamp = datetime.now().strftime("%H:%M:%S")
        print("[%s] change -> update" % stamp, flush=True)
        # `as_of` stays None across the whole run so each update stamps the day
        # it actually runs, not the day the watch was launched.
        rc = cmd_update(args)
        if rc != 0:
            print("[%s] update reported problems (exit %d) -- watch continues"
                  % (stamp, rc), file=sys.stderr, flush=True)

    # A change counts only if it is not a store write AND the corpus would
    # index it. The classifier that shapes `prune` for whole directories shapes
    # relevance for individual files too, so a churny but excluded file in a
    # watched dir (Claude Code rewriting .claude/usage-state.json every few
    # seconds) never triggers a full rebuild it would only be dropped from.
    def keep(path):
        return wat.relevant_to_corpus(
            path, ignore, lambda p: clf.explain(p).label == EXCLUDED)

    print("watching %s (inotify) -- Ctrl-C to stop" % root, flush=True)
    try:
        wat.watch_loop(source, trigger, keep=keep, debounce=args.debounce)
    except KeyboardInterrupt:
        print("\nstopped. nothing persists.", flush=True)
    finally:
        source.close()
    return 0


def _models_from(specs):
    out = {}
    for spec in specs:
        name, _, path = spec.partition("=")
        out[name] = path
    return out


def cmd_export(args):
    """Write a portable artifact.

    `--root` is optional by decision: it defaults to the configured root, the
    same one the models were built against. Convenient, and the reason it is
    printed rather than assumed -- exporting a graph rooted somewhere other
    than where you think it is rooted is a mistake nobody notices until an
    import lands in the wrong place.
    """
    from .export import ExportError, export

    root = getattr(args, "root", None)
    if not root:
        cfg = userconfig.load(getattr(args, "config", None))
        root = cfg.root
        print("root   %s  (from %s)" % (root, cfg.path))
    if args.redaction == "full":
        # stderr, and before the work rather than after it.
        print("WARNING  redaction=full carries the FULL TEXT of every file in "
              "these models. On a measured home corpus that is 5.9 million "
              "characters. Treat the artifact as private.", file=sys.stderr)
    try:
        report = export(_models_from(args.model), args.out, root,
                        redaction=args.redaction)
    except ExportError as exc:
        print("REFUSED  %s" % exc, file=sys.stderr)
        return 2
    for key, value in report.items():
        print("%-18s %s" % (key, value))
    if report["root_in_user_data"]:
        # Measured, not scrubbed. The structural fields are portable and CP-12
        # proves it; a title, an author or a line of prose may still name the
        # root, and rewriting the user's own text to hide it would be a lie
        # about what the file says.
        print("\nNOTE   the root's name still appears inside user data: %s. "
              "Paths and keys are portable; your own text was not rewritten."
              % report["root_in_user_data"])
    return 0


def cmd_inspect(args):
    """Print an artifact's manifest without importing it.

    An artifact arrives from somewhere else, and "run it to find out what it
    holds" is the wrong shape for that.
    """
    from .importer import ImportError_, read_manifest, verify
    try:
        manifest = read_manifest(args.artifact)
    except ImportError_ as exc:
        print("REFUSED  %s" % exc, file=sys.stderr)
        return 2
    for key in sorted(manifest):
        if key != "t":
            print("%-16s %s" % (key, manifest[key]))
    # `inspect` verifies. It printed whatever the manifest claimed, including
    # a `shape` label beside a `full` description, because nothing compared
    # the manifest to the artifact it heads -- a gate that cannot say no, on
    # the one command whose whole purpose is to let you look before you take
    # something in.
    problem = verify(args.artifact)
    print("%-16s %s" % ("integrity", problem or "digest and counts agree"))
    return 2 if problem else 0


def _cleanup(paths):
    import contextlib as _ctx
    for path in paths:
        with _ctx.suppress(OSError):
            os.remove(path)


def cmd_import(args):
    """Read an artifact into stores, rooted wherever the reader says."""
    import contextlib as _ctx

    from .importer import ImportError_, load
    from .store import Store

    models = _models_from(args.model)
    if not args.root or not args.root.strip():
        print("REFUSED  --root is empty; name the directory the imported "
              "paths should live under", file=sys.stderr)
        return 2
    root = os.path.abspath(os.path.expanduser(args.root))
    # The root is checked, not assumed. Every one of these was accepted
    # silently: a directory that does not exist, a FILE, a relative path
    # resolved against whatever the shell happened to be in, and the empty
    # string. An import into a root that is not there produces a graph where
    # every path names nothing, and the next `update` sees the whole corpus
    # as deleted.
    if not os.path.exists(root):
        print("REFUSED  no directory at %s. An import into a root that does "
              "not exist produces paths that name nothing." % root,
              file=sys.stderr)
        return 2
    if not os.path.isdir(root):
        print("REFUSED  %s is not a directory" % root, file=sys.stderr)
        return 2
    # Which files this command created, so a refusal can take them away
    # again. `Store(path)` creates the database, so without this a refused
    # import leaves an empty store behind -- something that looks built and
    # answers every query with nothing.
    fresh = [p for p in models.values() if not os.path.exists(p)]

    for name, path in sorted(models.items()):
        if path in fresh:
            continue
        with Store(path) as s:
            occupied = s.node_count()
        if occupied and not args.force:
            print("REFUSED  %s already holds %d node(s); pass --force to "
                  "replace it" % (path, occupied), file=sys.stderr)
            return 2
        if occupied:
            # `--force` says REPLACE, and it merged. An import is a series of
            # upserts, so a second artifact over the first produced the union
            # of two corpora -- a store larger and fresher than anything that
            # was ever built, which is the shape `update`'s equivalence claim
            # exists to prevent. The old store is moved aside rather than
            # unlinked, so a mistake is recoverable.
            backup = path + ".replaced"
            os.replace(path, backup)
            fresh.append(path)
            print("replaced   %s (previous store kept at %s)"
                  % (path, os.path.basename(backup)))

    try:
        with _ctx.ExitStack() as stack:
            stores = {name: stack.enter_context(_writing(path, model=name))
                      for name, path in sorted(models.items())}
            report = load(args.artifact, stores, root)
    except ImportError_ as exc:
        _cleanup(fresh)
        print("REFUSED  %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:                                    # noqa: BLE001
        # Anything else is still a failed import, and the store it created
        # must not stay: a bare KeyError used to leave a database behind that
        # looked built and answered every query with nothing.
        _cleanup(fresh)
        print("REFUSED  %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    for key, value in report.items():
        print("%-16s %s" % (key, value))
    print("\nThe mesh is not in the artifact -- it is derived. Run "
          "`homegraph mesh build --mesh-db ... --code-root %s` to rebuild it."
          % root)
    return 0


def _embed_store(db, embedder, force=False):
    """Write a vector for every node in `db` that has text. Returns the count.

    Through `_writing`, like every other writer, so CP-11's barrier check sees
    it: a command that opened `Store` directly would be a writer outside the
    lock. `model="unknown"` here means "do not relabel" -- the store already
    knows which model it is, and `migrate()` reads that back rather than
    stamping over it.

    `title` and `body` are joined because a section node carries its heading in
    both and a file node carries distinct text in each; embedding the join is
    what makes a heading query and a body query land near the same document. A
    node with no text at all is skipped rather than embedded as a zero vector --
    it could never rank, and storing it would only inflate the namespace count
    that decides whether the vector path runs.

    **A zero vector coming back for text that WAS there is skipped for exactly
    the same reason**, and that door was open until the CP-I1 audit found it.
    The rule used to apply only to empty text, so a provider answering all-zero
    -- a degenerate model, a matrix whose vocabulary misses the corpus, a proxy
    returning padding -- filled the namespace with vectors that cosine to 0.0
    against everything. `vector_search` then ran, the stable sort left the BM25
    shortlist in its original order, and `--mode vector` reported a "pure
    cosine ranking" that was lexical order with a 0.00000 beside every line.
    Nothing warned. That is the exact failure `_out_mode` exists to prevent,
    arriving from the provider side instead of the index side.

    **Incremental by default.** A node that already has a vector under this
    exact namespace is skipped, so filling the gap an `update` opened costs the
    new nodes and not the corpus: 1320 of 7376 on the author's store, four
    minutes against twenty. `force=True` re-embeds everything, which is what a
    changed *matrix* needs -- the namespace name would be identical while the
    numbers behind it moved, and nothing in the store could tell.

    Skipping is only safe because `update.forget` now deletes the vector of any
    node it rebuilds. Without that, a file whose text changed would keep its old
    vector, be counted as covered, and be skipped forever -- the incremental
    path would have turned a bug that one re-embed cured into a permanent one.
    The two changes are one change; neither is correct alone.

    Returns `(embedded, degenerate, skipped)`.
    """
    provider, model, dim = embedder.namespace
    embedded = degenerate = skipped = 0
    with _writing(db, model="unknown") as store:
        if force:
            rows = store.db.execute(
                "SELECT id, title, body FROM nodes").fetchall()
        else:
            rows = store.db.execute(
                "SELECT n.id, n.title, n.body FROM nodes n "
                "LEFT JOIN embeddings e ON e.node_id = n.id "
                "  AND e.provider = ? AND e.model = ? AND e.dim = ? "
                "WHERE e.node_id IS NULL", (provider, model, dim)).fetchall()
            skipped = store.embedding_count(provider, model, dim)
        for r in rows:
            text = " ".join(p for p in (r["title"], r["body"]) if p).strip()
            if not text:
                continue
            vec = embedder.embed(text)
            if not any(vec):
                degenerate += 1
                continue
            store.upsert_embedding(r["id"], provider, model, dim, vec)
            embedded += 1
    return embedded, degenerate, skipped


def cmd_embed(args):
    """Write vectors for a model's nodes, so `search --embeddings` can rerank.

    Config-driven, the same as `build` and `update`: the `[embeddings]` block
    names the provider, and the provider decides what else it needs -- a matrix
    data file for `static`, a reachable endpoint for `ollama`. Like them it
    refuses with exit 2 rather than doing nothing quietly, and each way of
    having no vectors says which it was:

      * no `[embeddings]` block          -- semantic search is off; add one
      * the block names a missing matrix -- distil it, or fix the path
      * the endpoint does not answer     -- start it, or fix the host
      * the server has no such model     -- pull an embedding model
      * a store path that does not exist -- build the model first

    A no-op that exits 0 here would leave `search --embeddings` degrading to
    lexical-only over an empty namespace, which is the silent failure the whole
    None-vs-empty contract exists to prevent.

    The provider is built ONCE, before any store is opened. For `ollama` that
    first call is also the reachability probe, so a dead endpoint costs one
    refusal rather than one per `--model` -- and no store is touched at all.
    """
    from . import providers
    from .providers import static_embed

    cfg = userconfig.load(getattr(args, "config", None))
    if cfg.embeddings is None:
        print("no [embeddings] block in %s -- semantic search is off. Uncomment "
              "the template there and point it at a matrix data file, or name "
              "an ollama endpoint, then re-run." % cfg.path, file=sys.stderr)
        return 2
    try:
        embedder = providers.from_config(cfg.embeddings)
    except static_embed.EmbedderDataMissing as exc:
        print("%s\n\nThe matrix is a build-time artefact: distil one offline "
              "and point [embeddings].path at it." % exc, file=sys.stderr)
        return 2
    except providers.PROVIDER_ERRORS as exc:
        print("%s" % exc, file=sys.stderr)
        return 2

    provider, model, dim = embedder.namespace
    failed = False
    for spec in args.model:
        name, _, db = spec.partition("=")
        if not db:
            print("%-6s REFUSED  no path; use --model %s=/path/to.db"
                  % (name, name), file=sys.stderr)
            failed = True
            continue
        if not os.path.exists(db):
            # os.path.exists BEFORE _writing, because Store() would CREATE the
            # file -- embedding a store that was never built would leave an
            # empty database that looks built. Refuse instead.
            print("%-6s REFUSED  no store at %s -- build the model first"
                  % (name, db), file=sys.stderr)
            failed = True
            continue
        try:
            n, degenerate, skipped = _embed_store(
                db, embedder, force=getattr(args, "force", False))
        except providers.PROVIDER_ERRORS as exc:
            # A provider that dies mid-store leaves NOTHING behind: `Store`
            # rolls back on any exception, so the store still holds whatever it
            # held before. Said out loud, because the alternative reading --
            # "some of my nodes are embedded now" -- would have the user run a
            # search over a half-filled namespace and trust the ranking.
            print("%-6s REFUSED  %s\n         Nothing was written to %s; it is "
                  "unchanged." % (name, exc, db), file=sys.stderr)
            failed = True
            continue
        if n == 0 and degenerate:
            # Every node with text embedded to the zero vector. That is not a
            # corpus with nothing to say; it is a provider that cannot answer,
            # and exiting 0 here would leave `search --mode vector` printing
            # 0.00000 beside a lexical ranking it calls semantic.
            print("%-6s REFUSED  every one of the %d node(s) with text embedded "
                  "to the zero vector.\n         The model answers, but says "
                  "nothing usable -- wrong model, or a matrix whose vocabulary "
                  "does not overlap this corpus. Nothing was written."
                  % (name, degenerate), file=sys.stderr)
            failed = True
            continue
        print("  %-6s embedded %d node(s) under namespace %s:%s:%d%s"
              % (name, n, provider, model, dim,
                 "" if not skipped else
                 " (%d already had one; --force re-embeds them)" % skipped))
        if degenerate:
            # Not fatal -- a handful of nodes whose whole text is out of the
            # embedder's vocabulary is a real and honest state -- but counted
            # out loud, because the alternative is a namespace quietly smaller
            # than the corpus and no way to notice.
            print("  %-6s %d node(s) embedded to the zero vector and were "
                  "skipped; they are not searchable by vector."
                  % (name, degenerate))
    return 2 if failed else 0


# Which builder owns each model. One table, beside the one `update` already
# keeps: `update.BUILDERS` says how to move a model forward, this says how to
# make one in the first place.
#
# It did not exist until now, and the gap was load-bearing: `homegraph build`
# printed "not implemented yet", `md build` covered M3 alone, and M1, M2 and
# M4 could be built only by importing their modules from a script. `update`
# refuses M4 with the advice "Rebuild M4" -- an instruction for an action the
# product did not have. The four stores in `~/.homegraph` were made by hand.
MODELS = {
    "m1": ("document", "m1_build"),
    "m2": ("image", "m2_build"),
    "m3": ("markdown", "m3_build"),
    "m4": ("misc", "m4_misc"),
}


def _build_model(model, db, root, as_of, config):
    """Build one model from scratch. The one path; `md build` calls it too.

    A second builder entry point would be two answers to "what does this
    model contain", and they would agree until one was edited.
    """
    import importlib
    from datetime import date

    from . import update as up
    from .corpus import Classifier

    label, module = MODELS[model]
    as_of = as_of or date.today().isoformat()
    clf = Classifier(home=root, config=config)
    paths, excluded = up.corpus_paths(root, label, config=config)
    print("%-6s %s" % (model, excluded.line()))

    mod = importlib.import_module(".models.%s" % module, __package__)
    kwargs = {}
    if model == "m3":
        kwargs["rules"] = mod.rules_from_config(clf.config)
    with _writing(db, model=model,
                  fingerprint=up.fingerprint(config)) as store:
        report = mod.build(store, paths, as_of, **kwargs)
        store.rebuild_fts()
        # Which layout this store was built under, so a later `update` can
        # tell a file change from a structural one.
        up.write_fingerprint(store, up.fingerprint(config))
    return report


def cmd_build(args):
    """Build one or more models from scratch."""
    cfg = userconfig.load(getattr(args, "config", None))
    root = os.path.abspath(os.path.expanduser(args.root or cfg.root))
    failed = False
    for spec in args.model:
        name, _, db = spec.partition("=")
        if name not in MODELS:
            print("%-6s REFUSED  unknown model; known: %s"
                  % (name, ", ".join(sorted(MODELS))), file=sys.stderr)
            failed = True
            continue
        if not db:
            print("%-6s REFUSED  no path; use --model %s=/path/to.db"
                  % (name, name), file=sys.stderr)
            failed = True
            continue
        report = _build_model(name, db, root, args.as_of, cfg)
        for key, value in report.summary().items():
            print("  %-20s %s" % (key, value))
        # The same warning `update` gives, for the same reason. A file that
        # was there when the tree was walked and gone when it was read leaves
        # a node with no text and no relations, and a build that reports it
        # only as a number in a summary and exits 0 is the partial result
        # nobody notices. Measured, not hypothetical: the first real run of
        # this command hit 56 of them -- transient cache files, all readable
        # again a minute later.
        stale = getattr(report, "unreadable", None)
        if stale:
            print("%-6s WARNING  %d file(s) could not be read during the "
                  "build; their nodes are missing or empty. Re-run."
                  % (name, len(stale)), file=sys.stderr)
            for path, why in stale[:5]:
                print("         %s  %s" % (path, why), file=sys.stderr)
            failed = True
    return 2 if failed else 0


def cmd_md_build(args):
    from datetime import date

    from . import update as up
    from .corpus import Classifier
    from .corpus import ExclusionReport
    from .models.m3_build import build, rules_from_config
    clf = Classifier()
    excluded = ExclusionReport(args.root or home_root())
    paths = []
    for p, is_link in _walk(args.root):
        d = excluded.record(p, clf.explain(p, is_symlink=is_link))
        if d.label == "markdown" and os.path.exists(p):
            paths.append(p)
    print("%-22s %s" % ("excluded", excluded.line()))
    with _writing(args.db, model="m3",
                  fingerprint=up.fingerprint(clf.config)) as s:
        report = build(s, paths, args.as_of or date.today().isoformat(),
                       rules=rules_from_config(clf.config))
        s.rebuild_fts()
        # Which layout this store was built under, so a later `update` can
        # tell a file change from a structural one. Written here rather than
        # only by `update`, or the first update after a build would have
        # nothing to compare against and would silently accept a config that
        # had changed in between.
        up.write_fingerprint(s, up.fingerprint(clf.config))
    for key, value in report.summary().items():
        print("%-22s %s" % (key, value))
    return 0


def cmd_md_backlinks(args):
    from .models.m3_build import backlinks
    from .store import Store
    with Store(args.db) as s:
        as_of = getattr(args, "as_of", None)
        found, note = backlinks(s, os.path.abspath(args.path), as_of=as_of)
        # The date is echoed because "3 inbound links" means something
        # different on two dates, and a reader scrolling back through a
        # terminal has no other way to tell which run was which.
        print("%d inbound wikilink(s)%s"
              % (len(found), " as of %s" % as_of if as_of else ""))
        for src in found:
            print("  %s" % src)
        if note:
            print("\nPARTIAL -- %s" % note)
    return 0


def cmd_md_broken(args):
    from .models.m3_build import broken_links
    from .store import Store
    with Store(args.db) as s:
        names = broken_links(s)
        # Printed with their sources: a bare list of 213 names reads as damage,
        # when 1 365 of the occurrences come from machine-generated reports and
        # only ~100 are a human marking something worth writing.
        print("%d unresolved wikilink target(s)" % len(names))
        for name in names[:args.limit]:
            srcs = backlink_sources(s, name)
            print("  %-44s <- %d file(s)%s"
                  % (name[:44], len(srcs),
                     "  e.g. %s" % os.path.basename(srcs[0]) if srcs else ""))
    return 0


def backlink_sources(store, name):
    from .models.m3_build import backlinks
    return backlinks(store, "wikilink:%s" % name)[0]


@contextlib.contextmanager
def _barrier(db_path, fingerprint=None):
    """Hold the one-writer lock on a store path, or exit 2 naming the holder.

    Separate from `_writing` because the mesh is written through `Mesh`, not
    through `Store`, and a barrier that only covered the `Store` path would
    leave `mesh build` exactly as unprotected as it was before.
    """
    from .lock import Locked, StoreLock

    def note(msg):
        print("NOTE  %s" % msg, file=sys.stderr)

    barrier = StoreLock(db_path, fingerprint=fingerprint, on_stale=note)
    try:
        barrier.acquire()
    except Locked as exc:
        # Exit 2, the refusal code the rest of the CLI uses. Naming the pid is
        # the point: "database is locked" sends the reader to the disk, and
        # the answer is almost always their own other run.
        print("REFUSED  %s\n(waiting is not offered: re-run when that "
              "process is done)" % exc, file=sys.stderr)
        raise SystemExit(2)
    try:
        yield barrier
    finally:
        barrier.release()


@contextlib.contextmanager
def _writing(db_path, model="unknown", fingerprint=None):
    """Open a store for writing behind both barriers.

    In this order: the lock file refuses a second homegraph writer by pid, and
    BEGIN IMMEDIATE refuses anything else holding the database -- before the
    work starts rather than after it.

    Every command that writes a store goes through here or through
    `_barrier`. A writer that opens `Store` directly is a writer outside the
    barrier, which is the whole failure this replaces; CP-11 parses for that
    with `ast` rather than trusting this sentence.
    """
    from .store import Store
    with _barrier(db_path, fingerprint=fingerprint):
        with Store(db_path, model=model) as store:
            store.begin_immediate()
            yield store


def _mesh(args):
    from .mesh import Mesh
    models = {}
    for spec in args.model:
        name, _, path = spec.partition("=")
        models[name] = path
    return Mesh(models, mesh_db=getattr(args, "mesh_db", None))


def cmd_mesh_search(args):
    with _mesh(args) as mesh:
        res = mesh.search(" ".join(args.query), limit=args.limit,
                          as_of=args.as_of, include_all=args.all)
        for w in res.warnings:
            print("WARNING: %s" % w)
        # The status is printed on every search, complete or not. A partial
        # answer that looks like a complete one is the failure this whole
        # model is built to avoid.
        print("\n%s -- %d hit(s) from %s"
              % (res.status.upper(), len(res),
                 ", ".join(res.models_queried) or "nothing"))
        for hit in res.hits:
            print("  %2d  %-6s %-44s %s"
                  % (hit["rank"], hit["model"], (hit.get("title") or "")[:44],
                     ",".join(hit["sources"])))
            # A code stub's title is its basename, and a basename is ambiguous
            # by construction -- two `handler.js` is the case CITES_CODE's
            # uniqueness rule exists for. Printing the path is the whole point
            # of making these searchable: the answer is WHICH file.
            if hit["model"] == "code" and hit.get("path"):
                print("      %s" % hit["path"])
    return 0 if not res.partial else 3


def cmd_mesh_explain(args):
    with _mesh(args) as mesh:
        for key, value in mesh.explain(" ".join(args.query),
                                       limit=args.limit).items():
            print("%-18s %s" % (key, value))
    return 0


def cmd_mesh_neighbors(args):
    from .store import provenance_note
    with _mesh(args) as mesh:
        edges = mesh.neighbours(args.node, depth=args.depth)
        for e in edges:
            # The marker is on the row that carries the guess, not only in a
            # footer: a reader who scans the list and stops has still seen it.
            mark = "" if e.confidence >= 1.0 else "  [%s]" % e.method
            print("  %-52s --%s--> %s%s"
                  % (e.src[-52:], e.rel, e.dst[-52:], mark))
        note = provenance_note(edges)
        if note:
            print("\nPARTIAL -- %s" % note)
    return 0


def cmd_mesh_path(args):
    with _mesh(args) as mesh:
        trail = mesh.path(args.src, args.dst, max_depth=args.max_depth)
        if trail is None:
            print("no path within %d hop(s)" % args.max_depth)
            return 1
        for i, node in enumerate(trail):
            print("  %d. %s" % (i + 1, node))
    return 0


def _code_inventory(root, config):
    """The paths CITES_CODE may point at, or None if none was asked for.

    The same walk the models are built from, asked for the one category no
    model owns. Not a second rule about what code is: `corpus_paths` is what
    `build` calls, so the inventory and the corpus cannot drift apart.
    """
    if not root:
        return None
    from . import update as up
    root = os.path.abspath(os.path.expanduser(root))
    paths, excluded = up.corpus_paths(root, "code", config=config)
    print("code   %s" % excluded.line())
    return paths


def cmd_mesh_build(args):
    from datetime import date
    if not args.mesh_db:
        print("REFUSED  mesh build writes a store; name it with --mesh-db",
              file=sys.stderr)
        return 2
    from .mesh import ModelUnavailable
    code_root = getattr(args, "code_root", None)
    cfg = userconfig.load(getattr(args, "config", None)) if code_root else None
    code_paths = _code_inventory(code_root, cfg)
    with _barrier(args.mesh_db):
        with _mesh(args) as mesh:
            try:
                report = mesh.build_edges(
                    args.as_of or date.today().isoformat(),
                    code_paths=code_paths)
            except ModelUnavailable as exc:
                # Exit 2, like every other refusal in this CLI. It came out as
                # an uncaught traceback and exit 1 for a day, which reads as a
                # crash rather than as a decision -- and this is the refusal a
                # user meets most often, since the inventory has to be named
                # again on every run.
                print("REFUSED  %s" % exc, file=sys.stderr)
                return 2
            for key, value in report.items():
                print("%-12s %s" % (key, value))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="homegraph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="scan a directory and write the config")
    p.add_argument("--root", default=None,
                   help="any directory; defaults to your home directory")
    p.add_argument("--yes", action="store_true",
                   help="accept the proposal without asking; does NOT "
                        "replace an existing config")
    p.add_argument("--force", action="store_true",
                   help="replace an existing config (the only flag that does)")
    p.add_argument("--config", default=None, help="write somewhere else")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("config", help="print the config this run would use")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("explain", help="why a path got its label")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("census", help="counts per category and subtype")
    p.add_argument("--root", default=home_root())
    p.add_argument("--top", type=int, default=TOP_DIRS,
                   help="how many excluded directories to name "
                        "(default %d; the output says when it cut the list)"
                        % TOP_DIRS)
    p.add_argument("--all", action="store_true",
                   help="name every excluded directory, no cap")
    p.set_defaults(func=cmd_census)

    p = sub.add_parser("query", help="one closed query over the graph "
                                     "(grammar: DECISIONS.md section 25)")
    p.add_argument("db")
    p.add_argument("query", nargs="+")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("status", help="store contents and index health")
    p.add_argument("db")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("search", help="hybrid search, reports _out_mode")
    p.add_argument("db")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--embeddings", default=None, metavar="LOCATOR",
                   help="turns on semantic reranking. Either a matrix data "
                        "file, or ollama://<model>@<host>[:<port>] for a "
                        "running Ollama (port defaults to 11434). The store "
                        "must have been embedded by the same one "
                        "(homegraph embed); a different one finds zero and "
                        "says so.")
    p.add_argument("--mode", choices=("auto", "vector", "fts"), default="auto",
                   help="auto (default): lexical, fused with vectors by RRF. "
                        "vector: pure cosine ranking, no lexical fusion (needs "
                        "--embeddings). fts: lexical only, ignores embeddings.")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("md", help="M3 markdown model")
    msub = p.add_subparsers(dest="mdcmd", required=True)
    q = msub.add_parser("build")
    q.add_argument("db")
    q.add_argument("--root", default=home_root())
    q.add_argument("--as-of", dest="as_of", default=None)
    q.set_defaults(func=cmd_md_build)
    q = msub.add_parser("backlinks")
    q.add_argument("db")
    q.add_argument("path")
    # The one command that reaches Store.edges_as_of. Without it the versioned
    # edge schema -- first_seen, last_seen, and the predicate that reads them --
    # was a capability with no way in.
    q.add_argument("--as-of", dest="as_of", default=None,
                   metavar="YYYY-MM-DD",
                   help="which files linked here on that date")
    q.set_defaults(func=cmd_md_backlinks)
    q = msub.add_parser("broken")
    q.add_argument("db")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(func=cmd_md_broken)

    p = sub.add_parser("mesh", help="M5 federation across models")
    xsub = p.add_subparsers(dest="meshcmd", required=True)
    for name, fn in (("search", cmd_mesh_search), ("explain", cmd_mesh_explain),
                     ("neighbors", cmd_mesh_neighbors), ("path", cmd_mesh_path),
                     ("build", cmd_mesh_build)):
        q = xsub.add_parser(name)
        q.add_argument("--model", action="append", required=True,
                       metavar="NAME=PATH",
                       help="repeatable, e.g. --model m3=/path/m3.db")
        q.add_argument("--mesh-db", dest="mesh_db", default=None)
        if name in ("search", "explain"):
            q.add_argument("query", nargs="+")
            q.add_argument("--limit", type=int, default=20)
        if name == "search":
            q.add_argument("--as-of", dest="as_of", default=None)
            q.add_argument("--all", action="store_true")
        if name == "build":
            q.add_argument("--as-of", dest="as_of", default=None)
            q.add_argument("--code-root", dest="code_root", default=None,
                           metavar="DIR",
                           help="walk DIR for the code inventory CITES_CODE "
                                "points at; without it that relation is not "
                                "computed, and the report says so")
            q.add_argument("--config", default=None)
        if name == "neighbors":
            q.add_argument("node")
            q.add_argument("--depth", type=int, default=1)
        if name == "path":
            q.add_argument("src")
            q.add_argument("dst")
            q.add_argument("--max-depth", dest="max_depth", type=int, default=4)
        q.set_defaults(func=fn)

    p = sub.add_parser("visualize", help="write a self-contained HTML graph")
    p.add_argument("--model", action="append", required=True,
                   metavar="NAME=PATH")
    p.add_argument("--mesh-db", dest="mesh_db", default=None,
                   help="also draw the code inventory and the cross-model "
                        "edges; without it the page shows four separate "
                        "graphs and its search cannot find a source file")
    p.add_argument("--out", default="graph.html")
    p.add_argument("--limit", type=int, default=2000,
                   help="max nodes per model")
    p.add_argument("--min-degree", dest="min_degree", type=int, default=0,
                   help="drop nodes with fewer than N edges")
    p.add_argument("--title", default="homegraph")
    p.add_argument("--embeddings", default=None, metavar="MATRIX",
                   help="a matrix data file; adds an abc/≈ toggle for semantic "
                        "title search and click-a-node-for-similar. Only the "
                        "on-screen titles' words are inlined (well under 1 MB).")
    p.set_defaults(func=cmd_visualize)

    p = sub.add_parser("mcp", help="run the MCP server on stdio")
    p.add_argument("--model", action="append", required=True,
                   metavar="NAME=PATH")
    p.add_argument("--mesh-db", dest="mesh_db", default=None)
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("update",
                       help="apply a filesystem diff to built models")
    p.add_argument("--model", action="append", required=True,
                   metavar="NAME=PATH",
                   help="repeatable, e.g. --model m3=/path/m3.db")
    p.add_argument("--root", default=None,
                   help="defaults to the root in your config")
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--mesh-db", dest="mesh_db", default=None,
                   help="also refresh the federation, pruning stale stubs")
    p.add_argument("--code-root", dest="code_root", default=None,
                   metavar="DIR",
                   help="re-walk DIR for the code inventory; required to "
                        "prune a federation that already has code stubs")
    p.add_argument("--allow-config-change", dest="allow_config_change",
                   action="store_true",
                   help="update anyway after the layout changed; the store "
                        "will then mix two configurations")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("watch",
                       help="foreground: re-run update when the corpus changes")
    p.add_argument("--model", action="append", required=True,
                   metavar="NAME=PATH",
                   help="repeatable, e.g. --model m3=/path/m3.db")
    p.add_argument("--root", default=None,
                   help="directory to watch; defaults to the configured root")
    p.add_argument("--as-of", dest="as_of", default=None,
                   help="pin the date; by default each update stamps its own")
    p.add_argument("--mesh-db", dest="mesh_db", default=None,
                   help="also refresh the federation on each change")
    p.add_argument("--code-root", dest="code_root", default=None,
                   metavar="DIR",
                   help="re-walk DIR for the code inventory on each change")
    p.add_argument("--allow-config-change", dest="allow_config_change",
                   action="store_true",
                   help="update anyway after the layout changed")
    p.add_argument("--debounce", type=float, default=1.5, metavar="SECONDS",
                   help="quiet period after the last change before updating "
                        "(default 1.5)")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("build", help="build one or more models from scratch")
    p.add_argument("--model", action="append", required=True, metavar="M=PATH",
                   help="m1|m2|m3|m4 and where its store goes; repeatable")
    p.add_argument("--root", default=None,
                   help="corpus root; defaults to the configured one")
    p.add_argument("--as-of", dest="as_of", default=None)
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("export", help="write a portable artifact")
    p.add_argument("--model", action="append", required=True, metavar="M=PATH")
    p.add_argument("--out", required=True)
    p.add_argument("--root", default=None,
                   help="the root these stores were built against; defaults "
                        "to the configured one")
    p.add_argument("--redaction", default="structure",
                   choices=("full", "structure", "shape"),
                   help="structure (default) carries paths, titles and every "
                        "edge but no file text")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("inspect", help="print an artifact's manifest")
    p.add_argument("artifact")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("import", help="read an artifact into stores")
    p.add_argument("artifact")
    p.add_argument("--model", action="append", required=True, metavar="M=PATH")
    p.add_argument("--root", required=True,
                   help="where the imported paths should live on THIS machine")
    p.add_argument("--force", action="store_true",
                   help="replace a store that already holds nodes")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("embed",
                       help="write vectors for a model's nodes (needs an "
                            "[embeddings] config block)")
    p.add_argument("--model", action="append", required=True, metavar="M=PATH",
                   help="which store(s) to embed; repeatable")
    p.add_argument("--force", action="store_true",
                   help="re-embed every node, not just the ones with no "
                        "vector in this namespace. Needed when the MATRIX "
                        "changed but its name did not -- the namespace "
                        "would look identical while the numbers moved.")
    p.add_argument("--config", default=None)
    p.set_defaults(func=cmd_embed)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except userconfig.ConfigMissing as exc:
        # One place, so no command can forget. Exit 2 and an instruction, not a
        # guessed layout that would build an empty model and call it a success.
        print("%s\n\nhomegraph does not assume where anything lives. Run:\n"
              "    homegraph init --root <any directory>\n"
              "and edit the file it writes if the proposal is wrong."
              % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
