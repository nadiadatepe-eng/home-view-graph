#!/usr/bin/env python3
"""Two bugs a type checker found, held down by a test that can go red.

Pinning `[tool.pyright]` in 2026-07 surfaced 14 findings on a tree where
`uvx mypy homegraph/` reported `Success: no issues found in 34 source files`.
Twelve were annotations. Two were live bugs, and both had the same shape: a
guard that could not fire on an input nobody had passed it yet.

  * `no_open_guard(b"/x")` -- the watch list kept whatever `os.fspath` returned,
    so a bytes root produced `str.startswith(bytes)` and raised
    `TypeError: startswith first arg must be str or a tuple of str, not bytes`.
    The guard never got to say M2 had opened an image. Measured both sides
    against the pre-change tree before this file existed.

  * `tools/call` with an unhashable `name` (`[]`, `{}`) -- `dict.get(name)`
    raised `TypeError: unhashable type` out of the dispatch table, past the
    JSON-RPC error path, so a malformed request killed the turn instead of
    being answered. `None` and `1` were hashable and already answered correctly.

Neither was reachable from the CLI, which is exactly why both survived: the
only callers passed str. That is the same failure this package keeps meeting --
the mechanism that decides what gets checked is itself unchecked -- so the fix
stops being a commit message and becomes a check.

**Every case below has a negative control.** A guard that fires on everything is
not a guard, so each block also asserts the case where it must stay silent: a
path outside the watched roots, and a tool name that really does exist.

Run:
    python3 tests/test_type_regressions.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from report import reporter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from homegraph.mcp_server import Server                      # noqa: E402

results, check = reporter(58)


# Each guard case runs in its own interpreter. `no_open_guard` says so itself:
# "Audit hooks cannot be removed ... the hook stays installed for the life of
# the process and keeps raising ImageOpened for ANY open under `watched`". Three
# cases in one process therefore run under one, two, then three live hooks, and
# a later check can pass or fail because of an earlier one's tripwire rather
# than its own. Measured: with the fix reverted, the str-root case failed with
# the BYTES case's TypeError. A subprocess is the only isolation available.
_PROBE = r'''
import os, sys
sys.path.insert(0, %r)
from homegraph.models.m2_build import ImageOpened, no_open_guard
root, target = sys.argv[1], sys.argv[2]
if sys.argv[3] == "bytes":
    root = os.fsencode(root)
try:
    with no_open_guard(root):
        with open(target, "rb"):
            pass
except ImageOpened:
    print("ImageOpened")
except TypeError as exc:
    print("TypeError: %%s" %% exc)
else:
    print("silent")
'''


def _fired(root: str, target: str, kind: str) -> str:
    """Open `target` under a guard watching `root`, in a fresh interpreter."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE % os.path.dirname(HERE), root, target, kind],
        capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip().splitlines()
    if not out:
        return "no output (rc=%d) %s" % (proc.returncode, proc.stderr.strip()[-120:])
    return out[-1]


def t_guard_bytes_root() -> None:
    d = tempfile.mkdtemp()
    inside = os.path.join(d, "x.png")
    with open(inside, "wb") as fh:
        fh.write(b"x")

    # The regression. Before the fix this was the TypeError, not the guard.
    got = _fired(d, inside, "bytes")
    check("bytes root: the guard fires", got == "ImageOpened", got)

    # Control: str roots always worked, and must keep working.
    got = _fired(d, inside, "str")
    check("str root: the guard fires", got == "ImageOpened", got)

    # Negative control. Without this the two above pass for a guard that
    # raises on every open, which would be a worse bug than the one fixed.
    outside = os.path.join(tempfile.mkdtemp(), "y.png")
    with open(outside, "wb") as fh:
        fh.write(b"y")
    got = _fired(d, outside, "bytes")
    check("bytes root: a file outside the roots stays silent",
          got == "silent", got)


def _call(name) -> dict:
    """One tools/call request through `handle`, without touching a store."""
    server = Server.__new__(Server)          # no model paths, no db opened
    return server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": name, "arguments": {}}})


def t_unhashable_tool_name() -> None:
    for name in ([], {}):
        try:
            reply = _call(name)
            code = reply.get("error", {}).get("code")
            check("tools/call name=%r answers -32601" % (name,),
                  code == -32601, "code=%r" % (code,))
        except TypeError as exc:
            check("tools/call name=%r answers -32601" % (name,), False,
                  "raised TypeError: %s" % exc)

    # Hashable non-strings behaved correctly before the fix. Listed so a future
    # change that narrows the guard to `isinstance(name, str)` only for lists
    # still has to keep these answering rather than raising.
    for name in (None, 1):
        reply = _call(name)
        code = reply.get("error", {}).get("code")
        check("tools/call name=%r answers -32601" % (name,),
              code == -32601, "code=%r" % (code,))

    # Negative control: a real tool name must NOT take the unknown-tool path.
    # `Server.__new__` leaves the handlers unbound, so reaching one raises
    # AttributeError -- which is the point. Anything except -32601 proves the
    # name was dispatched rather than rejected.
    try:
        reply = _call("mesh_search")
        code = reply.get("error", {}).get("code")
        dispatched = code != -32601
        detail = "code=%r" % (code,)
    except Exception as exc:                 # noqa: BLE001 -- any raise means dispatch
        dispatched, detail = True, type(exc).__name__
    check("a known tool name is dispatched, not rejected", dispatched, detail)


def main() -> int:
    t_guard_bytes_root()
    t_unhashable_tool_name()

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


def test_type_regressions():
    assert main() == 0, "see the printed report above for which check failed"


if __name__ == "__main__":
    sys.exit(main())
