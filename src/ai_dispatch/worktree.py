from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .jobs import WORKTREES_DIR, detect_repo_root


def sanitize_branch_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._/-]+", "-", str(name).strip())
    return clean.strip("-") or "job"


def prepare_worktree(job_id: str, cwd: str, mode: str) -> dict:
    if mode == "off":
        return {
            "mode": "off",
            "path": None,
            "branch": None,
            "repo_root": None,
            "created": False,
            "cleanup_status": "not_applicable",
        }

    repo_root = detect_repo_root(cwd)
    if not repo_root:
        raise ValueError("Worktree mode requires a git repository.")

    repo_name = Path(repo_root).name
    if mode == "auto":
        branch = f"ai-dispatch/{job_id}"
        path = WORKTREES_DIR / repo_name / job_id
    elif mode.startswith("branch:"):
        branch = mode.split(":", 1)[1].strip()
        if not branch:
            raise ValueError("Worktree mode 'branch:' requires a branch name.")
        path = WORKTREES_DIR / repo_name / sanitize_branch_name(branch)
    else:
        raise ValueError("Worktree mode must be one of: off, auto, branch:<name>.")

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Worktree path already exists: {path}")

    subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "-b", branch, str(path), "HEAD"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return {
        "mode": "named" if mode.startswith("branch:") else "auto",
        "path": str(path),
        "branch": branch,
        "repo_root": repo_root,
        "created": True,
        "cleanup_status": "retained",
    }


def cleanup_worktree(job: dict) -> dict:
    worktree = dict(job.get("worktree") or {})
    path = worktree.get("path")
    repo_root = worktree.get("repo_root")
    if not path or not repo_root:
        worktree["cleanup_status"] = "not_applicable"
        return worktree

    target = Path(path)
    if not target.exists():
        worktree["cleanup_status"] = "removed"
        return worktree

    completed = subprocess.run(
        ["git", "-C", repo_root, "worktree", "remove", "--force", path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    worktree["cleanup_status"] = "removed" if completed.returncode == 0 else "failed"
    return worktree
