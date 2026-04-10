from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .jobs import CONFIG_DIR, EXTRA_BUILTIN_AGENTS, PRIMARY_AGENTS

TARGET_ALIASES = {
    "openai-codex-cli": "codex",
    "codex-cli": "codex",
    "claude-code": "claude",
    "cursor-agent": "cursor",
    "agent": "cursor",
    "goose-cli": "goose",
    "gemini-cli": "gemini",
}

BUILTIN_TARGETS = set(PRIMARY_AGENTS + EXTRA_BUILTIN_AGENTS)


def adapters_file() -> Path:
    raw = os.environ.get("AI_BRIDGE_ADAPTERS_FILE")
    return Path(raw).expanduser() if raw else CONFIG_DIR / "adapters.json"


def normalize_target(target: str) -> str:
    clean = str(target or "").strip().lower()
    return TARGET_ALIASES.get(clean, clean)


def explicit_command_line(task: str) -> str | None:
    """Return an explicit shell command hint from task text, if present."""

    stripped = str(task or "").strip()
    if not stripped:
        return None

    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        inline = re.search(r"\b(?:from repo root run|run command|run)\b\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if inline:
            command = inline.group(1).strip().strip("`")
            if command:
                return command

    first_line = stripped.split("\n", 1)[0].strip()
    if first_line.startswith("$"):
        inner = first_line[1:].strip()
        return inner or None

    lowered = first_line.lower()
    prefixes = (
        "pytest ",
        "pytest\t",
        "python ",
        "python3 ",
        "ruff ",
        "mypy ",
        "make ",
        "cargo ",
        "nix ",
        "bash ",
        "sh ",
        "zsh ",
        "./",
        "uv run ",
        "poetry run ",
        "tox ",
        "pre-commit ",
    )
    if any(lowered.startswith(p) for p in prefixes):
        return first_line
    if lowered == "go" or lowered.startswith("go "):
        return first_line

    fenced = re.search(r"```(?:bash|sh|shell)?\n(.+?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        for fence_line in fenced.group(1).splitlines():
            candidate = fence_line.strip().strip("`")
            if candidate:
                return candidate
    return None


def build_prompt(user_prompt: str, difficulty: str, cwd: str, source: str, target: str) -> str:
    header = (
        f"You are being invoked as a delegated worker from {source}.\n"
        f"Target worker: {target}.\n"
        f"Task difficulty lane: {difficulty}.\n"
        f"Working directory: {cwd}\n"
        "Do the work directly if it is feasible. If you cannot complete it safely, "
        "say clearly that you could not complete it and why. Be concise.\n\n"
    )
    body = user_prompt.strip()
    execution_first = ""
    if explicit_command_line(body):
        execution_first = (
            "Execution-first mode\n"
            "The user task names an explicit shell command or a focused run step. "
            "Run that exact command in the working directory as your first action "
            "(adjust quoting or flags only if needed for correctness). "
            "Until it fails or its output clearly requires more context, avoid unrelated "
            "repository reads and broad exploration.\n\n"
        )
    return f"{header}{execution_first}User task:\n{body}\n"


def load_adapter_config() -> dict[str, Any]:
    path = adapters_file()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def render_adapter_command(
    template: list[str],
    *,
    prompt: str,
    cwd: str,
    source: str,
    target: str,
    difficulty: str,
) -> list[str]:
    rendered: list[str] = []
    replacements = {
        "{prompt}": prompt,
        "{cwd}": cwd,
        "{source}": source,
        "{target}": target,
        "{difficulty}": difficulty,
    }
    for token in template:
        value = str(token)
        for needle, replacement in replacements.items():
            value = value.replace(needle, replacement)
        rendered.append(value)
    return rendered


def worker_supports_permission_relay(worker: str) -> bool:
    return normalize_target(worker) == "codex"


def builtin_worker_command(worker: str, prompt: str, cwd: str, *, permission_policy: str = "skip") -> list[str]:
    if worker == "codex":
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--cd",
            cwd,
        ]
        if permission_policy == "skip":
            command.append("--full-auto")
        command.append(prompt)
        return command
    if worker == "claude":
        return [
            "claude-code-worker",
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            cwd,
            "--",
            prompt,
        ]
    if worker == "cursor":
        return [
            "agent-hard",
            "--print",
            "--output-format",
            "text",
            "--trust",
            "--force",
            "--approve-mcps",
            "--workspace",
            cwd,
            prompt,
        ]
    if worker == "opencode":
        return [
            "opencode-easy",
            "run",
            "--dir",
            cwd,
            "--dangerously-skip-permissions",
            prompt,
        ]
    if worker == "goose":
        return [
            "goose",
            "run",
            "--text",
            prompt,
        ]
    raise ValueError(f"Unknown worker '{worker}'")


def worker_command(
    worker: str,
    prompt: str,
    cwd: str,
    source: str,
    difficulty: str,
    *,
    permission_policy: str = "skip",
) -> list[str]:
    normalized = normalize_target(worker)
    if normalized in BUILTIN_TARGETS:
        return builtin_worker_command(normalized, prompt, cwd, permission_policy=permission_policy)

    adapters = load_adapter_config()
    adapter = adapters.get(normalized) or adapters.get(worker)
    if not adapter:
        raise ValueError(
            f"Unsupported target '{worker}'. Add an adapter in {adapters_file()} "
            "or use one of the built-in targets: codex, claude, cursor, opencode, goose."
        )

    command = adapter.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError(f"Adapter '{worker}' is missing a non-empty command list.")

    binary = str(command[0])
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved:
        raise ValueError(f"Adapter '{worker}' command '{binary}' is not available on PATH.")

    return render_adapter_command(
        [resolved, *[str(part) for part in command[1:]]],
        prompt=prompt,
        cwd=cwd,
        source=source,
        target=normalized,
        difficulty=difficulty,
    )
