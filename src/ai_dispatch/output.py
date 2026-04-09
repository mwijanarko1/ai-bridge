from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def json_output(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def format_timestamp(value: float | int | None) -> str:
    if not value:
        return "-"
    return dt.datetime.fromtimestamp(float(value)).isoformat(timespec="seconds")


def format_job_list(jobs: list[dict[str, Any]]) -> str:
    lines = []
    for job in jobs:
        lines.append(
            " | ".join(
                [
                    job.get("job_id", "-"),
                    format_timestamp(job.get("created_at")),
                    job.get("status", "-"),
                    f"winner={job.get('winner') or '-'}",
                    f"difficulty={job.get('difficulty') or '-'}",
                    f"target={job.get('target') or '-'}",
                    f"route={','.join(job.get('route') or []) or '-'}",
                    job.get("task_summary", "-"),
                ]
            )
        )
    return "\n".join(lines) if lines else "No matching jobs."


def format_job_show(job: dict[str, Any], *, log_attempt: int | None = None, include_log: bool = False) -> str:
    lines = [
        f"job_id: {job.get('job_id')}",
        f"status: {job.get('status')}",
        f"winner: {job.get('winner') or '-'}",
        f"created_at: {format_timestamp(job.get('created_at'))}",
        f"started_at: {format_timestamp(job.get('started_at'))}",
        f"finished_at: {format_timestamp(job.get('finished_at'))}",
        f"target: {job.get('target')}",
        f"difficulty: {job.get('difficulty')}",
        f"route: {', '.join(job.get('route') or []) or '-'}",
        f"route_reason: {job.get('route_reason') or '-'}",
        f"task: {job.get('task')}",
        f"classifier: {job.get('classifier')}",
        f"verification: {job.get('verification')}",
        f"worktree: {job.get('worktree')}",
        f"parent_job_id: {job.get('parent_job_id') or '-'}",
        f"retry_index: {job.get('retry_index') or 0}",
        "attempts:",
    ]
    for index, attempt in enumerate(job.get("attempts") or []):
        lines.append(f"  [{index}] {attempt}")
        if include_log and (log_attempt is None or log_attempt == index):
            log_path = attempt.get("log_path")
            if log_path and Path(log_path).exists():
                lines.append(f"  log[{index}]:")
                lines.append(Path(log_path).read_text(encoding="utf-8", errors="replace"))
    verification_log = (job.get("verification") or {}).get("log_path")
    if include_log and verification_log and Path(verification_log).exists():
        lines.append("verification_log:")
        lines.append(Path(verification_log).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(lines)
