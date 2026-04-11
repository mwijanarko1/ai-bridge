from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

STATE_ROOT = Path(os.environ.get("AI_DISPATCH_STATE_ROOT", "~/.local/state/ai-dispatch")).expanduser()
JOBS_DIR = STATE_ROOT / "jobs"
LOGS_DIR = STATE_ROOT / "logs"
WORKTREES_DIR = STATE_ROOT / "worktrees"
PERMISSION_RESPONSES_DIR = STATE_ROOT / "permission-responses"
CONFIG_DIR = Path(os.environ.get("AI_BRIDGE_CONFIG_DIR", "~/.config/ai-bridge")).expanduser()

DEFAULT_TIMEOUT = 900
PRIMARY_AGENTS = ("codex", "claude", "cursor", "opencode")
EXTRA_BUILTIN_AGENTS = ("goose", "qwen")


def now_ts() -> float:
    return time.time()


def ensure_state_root() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    PERMISSION_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)


def detect_repo_root(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return value or None


def summarize_task(task: str, limit: int = 96) -> str:
    clean = " ".join(str(task).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def generate_job_id() -> str:
    return uuid.uuid4().hex[:12]


def job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def permission_response_path(job_id: str) -> Path:
    return PERMISSION_RESPONSES_DIR / f"{job_id}.json"


def save_job(job: dict[str, Any]) -> str:
    ensure_state_root()
    path = job_path(job["job_id"])
    with tempfile.NamedTemporaryFile("w", delete=False, dir=JOBS_DIR, encoding="utf-8") as handle:
        json.dump(job, handle, indent=2, sort_keys=True)
        handle.flush()
        temp_path = Path(handle.name)
    temp_path.replace(path)
    return str(path)


def load_job(job_id: str) -> dict[str, Any]:
    return json.loads(job_path(job_id).read_text(encoding="utf-8"))


def iter_jobs() -> list[dict[str, Any]]:
    ensure_state_root()
    jobs: list[dict[str, Any]] = []
    for path in JOBS_DIR.glob("*.json"):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    jobs.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return jobs


def list_jobs(*, status: str = "all", limit: int = 20, session_key: str | None = None) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for job in iter_jobs():
        if status != "all" and job.get("status") != status:
            continue
        if session_key and job.get("session_key") != session_key:
            continue
        matches.append(job)
        if len(matches) >= max(1, limit):
            break
    return matches


def make_attempt(
    *,
    worker: str,
    command: list[str] | None = None,
    exit_code: int = 0,
    duration_seconds: float = 0,
    stdout: str = "",
    stderr: str = "",
    ok: bool = False,
    log_path: str = "",
) -> dict[str, Any]:
    return {
        "worker": worker,
        "command": command or [],
        "exit_code": int(exit_code),
        "duration_seconds": round(float(duration_seconds), 2),
        "stdout": stdout,
        "stderr": stderr,
        "ok": bool(ok),
        "log_path": log_path,
    }


def default_verification(mode: str) -> dict[str, Any]:
    return {
        "mode": "none" if mode == "off" else "profile",
        "profile": None if mode == "off" else mode,
        "status": "skipped" if mode == "off" else "pending",
        "command": None,
        "exit_code": None,
        "duration_seconds": 0,
        "log_path": None,
        "summary": "",
    }


def default_worktree(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "path": None,
        "branch": None,
        "repo_root": None,
        "created": False,
        "cleanup_status": "not_applicable" if mode == "off" else "retained",
    }


def default_artifacts() -> dict[str, Any]:
    return {
        "job_file": None,
        "attempt_logs": [],
        "verification_log": None,
    }


def default_permission_state(policy: str) -> dict[str, Any]:
    return {
        "policy": policy,
        "pending": None,
        "events": [],
    }
