from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def dispatch_main() -> int:
    from .cli import main

    return main()


def _resolve_dispatch_executable() -> str | None:
    env_bin = os.environ.get("AI_BRIDGE_DISPATCH_BIN")
    if env_bin:
        p = Path(env_bin)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        found = shutil.which(env_bin)
        if found:
            return found

    root = os.environ.get("AI_BRIDGE_ROOT")
    if root:
        candidate = Path(root) / "bin" / "ai-dispatch"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    found = shutil.which("ai-dispatch")
    if found:
        return found
    return None


def delegate_main() -> int:
    dispatch = _resolve_dispatch_executable()
    if not dispatch:
        print(
            "ai-delegate: unable to find ai-dispatch. Set AI_BRIDGE_DISPATCH_BIN or AI_BRIDGE_ROOT "
            "or install ai-bridge so ai-dispatch is on PATH.",
            file=sys.stderr,
        )
        return 1
    env = os.environ.copy()
    prog = Path(sys.argv[0]).name
    env.setdefault("AI_DISPATCH_PROG", prog if prog and prog != "-c" else "ai-delegate")
    return int(subprocess.call([dispatch, *sys.argv[1:]], env=env))


def peers_main() -> int:
    from ai_peers.cli import main

    main()
    return 0


def peers_server_main() -> int:
    from ai_peers.server import main

    main()
    return 0


def setup_hooks_main() -> int:
    from .setup_hooks import main

    return main()


def codex_orchestrator_main() -> int:
    _launch_worker(
        binary="codex",
        client="codex",
        role="orchestrator-reviewer",
        default_summary="Codex orchestrator and reviewer",
    )


def agent_hard_main() -> int:
    _launch_worker(
        binary="agent",
        client="cursor-agent",
        role="hard-programmer",
        default_summary="Cursor hard-task implementer",
    )


def opencode_easy_main() -> int:
    _launch_worker(
        binary="opencode",
        client="opencode",
        role="easy-programmer",
        default_summary="OpenCode easy-task implementer",
    )


def claude_code_worker_main() -> int:
    _launch_worker(
        binary="claude",
        client="claude-code",
        role="hard-programmer",
        default_summary="Claude Code worker",
    )


def _launch_worker(*, binary: str, client: str, role: str, default_summary: str) -> int:
    resolved = shutil.which(binary)
    if not resolved:
        raise SystemExit(f"{binary} is not available on PATH.")

    os.environ["AI_PEERS_CLIENT"] = client
    os.environ["AI_PEERS_ROLE"] = role
    os.environ["AI_PEERS_LAUNCH_CWD"] = os.getcwd()
    os.environ.setdefault("AI_PEERS_SESSION_KEY", uuid.uuid4().hex)
    os.environ.setdefault("AI_PEERS_SUMMARY", default_summary)
    os.execvp(resolved, [binary, *sys.argv[1:]])
    raise AssertionError("os.execvp returned unexpectedly")
