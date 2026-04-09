from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from .jobs import CONFIG_DIR, LOGS_DIR, now_ts


def verify_config_path(cwd: str | None, override_path: str | None = None) -> Path | None:
    if override_path:
        return Path(override_path).expanduser()

    candidates: list[Path] = []
    if cwd:
        candidates.append(Path(cwd) / ".ai-bridge" / "verify.json")
    candidates.append(CONFIG_DIR / "verify.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_verify_config(cwd: str | None, override_path: str | None = None) -> dict:
    path = verify_config_path(cwd, override_path=override_path)
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid verify config at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid verify config at {path}: top-level JSON object required.")
    return payload


def prepare_verification(mode: str, cwd: str | None, override_path: str | None = None) -> dict:
    if mode == "off":
        return {
            "mode": "none",
            "profile": None,
            "status": "skipped",
            "command": None,
            "exit_code": None,
            "duration_seconds": 0,
            "log_path": None,
            "summary": "",
        }

    config = load_verify_config(cwd, override_path=override_path)
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        path = verify_config_path(cwd, override_path=override_path)
        raise ValueError(f"Verify profile '{mode}' requested but no valid profiles were found in {path}.")

    profile = profiles.get(mode)
    if not isinstance(profile, dict):
        raise ValueError(f"Verify profile '{mode}' was not found.")

    command = profile.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError(f"Verify profile '{mode}' must define a non-empty command list.")

    return {
        "mode": "profile",
        "profile": mode,
        "status": "pending",
        "command": " ".join(shlex.quote(str(part)) for part in command),
        "command_list": [str(part) for part in command],
        "exit_code": None,
        "duration_seconds": 0,
        "log_path": None,
        "summary": "",
    }


def run_verification(job: dict, cwd: str) -> dict:
    command_list = job["verification"].get("command_list") or []
    if not command_list:
        return job["verification"]

    started = now_ts()
    log_path = LOGS_DIR / f"{job['job_id']}-verify.log"
    with log_path.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            command_list,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    finished = now_ts()
    output = log_path.read_text(encoding="utf-8", errors="replace").strip()
    verification = dict(job["verification"])
    verification.update(
        {
            "status": "passed" if completed.returncode == 0 else "failed",
            "exit_code": completed.returncode,
            "duration_seconds": round(finished - started, 2),
            "log_path": str(log_path),
            "summary": output[:280] + ("..." if len(output) > 280 else ""),
        }
    )
    verification.pop("command_list", None)
    return verification
