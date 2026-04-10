#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"


class PeersCliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["AI_PEERS_DB"] = os.path.join(self.tempdir.name, "peers-cli.db")
        self.env["AI_PEERS_SKIP_PID_CHECK"] = "1"
        self.env["AI_PEERS_SESSION_KEY"] = "cli-contract-session"
        self.env["AI_PEERS_CLIENT"] = "codex"
        self.env["AI_PEERS_ROLE"] = "orchestrator-reviewer"
        self.env["PYTHONPATH"] = str(SRC_ROOT)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "ai_peers.cli", *args],
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_whoami_emits_peer_metadata(self) -> None:
        result = self._run("whoami")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        for key in ("peer_id", "client", "role", "summary"):
            self.assertIn(key, payload)
        self.assertEqual(payload["peer_id"], "cli-contract-session")

    def test_peers_subcommand_matches_mcp_list_peers_envelope(self) -> None:
        result = self._run("peers", "--scope", "machine")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("peers", payload)
        self.assertIsInstance(payload["peers"], list)

    def test_route_subcommand_matches_recommend_peer_shape(self) -> None:
        result = self._run("route", "--task-kind", "implement", "--difficulty", "easy")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        for key in ("task_kind", "difficulty", "role_order", "recommended", "fallbacks"):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
