from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .adapters import build_prompt, normalize_target, worker_command
from .jobs import (
    DEFAULT_TIMEOUT,
    LOGS_DIR,
    default_artifacts,
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
from .output import format_job_list, format_job_show, json_output
from .routing import classify_task, route_task
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
    if any(re.search(pattern, stdout_text, flags=re.MULTILINE) for pattern in soft_failure_patterns):
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


def summarize_result(result: dict[str, Any]) -> str:
    output = result.get("stdout") or result.get("stderr") or ""
    output = re.sub(r"\x1b\[[0-9;]*m", "", str(output)).strip()
    if len(output) > 1400:
        output = output[:1397] + "..."
    return output


def collect_result(
    worker: str,
    command: list[str],
    cwd: str,
    timeout: int,
    job_id: str,
    attempt_index: int,
) -> dict[str, Any]:
    started = now_ts()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{job_id}-{attempt_index:02d}-{worker}.log"
    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            start_new_session=True,
        )
        try:
            exit_code = proc.wait(timeout=timeout)
        except KeyboardInterrupt:
            _terminate_worker_process(proc)
            try:
                proc.wait(timeout=5)
            except BaseException:
                pass
            exit_code = 130
            log_handle.write("\n[ai-dispatch] interrupted\n")
        except subprocess.TimeoutExpired:
            _terminate_worker_process(proc)
            try:
                proc.wait(timeout=5)
            except BaseException:
                pass
            exit_code = 124
            log_handle.write("\n[ai-dispatch] timed out\n")
    finished = now_ts()
    combined = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    return make_attempt(
        worker=worker,
        command=command,
        exit_code=exit_code,
        duration_seconds=finished - started,
        stdout=combined.strip() if exit_code == 0 else "",
        stderr="" if exit_code == 0 else combined.strip(),
        ok=exit_code == 0,
        log_path=str(log_path),
    )


def preflight_job(job: dict[str, Any], *, verify_config: str | None, worktree_mode: str) -> dict[str, Any]:
    try:
        verification = prepare_verification(job["verify_mode"], job["cwd"], override_path=verify_config)
        worktree = prepare_worktree(job["job_id"], job["cwd"], worktree_mode)
        execution_cwd = worktree["path"] or job["cwd"]
    except Exception as exc:
        job["status"] = "failed"
        job["finished_at"] = now_ts()
        job["attempts"] = [make_attempt(worker="preflight", exit_code=2, stderr=str(exc), ok=False)]
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


def run_sync(job: dict[str, Any], *, verify_config: str | None = None) -> dict[str, Any]:
    if job.get("status") == "failed" and job.get("attempts"):
        return job

    attempts: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None
    execution_cwd = job.get("execution_cwd") or job["cwd"]
    interrupted = False

    try:
        for index, worker in enumerate(job["route"]):
            prompt = build_prompt(job["task"], job["difficulty"], execution_cwd, job["from_agent"], worker)
            try:
                command = worker_command(worker, prompt, execution_cwd, job["from_agent"], job["difficulty"])
                result = collect_result(worker, command, execution_cwd, int(job["timeout"]), job["job_id"], index)
            except KeyboardInterrupt:
                interrupted = True
                result = make_attempt(
                    worker=worker,
                    exit_code=130,
                    stderr="[ai-dispatch] interrupted",
                    ok=False,
                )
            except Exception as exc:
                result = make_attempt(worker=worker, exit_code=2, stderr=str(exc), ok=False)
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
    job["artifacts"]["attempt_logs"] = [item["log_path"] for item in attempts if item.get("log_path")]
    job["winner"] = winner["worker"] if winner else None
    job["success"] = winner is not None
    job["status"] = "completed" if winner else "failed"
    job["finished_at"] = now_ts()
    job["interrupted"] = interrupted or any(int(a.get("exit_code") or 0) == 130 for a in attempts)

    if winner and job.get("verify_mode") != "off":
        verification_plan = prepare_verification(job["verify_mode"], job["cwd"], override_path=verify_config)
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


def spawn_monitor(job: dict[str, Any], *, verify_config: str | None) -> None:
    env = os.environ.copy()
    if verify_config:
        env["AI_BRIDGE_VERIFY_CONFIG_OVERRIDE"] = verify_config
    dispatch_bin = Path(__file__).resolve().parents[2] / "bin" / "ai-dispatch"
    subprocess.Popen(
        [str(dispatch_bin), "__monitor__", job["job_id"]],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=job["cwd"],
        start_new_session=True,
        env=env,
    )


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


def poll_completions(session_key: str, limit: int = 8, mark_seen: bool = True) -> dict[str, Any]:
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

    route_info = route_task(task=task, target=args.target, requested_difficulty=args.difficulty, cwd=cwd)
    job = {
        "job_id": generate_job_id(),
        "target": args.target,
        "from_agent": args.from_agent,
        "session_key": args.session_key or os.environ.get("AI_PEERS_SESSION_KEY", "").strip(),
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
        "worktree": default_worktree(args.worktree),
        "artifacts": default_artifacts(),
    }
    save_job(job)
    job["artifacts"]["job_file"] = str(job_path(job["job_id"]))
    save_job(job)
    return preflight_job(job, verify_config=args.verify_config, worktree_mode=args.worktree)


def format_started(job: dict[str, Any]) -> str:
    return (
        f"[ai-dispatch] started background job={job['job_id']} route={','.join(job['route'])} "
        f"difficulty={job['difficulty']} job_file={job_path(job['job_id'])}"
    )


def build_run_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", default="auto", help="Explicitly target one worker, or use auto routing.")
    parser.add_argument(
        "--difficulty",
        choices=["easy", "hard", "auto"],
        default="auto",
        help="Used by auto routing. Explicit values override classifier complexity.",
    )
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the delegated worker.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-worker timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON instead of a human summary.")
    parser.add_argument(
        "--from-agent",
        default=os.environ.get("AI_DISPATCH_SOURCE", "unknown-agent"),
        help="Name of the calling agent for prompt context.",
    )
    parser.add_argument("--background", action="store_true", help="Run the delegation job in the background.")
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
    parser.add_argument("--verify-config", help="Override path for verification config JSON.")
    parser.add_argument(
        "--worktree",
        default="off",
        help="Worktree mode: off, auto, or branch:<name>.",
    )
    parser.add_argument("task", nargs="+", help="Task prompt to delegate.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    public_commands = {
        "run",
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
    }
    if not argv or argv[0] not in public_commands:
        parser = argparse.ArgumentParser(prog="ai-dispatch")
        build_run_parser(parser)
        args = parser.parse_args(argv)
        args.command = "run"
        return args

    parser = argparse.ArgumentParser(prog="ai-dispatch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    build_run_parser(run_parser)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", choices=["queued", "running", "completed", "failed", "all"], default="all")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--session-key")
    list_parser.add_argument("--json", action="store_true")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("job_id")
    show_parser.add_argument("--attempt", type=int)
    show_parser.add_argument("--log", action="store_true")
    show_parser.add_argument("--json", action="store_true")

    retry_parser = subparsers.add_parser("retry")
    retry_parser.add_argument("job_id")
    retry_parser.add_argument("--feedback")
    retry_parser.add_argument("--background", action="store_true")
    retry_parser.add_argument("--notify-on-complete", action="store_true")
    retry_parser.add_argument("--json", action="store_true")

    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("job_id", nargs="?")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--json", action="store_true")
    watch_parser.add_argument("--interval", type=float, default=2.0)

    classify_parser = subparsers.add_parser("classify")
    classify_parser.add_argument("task")
    classify_parser.add_argument("--difficulty", choices=["easy", "hard", "auto"], default="auto")
    classify_parser.add_argument("--json", action="store_true")

    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("task")
    route_parser.add_argument("--target", default="auto")
    route_parser.add_argument("--difficulty", choices=["easy", "hard", "auto"], default="auto")
    route_parser.add_argument("--cwd", default=os.getcwd())
    route_parser.add_argument("--json", action="store_true")

    cleanup_parser = subparsers.add_parser("cleanup-worktree")
    cleanup_parser.add_argument("job_id")
    cleanup_parser.add_argument("--json", action="store_true")

    monitor_parser = subparsers.add_parser("__monitor__")
    monitor_parser.add_argument("job_id")

    poll_parser = subparsers.add_parser("poll-completions")
    poll_parser.add_argument("--session-key", default=os.environ.get("AI_PEERS_SESSION_KEY", ""))
    poll_parser.add_argument("--limit", type=int, default=8)
    poll_parser.add_argument("--keep-unseen", action="store_true")

    status_parser = subparsers.add_parser("job-status")
    status_parser.add_argument("job_id")

    return parser.parse_args(argv)


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
        spawn_monitor(job, verify_config=args.verify_config)
        if args.json:
            print(json_output(job))
        else:
            print(format_started(job))
        return 0

    job["status"] = "running"
    job["started_at"] = now_ts()
    save_job(job)
    job = run_sync(job, verify_config=args.verify_config)
    if args.json:
        print(json_output(job))
        if job.get("interrupted"):
            return 130
        return 0 if job["success"] else 1
    if job.get("interrupted"):
        detail = summarize_result(job["attempts"][-1]) if job["attempts"] else ""
        print(
            f"[ai-dispatch] interrupted job_file={job_path(job['job_id'])}\n"
            f"{detail}"
        )
        return 130
    if job["success"]:
        winner = next(attempt for attempt in job["attempts"] if attempt["worker"] == job["winner"])
        print(
            f"[ai-dispatch] winner={winner['worker']} difficulty={job['difficulty']} job_file={job_path(job['job_id'])}\n"
            f"{summarize_result(winner)}"
        )
        return 0
    detail = summarize_result(job["attempts"][0]) if job["attempts"] else ""
    print(
        f"[ai-dispatch] all delegated workers failed difficulty={job['difficulty']} job_file={job_path(job['job_id'])}\n"
        f"{detail}\nThe calling agent should handle this task directly."
    )
    return 1


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
        notify_on_complete=args.notify_on_complete or original.get("notify_on_complete", False),
        session_key=original.get("session_key", ""),
        verify=original.get("verify_mode", "off"),
        verify_config=None,
        worktree=original.get("requested_worktree_mode", "off"),
        task=[task],
        parent_job_id=original["job_id"],
        retry_index=int(original.get("retry_index") or 0) + 1,
    )
    return handle_run(retry_args)


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
                    if job.get("status") in {"queued", "running"}
                ]
            }
        if args.json:
            print(json_output(payload))
        else:
            print(format_job_list(payload["jobs"]))

        terminal = args.job_id and payload["jobs"] and payload["jobs"][0].get("status") in {"completed", "failed"}
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
    payload = route_task(task=args.task, target=args.target, requested_difficulty=args.difficulty, cwd=args.cwd)
    if args.json:
        print(json_output(payload))
    else:
        route = ", ".join(payload["route"]) or "-"
        print(f"route={route}\ndifficulty={payload['difficulty']}\nreason={payload['route_reason']}")
    return 0


def handle_cleanup_worktree(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    updated = cleanup_worktree(job)
    job["worktree"] = updated
    save_job(job)
    if args.json:
        print(json_output({"job_id": job["job_id"], "worktree": updated}))
    else:
        print(f"job={job['job_id']} cleanup_status={updated.get('cleanup_status')} path={updated.get('path')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    ensure_state_root()

    if args.command == "run":
        return handle_run(args)
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
    raise SystemExit(f"Unknown command: {args.command}")
