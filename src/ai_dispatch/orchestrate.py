from __future__ import annotations

import re
import uuid
from typing import Any

from .output import summarize_result

STOP_COMPLETED = "completed"
STOP_USER_QUESTION = "user_question"
STOP_MAX_TURNS = "max_turns"
STOP_VERIFICATION_FAILED = "verification_failed"
STOP_PENDING_PERMISSION = "pending_permission"
STOP_INTERRUPTED = "interrupted"
STOP_PREFLIGHT_FAILED = "preflight_failed"

STATUS_DONE = "done"
STATUS_CONTINUE = "continue"
STATUS_BLOCKED = "blocked"

_STATUS_RE = re.compile(
    r"^\s*AI_BRIDGE_STATUS:\s*(?P<status>[A-Za-z_-]+)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_QUESTION_RE = re.compile(
    r"^\s*AI_BRIDGE_USER_QUESTION:\s*(?P<question>.+?)\s*$",
    flags=re.IGNORECASE | re.MULTILINE,
)


def new_orchestration_id() -> str:
    return uuid.uuid4().hex[:10]


def winner_stdout(job: dict[str, Any]) -> str:
    if not job.get("winner"):
        return ""
    winner = str(job["winner"])
    for att in job.get("attempts") or []:
        if str(att.get("worker")) == winner and att.get("ok"):
            return str(att.get("stdout") or "")
    return ""


def worker_status(job: dict[str, Any]) -> str | None:
    text = winner_stdout(job)
    match = _STATUS_RE.search(text)
    if not match:
        return None
    status = match.group("status").strip().lower().replace("-", "_")
    aliases = {
        "complete": STATUS_DONE,
        "completed": STATUS_DONE,
        "done": STATUS_DONE,
        "needs_followup": STATUS_CONTINUE,
        "followup": STATUS_CONTINUE,
        "continue": STATUS_CONTINUE,
        "blocked": STATUS_BLOCKED,
        "needs_input": STATUS_BLOCKED,
        "user_question": STATUS_BLOCKED,
    }
    return aliases.get(status)


def worker_user_question(job: dict[str, Any]) -> str:
    text = winner_stdout(job)
    match = _QUESTION_RE.search(text)
    if match:
        return match.group("question").strip()
    return ""


def output_suggests_user_question_block(job: dict[str, Any]) -> bool:
    """Heuristic: worker succeeded but appears to be asking the operator a question."""
    if worker_status(job) == STATUS_BLOCKED:
        return True
    if not job.get("success") or not job.get("winner"):
        return False
    text = winner_stdout(job)
    if not text.strip():
        return False
    if "?" not in text:
        return False
    lowered = text.lower()
    markers = (
        "clarify",
        "which ",
        "what ",
        "should i",
        "would you",
        "please specify",
        "need your",
        "which option",
        "confirm ",
        "prefer ",
        "could you",
        "do you want",
    )
    return any(m in lowered for m in markers)


def verification_failed(job: dict[str, Any]) -> bool:
    ver = job.get("verification") or {}
    return bool(job.get("winner")) and ver.get("status") == "failed"


def build_turn_task(
    base_task: str,
    *,
    turn: int,
    max_turns: int,
    prev_job: dict[str, Any] | None = None,
) -> str:
    previous = ""
    if prev_job is not None:
        attempts = list(prev_job.get("attempts") or [])
        detail = summarize_result(attempts[-1]) if attempts else ""
        previous = (
            "\nPrevious worker output:\n"
            f"{detail}\n"
        )
    return (
        f"{base_task.rstrip()}\n\n---\n"
        "Autonomous orchestration mode:\n"
        f"- This is turn {turn} of at most {max_turns}.\n"
        "- Continue toward the original goal without asking the user unless you are blocked by a decision only the user can make.\n"
        "- At the end of your response, include exactly one status line:\n"
        "  AI_BRIDGE_STATUS: done | continue | blocked\n"
        "- Use `done` only when the requested work is complete.\n"
        "- Use `continue` when useful work remains and another autonomous turn should continue from your output.\n"
        "- Use `blocked` only when a user decision is required. If blocked, also include:\n"
        "  AI_BRIDGE_USER_QUESTION: <one concise question>\n"
        f"{previous}"
    )


def build_followup_task(base_task: str, prev_job: dict[str, Any], next_turn: int) -> str:
    """Append autonomous follow-up context for another delegation turn."""
    max_turns = int(((prev_job.get("orchestration") or {}).get("max_turns")) or next_turn)
    return build_turn_task(
        base_task,
        turn=next_turn,
        max_turns=max_turns,
        prev_job=prev_job,
    )


def orchestration_exit_code(stop_reason: str, last_job: dict[str, Any]) -> int:
    if stop_reason == STOP_INTERRUPTED:
        return 130
    if stop_reason == STOP_PENDING_PERMISSION:
        return 2
    if stop_reason == STOP_USER_QUESTION:
        return 3
    if stop_reason == STOP_COMPLETED:
        return 0 if last_job.get("success") else 1
    return 1
