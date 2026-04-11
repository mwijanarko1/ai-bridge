from __future__ import annotations

import argparse
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]


class OrchestrateParseTests(unittest.TestCase):
    def test_cli_registers_orchestrate_with_max_turns(self) -> None:
        from ai_dispatch.cli import parse_args

        args = parse_args(
            [
                "orchestrate",
                "--max-turns",
                "2",
                "--target",
                "cursor",
                "--cwd",
                "/tmp",
                "--from-agent",
                "codex",
                "Implement",
                "the",
                "feature",
            ]
        )
        self.assertEqual(args.command, "orchestrate")
        self.assertEqual(args.max_turns, 2)
        self.assertEqual(args.task, ["Implement", "the", "feature"])


class OrchestrateHeuristicTests(unittest.TestCase):
    def test_user_question_block_requires_marker_and_question_mark(self) -> None:
        from ai_dispatch import orchestrate as orch

        job_ok = {
            "success": True,
            "winner": "opencode",
            "attempts": [
                {
                    "worker": "opencode",
                    "ok": True,
                    "stdout": "Which file should I edit first?",
                    "stderr": "",
                }
            ],
        }
        self.assertTrue(orch.output_suggests_user_question_block(job_ok))

        no_marker = {
            "success": True,
            "winner": "opencode",
            "attempts": [
                {"worker": "opencode", "ok": True, "stdout": "Done?", "stderr": ""}
            ],
        }
        self.assertFalse(orch.output_suggests_user_question_block(no_marker))

    def test_verification_failed_detects_winner_plus_failed_status(self) -> None:
        from ai_dispatch import orchestrate as orch

        self.assertTrue(
            orch.verification_failed(
                {
                    "winner": "opencode",
                    "verification": {"status": "failed"},
                }
            )
        )
        self.assertFalse(
            orch.verification_failed(
                {"winner": None, "verification": {"status": "failed"}}
            )
        )

    def test_worker_status_protocol(self) -> None:
        from ai_dispatch import orchestrate as orch

        job = {
            "success": True,
            "winner": "opencode",
            "attempts": [
                {
                    "worker": "opencode",
                    "ok": True,
                    "stdout": "Implemented part 1\nAI_BRIDGE_STATUS: continue",
                    "stderr": "",
                }
            ],
        }
        self.assertEqual(orch.worker_status(job), orch.STATUS_CONTINUE)

        blocked = {
            **job,
            "attempts": [
                {
                    "worker": "opencode",
                    "ok": True,
                    "stdout": (
                        "Need a product decision\n"
                        "AI_BRIDGE_STATUS: blocked\n"
                        "AI_BRIDGE_USER_QUESTION: Which auth provider should be primary?"
                    ),
                    "stderr": "",
                }
            ],
        }
        self.assertEqual(orch.worker_status(blocked), orch.STATUS_BLOCKED)
        self.assertEqual(
            orch.worker_user_question(blocked),
            "Which auth provider should be primary?",
        )

    def test_orchestration_exit_codes(self) -> None:
        from ai_dispatch import orchestrate as orch

        job = {"success": True}
        self.assertEqual(orch.orchestration_exit_code(orch.STOP_COMPLETED, job), 0)
        self.assertEqual(orch.orchestration_exit_code(orch.STOP_USER_QUESTION, job), 3)
        self.assertEqual(orch.orchestration_exit_code(orch.STOP_PENDING_PERMISSION, job), 2)
        self.assertEqual(orch.orchestration_exit_code(orch.STOP_INTERRUPTED, job), 130)

    def test_build_followup_includes_base_task(self) -> None:
        from ai_dispatch import orchestrate as orch

        prev = {
            "attempts": [
                {"worker": "x", "ok": False, "stdout": "", "stderr": "compile error"}
            ]
        }
        text = orch.build_followup_task("Ship the feature", prev, 2)
        self.assertIn("Ship the feature", text)
        self.assertIn("turn 2", text)
        self.assertIn("compile error", text)
        self.assertIn("AI_BRIDGE_STATUS", text)


class OrchestrateLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tempdir.name) / "state"
        os.environ["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)
        self.config_dir = Path(self.tempdir.name) / "config"
        self.config_dir.mkdir(parents=True)

        import ai_dispatch.jobs as jobs_module
        import ai_dispatch.cli as cli_module

        importlib.reload(jobs_module)
        self.cli = importlib.reload(cli_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_DISPATCH_STATE_ROOT", None)

    def _ns(self, **overrides):
        base = dict(
            target="opencode",
            difficulty="easy",
            cwd=str(REPO_ROOT),
            timeout=60,
            json=True,
            from_agent="codex",
            background=False,
            notify_on_complete=False,
            session_key="",
            verify="off",
            verify_config=None,
            permissions="skip",
            worktree="off",
            task=["Do the thing"],
            max_turns=3,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    @patch("ai_dispatch.cli.complete_job_sync")
    def test_second_turn_succeeds_after_worker_failure(self, mock_complete) -> None:
        def side_effect(job, run_ns, stream=None):
            if job.get("retry_index", 0) == 0:
                return {
                    **job,
                    "success": False,
                    "winner": None,
                    "status": "failed",
                    "interrupted": False,
                    "attempts": [
                        {
                            "worker": "opencode",
                            "ok": False,
                            "stdout": "",
                            "stderr": "try again",
                        }
                    ],
                    "verification": {"status": "skipped"},
                }
            return {
                **job,
                "success": True,
                "winner": "opencode",
                "status": "completed",
                "interrupted": False,
                "attempts": [
                    {
                        "worker": "opencode",
                        "ok": True,
                        "stdout": "fixed",
                        "stderr": "",
                    }
                ],
                "verification": {"status": "skipped"},
            }

        mock_complete.side_effect = side_effect
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            code = self.cli.handle_orchestrate(self._ns(json=False))
        self.assertEqual(code, 0)
        self.assertEqual(mock_complete.call_count, 2)
        out = buf.getvalue()
        self.assertIn("stop=completed", out)

    @patch("ai_dispatch.cli.complete_job_sync")
    def test_stops_on_verification_failure_without_retry(self, mock_complete) -> None:
        mock_complete.return_value = {
            "job_id": "fake",
            "success": False,
            "winner": "opencode",
            "status": "failed",
            "interrupted": False,
            "attempts": [
                {"worker": "opencode", "ok": True, "stdout": "patched", "stderr": ""}
            ],
            "verification": {"status": "failed", "profile": "default", "summary": "boom"},
        }
        with patch("sys.stdout", io.StringIO()):
            code = self.cli.handle_orchestrate(self._ns())
        self.assertEqual(code, 1)
        self.assertEqual(mock_complete.call_count, 1)

    @patch("ai_dispatch.cli.complete_job_sync")
    def test_user_question_stops_with_exit_3(self, mock_complete) -> None:
        mock_complete.return_value = {
            "job_id": "fake",
            "success": True,
            "winner": "opencode",
            "status": "completed",
            "interrupted": False,
            "attempts": [
                {
                    "worker": "opencode",
                    "ok": True,
                    "stdout": "Which approach should I use?",
                    "stderr": "",
                }
            ],
            "verification": {"status": "skipped"},
        }
        with patch("sys.stdout", io.StringIO()):
            code = self.cli.handle_orchestrate(self._ns())
        self.assertEqual(code, 3)
        self.assertEqual(mock_complete.call_count, 1)

    @patch("ai_dispatch.cli.complete_job_sync")
    def test_continue_status_runs_another_turn(self, mock_complete) -> None:
        def side_effect(job, run_ns, stream=None):
            if job.get("retry_index", 0) == 0:
                return {
                    **job,
                    "success": True,
                    "winner": "opencode",
                    "status": "completed",
                    "interrupted": False,
                    "attempts": [
                        {
                            "worker": "opencode",
                            "ok": True,
                            "stdout": "Started\nAI_BRIDGE_STATUS: continue",
                            "stderr": "",
                        }
                    ],
                    "verification": {"status": "skipped"},
                }
            return {
                **job,
                "success": True,
                "winner": "opencode",
                "status": "completed",
                "interrupted": False,
                "attempts": [
                    {
                        "worker": "opencode",
                        "ok": True,
                        "stdout": "Done\nAI_BRIDGE_STATUS: done",
                        "stderr": "",
                    }
                ],
                "verification": {"status": "skipped"},
            }

        mock_complete.side_effect = side_effect
        with patch("sys.stdout", io.StringIO()):
            code = self.cli.handle_orchestrate(self._ns())
        self.assertEqual(code, 0)
        self.assertEqual(mock_complete.call_count, 2)

    @patch("ai_dispatch.cli.complete_job_sync")
    def test_blocked_status_stops_with_exit_3(self, mock_complete) -> None:
        mock_complete.return_value = {
            "job_id": "fake",
            "success": True,
            "winner": "opencode",
            "status": "completed",
            "interrupted": False,
            "attempts": [
                {
                    "worker": "opencode",
                    "ok": True,
                    "stdout": (
                        "Need direction\n"
                        "AI_BRIDGE_STATUS: blocked\n"
                        "AI_BRIDGE_USER_QUESTION: Which database should I use?"
                    ),
                    "stderr": "",
                }
            ],
            "verification": {"status": "skipped"},
        }
        with patch("sys.stdout", io.StringIO()):
            code = self.cli.handle_orchestrate(self._ns())
        self.assertEqual(code, 3)
        self.assertEqual(mock_complete.call_count, 1)


if __name__ == "__main__":
    unittest.main()
