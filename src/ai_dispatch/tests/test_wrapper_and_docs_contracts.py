from __future__ import annotations

import os
import subprocess
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class WrapperContractTests(unittest.TestCase):
    def test_delegate_wrapper_invokes_python_module(self) -> None:
        script = (REPO_ROOT / "bin" / "ai-delegate").read_text(encoding="utf-8")
        self.assertIn("AI_BRIDGE_ROOT", script)
        self.assertIn("export AI_BRIDGE_ROOT", script)
        self.assertIn("AI_DISPATCH_PROG", script)
        self.assertIn("PYTHONPATH", script)
        self.assertIn("delegate_main", script)
        self.assertIn("ai_dispatch.entrypoints", script)

    def test_delegate_entrypoint_resolves_dispatch_like_bash_contract(self) -> None:
        src = (REPO_ROOT / "src" / "ai_dispatch" / "entrypoints.py").read_text(encoding="utf-8")
        self.assertIn("AI_BRIDGE_DISPATCH_BIN", src)
        self.assertIn("AI_BRIDGE_ROOT", src)
        self.assertIn("AI_DISPATCH_PROG", src)
        self.assertIn("ai-dispatch", src)

    def test_delegate_help_uses_delegate_program_name(self) -> None:
        env = os.environ.copy()
        env.pop("AI_BRIDGE_DISPATCH_BIN", None)
        env["AI_BRIDGE_ROOT"] = str(REPO_ROOT)
        env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{env.get('PYTHONPATH', '')}"

        result = subprocess.run(
            [str(REPO_ROOT / "bin" / "ai-delegate"), "--help"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: ai-delegate", result.stdout)
        self.assertIn("ai-delegate run --target opencode", result.stdout)

    def test_peers_wrapper_respects_cli_and_python_overrides(self) -> None:
        script = (REPO_ROOT / "bin" / "ai-peers").read_text(encoding="utf-8")
        self.assertIn("AI_BRIDGE_ROOT", script)
        self.assertIn("AI_BRIDGE_PEERS_CLI", script)
        self.assertIn("AI_BRIDGE_PEERS_PYTHON", script)
        self.assertIn("ai_peers.cli", script)
        self.assertIn("PYTHONPATH", script)

    def test_pyproject_declares_console_scripts(self) -> None:
        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = pyproject["project"]["scripts"]
        for command in (
            "ai-dispatch",
            "ai-delegate",
            "ai-peers",
            "ai-peers-mcp",
            "codex-orchestrator",
            "agent-hard",
            "opencode-easy",
            "claude-code-worker",
        ):
            self.assertIn(command, scripts)

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
