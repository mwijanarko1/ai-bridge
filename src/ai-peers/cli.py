#!/usr/bin/env python3
"""Compatibility shim: adds ``src`` to path and runs the real CLI in ``ai_peers``."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ai_peers.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
