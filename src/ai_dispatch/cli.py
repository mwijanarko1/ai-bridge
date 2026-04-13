from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO

from .adapters import (
    build_prompt,
    normalize_target,
    worker_command,
    worker_supports_permission_relay,
)
from .jobs import (
    DEFAULT_TIMEOUT,
    LOGS_DIR,
    permission_response_path,
    default_artifacts,
    default_permission_state,
    default_verification,
    default_worktree,
    detect_repo_root,
    ensure_state_root,
    generate_job_id,
    iter_jobs,
    job_path,
    list_jobs,
    load_job,
    make_attempt,
    now_ts,
    save_job,
    summarize_task,
)
from .output import format_job_list, format_job_show, json_output, summarize_result
from .routing import classify_task, route_task
from . import orchestrate as orchestrate_mod
from .verify import prepare_verification, run_verification
from .worktree import cleanup_worktree, prepare_worktree


def failure_like(result: dict[str, Any]) -> bool:
    if result["exit_code"] != 0:
        return True
    stdout_text = str(result.get("stdout") or "").lower()
    stderr_text = str(result.get("stderr") or "").lower()
    soft_failure_patterns = [
        r"^\s*(could not complete|cannot complete|can't complete|unable to complete)\b",
        r"^\s*ran into this error\b",
        r"^\s*execution error:",
    ]
    hard_error_markers = [
        "permission denied",
        "timed out",
        "unexpected argument",
        "usage:",
        "not logged in",
    ]
    if any(
        re.search(pattern, stdout_text, flags=re.MULTILINE)
        for pattern in soft_failure_patterns
    ):
        return True
    if stdout_text.strip():
        return False
    return any(marker in stderr_text for marker in hard_error_markers)


def _terminate_worker_process(proc: subprocess.Popen[str]) -> None:
    try:
        if proc.poll() is None and proc.pid:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


PERMISSION_WAITING_STATUS = "pending_permission"
PERMISSION_POLL_INTERVAL = 0.1
UNSET = object()
PUBLIC_COMMANDS = {
    "run",
    "orchestrate",
    "list",
    "show",
    "retry",
    "watch",
    "classify",
    "route",
    "cleanup-worktree",
    "__monitor__",
    "poll-completions",
    "job-status",
    "permission-response",
}
DISPATCH_DESCRIPTION = (
    "Delegate coding work to another local AI worker, track the job, and inspect "
    "or retry the result."
)
TOP_LEVEL_EPILOG = """\
Common commands:
  {prog} --target cursor --cwd "$PWD" -- "Fix the failing parser tests"
  {prog} run --target opencode -- "Rename the settings toggle label"
  {prog} orchestrate --target cursor --max-turns 5 -- "Implement the PRD"
  {prog} list --status running
  {prog} show <job_id> --log
  {prog} retry <job_id> --feedback "Keep the diff smaller"
  {prog} watch <job_id>

Targets:
  auto routes across the primary workers: codex, claude, cursor, opencode.
  Explicit targets may also include goose, qwen, or adapter-configured tools.

Notes:
  ai-delegate is the preferred alias for the default run command.
  Put -- before the task prompt when the prompt may start with an option-like dash.
  Use '<command> --help' for command-specific instructions.
"""
RUN_EPILOG = """\
Examples:
  {prog} --target auto --difficulty hard --cwd "$PWD" -- "Debug the race condition"
  {prog} run --target cursor --verify default -- "Refactor the auth flow"
  {prog} run --target opencode --background --notify-on-complete -- "Add a docs note"

Instructions:
  Use --target auto for default routing across codex, claude, cursor, and opencode.
  Use --target <name> to force a specific worker.
  Use --verify default|quick|full to run a configured verification profile after success.
  Use --worktree auto or --worktree branch:<name> for opt-in worktree isolation.
  Put -- before the task prompt when the prompt may start with an option-like dash.
"""
ORCHESTRATE_EPILOG = """\
Examples:
  {prog} orchestrate --target cursor --max-turns 5 --cwd "$PWD" -- "Implement section 3"
  {prog} orchestrate --target auto --verify default --max-turns 3 -- "Finish the migration"

Instructions:
  Each turn creates a normal job linked to the previous turn.
  Foreground non-JSON mode prints a live Codex -> worker / worker -> Codex transcript.
  The worker should finish with AI_BRIDGE_STATUS: done, continue, or blocked.
  Orchestration stops on success, verification failure, permission prompts, user questions, or max turns.
  --background is not supported for orchestrate.
"""


def _program_name() -> str:
    return os.environ.get("AI_DISPATCH_PROG", "ai-dispatch").strip() or "ai-dispatch"


def _help_text(template: str, *, prog: str) -> str:
    return template.format(prog=prog)


def normalize_permission_policy(policy: str | None) -> str:
    clean = str(policy or "").strip().lower()
    return clean if clean in {"relay", "skip", "deny"} else "skip"


def effective_permission_policy(job: dict[str, Any], worker: str) -> str:
    requested = normalize_permission_policy(
        (job.get("permissions") or {}).get("policy")
    )
    if requested == "skip":
        return "skip"
    if worker_supports_permission_relay(worker):
        return requested
    return "skip"


def permission_prompt_excerpt(worker: str, output: str) -> str | None:
    if normalize_target(worker) != "codex":
        return None
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    prompt_markers = ("[y/n]", "[y/N]", "(y/n)", "(y/N)", "yes/no")
    for line in reversed(lines[-8:]):
        lowered = line.lower()
        if (
            "allow" in lowered or "approve" in lowered or "permission" in lowered
        ) and any(marker.lower() in lowered for marker in prompt_markers):
            return line[:400]
    return None


def write_permission_response(job_id: str, decision: str) -> Path:
    path = permission_response_path(job_id)
    payload = {"decision": decision, "responded_at": now_ts()}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def consume_permission_response(job_id: str) -> str | None:
    path = permission_response_path(job_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        path.unlink(missing_ok=True)
        return None
    path.unlink(missing_ok=True)
    decision = str(payload.get("decision") or "").strip().lower()
    if decision in {"allow", "approve", "yes", "y"}:
        return "allow"
    if decision in {"deny", "no", "n"}:
        return "deny"
    return None


def update_job_permission_state(
    job_id: str,
    *,
    status: str | None = None,
    pending: dict[str, Any] | None | object = UNSET,
    event: dict[str, Any] | None = None,
) -> None:
    job = load_job(job_id)
    permissions = job.get("permissions") or default_permission_state("skip")
    if status:
        job["status"] = status
    if pending is not UNSET:
        permissions["pending"] = pending
    if event:
        permissions.setdefault("events", []).append(event)
    job["permissions"] = permissions
    save_job(job)


def wait_for_permission_decision(job_id: str, proc: subprocess.Popen[bytes]) -> str:
    while True:
        decision = consume_permission_response(job_id)
        if decision:
            return decision
        if proc.poll() is not None:
            return "deny"
        time.sleep(PERMISSION_POLL_INTERVAL)


def emit_live_output(text: str, stream: TextIO | None) -> None:
    if not text or stream is None:
        return
    stream.write(text)
    stream.flush()


def collect_result(
    worker: str,
    command: list[str],
    cwd: str,
    timeout: int,
    job_id: str,
    attempt_index: int,
    *,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    started = now_ts()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{job_id}-{attempt_index:02d}-{worker}.log"
    output_chunks: list[str] = []
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
        deadline = started + timeout
        try:
            assert proc.stdout is not None
            while True:
                if time.time() >= deadline:
                    _terminate_worker_process(proc)
                    try:
                        proc.wait(timeout=5)
                    except BaseException:
                        pass
                    exit_code = 124
                    log_handle.write("\n[ai-dispatch] timed out\n")
                    break

                try:
                    ready, _, _ = select.select([proc.stdout], [], [], PERMISSION_POLL_INTERVAL)
                except (AttributeError, OSError, TypeError, ValueError):
                    exit_code = int(
                        proc.wait(timeout=max(deadline - time.time(), PERMISSION_POLL_INTERVAL)) or 0
                    )
                    break
                if not ready:
                    if proc.poll() is not None:
                        exit_code = int(proc.returncode or 0)
                        break
                    continue

                try:
                    data = os.read(proc.stdout.fileno(), 4096)
                except (AttributeError, OSError, TypeError, ValueError):
                    exit_code = int(
                        proc.wait(timeout=max(deadline - time.time(), PERMISSION_POLL_INTERVAL)) or 0
                    )
                    break
                if data:
                    text = data.decode("utf-8", errors="replace")
                    output_chunks.append(text)
                    log_handle.write(text)
                    log_handle.flush()
                    emit_live_output(text, stream)
                    continue

                exit_code = int(proc.wait(timeout=0))
                break
        except subprocess.TimeoutExpired:
            _terminate_worker_process(proc)
            try:
                proc.wait(timeout=5)
            except BaseException:
                pass
            exit_code = 124
            log_handle.write("\n[ai-dispatch] timed out\n")
        except KeyboardInterrupt:
            _terminate_worker_process(proc)
            try:
                proc.wait(timeout=5)
            except BaseException:
                pass
            exit_code = 130
            log_handle.write("\n[ai-dispatch] interrupted\n")
        finally:
            if proc.stdout is not None:
                try:
                    remaining = os.read(proc.stdout.fileno(), 4096)
                except OSError:
                    remaining = b""
                if remaining:
                    text = remaining.decode("utf-8", errors="replace")
                    output_chunks.append(text)
                    log_handle.write(text)
                    emit_live_output(text, stream)
                log_handle.flush()
    finished = now_ts()
    combined = "".join(output_chunks).strip()
    if not combined and log_path.exists():
        combined = log_path.read_text(encoding="utf-8", errors="replace").strip()
    return make_attempt(
        worker=worker,
        command=command,
        exit_code=exit_code,
        duration_seconds=finished - started,
        stdout=combined if exit_code == 0 else "",
        stderr="" if exit_code == 0 else combined.strip(),
        ok=exit_code == 0,
        log_path=str(log_path),
    )


def collect_interactive_result(
    worker: str,
    command: list[str],
    cwd: str,
    timeout: int,
    job_id: str,
    attempt_index: int,
    *,
    permission_policy: str,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    started = now_ts()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{job_id}-{attempt_index:02d}-{worker}.log"
    output_chunks: list[str] = []
    last_prompt_excerpt = ""

    master_fd, slave_fd = pty.openpty()
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        exit_code = 0
        deadline = started + timeout

        try:
            while True:
                if time.time() >= deadline:
                    _terminate_worker_process(proc)
                    try:
                        proc.wait(timeout=5)
                    except BaseException:
                        pass
                    exit_code = 124
                    log_handle.write("\n[ai-dispatch] timed out\n")
                    break

                ready, _, _ = select.select(
                    [master_fd], [], [], PERMISSION_POLL_INTERVAL
                )
                if ready:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        data = b""
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        output_chunks.append(text)
                        log_handle.write(text)
                        log_handle.flush()
                        emit_live_output(text, stream)
                        excerpt = permission_prompt_excerpt(
                            worker, "".join(output_chunks)[-4000:]
                        )
                        if excerpt and excerpt != last_prompt_excerpt:
                            pending = {
                                "worker": worker,
                                "attempt_index": attempt_index,
                                "prompt": excerpt,
                                "detected_at": now_ts(),
                            }
                            update_job_permission_state(
                                job_id,
                                status=PERMISSION_WAITING_STATUS,
                                pending=pending,
                            )
                            decision = "deny" if permission_policy == "deny" else None
                            if permission_policy == "relay":
                                decision = wait_for_permission_decision(job_id, proc)
                            event = {
                                "worker": worker,
                                "attempt_index": attempt_index,
                                "prompt": excerpt,
                                "decision": decision or "allow",
                                "recorded_at": now_ts(),
                            }
                            update_job_permission_state(
                                job_id, status="running", pending=None, event=event
                            )
                            last_prompt_excerpt = excerpt
                            os.write(
                                master_fd,
                                b"y\n" if (decision or "allow") == "allow" else b"n\n",
                            )
                            continue
                    elif proc.poll() is not None:
                        exit_code = int(proc.returncode or 0)
                        break

                if proc.poll() is not None:
                    exit_code = int(proc.returncode or 0)
                    break
        except KeyboardInterrupt:
            _terminate_worker_process(proc)
            try:
                proc.wait(timeout=5)
            except BaseException:
                pass
            exit_code = 130
            log_handle.write("\n[ai-dispatch] interrupted\n")
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass

    finished = now_ts()
    combined = "".join(output_chunks).strip()
    return make_attempt(
        worker=worker,
        command=command,
        exit_code=exit_code,
        duration_seconds=finished - started,
        stdout=combined if exit_code == 0 else "",
        stderr=""
        if exit_code == 0
        else combined or log_path.read_text(encoding="utf-8", errors="replace").strip(),
        ok=exit_code == 0,
        log_path=str(log_path),
    )


def preflight_job(
    job: dict[str, Any], *, verify_config: str | None, worktree_mode: str
) -> dict[str, Any]:
    try:
        verification = prepare_verification(
            job["verify_mode"], job["cwd"], override_path=verify_config
        )
        worktree = prepare_worktree(job["job_id"], job["cwd"], worktree_mode)
        execution_cwd = worktree["path"] or job["cwd"]
    except Exception as exc:
        job["status"] = "failed"
        job["finished_at"] = now_ts()
        job["attempts"] = [
            make_attempt(worker="preflight", exit_code=2, stderr=str(exc), ok=False)
        ]
        job["success"] = False
        job["winner"] = None
        save_job(job)
        return job

    verification.pop("command_list", None)
    job["verification"] = verification
    job["worktree"] = worktree
    job["execution_cwd"] = execution_cwd
    save_job(job)
    return job


def run_sync(
    job: dict[str, Any],
    *,
    verify_config: str | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    if job.get("status") == "failed" and job.get("attempts"):
        return job

    attempts: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None
    execution_cwd = job.get("execution_cwd") or job["cwd"]
    interrupted = False

    try:
        for index, worker in enumerate(job["route"]):
            prompt = build_prompt(
                job["task"], job["difficulty"], execution_cwd, job["from_agent"], worker
            )
            permission_policy = effective_permission_policy(job, worker)
            try:
                command = worker_command(
                    worker,
                    prompt,
                    execution_cwd,
                    job["from_agent"],
                    job["difficulty"],
                    permission_policy=permission_policy,
                )
                if worker_supports_permission_relay(worker) and permission_policy in {
                    "relay",
                    "deny",
                }:
                    result = collect_interactive_result(
                        worker,
                        command,
                        execution_cwd,
                        int(job["timeout"]),
                        job["job_id"],
                        index,
                        permission_policy=permission_policy,
                        stream=stream,
                    )
                else:
                    result = collect_result(
                        worker,
                        command,
                        execution_cwd,
                        int(job["timeout"]),
                        job["job_id"],
                        index,
                        stream=stream,
                    )
            except KeyboardInterrupt:
                interrupted = True
                result = make_attempt(
                    worker=worker,
                    exit_code=130,
                    stderr="[ai-dispatch] interrupted",
                    ok=False,
                )
            except Exception as exc:
                result = make_attempt(
                    worker=worker, exit_code=2, stderr=str(exc), ok=False
                )
            attempts.append(result)
            if interrupted or int(result.get("exit_code") or 0) == 130:
                interrupted = True
                break
            if not failure_like(result):
                winner = result
                break
    except KeyboardInterrupt:
        interrupted = True

    job["attempts"] = attempts
    job["artifacts"]["attempt_logs"] = [
        item["log_path"] for item in attempts if item.get("log_path")
    ]
    job["winner"] = winner["worker"] if winner else None
    job["success"] = winner is not None
    job["status"] = "completed" if winner else "failed"
    job["finished_at"] = now_ts()
    job["interrupted"] = interrupted or any(
        int(a.get("exit_code") or 0) == 130 for a in attempts
    )
    job["monitor_pid"] = None
    try:
        latest_job = load_job(job["job_id"])
    except FileNotFoundError:
        latest_job = job
    permissions = latest_job.get("permissions") or default_permission_state("skip")
    permissions["pending"] = None
    job["permissions"] = permissions

    if winner and job.get("verify_mode") != "off":
        verification_plan = prepare_verification(
            job["verify_mode"], job["cwd"], override_path=verify_config
        )
        job["verification"] = run_verification(
            {"job_id": job["job_id"], "verification": verification_plan},
            cwd=execution_cwd,
        )
        job["artifacts"]["verification_log"] = job["verification"]["log_path"]
        if job["verification"]["status"] != "passed":
            job["success"] = False
            job["status"] = "failed"
    else:
        job["verification"] = default_verification("off")

    save_job(job)
    return job


def spawn_monitor(job: dict[str, Any], *, verify_config: str | None) -> int:
    env = os.environ.copy()
    if verify_config:
        env["AI_BRIDGE_VERIFY_CONFIG_OVERRIDE"] = verify_config
    dispatch_bin = Path(__file__).resolve().parents[2] / "bin" / "ai-dispatch"
    proc = subprocess.Popen(
        [str(dispatch_bin), "__monitor__", job["job_id"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=job["cwd"],
        start_new_session=True,
        env=env,
    )
    return int(proc.pid)


def terminate_monitor(job: dict[str, Any]) -> None:
    pid = int(job.get("monitor_pid") or 0)
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        pass


def stream_job_logs(job_id: str, offsets: dict[str, int], *, stream: TextIO | None) -> None:
    for path in sorted(LOGS_DIR.glob(f"{job_id}-*.log")):
        key = str(path)
        start = offsets.get(key, 0)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        if size <= start:
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(start)
            emit_live_output(handle.read(), stream)
        offsets[key] = size


def wait_for_job_state(
    job_id: str,
    *,
    statuses: set[str],
    timeout: float,
    stream_logs: bool = False,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_job: dict[str, Any] | None = None
    log_offsets: dict[str, int] = {}
    while time.time() < deadline:
        last_job = load_job(job_id)
        if stream_logs:
            stream_job_logs(job_id, log_offsets, stream=stream)
        if last_job.get("status") in statuses:
            if stream_logs:
                stream_job_logs(job_id, log_offsets, stream=stream)
            return last_job
        time.sleep(PERMISSION_POLL_INTERVAL)
    if stream_logs:
        stream_job_logs(job_id, log_offsets, stream=stream)
    return last_job or load_job(job_id)


def run_monitor(job_id: str) -> int:
    job = load_job(job_id)
    if job.get("status") == "failed" and job.get("attempts"):
        return 1
    job["status"] = "running"
    job["started_at"] = now_ts()
    save_job(job)
    verify_override = os.environ.get("AI_BRIDGE_VERIFY_CONFIG_OVERRIDE")
    run_sync(job, verify_config=verify_override)
    return 0


def poll_completions(
    session_key: str, limit: int = 8, mark_seen: bool = True
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for job in iter_jobs():
        if session_key and job.get("session_key") != session_key:
            continue
        if not job.get("notify_on_complete"):
            continue
        if job.get("status") not in {"completed", "failed"}:
            continue
        if job.get("completion_seen_at"):
            continue
        matches.append(job)
        if len(matches) >= max(1, min(limit, 100)):
            break
    if mark_seen and matches:
        seen_at = now_ts()
        for job in matches:
            job["completion_seen_at"] = seen_at
            save_job(job)
    return {"jobs": matches}


def create_job(args: argparse.Namespace) -> dict[str, Any]:
    task = " ".join(args.task).strip()
    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    repo_root = detect_repo_root(cwd)
    if repo_root:
        cwd = repo_root

    route_info = route_task(
        task=task, target=args.target, requested_difficulty=args.difficulty, cwd=cwd
    )
    job = {
        "job_id": generate_job_id(),
        "target": args.target,
        "from_agent": args.from_agent,
        "session_key": args.session_key
        or os.environ.get("AI_PEERS_SESSION_KEY", "").strip(),
        "notify_on_complete": bool(args.notify_on_complete),
        "difficulty": route_info["difficulty"],
        "cwd": cwd,
        "execution_cwd": cwd,
        "task": task,
        "task_summary": summarize_task(task),
        "route": route_info["route"],
        "route_reason": route_info["route_reason"],
        "timeout": int(args.timeout),
        "created_at": now_ts(),
        "started_at": None,
        "finished_at": None,
        "status": "queued",
        "winner": None,
        "success": False,
        "attempts": [],
        "completion_seen_at": None,
        "parent_job_id": getattr(args, "parent_job_id", None),
        "retry_index": int(getattr(args, "retry_index", 0)),
        "classifier": route_info["classifier"],
        "scores": route_info["scores"],
        "routing_config": route_info["routing_config"],
        "verify_mode": args.verify,
        "requested_worktree_mode": args.worktree,
        "verification": default_verification(args.verify),
        "permissions": default_permission_state(
            normalize_permission_policy(getattr(args, "permissions", "skip"))
        ),
        "worktree": default_worktree(args.worktree),
        "artifacts": default_artifacts(),
    }
    save_job(job)
    job["artifacts"]["job_file"] = str(job_path(job["job_id"]))
    save_job(job)
    return preflight_job(
        job, verify_config=args.verify_config, worktree_mode=args.worktree
    )


def format_started(job: dict[str, Any]) -> str:
    return (
        f"[ai-dispatch] started background job={job['job_id']} route={','.join(job['route'])} "
        f"difficulty={job['difficulty']} job_file={job_path(job['job_id'])}"
    )


def _help_parser(
    *,
    prog: str,
    description: str | None = None,
    epilog: str | None = None,
) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def build_orchestrate_parser(parser: argparse.ArgumentParser) -> None:
    build_run_parser(parser)
    parser.add_argument(
        "--max-turns",
        type=int,
        default=5,
        metavar="N",
        help="Maximum autonomous delegation rounds (each round creates one job). Default: 5.",
    )


def build_run_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        default="auto",
        help="Explicitly target one worker, or use auto routing.",
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "hard", "auto"],
        default="auto",
        help="Used by auto routing. Explicit values override classifier complexity.",
    )
    parser.add_argument(
        "--cwd", default=os.getcwd(), help="Working directory for the delegated worker."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Per-worker timeout in seconds.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit full JSON instead of a human summary."
    )
    parser.add_argument(
        "--from-agent",
        default=os.environ.get("AI_DISPATCH_SOURCE", "unknown-agent"),
        help="Name of the calling agent for prompt context.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Run the delegation job in the background.",
    )
    parser.add_argument(
        "--notify-on-complete",
        action="store_true",
        help="If backgrounded, queue a completion notice for the calling agent session.",
    )
    parser.add_argument(
        "--session-key",
        default=os.environ.get("AI_PEERS_SESSION_KEY", ""),
        help="Stable session key for completion notifications.",
    )
    parser.add_argument(
        "--verify",
        choices=["off", "default", "quick", "full"],
        default="off",
        help="Optional verification profile to run after a successful worker result.",
    )
    parser.add_argument(
        "--verify-config", help="Override path for verification config JSON."
    )
    parser.add_argument(
        "--permissions",
        choices=["relay", "skip", "deny"],
        default=os.environ.get("AI_DISPATCH_PERMISSION_POLICY", "relay"),
        help="How the top-level agent should handle worker permission prompts when supported.",
    )
    parser.add_argument(
        "--worktree",
        default="off",
        help="Worktree mode: off, auto, or branch:<name>.",
    )
    parser.add_argument("task", nargs="+", help="Task prompt to delegate.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    prog = _program_name()
    if argv and argv[0] in {"-h", "--help"}:
        parser = build_command_parser(prog=prog, include_internal=False)
        return parser.parse_args(argv)

    if not argv or argv[0] not in PUBLIC_COMMANDS:
        parser = _help_parser(
            prog=prog,
            description=DISPATCH_DESCRIPTION,
            epilog=_help_text(RUN_EPILOG, prog=prog),
        )
        build_run_parser(parser)
        args = parser.parse_args(argv)
        args.command = "run"
        return args

    parser = build_command_parser(prog=prog)
    return parser.parse_args(argv)


def build_command_parser(
    *, prog: str = "ai-dispatch", include_internal: bool = True
) -> argparse.ArgumentParser:
    parser = _help_parser(
        prog=prog,
        description=DISPATCH_DESCRIPTION,
        epilog=_help_text(TOP_LEVEL_EPILOG, prog=prog),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="command",
        title="commands",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="delegate one task and optionally verify the result",
        description="Delegate one task to a worker and store the job result.",
        epilog=_help_text(RUN_EPILOG, prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    build_run_parser(run_parser)

    orchestrate_parser = subparsers.add_parser(
        "orchestrate",
        help="run bounded multi-turn delegation on one task",
        description="Run sequential delegation turns until the task completes or stops.",
        epilog=_help_text(ORCHESTRATE_EPILOG, prog=prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    build_orchestrate_parser(orchestrate_parser)

    list_parser = subparsers.add_parser(
        "list",
        help="list stored jobs",
        description="List stored delegation jobs.",
    )
    list_parser.add_argument(
        "--status",
        choices=[
            "queued",
            "running",
            "pending_permission",
            "completed",
            "failed",
            "all",
        ],
        default="all",
    )
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--session-key")
    list_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser(
        "show",
        help="show one job, optionally including logs",
        description="Show a stored delegation job.",
    )
    show_parser.add_argument("job_id")
    show_parser.add_argument("--attempt", type=int)
    show_parser.add_argument("--log", action="store_true")
    show_parser.add_argument("--json", action="store_true")

    retry_parser = subparsers.add_parser(
        "retry",
        help="retry a job with optional feedback",
        description="Retry a stored job, preserving its routing and verification options.",
    )
    retry_parser.add_argument("job_id")
    retry_parser.add_argument("--feedback")
    retry_parser.add_argument("--background", action="store_true")
    retry_parser.add_argument("--notify-on-complete", action="store_true")
    retry_parser.add_argument("--json", action="store_true")

    watch_parser = subparsers.add_parser(
        "watch",
        help="watch a job or active session jobs",
        description="Watch a job until it reaches a terminal or waiting state.",
    )
    watch_parser.add_argument("job_id", nargs="?")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--json", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=2.0)

    classify_parser = subparsers.add_parser(
        "classify",
        help="classify a task for routing",
        description="Classify a task without creating a job.",
    )
    classify_parser.add_argument("task")
    classify_parser.add_argument(
        "--difficulty", choices=["easy", "hard", "auto"], default="auto"
    )
    classify_parser.add_argument("--json", action="store_true")

    route_parser = subparsers.add_parser(
        "route",
        help="preview the worker route for a task",
        description="Preview routing without creating a job.",
    )
    route_parser.add_argument("task")
    route_parser.add_argument("--target", default="auto")
    route_parser.add_argument(
        "--difficulty", choices=["easy", "hard", "auto"], default="auto"
    )
    route_parser.add_argument("--cwd", default=os.getcwd())
    route_parser.add_argument("--json", action="store_true")

    cleanup_parser = subparsers.add_parser(
        "cleanup-worktree",
        help="remove a retained job worktree",
        description="Remove a retained worktree for a job.",
    )
    cleanup_parser.add_argument("job_id")
    cleanup_parser.add_argument("--json", action="store_true")

    if include_internal:
        monitor_parser = subparsers.add_parser("__monitor__")
        monitor_parser.add_argument("job_id")

        poll_parser = subparsers.add_parser(
            "poll-completions",
            description="Poll unseen completion notifications for a session.",
        )
        poll_parser.add_argument(
            "--session-key", default=os.environ.get("AI_PEERS_SESSION_KEY", "")
        )
        poll_parser.add_argument("--limit", type=int, default=8)
        poll_parser.add_argument("--keep-unseen", action="store_true")

        status_parser = subparsers.add_parser(
            "job-status",
            description="Emit one job status as JSON.",
        )
        status_parser.add_argument("job_id")

    perm_parser = subparsers.add_parser(
        "permission-response",
        help="answer a pending worker permission prompt",
        description="Answer a pending worker permission prompt.",
    )
    perm_parser.add_argument("job_id")
    perm_parser.add_argument("decision", choices=["allow", "deny"])
    perm_parser.add_argument("--json", action="store_true")

    return parser


def complete_job_sync(
    job: dict[str, Any],
    args: argparse.Namespace,
    *,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    """Run a preflight-clean job synchronously (workers + optional verification)."""
    needs_permission_monitor = any(
        worker_supports_permission_relay(worker)
        and effective_permission_policy(job, worker) in {"relay", "deny"}
        for worker in job["route"]
    )

    if needs_permission_monitor:
        job["status"] = "running"
        job["started_at"] = now_ts()
        save_job(job)
        job["monitor_pid"] = spawn_monitor(job, verify_config=args.verify_config)
        save_job(job)
        try:
            job = wait_for_job_state(
                job["job_id"],
                statuses={PERMISSION_WAITING_STATUS, "completed", "failed"},
                timeout=max(float(job["timeout"]) + 5.0, 5.0),
                stream_logs=stream is not None,
                stream=stream,
            )
        except KeyboardInterrupt:
            terminate_monitor(job)
            interrupted_job = load_job(job["job_id"])
            attempts = list(interrupted_job.get("attempts") or [])
            if not attempts:
                attempts = [
                    make_attempt(
                        worker=(job.get("route") or ["unknown"])[0],
                        exit_code=130,
                        stderr="[ai-dispatch] interrupted",
                        ok=False,
                    )
                ]
            interrupted_job["attempts"] = attempts
            interrupted_job["status"] = "failed"
            interrupted_job["success"] = False
            interrupted_job["winner"] = None
            interrupted_job["finished_at"] = now_ts()
            interrupted_job["interrupted"] = True
            save_job(interrupted_job)
            job = interrupted_job
    else:
        job["status"] = "running"
        job["started_at"] = now_ts()
        save_job(job)
        job = run_sync(job, verify_config=args.verify_config, stream=stream)
    return job


def handle_run(args: argparse.Namespace) -> int:
    job = create_job(args)
    if job.get("status") == "failed" and job.get("attempts"):
        if args.json:
            print(json_output(job))
        else:
            print(
                f"[ai-dispatch] preflight failed job_file={job_path(job['job_id'])}\n"
                f"{summarize_result(job['attempts'][0])}"
            )
        return 1

    if args.background:
        job["monitor_pid"] = spawn_monitor(job, verify_config=args.verify_config)
        save_job(job)
        if args.json:
            print(json_output(job))
        else:
            print(format_started(job))
        return 0

    stream = sys.stdout if not args.json else None
    job = complete_job_sync(job, args, stream=stream)
    if args.json:
        print(json_output(job))
        if job.get("interrupted"):
            return 130
        if job.get("status") == PERMISSION_WAITING_STATUS:
            return 2
        return 0 if job["success"] else 1
    if job.get("interrupted"):
        detail = summarize_result(job["attempts"][-1]) if job["attempts"] else ""
        print(f"[ai-dispatch] interrupted job_file={job_path(job['job_id'])}\n{detail}")
        return 130
    if job["success"]:
        winner = next(
            attempt for attempt in job["attempts"] if attempt["worker"] == job["winner"]
        )
        print(
            f"[ai-dispatch] winner={winner['worker']} difficulty={job['difficulty']} job_file={job_path(job['job_id'])}\n"
            f"{summarize_result(winner)}"
        )
        return 0
    if job.get("status") == PERMISSION_WAITING_STATUS:
        pending = (job.get("permissions") or {}).get("pending") or {}
        print(
            f"[ai-dispatch] pending permission job={job['job_id']} "
            f"job_file={job_path(job['job_id'])}\n"
            f"{pending.get('prompt') or 'Delegated worker is waiting for a permission decision.'}\n"
            f"Respond with: ai-dispatch permission-response {job['job_id']} allow|deny"
        )
        return 2
    detail = summarize_result(job["attempts"][0]) if job["attempts"] else ""
    print(
        f"[ai-dispatch] all delegated workers failed difficulty={job['difficulty']} job_file={job_path(job['job_id'])}\n"
        f"{detail}\nThe calling agent should handle this task directly."
    )
    return 1


def handle_orchestrate(args: argparse.Namespace) -> int:
    max_turns = int(args.max_turns)
    if max_turns < 1:
        print(
            "[ai-dispatch] orchestrate: --max-turns must be at least 1.",
            file=sys.stderr,
        )
        return 2

    if args.background:
        print(
            "[ai-dispatch] orchestrate does not support --background; run a single job with "
            "`ai-dispatch run --background` instead.",
            file=sys.stderr,
        )
        return 2

    base_task = " ".join(args.task).strip()
    orch_id = orchestrate_mod.new_orchestration_id()
    current_task = orchestrate_mod.build_turn_task(
        base_task,
        turn=1,
        max_turns=max_turns,
    )
    last_job: dict[str, Any] | None = None
    stop_reason = orchestrate_mod.STOP_MAX_TURNS
    stream = sys.stdout if not args.json else None

    for turn in range(1, max_turns + 1):
        run_ns = argparse.Namespace(
            target=args.target,
            difficulty=args.difficulty,
            cwd=args.cwd,
            timeout=args.timeout,
            json=args.json,
            from_agent=args.from_agent,
            background=False,
            notify_on_complete=args.notify_on_complete,
            session_key=args.session_key,
            verify=args.verify,
            verify_config=args.verify_config,
            permissions=args.permissions,
            worktree=args.worktree,
            task=[current_task],
            parent_job_id=last_job["job_id"] if last_job else None,
            retry_index=turn - 1,
        )

        job = create_job(run_ns)
        if job.get("status") == "failed" and job.get("attempts"):
            last_job = job
            stop_reason = orchestrate_mod.STOP_PREFLIGHT_FAILED
            break

        job["orchestration"] = {
            "id": orch_id,
            "turn": turn,
            "max_turns": max_turns,
            "base_task": base_task,
        }
        save_job(job)

        worker_stream: TextIO | None = None
        if stream is not None:
            orchestrate_mod.write_live_turn_header(
                stream,
                orchestration_id=orch_id,
                turn=turn,
                max_turns=max_turns,
                from_agent=args.from_agent,
                route=job.get("route") or [],
                job_id=job["job_id"],
                task=current_task,
            )
            worker_stream = orchestrate_mod.LiveConversationStream(
                stream, orchestrate_mod.route_label(job.get("route") or [])
            )

        job = complete_job_sync(job, run_ns, stream=worker_stream)
        if worker_stream is not None:
            worker_stream.finish()
        last_job = job

        if job.get("interrupted"):
            stop_reason = orchestrate_mod.STOP_INTERRUPTED
            break
        if job.get("status") == PERMISSION_WAITING_STATUS:
            stop_reason = orchestrate_mod.STOP_PENDING_PERMISSION
            break
        if orchestrate_mod.verification_failed(job):
            stop_reason = orchestrate_mod.STOP_VERIFICATION_FAILED
            break
        if job.get("success"):
            worker_status = orchestrate_mod.worker_status(job)
            if (
                worker_status == orchestrate_mod.STATUS_BLOCKED
                or orchestrate_mod.output_suggests_user_question_block(job)
            ):
                stop_reason = orchestrate_mod.STOP_USER_QUESTION
            elif worker_status == orchestrate_mod.STATUS_CONTINUE and turn < max_turns:
                current_task = orchestrate_mod.build_turn_task(
                    base_task,
                    turn=turn + 1,
                    max_turns=max_turns,
                    prev_job=job,
                )
                continue
            elif worker_status == orchestrate_mod.STATUS_CONTINUE:
                stop_reason = orchestrate_mod.STOP_MAX_TURNS
            else:
                stop_reason = orchestrate_mod.STOP_COMPLETED
            break
        if turn >= max_turns:
            stop_reason = orchestrate_mod.STOP_MAX_TURNS
            break
        current_task = orchestrate_mod.build_turn_task(
            base_task,
            turn=turn + 1,
            max_turns=max_turns,
            prev_job=job,
        )

    if last_job is None:
        print("[ai-dispatch] orchestrate: no job was created.", file=sys.stderr)
        return 1

    exit_code = orchestrate_mod.orchestration_exit_code(stop_reason, last_job)
    summary = {
        "orchestration_id": orch_id,
        "stop_reason": stop_reason,
        "last_job_id": last_job["job_id"],
        "last_job_file": str(job_path(last_job["job_id"])),
    }

    if args.json:
        print(json_output({"orchestration": summary, "job": last_job}))
        return exit_code

    print(
        f"[ai-dispatch] orchestrate stop={stop_reason} id={orch_id} "
        f"last_job={last_job['job_id']} job_file={job_path(last_job['job_id'])}"
    )
    if stop_reason == orchestrate_mod.STOP_COMPLETED and last_job.get("success"):
        winner_id = last_job.get("winner")
        winner_attempt = next(
            (a for a in (last_job.get("attempts") or []) if a.get("worker") == winner_id),
            None,
        )
        if winner_attempt:
            print(
                f"[ai-dispatch] winner={winner_attempt['worker']} "
                f"difficulty={last_job['difficulty']}\n{summarize_result(winner_attempt)}"
            )
    elif stop_reason == orchestrate_mod.STOP_PENDING_PERMISSION:
        pending = (last_job.get("permissions") or {}).get("pending") or {}
        print(
            f"{pending.get('prompt') or 'Worker is waiting for a permission decision.'}\n"
            f"Respond with: ai-dispatch permission-response {last_job['job_id']} allow|deny"
        )
    elif stop_reason == orchestrate_mod.STOP_USER_QUESTION:
        w = last_job.get("winner")
        att = next(
            (a for a in (last_job.get("attempts") or []) if a.get("worker") == w),
            None,
        )
        detail = summarize_result(att) if att else ""
        question = orchestrate_mod.worker_user_question(last_job)
        print(
            "[ai-dispatch] orchestrate paused: worker output looks like a question for the user.\n"
            f"{question or detail}\n"
            f"Continue manually or retry with `ai-dispatch retry {last_job['job_id']} --feedback \"...\"`."
        )
    elif stop_reason == orchestrate_mod.STOP_VERIFICATION_FAILED:
        ver = last_job.get("verification") or {}
        print(
            f"[ai-dispatch] verification failed profile={ver.get('profile')!r} "
            f"exit_code={ver.get('exit_code')!r}\n{ver.get('summary') or ''}"
        )
    elif stop_reason == orchestrate_mod.STOP_PREFLIGHT_FAILED and last_job.get("attempts"):
        print(summarize_result(last_job["attempts"][0]))
    elif stop_reason in {
        orchestrate_mod.STOP_MAX_TURNS,
        orchestrate_mod.STOP_INTERRUPTED,
    }:
        if last_job.get("attempts"):
            print(summarize_result(last_job["attempts"][-1]))

    return exit_code


def handle_list(args: argparse.Namespace) -> int:
    jobs = list_jobs(status=args.status, limit=args.limit, session_key=args.session_key)
    if args.json:
        print(json_output({"jobs": jobs}))
    else:
        print(format_job_list(jobs))
    return 0


def handle_show(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    if args.json:
        print(json_output(job))
    else:
        print(format_job_show(job, log_attempt=args.attempt, include_log=args.log))
    return 0


def handle_retry(args: argparse.Namespace) -> int:
    original = load_job(args.job_id)
    task = original["task"]
    if args.feedback:
        task = f"{task}\n\nFollow-up feedback:\n{args.feedback.strip()}"

    retry_args = argparse.Namespace(
        target=original["target"],
        difficulty=original["difficulty"],
        cwd=original["cwd"],
        timeout=original["timeout"],
        json=args.json,
        from_agent=original["from_agent"],
        background=args.background,
        notify_on_complete=args.notify_on_complete
        or original.get("notify_on_complete", False),
        session_key=original.get("session_key", ""),
        verify=original.get("verify_mode", "off"),
        verify_config=None,
        permissions=(original.get("permissions") or {}).get("policy", "relay"),
        worktree=original.get("requested_worktree_mode", "off"),
        task=[task],
        parent_job_id=original["job_id"],
        retry_index=int(original.get("retry_index") or 0) + 1,
    )
    return handle_run(retry_args)


def handle_permission_response(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    if job.get("status") != PERMISSION_WAITING_STATUS:
        if args.json:
            print(json_output(job))
        return 1
    write_permission_response(args.job_id, args.decision)
    if args.json:
        print(json_output(load_job(args.job_id)))
    return 0


def handle_watch(args: argparse.Namespace) -> int:
    while True:
        if args.job_id:
            payload = {"jobs": [load_job(args.job_id)]}
        else:
            payload = {
                "jobs": [
                    job
                    for job in list_jobs(
                        status="all",
                        limit=20,
                        session_key=os.environ.get("AI_PEERS_SESSION_KEY", "") or None,
                    )
                    if job.get("status")
                    in {"queued", "running", PERMISSION_WAITING_STATUS}
                ]
            }
        if args.json:
            print(json_output(payload))
        else:
            print(format_job_list(payload["jobs"]))

        terminal = (
            args.job_id
            and payload["jobs"]
            and payload["jobs"][0].get("status")
            in {
                "completed",
                "failed",
                PERMISSION_WAITING_STATUS,
            }
        )
        if args.once or terminal:
            return 0
        time.sleep(max(0.2, args.interval))


def handle_classify(args: argparse.Namespace) -> int:
    payload = classify_task(args.task, requested_difficulty=args.difficulty)
    if args.json:
        print(json_output(payload))
    else:
        print(str(payload))
    return 0


def handle_route(args: argparse.Namespace) -> int:
    payload = route_task(
        task=args.task,
        target=args.target,
        requested_difficulty=args.difficulty,
        cwd=args.cwd,
    )
    if args.json:
        print(json_output(payload))
    else:
        route = ", ".join(payload["route"]) or "-"
        print(
            f"route={route}\ndifficulty={payload['difficulty']}\nreason={payload['route_reason']}"
        )
    return 0


def handle_cleanup_worktree(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    updated = cleanup_worktree(job)
    job["worktree"] = updated
    save_job(job)
    if args.json:
        print(json_output({"job_id": job["job_id"], "worktree": updated}))
    else:
        print(
            f"job={job['job_id']} cleanup_status={updated.get('cleanup_status')} path={updated.get('path')}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    ensure_state_root()

    if args.command == "run":
        return handle_run(args)
    if args.command == "orchestrate":
        return handle_orchestrate(args)
    if args.command == "list":
        return handle_list(args)
    if args.command == "show":
        return handle_show(args)
    if args.command == "retry":
        return handle_retry(args)
    if args.command == "watch":
        return handle_watch(args)
    if args.command == "classify":
        return handle_classify(args)
    if args.command == "route":
        return handle_route(args)
    if args.command == "cleanup-worktree":
        return handle_cleanup_worktree(args)
    if args.command == "__monitor__":
        return run_monitor(args.job_id)
    if args.command == "poll-completions":
        payload = poll_completions(
            session_key=args.session_key.strip(),
            limit=int(args.limit),
            mark_seen=not args.keep_unseen,
        )
        print(json_output(payload))
        return 0
    if args.command == "job-status":
        print(json_output(load_job(args.job_id)))
        return 0
    if args.command == "permission-response":
        return handle_permission_response(args)
    raise SystemExit(f"Unknown command: {args.command}")
