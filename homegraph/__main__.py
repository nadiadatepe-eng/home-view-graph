#!/usr/bin/env python3
"""Entry point for `python3 -m homegraph`.

Three ways in, and they should all reach the same place:

    homegraph …                 the installed console script
    python3 -m homegraph …      this file, no install needed
    python3 -m homegraph.cli …  the module directly

Without this file the second form fails with "No module named homegraph.__main__",
which reads as "the package is broken" rather than "spell it differently".
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
