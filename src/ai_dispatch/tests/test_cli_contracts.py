from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class FailureLikeTests(unittest.TestCase):
    def test_soft_failure_phrase_with_zero_exit(self) -> None:
        from ai_dispatch import cli as cli_module

        result = {
            "exit_code": 0,
            "stdout": "Could not complete the change safely.\n",
            "stderr": "",
        }
        self.assertTrue(cli_module.failure_like(result))

    def test_success_json_output_not_failure_like(self) -> None:
        from ai_dispatch import cli as cli_module

        result = {
            "exit_code": 0,
            "stdout": '{"argv": [], "cwd": "/tmp"}\n',
            "stderr": "",
        }
        self.assertFalse(cli_module.failure_like(result))

    def test_empty_stdout_and_stderr_permission_denied(self) -> None:
        from ai_dispatch import cli as cli_module

        result = {"exit_code": 0, "stdout": "", "stderr": "permission denied opening file"}
        self.assertTrue(cli_module.failure_like(result))

    def test_empty_stdout_and_stderr_usage_marker(self) -> None:
        from ai_dispatch import cli as cli_module

        result = {"exit_code": 0, "stdout": "", "stderr": "usage: worker [OPTIONS]"}
        self.assertTrue(cli_module.failure_like(result))


class PollCompletionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tempdir.name) / "state"
        self.state_root.mkdir(parents=True)
        os.environ["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)

        import ai_dispatch.jobs as jobs_module
        import ai_dispatch.cli as cli_module

        importlib.reload(jobs_module)
        self.cli = importlib.reload(cli_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_DISPATCH_STATE_ROOT", None)

    def _write_job(
        self,
        *,
        job_id: str,
        session_key: str,
        status: str,
        notify: bool = True,
        completion_seen_at: float | None = None,
    ) -> None:
        from ai_dispatch.jobs import JOBS_DIR, ensure_state_root, save_job

        ensure_state_root()
        job = {
            "job_id": job_id,
            "target": "opencode",
            "from_agent": "codex",
            "session_key": session_key,
            "notify_on_complete": notify,
            "difficulty": "easy",
            "cwd": "/tmp",
            "execution_cwd": "/tmp",
            "task": "t",
            "task_summary": "t",
            "route": ["opencode"],
            "route_reason": "test",
            "timeout": 60,
            "created_at": 1.0,
            "started_at": 2.0,
            "finished_at": 3.0,
            "status": status,
            "winner": "opencode" if status == "completed" else None,
            "success": status == "completed",
            "attempts": [],
            "completion_seen_at": completion_seen_at,
            "parent_job_id": None,
            "retry_index": 0,
            "classifier": {},
            "scores": [],
            "routing_config": {},
            "verify_mode": "off",
            "requested_worktree_mode": "off",
            "verification": {},
            "worktree": {},
            "artifacts": {},
        }
        save_job(job)

    def test_poll_completions_filters_session_and_marks_seen(self) -> None:
        self._write_job(job_id="aaa111", session_key="sk-a", status="completed")
        self._write_job(job_id="bbb222", session_key="sk-b", status="completed")

        first = self.cli.poll_completions("sk-a", limit=8, mark_seen=True)
        self.assertEqual(len(first["jobs"]), 1)
        self.assertEqual(first["jobs"][0]["job_id"], "aaa111")
        self.assertIsNotNone(first["jobs"][0].get("completion_seen_at"))

        second = self.cli.poll_completions("sk-a", limit=8, mark_seen=True)
        self.assertEqual(second["jobs"], [])

    def test_poll_completions_keep_unseen_skips_mark(self) -> None:
        self._write_job(job_id="ccc333", session_key="sk-c", status="completed")

        first = self.cli.poll_completions("sk-c", limit=8, mark_seen=False)
        self.assertEqual(len(first["jobs"]), 1)
        self.assertIsNone(first["jobs"][0].get("completion_seen_at"))

        second = self.cli.poll_completions("sk-c", limit=8, mark_seen=True)
        self.assertEqual(len(second["jobs"]), 1)


class BuildPromptContractTests(unittest.TestCase):
    def test_prompt_contains_delegation_context(self) -> None:
        from ai_dispatch.adapters import build_prompt

        text = build_prompt(
            user_prompt="Fix the bug",
            difficulty="hard",
            cwd="/tmp/repo",
            source="codex",
            target="cursor",
        )
        self.assertIn("delegated worker from codex", text)
        self.assertIn("Target worker: cursor", text)
        self.assertIn("Task difficulty lane: hard", text)
        self.assertIn("Working directory: /tmp/repo", text)
        self.assertIn("User task:\nFix the bug", text)

    def test_prompt_marks_execution_first_for_inline_run_instruction(self) -> None:
        from ai_dispatch.adapters import build_prompt

        text = build_prompt(
            user_prompt="Please verify quickly. From repo root run: python3 -m unittest discover -s src/ai_peers/tests -v",
            difficulty="easy",
            cwd="/tmp/repo",
            source="codex",
            target="opencode",
        )
        self.assertIn("Execution-first", text)
        self.assertIn("python3 -m unittest discover -s src/ai_peers/tests -v", text)

    def test_execution_first_when_task_starts_with_shell_command(self) -> None:
        from ai_dispatch.adapters import build_prompt

        text = build_prompt(
            user_prompt="pytest -q tests/test_foo.py",
            difficulty="easy",
            cwd="/tmp/repo",
            source="codex",
            target="opencode",
        )
        self.assertIn("delegated worker from codex", text)
        self.assertIn("Execution-first mode", text)
        self.assertIn("explicit shell command", text)
        self.assertIn("User task:\npytest -q tests/test_foo.py", text)

    def test_dollar_prefix_line_is_explicit_command(self) -> None:
        from ai_dispatch.adapters import explicit_command_line

        self.assertEqual(explicit_command_line("$ pytest -q"), "pytest -q")
        self.assertIsNone(explicit_command_line("Please run pytest -q"))


class InterruptJobStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tempdir.name) / "state"
        self.state_root.mkdir(parents=True)
        os.environ["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)

        import ai_dispatch.jobs as jobs_module
        import ai_dispatch.cli as cli_module

        importlib.reload(jobs_module)
        self.cli = importlib.reload(cli_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_DISPATCH_STATE_ROOT", None)

    def test_interrupt_during_worker_wait_persists_failed_job(self) -> None:
        proc = MagicMock()
        proc.pid = 99901
        proc.poll.return_value = None
        proc.wait.side_effect = KeyboardInterrupt()

        job: dict = {
            "job_id": "deadbeef0101",
            "status": "running",
            "task": "pytest -q",
            "difficulty": "easy",
            "from_agent": "codex",
            "route": ["opencode"],
            "timeout": 60,
            "cwd": "/tmp",
            "execution_cwd": "/tmp",
            "verify_mode": "off",
            "artifacts": {"job_file": None, "attempt_logs": [], "verification_log": None},
        }

        with patch.object(self.cli, "worker_command", return_value=["true"]):
            with patch.object(self.cli.subprocess, "Popen", return_value=proc):
                with patch.object(self.cli.os, "killpg"):
                    finished = self.cli.run_sync(job, verify_config=None)

        self.assertTrue(finished.get("interrupted"))
        self.assertEqual(finished["status"], "failed")
        self.assertFalse(finished["success"])
        self.assertIsNotNone(finished.get("finished_at"))
        self.assertEqual(len(finished["attempts"]), 1)
        self.assertEqual(finished["attempts"][0]["exit_code"], 130)

        from ai_dispatch.jobs import load_job

        disk = load_job("deadbeef0101")
        self.assertEqual(disk["status"], "failed")
        self.assertTrue(disk.get("interrupted"))
        self.assertEqual(disk["attempts"][0]["exit_code"], 130)

    def test_keyboard_interrupt_before_attempt_records_failed_state(self) -> None:
        job: dict = {
            "job_id": "deadbeef0202",
            "status": "running",
            "task": "noop",
            "difficulty": "easy",
            "from_agent": "codex",
            "route": ["opencode"],
            "timeout": 60,
            "cwd": "/tmp",
            "execution_cwd": "/tmp",
            "verify_mode": "off",
            "artifacts": {"job_file": None, "attempt_logs": [], "verification_log": None},
        }

        with patch.object(self.cli, "build_prompt", side_effect=KeyboardInterrupt):
            finished = self.cli.run_sync(job, verify_config=None)

        self.assertTrue(finished.get("interrupted"))
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["attempts"], [])

        from ai_dispatch.jobs import load_job

        disk = load_job("deadbeef0202")
        self.assertEqual(disk["status"], "failed")
        self.assertTrue(disk.get("interrupted"))


class SummarizeResultTests(unittest.TestCase):
    def test_large_output_is_truncated(self) -> None:
        from ai_dispatch import cli as cli_module

        result = {"stdout": "A" * 2000, "stderr": "", "exit_code": 0}
        summary = cli_module.summarize_result(result)
        self.assertTrue(summary.endswith("..."))
        self.assertLessEqual(len(summary), 1400)


if __name__ == "__main__":
    unittest.main()
