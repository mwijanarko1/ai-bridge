#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PeerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AI_PEERS_DB"] = os.path.join(self.tempdir.name, "peers.db")
        os.environ["AI_PEERS_SKIP_PID_CHECK"] = "1"
        global store_module
        import ai_peers.store as store_module  # type: ignore

        store_module = importlib.reload(store_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_PEERS_DB", None)
        os.environ.pop("AI_PEERS_SKIP_PID_CHECK", None)

    def test_message_round_trip(self) -> None:
        first = store_module.PeerStore(
            {
                "peer_id": "peer-a",
                "client": "cursor-agent",
                "role": "hard-programmer",
                "hostname": "host",
                "pid": 10001,
                "cwd": "/tmp/project-a",
                "repo_root": "/tmp/project-a",
                "summary": "editing api",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        second = store_module.PeerStore(
            {
                "peer_id": "peer-b",
                "client": "opencode",
                "role": "easy-programmer",
                "hostname": "host",
                "pid": 10002,
                "cwd": "/tmp/project-b",
                "repo_root": "/tmp/project-b",
                "summary": "editing ui",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )

        sent = first.send_message("peer-b", "avoid auth.ts, I am in there")
        self.assertEqual(sent["to_peer_id"], "peer-b")

        unread = second.check_messages()
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0]["body"], "avoid auth.ts, I am in there")

        after_read = second.check_messages()
        self.assertEqual(after_read, [])

    def test_set_summary_updates_files(self) -> None:
        store = store_module.PeerStore(
            {
                "peer_id": "peer-c",
                "client": "cursor-agent",
                "role": "hard-programmer",
                "hostname": "host",
                "pid": 10003,
                "cwd": "/tmp/project-c",
                "repo_root": "/tmp/project-c",
                "summary": "starting",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )

        peer = store.set_summary("touching auth flow", ["src/auth.ts", "src/ui.tsx"])
        self.assertEqual(peer["summary"], "touching auth flow")
        self.assertEqual(peer["active_files"], ["src/auth.ts", "src/ui.tsx"])

    def test_recommend_peer_prefers_requested_lane(self) -> None:
        codex = store_module.PeerStore(
            {
                "peer_id": "peer-orch",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 10004,
                "cwd": "/tmp/project-orch",
                "repo_root": "/tmp/project-orch",
                "summary": "reviewing",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        store_module.PeerStore(
            {
                "peer_id": "peer-easy",
                "client": "opencode",
                "role": "easy-programmer",
                "hostname": "host",
                "pid": 10005,
                "cwd": "/tmp/project-easy",
                "repo_root": "/tmp/project-easy",
                "summary": "easy",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        store_module.PeerStore(
            {
                "peer_id": "peer-hard",
                "client": "cursor-agent",
                "role": "hard-programmer",
                "hostname": "host",
                "pid": 10006,
                "cwd": "/tmp/project-hard",
                "repo_root": "/tmp/project-hard",
                "summary": "hard",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )

        easy = codex.recommend_peer(task_kind="implement", difficulty="easy")
        self.assertEqual(easy["recommended"]["peer_id"], "peer-easy")

        hard = codex.recommend_peer(task_kind="implement", difficulty="hard")
        self.assertEqual(hard["recommended"]["peer_id"], "peer-hard")

        review = codex.recommend_peer(task_kind="review", difficulty="hard")
        self.assertEqual(review["recommended"]["peer_id"], "peer-orch")

    def test_check_messages_respects_mark_read_false(self) -> None:
        sender = store_module.PeerStore(
            {
                "peer_id": "peer-s1",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 20001,
                "cwd": "/tmp/a",
                "repo_root": "/tmp/a",
                "summary": "s",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        receiver = store_module.PeerStore(
            {
                "peer_id": "peer-r1",
                "client": "opencode",
                "role": "easy-programmer",
                "hostname": "host",
                "pid": 20002,
                "cwd": "/tmp/a",
                "repo_root": "/tmp/a",
                "summary": "r",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        sender.send_message("peer-r1", "hello")

        first = receiver.check_messages(mark_read=False)
        self.assertEqual(len(first), 1)
        second = receiver.check_messages(mark_read=False)
        self.assertEqual(len(second), 1)
        third = receiver.check_messages(mark_read=True)
        self.assertEqual(len(third), 1)
        self.assertEqual(receiver.check_messages(), [])

    def test_send_message_rejects_unknown_peer(self) -> None:
        store = store_module.PeerStore(
            {
                "peer_id": "peer-alone",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 20003,
                "cwd": "/tmp/x",
                "repo_root": "/tmp/x",
                "summary": "solo",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        with self.assertRaisesRegex(ValueError, "not found"):
            store.send_message("missing-peer-id", "ping")

    def test_list_peers_repo_scope_filters_by_repo_root(self) -> None:
        store_module.PeerStore(
            {
                "peer_id": "peer-repo-a-1",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 20010,
                "cwd": "/projects/foo",
                "repo_root": "/projects/foo",
                "summary": "a1",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        store_module.PeerStore(
            {
                "peer_id": "peer-repo-a-2",
                "client": "opencode",
                "role": "easy-programmer",
                "hostname": "host",
                "pid": 20011,
                "cwd": "/projects/foo/src",
                "repo_root": "/projects/foo",
                "summary": "a2",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        subject = store_module.PeerStore(
            {
                "peer_id": "peer-subject",
                "client": "cursor-agent",
                "role": "hard-programmer",
                "hostname": "host",
                "pid": 20012,
                "cwd": "/projects/foo",
                "repo_root": "/projects/foo",
                "summary": "subject",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        store_module.PeerStore(
            {
                "peer_id": "peer-other-repo",
                "client": "claude-code",
                "role": "worker",
                "hostname": "host",
                "pid": 20013,
                "cwd": "/other",
                "repo_root": "/other",
                "summary": "other",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )

        peers = subject.list_peers(scope="repo", include_self=False)
        ids = {p["peer_id"] for p in peers}
        self.assertTrue({"peer-repo-a-1", "peer-repo-a-2"}.issubset(ids))
        self.assertNotIn("peer-other-repo", ids)

    def test_heartbeat_updates_last_seen(self) -> None:
        store = store_module.PeerStore(
            {
                "peer_id": "peer-hb",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 20020,
                "cwd": "/tmp/hb",
                "repo_root": "/tmp/hb",
                "summary": "hb",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        before = store.get_self()["last_seen"]
        store.heartbeat()
        after = store.get_self()["last_seen"]
        self.assertGreaterEqual(after, before)

    def test_cleanup_stale_peers_removes_expired_rows(self) -> None:
        global store_module
        os.environ["AI_PEERS_ACTIVE_WINDOW"] = "1"
        store_module = importlib.reload(store_module)
        try:
            peer_id = "peer-stale"
            store_module.PeerStore(
                {
                    "peer_id": peer_id,
                    "client": "codex",
                    "role": "orchestrator-reviewer",
                    "hostname": "host",
                    "pid": 20030,
                    "cwd": "/tmp/stale",
                    "repo_root": "/tmp/stale",
                    "summary": "stale",
                    "active_files_json": "[]",
                    "started_at": 1.0,
                    "last_seen": 1.0,
                }
            )
            with store_module.connect() as conn:
                conn.execute("UPDATE peers SET last_seen = ? WHERE peer_id = ?", (0.0, peer_id))
                conn.commit()

            removed = store_module.cleanup_stale_peers()
            self.assertGreaterEqual(removed, 1)
            self.assertIsNone(store_module.get_peer(peer_id))
        finally:
            os.environ.pop("AI_PEERS_ACTIVE_WINDOW", None)
            store_module = importlib.reload(store_module)

    def test_store_api_matches_mcp_tool_contracts(self) -> None:
        """Keys mirror ai_peers.server MCP tool wrappers (stdio MCP not started)."""
        store = store_module.PeerStore(
            {
                "peer_id": "peer-mcp",
                "client": "codex",
                "role": "orchestrator-reviewer",
                "hostname": "host",
                "pid": 20040,
                "cwd": "/tmp/mcp",
                "repo_root": "/tmp/mcp",
                "summary": "mcp",
                "active_files_json": "[]",
                "started_at": 1.0,
                "last_seen": 1.0,
            }
        )
        list_payload = {"peers": store.list_peers()}
        self.assertIsInstance(list_payload["peers"], list)

        rec = store.recommend_peer(task_kind="implement", difficulty="easy")
        for key in ("task_kind", "difficulty", "role_order", "recommended", "fallbacks"):
            self.assertIn(key, rec)

        msg_payload = {"messages": store.check_messages(limit=5, mark_read=False)}
        self.assertIsInstance(msg_payload["messages"], list)

        summary_payload = {"peer": store.set_summary("working", ["src/a.ts"])}
        self.assertEqual(summary_payload["peer"]["summary"], "working")
        self.assertEqual(summary_payload["peer"]["active_files"], ["src/a.ts"])


if __name__ == "__main__":
    unittest.main()
