from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPATCH_BIN = REPO_ROOT / "bin" / "ai-dispatch"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class DispatchCliMoreTests(unittest.TestCase):
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

        self._install_fake_workers()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _install_fake_workers(self) -> None:
        ok_script = """#!/usr/bin/env python3
import json
import os
import sys
print(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}))
"""
        write_executable(self.bin_dir / "codex", ok_script)
        write_executable(self.bin_dir / "claude-code-worker", ok_script)
        write_executable(self.bin_dir / "agent-hard", ok_script)
        write_executable(self.bin_dir / "opencode-easy", ok_script)
        write_executable(self.bin_dir / "goose", ok_script)

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

    def test_soft_failure_falls_through_to_next_worker(self) -> None:
        soft = """#!/usr/bin/env python3
print("Could not complete it safely.", flush=True)
"""
        write_executable(self.bin_dir / "agent-hard", soft)
        result = self._run(
            "--target",
            "auto",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Debug the race condition in the sync engine",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreaterEqual(len(payload["attempts"]), 2)
        self.assertEqual(payload["attempts"][0]["worker"], "cursor")
        self.assertEqual(payload["attempts"][1]["worker"], "claude")
        self.assertEqual(payload["winner"], "claude")

    def test_top_level_help_gives_command_instructions(self) -> None:
        result = self._run("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Common commands:", result.stdout)
        self.assertIn("ai-dispatch run --target opencode", result.stdout)
        self.assertIn("Use '<command> --help'", result.stdout)
        self.assertIn("run                delegate one task", result.stdout)
        self.assertIn("orchestrate        run bounded multi-turn", result.stdout)
        self.assertNotIn("==SUPPRESS==", result.stdout)
        self.assertNotIn("__monitor__", result.stdout)

    def test_run_help_gives_prompt_and_routing_instructions(self) -> None:
        result = self._run("run", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Examples:", result.stdout)
        self.assertIn("Use --target auto", result.stdout)
        self.assertIn("Use --verify default|quick|full", result.stdout)
        self.assertIn("Put -- before the task prompt", result.stdout)

    def test_orchestrate_help_gives_stop_conditions(self) -> None:
        result = self._run("orchestrate", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Examples:", result.stdout)
        self.assertIn("AI_BRIDGE_STATUS: done, continue, or blocked", result.stdout)
        self.assertIn("--background is not supported for orchestrate", result.stdout)

    def test_verification_failure_marks_job_failed(self) -> None:
        (self.config_dir / "verify.json").write_text(
            json.dumps(
                {
                    "profiles": {
                        "default": {
                            "command": [sys.executable, "-c", "import sys; sys.exit(1)"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self._run(
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
            "Rename the settings label",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["verification"]["status"], "failed")
        self.assertFalse(payload["success"])

    def test_invalid_routing_config_exits_nonzero(self) -> None:
        (self.config_dir / "routing.json").write_text("{ not-json", encoding="utf-8")
        result = self._run(
            "--target",
            "auto",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename the toggle",
        )
        self.assertNotEqual(result.returncode, 0)
        combined = f"{result.stdout}\n{result.stderr}".lower()
        self.assertTrue("routing" in combined and "config" in combined)

    def test_adapter_missing_binary_surfaces_as_attempt_error(self) -> None:
        (self.config_dir / "adapters.json").write_text(
            json.dumps({"gemini": {"command": ["missing-binary-xyz-adapter-test", "{prompt}"]}}),
            encoding="utf-8",
        )
        result = self._run(
            "--target",
            "gemini",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Summarize the module",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["success"])
        self.assertTrue(any("not available" in (a.get("stderr") or "") for a in payload.get("attempts", [])))

    def test_explicit_target_normalizes_alias(self) -> None:
        result = self._run(
            "--target",
            "cursor-agent",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Small docs tweak",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["winner"], "cursor")
        self.assertEqual(payload["route"], ["cursor"])

    def test_difficulty_hard_override_in_json(self) -> None:
        result = self._run(
            "--target",
            "auto",
            "--difficulty",
            "hard",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename the toggle",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["difficulty"], "hard")

    def test_classify_subcommand_json(self) -> None:
        result = self._run("classify", "Review the API for security issues", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["category"], "review")

    def test_list_filters_by_session_key(self) -> None:
        env = self.base_env.copy()
        env["AI_PEERS_SESSION_KEY"] = "sess-list-test"
        first = self._run(
            "--target",
            "opencode",
            "--json",
            "--session-key",
            "sess-list-test",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename label",
            env=env,
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        listed = self._run("list", "--json", "--session-key", "sess-list-test", env=env)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        jobs = json.loads(listed.stdout)["jobs"]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["session_key"], "sess-list-test")

        other = self._run("list", "--json", "--session-key", "other-session", env=env)
        self.assertEqual(json.loads(other.stdout)["jobs"], [])

    def test_watch_once_and_job_status(self) -> None:
        result = self._run(
            "--target",
            "opencode",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename label",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        job_id = json.loads(result.stdout)["job_id"]

        watch = self._run("watch", job_id, "--once", "--json")
        self.assertEqual(watch.returncode, 0, watch.stderr)
        watch_payload = json.loads(watch.stdout)
        self.assertEqual(watch_payload["jobs"][0]["status"], "completed")

        status = self._run("job-status", job_id)
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["job_id"], job_id)

    def test_retry_preserves_parent_and_increments_retry_index(self) -> None:
        first = self._run(
            "--target",
            "opencode",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Original task",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        parent_id = json.loads(first.stdout)["job_id"]

        retry = self._run("retry", parent_id, "--feedback", "More detail", "--json")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        child = json.loads(retry.stdout)
        self.assertEqual(child["parent_job_id"], parent_id)
        self.assertEqual(child["retry_index"], 1)

    def test_background_notify_and_poll_completions(self) -> None:
        env = self.base_env.copy()
        env["AI_PEERS_SESSION_KEY"] = "bg-session"
        start = self._run(
            "--target",
            "opencode",
            "--background",
            "--notify-on-complete",
            "--session-key",
            "bg-session",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(REPO_ROOT),
            "--",
            "Rename label",
            env=env,
        )
        self.assertEqual(start.returncode, 0, start.stderr)
        job_id = json.loads(start.stdout)["job_id"]

        deadline = time.time() + 15.0
        status_payload: dict | None = None
        while time.time() < deadline:
            st = self._run("job-status", job_id, env=env)
            status_payload = json.loads(st.stdout)
            if status_payload.get("status") in {"completed", "failed"}:
                break
            time.sleep(0.15)
        self.assertIsNotNone(status_payload)
        self.assertIn(status_payload.get("status"), {"completed", "failed"})

        polled = self._run("poll-completions", "--session-key", "bg-session", env=env)
        self.assertEqual(polled.returncode, 0, polled.stderr)
        polled_payload = json.loads(polled.stdout)
        self.assertTrue(any(j["job_id"] == job_id for j in polled_payload["jobs"]))

        again = self._run("poll-completions", "--session-key", "bg-session", env=env)
        self.assertEqual(json.loads(again.stdout)["jobs"], [])

    def test_worktree_branch_mode_and_cleanup(self) -> None:
        self._init_git_repo()
        branch = f"test-branch-{os.urandom(4).hex()}"
        result = self._run(
            "--target",
            "opencode",
            "--worktree",
            f"branch:{branch}",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Rename readme title",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["worktree"]["path"])
        self.assertEqual(payload["worktree"]["branch"], branch)

        cleanup = self._run("cleanup-worktree", payload["job_id"], "--json")
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        self.assertEqual(json.loads(cleanup.stdout)["worktree"]["cleanup_status"], "removed")

    def test_preflight_fails_on_invalid_verify_config(self) -> None:
        (self.config_dir / "verify.json").write_text("{", encoding="utf-8")
        result = self._run(
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
            "Rename label",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["attempts"][0]["worker"], "preflight")

    def test_sigint_marks_job_failed_instead_of_running(self) -> None:
        slow_script = """#!/usr/bin/env python3
import time
time.sleep(10)
print("done")
"""
        write_executable(self.bin_dir / "codex", slow_script)

        proc = subprocess.Popen(
            [
                sys.executable,
                str(DISPATCH_BIN),
                "--target",
                "codex",
                "--json",
                "--from-agent",
                "codex",
                "--cwd",
                str(REPO_ROOT),
                "--",
                "Run a long check",
            ],
            cwd=str(REPO_ROOT),
            env=self.base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.4)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertTrue(payload.get("interrupted"))
        self.assertTrue(payload["attempts"])
        self.assertEqual(payload["attempts"][0]["exit_code"], 130)


if __name__ == "__main__":
    unittest.main()
