#!/usr/bin/env python3
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def registration(
    *,
    peer_id: str,
    client: str,
    role: str,
    cwd: str,
    repo_root: str,
    pid: int,
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


class PeerStoreExtendedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["AI_PEERS_DB"] = os.path.join(self.tempdir.name, "peers.db")
        os.environ["AI_PEERS_SKIP_PID_CHECK"] = "1"
        os.environ["AI_PEERS_ACTIVE_WINDOW"] = "1"
        global store_module
        import ai_peers.store as store_module  # type: ignore

        store_module = importlib.reload(store_module)

    def tearDown(self) -> None:
        self.tempdir.cleanup()
        os.environ.pop("AI_PEERS_DB", None)
        os.environ.pop("AI_PEERS_SKIP_PID_CHECK", None)
        os.environ.pop("AI_PEERS_ACTIVE_WINDOW", None)
        os.environ.pop("AI_PEERS_CLIENT", None)
        os.environ.pop("AI_PEERS_ROLE", None)
        os.environ.pop("AI_PEERS_SESSION_KEY", None)
        os.environ.pop("AI_PEERS_SUMMARY", None)
        os.environ.pop("AI_PEERS_LAUNCH_CWD", None)

    def test_registration_and_heartbeat_refresh(self) -> None:
        store = store_module.PeerStore(registration=registration(
            peer_id="peer-a",
            client="codex",
            role="orchestrator-reviewer",
            cwd="/tmp/a",
            repo_root="/tmp/a",
            pid=10011,
        ))
        first = store.get_self()
        time.sleep(0.01)
        store.heartbeat()
        second = store.get_self()
        self.assertEqual(first["peer_id"], second["peer_id"])
        self.assertGreaterEqual(second["last_seen"], first["last_seen"])

    def test_cleanup_stale_peers_removes_expired_rows(self) -> None:
        store = store_module.PeerStore(registration=registration(
            peer_id="peer-stale",
            client="cursor-agent",
            role="hard-programmer",
            cwd="/tmp/stale",
            repo_root="/tmp/stale",
            pid=10012,
        ))
        with sqlite3.connect(os.environ["AI_PEERS_DB"]) as conn:
            conn.execute("UPDATE peers SET last_seen = ? WHERE peer_id = ?", (0.0, "peer-stale"))
            conn.commit()
        removed = store_module.cleanup_stale_peers()
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(store_module.get_peer("peer-stale"))
        store.remove_self()

    def test_list_peers_scope_role_and_include_self(self) -> None:
        current = store_module.PeerStore(registration=registration(
            peer_id="peer-self",
            client="codex",
            role="orchestrator-reviewer",
            cwd="/tmp/repo-a",
            repo_root="/tmp/repo-a",
            pid=10013,
        ))
        store_module.PeerStore(registration=registration(
            peer_id="peer-same-repo",
            client="cursor-agent",
            role="hard-programmer",
            cwd="/tmp/repo-a/other-dir",
            repo_root="/tmp/repo-a",
            pid=10014,
        ))
        store_module.PeerStore(registration=registration(
            peer_id="peer-same-dir",
            client="opencode",
            role="easy-programmer",
            cwd="/tmp/repo-a",
            repo_root="/tmp/repo-a",
            pid=10015,
        ))
        store_module.PeerStore(registration=registration(
            peer_id="peer-other",
            client="claude-code",
            role="hard-programmer",
            cwd="/tmp/repo-b",
            repo_root="/tmp/repo-b",
            pid=10016,
        ))

        machine = current.list_peers(scope="machine", include_self=False)
        self.assertIn("peer-other", {peer["peer_id"] for peer in machine})

        repo = current.list_peers(scope="repo", include_self=False)
        self.assertEqual({peer["peer_id"] for peer in repo}, {"peer-same-repo", "peer-same-dir"})

        directory = current.list_peers(scope="directory", include_self=False)
        self.assertEqual({peer["peer_id"] for peer in directory}, {"peer-same-dir"})

        hard_only = current.list_peers(scope="machine", include_self=False, role="hard-programmer")
        self.assertEqual({peer["peer_id"] for peer in hard_only}, {"peer-same-repo", "peer-other"})

        including_self = current.list_peers(scope="machine", include_self=True)
        self.assertIn("peer-self", {peer["peer_id"] for peer in including_self})

    def test_message_keep_unread_and_error_paths(self) -> None:
        first = store_module.PeerStore(registration=registration(
            peer_id="peer-a",
            client="cursor-agent",
            role="hard-programmer",
            cwd="/tmp/a",
            repo_root="/tmp/a",
            pid=10017,
        ))
        second = store_module.PeerStore(registration=registration(
            peer_id="peer-b",
            client="opencode",
            role="easy-programmer",
            cwd="/tmp/b",
            repo_root="/tmp/b",
            pid=10018,
        ))
        first.send_message("peer-b", "coordinate file ownership")
        unread_one = second.check_messages(mark_read=False)
        self.assertEqual(len(unread_one), 1)
        unread_two = second.check_messages(mark_read=False)
        self.assertEqual(len(unread_two), 1)
        read_once = second.check_messages(mark_read=True)
        self.assertEqual(len(read_once), 1)
        read_twice = second.check_messages(mark_read=True)
        self.assertEqual(read_twice, [])

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            first.send_message("peer-b", "   ")
        with self.assertRaisesRegex(ValueError, "was not found"):
            first.send_message("missing-peer", "hello")

    def test_message_survives_sender_peer_cleanup(self) -> None:
        sender = store_module.PeerStore(registration=registration(
            peer_id="peer-sender",
            client="codex",
            role="orchestrator-reviewer",
            cwd="/tmp/a",
            repo_root="/tmp/a",
            pid=10017,
        ))
        receiver = store_module.PeerStore(registration=registration(
            peer_id="peer-receiver",
            client="opencode",
            role="easy-programmer",
            cwd="/tmp/b",
            repo_root="/tmp/b",
            pid=10018,
        ))

        sender.send_message("peer-receiver", "still here")
        sender.remove_self()

        unread = receiver.check_messages(mark_read=False)
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0]["body"], "still here")
        self.assertEqual(unread[0]["from_peer_id"], "peer-sender")

    def test_recommendation_with_no_candidates_and_role_inference(self) -> None:
        store = store_module.PeerStore(registration=registration(
            peer_id="peer-orch",
            client="codex",
            role="orchestrator-reviewer",
            cwd="/tmp/orch",
            repo_root="/tmp/orch",
            pid=10019,
        ))
        hard = store.recommend_peer(task_kind="implement", difficulty="hard")
        self.assertIsNone(hard["recommended"])
        self.assertEqual(hard["role_order"][0], "hard-programmer")

        review = store.recommend_peer(task_kind="review", difficulty="hard")
        self.assertEqual(review["recommended"]["peer_id"], "peer-orch")

        self.assertEqual(store_module.infer_role("opencode"), "easy-programmer")
        self.assertEqual(store_module.infer_role("cursor-agent"), "hard-programmer")
        self.assertEqual(store_module.infer_role("codex"), "orchestrator-reviewer")
        self.assertEqual(store_module.infer_role("unknown"), "worker")

    def test_set_summary_refreshes_last_seen(self) -> None:
        store = store_module.PeerStore(registration=registration(
            peer_id="peer-sum",
            client="cursor-agent",
            role="hard-programmer",
            cwd="/tmp/sum",
            repo_root="/tmp/sum",
            pid=10020,
        ))
        before = store.get_self()["last_seen"]
        time.sleep(0.01)
        peer = store.set_summary("editing auth", ["src/auth.ts"])
        self.assertEqual(peer["summary"], "editing auth")
        self.assertEqual(peer["active_files"], ["src/auth.ts"])
        self.assertGreater(peer["last_seen"], before)


if __name__ == "__main__":
    unittest.main()
