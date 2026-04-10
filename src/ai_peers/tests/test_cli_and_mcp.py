#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# Strip when spawning CLI so parent delegated-worker env (session key, role, etc.) cannot satisfy
# identity checks that these tests expect to fail without --peer-id.
_PEER_SUBPROCESS_STRIP_KEYS = (
    "AI_PEERS_SESSION_KEY",
    "AI_PEERS_CLIENT",
    "AI_PEERS_ROLE",
    "AI_PEERS_LAUNCH_CWD",
    "AI_PEERS_SUMMARY",
)


def registration(
    *,
    peer_id: str,
    client: str,
    role: str,
    pid: int,
    cwd: str = "/tmp/repo",
    repo_root: str = "/tmp/repo",
) -> dict[str, object]:
    return {
        "peer_id": peer_id,
        "client": client,
        "role": role,
        "hostname": "host",
        "pid": pid,
        "cwd": cwd,
        "repo_root": repo_root,
        "summary": "available",
        "active_files_json": "[]",
        "started_at": 1.0,
        "last_seen": 1.0,
    }


class PeerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base_env = os.environ.copy()
        for key in (
            "AI_PEERS_SESSION_KEY",
            "AI_PEERS_CLIENT",
            "AI_PEERS_ROLE",
            "AI_PEERS_LAUNCH_CWD",
            "AI_PEERS_SUMMARY",
        ):
            self.base_env.pop(key, None)
            os.environ.pop(key, None)
        self.base_env["AI_PEERS_DB"] = os.path.join(self.tempdir.name, "peers.db")
        self.base_env["AI_PEERS_SKIP_PID_CHECK"] = "1"
        self.base_env["PYTHONPATH"] = f"{SRC_ROOT}:{self.base_env.get('PYTHONPATH', '')}"
        os.environ["AI_PEERS_DB"] = self.base_env["AI_PEERS_DB"]
        os.environ["AI_PEERS_SKIP_PID_CHECK"] = "1"
        global store_module
        import ai_peers.store as store_module  # type: ignore

        store_module = importlib.reload(store_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_PEERS_DB", None)
        os.environ.pop("AI_PEERS_SKIP_PID_CHECK", None)

    def _peer_cli_env(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        env = self.base_env.copy()
        for key in _PEER_SUBPROCESS_STRIP_KEYS:
            env.pop(key, None)
        if overrides:
            env.update(overrides)
        return env

    def _run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = self._peer_cli_env(env)
        return subprocess.run(
            [sys.executable, "-m", "ai_peers.cli", *args],
            cwd=str(REPO_ROOT),
            env=merged,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_poll_and_set_summary_require_peer_identity(self) -> None:
        poll = self._run_cli("poll")
        self.assertNotEqual(poll.returncode, 0)
        self.assertIn("Missing peer id", poll.stderr)

        update = self._run_cli("set-summary-for", "working")
        self.assertNotEqual(update.returncode, 0)
        self.assertIn("Missing peer id", update.stderr)

    def test_cli_poll_set_summary_and_route(self) -> None:
        peer_a = store_module.PeerStore(
            registration=registration(peer_id="peer-a", client="codex", role="orchestrator-reviewer", pid=10101)
        )
        peer_b = store_module.PeerStore(
            registration=registration(peer_id="peer-b", client="cursor-agent", role="hard-programmer", pid=10102)
        )
        peer_c = store_module.PeerStore(
            registration=registration(peer_id="peer-c", client="opencode", role="easy-programmer", pid=10103)
        )

        update = self._run_cli(
            "set-summary-for",
            "editing router",
            "--peer-id",
            "peer-a",
            "--active-file",
            "src/router.py",
            "--active-file",
            "src/store.py",
        )
        self.assertEqual(update.returncode, 0, update.stderr)
        update_payload = json.loads(update.stdout)
        self.assertEqual(update_payload["peer"]["summary"], "editing router")
        self.assertEqual(update_payload["peer"]["active_files"], ["src/router.py", "src/store.py"])

        peer_b.send_message("peer-a", "avoid router.py for now")
        first_poll = self._run_cli("poll", "--peer-id", "peer-a", "--keep-unread")
        self.assertEqual(first_poll.returncode, 0, first_poll.stderr)
        first_payload = json.loads(first_poll.stdout)
        self.assertEqual(len(first_payload["messages"]), 1)
        self.assertEqual(first_payload["messages"][0]["body"], "avoid router.py for now")

        second_poll = self._run_cli("poll", "--peer-id", "peer-a")
        self.assertEqual(second_poll.returncode, 0, second_poll.stderr)
        second_payload = json.loads(second_poll.stdout)
        self.assertEqual(len(second_payload["messages"]), 1)

        third_poll = self._run_cli("poll", "--peer-id", "peer-a")
        self.assertEqual(third_poll.returncode, 0, third_poll.stderr)
        third_payload = json.loads(third_poll.stdout)
        self.assertEqual(third_payload["messages"], [])

        route = self._run_cli("route", "--task-kind", "implement", "--difficulty", "hard")
        self.assertEqual(route.returncode, 0, route.stderr)
        route_payload = json.loads(route.stdout)
        self.assertEqual(route_payload["recommended"]["role"], "hard-programmer")

        peer_a.remove_self()
        peer_b.remove_self()
        peer_c.remove_self()


class PeerMcpParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AI_PEERS_DB"] = os.path.join(self.tempdir.name, "peers.db")
        os.environ["AI_PEERS_SKIP_PID_CHECK"] = "1"
        os.environ["AI_PEERS_SESSION_KEY"] = "mcp-self"
        os.environ["AI_PEERS_CLIENT"] = "codex"
        os.environ["AI_PEERS_ROLE"] = "orchestrator-reviewer"
        os.environ["AI_PEERS_LAUNCH_CWD"] = "/tmp/repo"
        os.environ["AI_PEERS_SUMMARY"] = "mcp parity"
        global store_module
        import ai_peers.store as store_module  # type: ignore

        store_module = importlib.reload(store_module)
        self.saved_modules = {
            name: sys.modules.get(name)
            for name in ("mcp", "mcp.server", "mcp.server.fastmcp", "ai_peers.server")
        }
        self.server_module = self._load_server_module()

    def tearDown(self) -> None:
        if hasattr(self, "server_module"):
            self.server_module.STORE.remove_self()
        self.tempdir.cleanup()
        for name, value in self.saved_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        for key in (
            "AI_PEERS_DB",
            "AI_PEERS_SKIP_PID_CHECK",
            "AI_PEERS_SESSION_KEY",
            "AI_PEERS_CLIENT",
            "AI_PEERS_ROLE",
            "AI_PEERS_LAUNCH_CWD",
            "AI_PEERS_SUMMARY",
        ):
            os.environ.pop(key, None)

    def _load_server_module(self):
        mcp_mod = types.ModuleType("mcp")
        mcp_server_mod = types.ModuleType("mcp.server")
        fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

        class FakeFastMCP:
            def __init__(self, *_args, **_kwargs) -> None:
                self._tools: dict[str, object] = {}

            def tool(self, description: str = ""):
                del description

                def decorator(fn):
                    self._tools[fn.__name__] = fn
                    return fn

                return decorator

            def run(self, transport: str = "stdio") -> None:
                del transport

        fastmcp_mod.FastMCP = FakeFastMCP
        sys.modules["mcp"] = mcp_mod
        sys.modules["mcp.server"] = mcp_server_mod
        sys.modules["mcp.server.fastmcp"] = fastmcp_mod
        sys.modules.pop("ai_peers.server", None)
        import ai_peers.server as server_module  # type: ignore

        return importlib.reload(server_module)

    def test_server_tool_contracts_match_store_behavior(self) -> None:
        peer_easy = store_module.PeerStore(
            registration=registration(
                peer_id="peer-easy",
                client="opencode",
                role="easy-programmer",
                pid=10201,
            )
        )

        listed = self.server_module.list_peers(scope="machine", include_self=False, only_active=True)
        self.assertIn("peer-easy", {peer["peer_id"] for peer in listed["peers"]})

        updated = self.server_module.set_summary("editing", ["src/a.py"])
        self.assertEqual(updated["peer"]["summary"], "editing")
        self.assertEqual(updated["peer"]["active_files"], ["src/a.py"])

        sent = self.server_module.send_message("peer-easy", "hello from mcp")
        self.assertEqual(sent["sent"]["to_peer_id"], "peer-easy")
        inbox_easy = peer_easy.check_messages()
        self.assertEqual(inbox_easy[0]["body"], "hello from mcp")

        peer_easy.send_message(self.server_module.whoami()["peer_id"], "reply to mcp")
        inbox_self = self.server_module.check_messages(limit=10, mark_read=True)
        self.assertEqual(inbox_self["messages"][0]["body"], "reply to mcp")

        recommend = self.server_module.recommend_peer(task_kind="implement", difficulty="easy")
        self.assertEqual(recommend["recommended"]["peer_id"], "peer-easy")

        peer_easy.remove_self()


if __name__ == "__main__":
    unittest.main()
