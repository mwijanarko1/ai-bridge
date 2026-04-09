from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCH_BIN = REPO_ROOT / "bin" / "ai-dispatch"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class DispatchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state_root = self.root / "state"
        self.config_dir = self.root / "config"
        self.bin_dir = self.root / "bin"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.repo_dir = self.root / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        self.base_env = os.environ.copy()
        self.base_env["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)
        self.base_env["AI_BRIDGE_CONFIG_DIR"] = str(self.config_dir)
        self.base_env["PATH"] = f"{self.bin_dir}:{self.base_env.get('PATH', '')}"
        self.base_env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{self.base_env.get('PYTHONPATH', '')}"

        self._install_fake_worker_binaries()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _install_fake_worker_binaries(self) -> None:
        script = """#!/usr/bin/env python3
import json
import os
import sys
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}))
"""
        for name in ["codex", "claude-code-worker", "agent-hard", "opencode-easy", "goose"]:
            write_executable(self.bin_dir / name, script)

    def _run(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DISPATCH_BIN), *args],
            cwd=str(cwd or REPO_ROOT),
            env=env or self.base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _init_git_repo(self) -> None:
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo_dir / "README.md").write_text("hello\n", encoding="utf-8")
        env = self.base_env.copy()
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_auto_run_uses_primary_four_pool(self) -> None:
        result = self._run(
            "--target",
            "auto",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename the settings toggle label",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["winner"], "opencode")
        self.assertTrue(set(payload["route"]).issubset({"codex", "claude", "cursor", "opencode"}))

    def test_auto_route_can_use_allowlisted_optional_agent(self) -> None:
        (self.config_dir / "routing.json").write_text(
            json.dumps(
                {
                    "auto_routing": {
                        "enabled_agents": ["codex", "claude", "cursor", "opencode"],
                        "optional_allowlist": ["goose"],
                    },
                    "agents": {
                        "goose": {
                            "scores": {
                                "simple_edit": 20,
                                "implementation": 6,
                                "debugging": 6,
                                "refactor": 5,
                                "research": 5,
                                "review": 4,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        result = self._run(
            "--target",
            "auto",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename the settings toggle label",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["winner"], "goose")

    def test_explicit_optional_target_works_without_allowlist(self) -> None:
        result = self._run(
            "--target",
            "goose",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Investigate the flaky test",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["winner"], "goose")

    def test_list_show_and_retry_commands(self) -> None:
        first = self._run(
            "--target",
            "opencode",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)

        listed = self._run("list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        listed_payload = json.loads(listed.stdout)
        self.assertEqual(len(listed_payload["jobs"]), 1)

        shown = self._run("show", payload["job_id"], "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        shown_payload = json.loads(shown.stdout)
        self.assertEqual(shown_payload["job_id"], payload["job_id"])

        retried = self._run("retry", payload["job_id"], "--feedback", "Tighten the prompt", "--json")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        retried_payload = json.loads(retried.stdout)
        self.assertEqual(retried_payload["parent_job_id"], payload["job_id"])
        self.assertIn("Follow-up feedback", retried_payload["task"])

    def test_verify_and_route_commands(self) -> None:
        (self.config_dir / "verify.json").write_text(
            json.dumps({"profiles": {"default": {"command": [sys.executable, "-c", "print('verified')"]}}}),
            encoding="utf-8",
        )
        routed = self._run("route", "Debug the race condition in sync engine", "--json")
        self.assertEqual(routed.returncode, 0, routed.stderr)
        route_payload = json.loads(routed.stdout)
        self.assertEqual(route_payload["route"][0], "cursor")

        verified = self._run(
            "--target",
            "opencode",
            "--verify",
            "default",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        verified_payload = json.loads(verified.stdout)
        self.assertEqual(verified_payload["verification"]["status"], "passed")

    def test_worktree_and_cleanup_worktree(self) -> None:
        self._init_git_repo()
        result = self._run(
            "--target",
            "opencode",
            "--worktree",
            "auto",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        worktree_path = payload["worktree"]["path"]
        self.assertTrue(worktree_path)
        self.assertTrue(Path(worktree_path).exists())

        cleanup = self._run("cleanup-worktree", payload["job_id"], "--json")
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        cleanup_payload = json.loads(cleanup.stdout)
        self.assertEqual(cleanup_payload["worktree"]["cleanup_status"], "removed")
