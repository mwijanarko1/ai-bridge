#!/usr/bin/env python3
"""Compatibility shim for ``python src/ai-peers/server.py``; implementation is ``ai_peers.server``."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ai_peers.server import main

if __name__ == "__main__":
    main()
