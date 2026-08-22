from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .jobs import (
    CONFIG_DIR,
    JOBS_DIR,
    LOGS_DIR,
    PERMISSION_RESPONSES_DIR,
    STATE_ROOT,
    WORKTREES_DIR,
)

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

PRIMARY_WORKER_COMMANDS = (
    "codex",
    "claude-code-worker",
    "agent-hard",
    "opencode-easy",
)
BRIDGE_COMMANDS = (
    "ai-peers",
    "ai-peers-mcp",
    "ai-bridge-setup-hooks",
)


def _result(
    name: str,
    status: str,
    summary: str,
    *,
    detail: str = "",
    fix: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "detail": detail,
        "fix": fix,
    }


def _status_rank(status: str) -> int:
    if status == STATUS_FAIL:
        return 2
    if status == STATUS_WARN:
        return 1
    return 0


def _overall_status(checks: list[dict[str, Any]]) -> str:
    worst = max((_status_rank(item["status"]) for item in checks), default=0)
    if worst >= 2:
        return STATUS_FAIL
    if worst == 1:
        return STATUS_WARN
    return STATUS_OK


def _resolve_config_dir(config_dir: str | None, home: Path) -> Path:
    raw = Path(config_dir).expanduser() if config_dir else CONFIG_DIR
    return raw if raw.is_absolute() else home / raw


def _peer_db_path() -> Path:
    raw = os.environ.get("AI_PEERS_DB", "~/.local/state/ai-peers/peers.db")
    return Path(raw).expanduser()


def _probe_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".ai-bridge-doctor-", delete=True):
            pass
    except Exception as exc:
        return False, str(exc)
    return True, ""


def check_state_root() -> dict[str, Any]:
    directories = [STATE_ROOT, JOBS_DIR, LOGS_DIR, WORKTREES_DIR, PERMISSION_RESPONSES_DIR]
    failures: list[str] = []
    for path in directories:
        ok, detail = _probe_writable_dir(path)
        if not ok:
            failures.append(f"{path}: {detail}")

    if failures:
        return _result(
            "state-root",
            STATUS_FAIL,
            "Local ai-dispatch state directories are not writable.",
            detail=" | ".join(failures),
        )

    return _result(
        "state-root",
        STATUS_OK,
        "Local ai-dispatch state directories are present and writable.",
        detail=str(STATE_ROOT),
    )


def check_peer_db() -> dict[str, Any]:
    db_path = _peer_db_path()
    ok, detail = _probe_writable_dir(db_path.parent)
    if not ok:
        return _result(
            "peer-db",
            STATUS_FAIL,
            "Peer bus database directory is not writable.",
            detail=f"{db_path.parent}: {detail}",
        )
    return _result(
        "peer-db",
        STATUS_OK,
        "Peer bus database directory is writable.",
        detail=str(db_path),
    )


def check_commands(name: str, commands: tuple[str, ...], *, fix: str = "") -> dict[str, Any]:
    found: list[str] = []
    missing: list[str] = []
    for command in commands:
        resolved = shutil.which(command)
        if resolved:
            found.append(f"{command}={resolved}")
        else:
            missing.append(command)

    if missing:
        return _result(
            name,
            STATUS_WARN,
            f"Missing commands: {', '.join(missing)}.",
            detail=" | ".join(found) if found else "",
            fix=fix,
        )

    return _result(
        name,
        STATUS_OK,
        "All expected commands are available on PATH.",
        detail=" | ".join(found),
    )


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid {label} at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {label} at {path}: top-level JSON object required.")
    return payload


def check_routing_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _result(
            "routing-config",
            STATUS_OK,
            "No routing config found; built-in auto-routing defaults will be used.",
            detail=str(path),
        )

    try:
        payload = _load_json_object(path, label="routing config")
    except ValueError as exc:
        return _result("routing-config", STATUS_FAIL, str(exc), fix=f"Fix or remove {path}.")

    auto_payload = payload.get("auto_routing")
    if auto_payload is not None and not isinstance(auto_payload, dict):
        return _result(
            "routing-config",
            STATUS_FAIL,
            f"Invalid routing config at {path}: auto_routing must be an object when present.",
            fix=f"Fix or remove {path}.",
        )

    return _result(
        "routing-config",
        STATUS_OK,
        "Routing config is readable.",
        detail=str(path),
    )


def check_verify_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _result(
            "verify-config",
            STATUS_OK,
            "No verify config found; post-run verification is optional.",
            detail=str(path),
        )

    try:
        payload = _load_json_object(path, label="verify config")
    except ValueError as exc:
        return _result("verify-config", STATUS_FAIL, str(exc), fix=f"Fix or remove {path}.")

    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        return _result(
            "verify-config",
            STATUS_FAIL,
            f"Invalid verify config at {path}: profiles must be an object.",
            fix=f"Fix or remove {path}.",
        )
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            return _result(
                "verify-config",
                STATUS_FAIL,
                f"Invalid verify config at {path}: profiles.{name} must be an object.",
                fix=f"Fix or remove {path}.",
            )
        command = profile.get("command")
        if not isinstance(command, list) or not command:
            return _result(
                "verify-config",
                STATUS_FAIL,
                f"Invalid verify config at {path}: profiles.{name}.command must be a non-empty list.",
                fix=f"Fix or remove {path}.",
            )

    return _result(
        "verify-config",
        STATUS_OK,
        "Verify config is readable.",
        detail=str(path),
    )


def check_adapters_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _result(
            "adapters-config",
            STATUS_OK,
            "No adapters config found; only built-in targets are available.",
            detail=str(path),
        )

    try:
        payload = _load_json_object(path, label="adapters config")
    except ValueError as exc:
        return _result("adapters-config", STATUS_FAIL, str(exc), fix=f"Fix or remove {path}.")

    for name, config in payload.items():
        if not isinstance(config, dict):
            return _result(
                "adapters-config",
                STATUS_FAIL,
                f"Invalid adapters config at {path}: adapter '{name}' must be an object.",
                fix=f"Fix or remove {path}.",
            )
        command = config.get("command")
        if not isinstance(command, list) or not command:
            return _result(
                "adapters-config",
                STATUS_FAIL,
                f"Invalid adapters config at {path}: adapter '{name}' must define a non-empty command list.",
                fix=f"Fix or remove {path}.",
            )

    return _result(
        "adapters-config",
        STATUS_OK,
        "Adapters config is readable.",
        detail=str(path),
    )


def check_hooks(*, home: Path, config_dir: Path) -> dict[str, Any]:
    expected = {
        "peers-hook": config_dir / "hooks" / "ai-peers-context.mjs",
        "orchestrator-hook": config_dir / "hooks" / "codex-orchestrator-context.mjs",
        "opencode-plugin": home / ".config" / "opencode" / "plugins" / "ai-bridge-peers.ts",
    }
    missing = [f"{name}={path}" for name, path in expected.items() if not path.exists()]
    present = [f"{name}={path}" for name, path in expected.items() if path.exists()]

    if missing:
        return _result(
            "hooks",
            STATUS_WARN,
            "Hook setup is incomplete.",
            detail=" | ".join(present + missing),
            fix="Run `ai-bridge-setup-hooks` and restart the supported agent sessions.",
        )

    return _result(
        "hooks",
        STATUS_OK,
        "Hook files and OpenCode plugin are present.",
        detail=" | ".join(present),
    )


def build_doctor_report(*, home: str | None = None, config_dir: str | None = None) -> dict[str, Any]:
    home_path = Path(home).expanduser() if home else Path.home()
    config_path = _resolve_config_dir(config_dir, home_path)

    checks = [
        check_state_root(),
        check_peer_db(),
        check_commands(
            "workers",
            PRIMARY_WORKER_COMMANDS,
            fix="Install or expose the primary worker commands on PATH for default auto-routing.",
        ),
        check_commands(
            "bridge-tools",
            BRIDGE_COMMANDS,
            fix="Install the bridge entrypoints on PATH, usually via `pipx install .` or your wrapper setup.",
        ),
        check_routing_config(config_path / "routing.json"),
        check_verify_config(config_path / "verify.json"),
        check_adapters_config(config_path / "adapters.json"),
        check_hooks(home=home_path, config_dir=config_path),
    ]

    status = _overall_status(checks)
    return {
        "ok": status != STATUS_FAIL,
        "status": status,
        "home": str(home_path),
        "config_dir": str(config_path),
        "checks": checks,
    }


def format_doctor_report(report: dict[str, Any]) -> str:
    lines = [f"ai-bridge doctor: {report['status']}"]
    for item in report["checks"]:
        lines.append(f"[{item['status']}] {item['name']}: {item['summary']}")
        if item.get("detail"):
            lines.append(f"  detail: {item['detail']}")
        if item.get("fix"):
            lines.append(f"  fix: {item['fix']}")
    return "\n".join(lines)
