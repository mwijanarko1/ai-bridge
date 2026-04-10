from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
DISPATCH_BIN = REPO_ROOT / "bin" / "ai-dispatch"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class CodexPermissionHelpersTests(unittest.TestCase):
    def test_builtin_codex_command_uses_full_auto_only_for_skip_policy(self) -> None:
        from ai_dispatch.adapters import builtin_worker_command

        skip = builtin_worker_command("codex", "do the thing", "/tmp/ws", permission_policy="skip")
        relay = builtin_worker_command("codex", "do the thing", "/tmp/ws", permission_policy="relay")

        self.assertIn("--full-auto", skip)
        self.assertNotIn("--full-auto", relay)

    def test_permission_prompt_excerpt_detects_codex_prompt(self) -> None:
        from ai_dispatch.cli import permission_prompt_excerpt

        excerpt = permission_prompt_excerpt("codex", "Allow Codex to write files? [y/N]\n")
        self.assertEqual(excerpt, "Allow Codex to write files? [y/N]")

    def test_collect_interactive_result_streams_live_output(self) -> None:
        from ai_dispatch import cli as cli_module

        with tempfile.TemporaryDirectory() as tempdir:
            script_path = Path(tempdir) / "interactive-worker.py"
            script_path.write_text(
                """import json
import sys

print("Allow Codex to write files? [y/N]", flush=True)
answer = sys.stdin.readline().strip().lower()
print(json.dumps({"answer": answer}), flush=True)
""",
                encoding="utf-8",
            )
            stream = io.StringIO()
            with patch.object(cli_module, "update_job_permission_state"), patch.object(
                cli_module,
                "wait_for_permission_decision",
                return_value="allow",
            ):
                result = cli_module.collect_interactive_result(
                    "codex",
                    [sys.executable, str(script_path)],
                    tempdir,
                    2,
                    "job123",
                    0,
                    permission_policy="relay",
                    stream=stream,
                )

        self.assertEqual(result["exit_code"], 0)
        self.assertIn("Allow Codex to write files? [y/N]", stream.getvalue())
        self.assertIn('"answer": "y"', stream.getvalue())


class CodexPermissionRelayCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state_root = self.root / "state"
        self.config_dir = self.root / "config"
        self.bin_dir = self.root / "bin"
        self.repo_dir = self.root / "repo"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        self.base_env = os.environ.copy()
        self.base_env["AI_DISPATCH_STATE_ROOT"] = str(self.state_root)
        self.base_env["AI_BRIDGE_CONFIG_DIR"] = str(self.config_dir)
        self.base_env["PATH"] = f"{self.bin_dir}:{self.base_env.get('PATH', '')}"
        self.base_env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}:{self.base_env.get('PYTHONPATH', '')}"

        worker_script = """#!/usr/bin/env python3
import json
import sys

argv = sys.argv[1:]
if "--full-auto" in argv:
    print(json.dumps({"mode": "skip", "argv": argv}))
    raise SystemExit(0)

print("Allow Codex to write files? [y/N]", flush=True)
answer = sys.stdin.readline().strip().lower()
print(json.dumps({"mode": "relay", "answer": answer, "argv": argv}), flush=True)
raise SystemExit(0 if answer in {"y", "yes"} else 1)
"""
        write_executable(self.bin_dir / "codex", worker_script)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DISPATCH_BIN), *args],
            cwd=str(REPO_ROOT),
            env=env or self.base_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _wait_for_status(self, job_id: str, expected: set[str], timeout: float = 8.0) -> dict[str, object]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._run("job-status", job_id)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            if payload.get("status") in expected:
                return payload
            time.sleep(0.1)
        self.fail(f"Timed out waiting for {job_id} -> {expected}")

    def test_relay_policy_waits_for_permission_response(self) -> None:
        first = self._run(
            "--target",
            "codex",
            "--permissions",
            "relay",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Ship the fix",
        )
        self.assertEqual(first.returncode, 2, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["status"], "pending_permission")
        pending = payload["permissions"]["pending"]
        self.assertEqual(pending["worker"], "codex")
        self.assertIn("Allow Codex", pending["prompt"])

        listed = self._run("list", "--status", "pending_permission", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        rows = json.loads(listed.stdout)["jobs"]
        self.assertTrue(any(job["job_id"] == payload["job_id"] for job in rows))

        resume = self._run("permission-response", payload["job_id"], "allow", "--json")
        self.assertEqual(resume.returncode, 0, resume.stderr)

        done = self._wait_for_status(payload["job_id"], {"completed", "failed"})
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["success"])
        self.assertEqual(done["winner"], "codex")
        self.assertIsNone(done["permissions"]["pending"])
        self.assertEqual(done["permissions"]["events"][0]["decision"], "allow")

    def test_deny_response_leaves_job_failed(self) -> None:
        first = self._run(
            "--target",
            "codex",
            "--permissions",
            "relay",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Ship the fix",
        )
        self.assertEqual(first.returncode, 2, first.stderr)
        job_id = json.loads(first.stdout)["job_id"]

        deny = self._run("permission-response", job_id, "deny", "--json")
        self.assertEqual(deny.returncode, 0, deny.stderr)

        done = self._wait_for_status(job_id, {"completed", "failed"})
        self.assertEqual(done["status"], "failed")
        self.assertFalse(done["success"])
        self.assertEqual(done["permissions"]["events"][0]["decision"], "deny")

    def test_skip_policy_uses_full_auto_and_completes_without_pending_state(self) -> None:
        result = self._run(
            "--target",
            "codex",
            "--permissions",
            "skip",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Quick task",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["winner"], "codex")
        self.assertIsNone(payload["permissions"]["pending"])
        self.assertIn("--full-auto", payload["attempts"][0]["command"])

    def test_permission_response_rejects_non_pending_job(self) -> None:
        run = self._run(
            "--target",
            "codex",
            "--permissions",
            "skip",
            "--json",
            "--from-agent",
            "codex",
            "--cwd",
            str(self.repo_dir),
            "--",
            "Done",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        job_id = json.loads(run.stdout)["job_id"]

        bad = self._run("permission-response", job_id, "allow")
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
