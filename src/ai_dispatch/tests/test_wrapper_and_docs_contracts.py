from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class WrapperContractTests(unittest.TestCase):
    def test_delegate_wrapper_respects_override_env(self) -> None:
        script = (REPO_ROOT / "bin" / "ai-delegate").read_text(encoding="utf-8")
        self.assertIn("AI_BRIDGE_ROOT", script)
        self.assertIn("AI_BRIDGE_DISPATCH_BIN", script)
        self.assertIn('exec "$DISPATCH" "$@"', script)

    def test_peers_wrapper_respects_cli_and_python_overrides(self) -> None:
        script = (REPO_ROOT / "bin" / "ai-peers").read_text(encoding="utf-8")
        self.assertIn("AI_BRIDGE_ROOT", script)
        self.assertIn("AI_BRIDGE_PEERS_CLI", script)
        self.assertIn("AI_BRIDGE_PEERS_PYTHON", script)
        self.assertIn('exec "$PYTHON_BIN" "$CLI" "$@"', script)

    def test_worker_launchers_set_peer_context(self) -> None:
        launcher_expectations = {
            "codex-orchestrator": ("AI_PEERS_CLIENT=\"codex\"", "AI_PEERS_ROLE=\"orchestrator-reviewer\"", "exec codex"),
            "agent-hard": ("AI_PEERS_CLIENT=\"cursor-agent\"", "AI_PEERS_ROLE=\"hard-programmer\"", "exec agent"),
            "opencode-easy": ("AI_PEERS_CLIENT=\"opencode\"", "AI_PEERS_ROLE=\"easy-programmer\"", "exec opencode"),
            "claude-code-worker": ("AI_PEERS_CLIENT=\"claude-code\"", "AI_PEERS_ROLE=\"hard-programmer\"", "exec claude"),
        }
        for filename, expected_snippets in launcher_expectations.items():
            script = (REPO_ROOT / "bin" / filename).read_text(encoding="utf-8")
            self.assertIn("AI_PEERS_SESSION_KEY", script)
            self.assertIn("AI_PEERS_LAUNCH_CWD", script)
            for snippet in expected_snippets:
                self.assertIn(snippet, script)


class DocsContractTests(unittest.TestCase):
    def test_readme_declares_non_features(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("does not include a TUI", readme)
        self.assertIn("cost tracking", readme)
        self.assertIn("sandboxing", readme)
        self.assertIn("auto-merge", readme)


if __name__ == "__main__":
    unittest.main()
