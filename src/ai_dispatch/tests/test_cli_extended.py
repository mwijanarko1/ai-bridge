from __future__ import annotations

import json
import os
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


class DispatchCliExtendedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state_root = self.root / "state"
        self.config_dir = self.root / "config"
        self.bin_dir = self.root / "bin"
        self.repo_dir = self.root / "repo"
        self.nested_dir = self.repo_dir / "nested" / "dir"

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        self.base_env = os.environ.copy()
        self.base_env["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)
        self.base_env["AI_BRIDGE_CONFIG_DIR"] = str(self.config_dir)
        self.base_env["PATH"] = f"{self.bin_dir}:{self.base_env.get('PATH', '')}"
        self.base_env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{self.base_env.get('PYTHONPATH', '')}"

        self._install_fake_workers()
        self._init_git_repo()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _install_fake_workers(self) -> None:
        script = """#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

name = Path(sys.argv[0]).name
env_name = "FAKE_WORKER_BEHAVIOR_" + name.upper().replace("-", "_")
mode = os.environ.get(env_name, "success")

if mode == "soft_fail":
    print("Could not complete this task safely.")
    raise SystemExit(0)
if mode == "hard_fail":
    print("execution error: synthetic worker failure")
    raise SystemExit(2)
if mode == "timeout":
    time.sleep(2.5)
    raise SystemExit(0)
if mode == "large":
    print("X" * 2200)
    raise SystemExit(0)

print(json.dumps({"worker": name, "argv": sys.argv[1:], "cwd": os.getcwd()}))
"""
        for name in ("codex", "claude-code-worker", "agent-hard", "opencode-easy", "goose"):
            write_executable(self.bin_dir / name, script)

    def _init_git_repo(self) -> None:
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (self.repo_dir / "README.md").write_text("hello\n", encoding="utf-8")
        self.nested_dir.mkdir(parents=True, exist_ok=True)
        env = self.base_env.copy()
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "test@example.com"
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _run(
        self,
        *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DISPATCH_BIN), *args],
            cwd=str(cwd or REPO_ROOT),
            env=env or self.base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _wait_for_terminal_status(self, job_id: str, timeout: float = 8.0) -> dict[str, object]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._run("job-status", job_id)
            self.assertEqual(status.returncode, 0, status.stderr)
            payload = json.loads(status.stdout)
            if payload.get("status") in {"completed", "failed"}:
                return payload
            time.sleep(0.1)
        self.fail(f"Timed out waiting for job {job_id} terminal status")

    def test_soft_failure_falls_through_to_next_worker(self) -> None:
        env = self.base_env.copy()
        env["FAKE_WORKER_BEHAVIOR_AGENT_HARD"] = "soft_fail"
        result = self._run(
            "--target",
            "auto",
            "--difficulty",
            "hard",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Debug race condition in sync engine across modules",
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertGreaterEqual(len(payload["attempts"]), 2)
        self.assertEqual(payload["attempts"][0]["worker"], "cursor")
        self.assertEqual(payload["attempts"][1]["worker"], "claude")
        self.assertEqual(payload["winner"], "claude")

    def test_background_job_and_completion_polling(self) -> None:
        started = self._run(
            "--target",
            "opencode",
            "--background",
            "--notify-on-complete",
            "--session-key",
            "session-bg",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple setting toggle",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        start_payload = json.loads(started.stdout)
        final_payload = self._wait_for_terminal_status(start_payload["job_id"])
        self.assertEqual(final_payload["status"], "completed")

        first_poll = self._run("poll-completions", "--session-key", "session-bg")
        self.assertEqual(first_poll.returncode, 0, first_poll.stderr)
        first_payload = json.loads(first_poll.stdout)
        self.assertEqual(len(first_payload["jobs"]), 1)
        self.assertEqual(first_payload["jobs"][0]["job_id"], start_payload["job_id"])

        second_poll = self._run("poll-completions", "--session-key", "session-bg")
        self.assertEqual(second_poll.returncode, 0, second_poll.stderr)
        second_payload = json.loads(second_poll.stdout)
        self.assertEqual(second_payload["jobs"], [])

    def test_poll_completions_keep_unseen_via_cli(self) -> None:
        result = self._run(
            "--target",
            "opencode",
            "--notify-on-complete",
            "--session-key",
            "session-keep",
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

        first = self._run("poll-completions", "--session-key", "session-keep", "--keep-unseen")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_payload = json.loads(first.stdout)
        self.assertEqual(len(first_payload["jobs"]), 1)
        self.assertEqual(first_payload["jobs"][0]["job_id"], payload["job_id"])

        second = self._run("poll-completions", "--session-key", "session-keep", "--keep-unseen")
        self.assertEqual(second.returncode, 0, second.stderr)
        second_payload = json.loads(second.stdout)
        self.assertEqual(len(second_payload["jobs"]), 1)

    def test_watch_show_and_job_status(self) -> None:
        result = self._run(
            "--target",
            "opencode",
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

        watched = self._run("watch", payload["job_id"], "--once", "--json")
        self.assertEqual(watched.returncode, 0, watched.stderr)
        watched_payload = json.loads(watched.stdout)
        self.assertEqual(watched_payload["jobs"][0]["job_id"], payload["job_id"])

        status = self._run("job-status", payload["job_id"])
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["status"], "completed")

        shown = self._run("show", payload["job_id"], "--log")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("attempts:", shown.stdout)
        self.assertIn("argv", shown.stdout)

    def test_foreground_non_json_streams_worker_output_before_summary(self) -> None:
        result = self._run(
            "--target",
            "opencode",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        non_blank_lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertTrue(non_blank_lines, result.stdout)
        self.assertTrue(non_blank_lines[0].startswith("{"), result.stdout)
        self.assertIn("[ai-dispatch] winner=opencode", result.stdout)

    def test_json_mode_remains_machine_readable(self) -> None:
        result = self._run(
            "--target",
            "opencode",
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
        self.assertEqual(payload["winner"], "opencode")
        self.assertEqual(payload["attempts"][0]["worker"], "opencode")

    def test_retry_preserves_routing_inputs(self) -> None:
        initial = self._run(
            "--target",
            "opencode",
            "--timeout",
            "123",
            "--verify",
            "off",
            "--worktree",
            "off",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        initial_payload = json.loads(initial.stdout)

        retried = self._run("retry", initial_payload["job_id"], "--feedback", "Tighten the fix", "--json")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        retry_payload = json.loads(retried.stdout)
        self.assertEqual(retry_payload["parent_job_id"], initial_payload["job_id"])
        self.assertEqual(retry_payload["target"], initial_payload["target"])
        self.assertEqual(retry_payload["difficulty"], initial_payload["difficulty"])
        self.assertEqual(retry_payload["timeout"], initial_payload["timeout"])
        self.assertEqual(retry_payload["verify_mode"], initial_payload["verify_mode"])
        self.assertEqual(retry_payload["requested_worktree_mode"], initial_payload["requested_worktree_mode"])
        self.assertEqual(retry_payload["retry_index"], 1)

    def test_verification_failure_flips_success(self) -> None:
        (self.config_dir / "verify.json").write_text(
            json.dumps({"profiles": {"default": {"command": [sys.executable, "-c", "import sys; sys.exit(1)"]}}}),
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
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["verification"]["status"], "failed")
        self.assertTrue(payload["verification"]["log_path"])

    def test_verify_missing_profile_and_invalid_config(self) -> None:
        missing = self._run(
            "--target",
            "opencode",
            "--verify",
            "default",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(missing.returncode, 1, missing.stderr)
        self.assertIn("preflight failed", missing.stdout)
        self.assertIn("Verify profile 'default' requested", missing.stdout)

        invalid_path = self.root / "invalid-verify.json"
        invalid_path.write_text("{not-json", encoding="utf-8")
        invalid = self._run(
            "--target",
            "opencode",
            "--verify",
            "default",
            "--verify-config",
            str(invalid_path),
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add a simple settings toggle",
        )
        self.assertEqual(invalid.returncode, 1, invalid.stderr)
        self.assertIn("Invalid verify config", invalid.stdout)
        self.assertIn(str(invalid_path), invalid.stdout)

    def test_worktree_modes_and_cleanup_paths(self) -> None:
        named = self._run(
            "--target",
            "opencode",
            "--worktree",
            "branch:feature/test-worktree",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Refactor auth flow",
        )
        self.assertEqual(named.returncode, 0, named.stderr)
        named_payload = json.loads(named.stdout)
        self.assertEqual(named_payload["worktree"]["mode"], "named")
        self.assertEqual(named_payload["worktree"]["branch"], "feature/test-worktree")
        self.assertTrue(Path(named_payload["worktree"]["path"]).exists())

        cleaned = self._run("cleanup-worktree", named_payload["job_id"], "--json")
        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        cleaned_payload = json.loads(cleaned.stdout)
        self.assertEqual(cleaned_payload["worktree"]["cleanup_status"], "removed")

        off_job = self._run(
            "--target",
            "opencode",
            "--worktree",
            "off",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Minor text change",
        )
        self.assertEqual(off_job.returncode, 0, off_job.stderr)
        off_payload = json.loads(off_job.stdout)
        off_cleanup = self._run("cleanup-worktree", off_payload["job_id"], "--json")
        self.assertEqual(off_cleanup.returncode, 0, off_cleanup.stderr)
        off_cleanup_payload = json.loads(off_cleanup.stdout)
        self.assertEqual(off_cleanup_payload["worktree"]["cleanup_status"], "not_applicable")

    def test_worktree_requires_git_repo(self) -> None:
        plain = self.root / "plain"
        plain.mkdir(parents=True, exist_ok=True)
        result = self._run(
            "--target",
            "opencode",
            "--worktree",
            "auto",
            "--from-agent",
            "codex",
            "--cwd",
            str(plain),
            "--",
            "Refactor this",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Worktree mode requires a git repository.", result.stdout)

    def test_adapter_error_paths(self) -> None:
        unsupported = self._run(
            "--target",
            "gemini",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Investigate migration issue",
        )
        self.assertEqual(unsupported.returncode, 1, unsupported.stderr)
        self.assertIn("Unsupported target 'gemini'", unsupported.stdout)

        (self.config_dir / "adapters.json").write_text(
            json.dumps({"gemini": {"command": "not-a-list"}}),
            encoding="utf-8",
        )
        invalid_shape = self._run(
            "--target",
            "gemini",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Investigate migration issue",
        )
        self.assertEqual(invalid_shape.returncode, 1, invalid_shape.stderr)
        self.assertIn("missing a non-empty command list", invalid_shape.stdout)

        (self.config_dir / "adapters.json").write_text(
            json.dumps({"gemini": {"command": ["definitely-not-a-real-binary-xyz", "--prompt", "{prompt}"]}}),
            encoding="utf-8",
        )
        missing_binary = self._run(
            "--target",
            "gemini",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Investigate migration issue",
        )
        self.assertEqual(missing_binary.returncode, 1, missing_binary.stderr)
        self.assertIn("is not available on PATH", missing_binary.stdout)

    def test_timeout_attempt_records_failure(self) -> None:
        env = self.base_env.copy()
        env["FAKE_WORKER_BEHAVIOR_OPENCODE_EASY"] = "timeout"
        timed = self._run(
            "--target",
            "opencode",
            "--timeout",
            "1",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Add toggle",
            env=env,
        )
        self.assertEqual(timed.returncode, 1, timed.stderr)
        payload = json.loads(timed.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["attempts"][0]["exit_code"], 124)
        self.assertIn("timed out", payload["attempts"][0]["stderr"].lower())

    def test_classify_route_output_and_repo_root_detection(self) -> None:
        classify = self._run("classify", "Review the auth code")
        self.assertEqual(classify.returncode, 0, classify.stderr)
        self.assertIn("'category': 'review'", classify.stdout)

        route = self._run("route", "Add a new endpoint", "--target", "auto")
        self.assertEqual(route.returncode, 0, route.stderr)
        self.assertIn("route=", route.stdout)
        self.assertIn("difficulty=", route.stdout)
        self.assertIn("reason=", route.stdout)

        run_from_nested = self._run(
            "--target",
            "opencode",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.nested_dir),
            "--",
            "Rename a label",
        )
        self.assertEqual(run_from_nested.returncode, 0, run_from_nested.stderr)
        nested_payload = json.loads(run_from_nested.stdout)
        self.assertEqual(Path(nested_payload["cwd"]).resolve(), Path(self.repo_dir).resolve())

    def test_difficulty_override_in_route_command(self) -> None:
        hard = self._run("route", "Rename docs label", "--target", "auto", "--difficulty", "hard", "--json")
        self.assertEqual(hard.returncode, 0, hard.stderr)
        hard_payload = json.loads(hard.stdout)
        self.assertEqual(hard_payload["difficulty"], "hard")

        easy = self._run("route", "Debug race condition in sync engine", "--target", "auto", "--difficulty", "easy", "--json")
        self.assertEqual(easy.returncode, 0, easy.stderr)
        easy_payload = json.loads(easy.stdout)
        self.assertEqual(easy_payload["difficulty"], "easy")


if __name__ == "__main__":
    unittest.main()
