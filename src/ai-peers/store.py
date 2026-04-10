#!/usr/bin/env python3
"""Compatibility shim for older imports; implementation is ``ai_peers.store``."""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ai_peers.store import *  # noqa: F403
