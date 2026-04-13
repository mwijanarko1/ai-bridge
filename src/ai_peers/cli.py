from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .store import (
    PeerStore,
    check_messages_for_peer,
    cleanup_stale_peers,
    get_peer,
    set_summary_for_peer,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-peers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    peers = subparsers.add_parser("peers", help="list active peers")
    peers.add_argument("--scope", choices=["machine", "repo", "directory"], default="machine")
    peers.add_argument("--include-self", action="store_true")
    peers.add_argument("--role")

    route = subparsers.add_parser("route", help="recommend a peer for a task")
    route.add_argument("--task-kind", choices=["implement", "review"], default="implement")
    route.add_argument("--difficulty", choices=["easy", "hard"], default="easy")

    send = subparsers.add_parser("send", help="send a message to a peer")
    send.add_argument("peer_id")
    send.add_argument("message")

    send_target = subparsers.add_parser(
        "send-target",
        aliases=["message"],
        help="send a message to one active peer by target name",
    )
    send_target.add_argument("target")
    send_target.add_argument("message")
    send_target.add_argument("--scope", choices=["machine", "repo", "directory"], default="machine")

    inbox = subparsers.add_parser("inbox", help="show unread messages")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.add_argument("--keep-unread", action="store_true")

    poll = subparsers.add_parser("poll", help="show unread messages for the current AI_PEERS_SESSION_KEY or explicit peer id")
    poll.add_argument("--peer-id")
    poll.add_argument("--limit", type=int, default=20)
    poll.add_argument("--keep-unread", action="store_true")

    watch = subparsers.add_parser("watch", aliases=["daemon"], help="stream unread messages as they arrive")
    watch.add_argument("--peer-id")
    watch.add_argument("--limit", type=int, default=20)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--timeout", type=float, default=0.0, help="Seconds to wait before exiting; 0 means forever.")
    watch.add_argument("--once", action="store_true", help="Exit after the first non-empty message batch.")
    watch.add_argument("--keep-unread", action="store_true")

    ask = subparsers.add_parser("ask", help="send a target peer message and wait for one reply")
    ask.add_argument("target")
    ask.add_argument("message")
    ask.add_argument("--scope", choices=["machine", "repo", "directory"], default="machine")
    ask.add_argument("--interval", type=float, default=1.0)
    ask.add_argument("--timeout", type=float, default=60.0)
    ask.add_argument("--keep-unread", action="store_true")

    update = subparsers.add_parser("set-summary-for", help="update summary for the current AI_PEERS_SESSION_KEY or explicit peer id")
    update.add_argument("summary")
    update.add_argument("--peer-id")
    update.add_argument("--active-file", action="append", default=[])

    subparsers.add_parser("whoami", help="show current peer identity")
    cleanup = subparsers.add_parser("cleanup", help="remove stale peers")
    cleanup.add_argument("--json", action="store_true")

    return parser


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def emit_json_line(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def resolve_session_peer_id(args: argparse.Namespace) -> str:
    peer_id = args.peer_id if hasattr(args, "peer_id") and args.peer_id else None
    peer_id = peer_id or os.environ.get("AI_PEERS_SESSION_KEY")
    if not peer_id:
        raise SystemExit("Missing peer id. Set AI_PEERS_SESSION_KEY or pass --peer-id.")
    return peer_id


def handle_watch(args: argparse.Namespace) -> None:
    peer_id = resolve_session_peer_id(args)
    deadline = time.time() + float(args.timeout) if float(args.timeout) > 0 else None
    interval = max(0.05, float(args.interval))
    while True:
        peer = get_peer(peer_id)
        messages = check_messages_for_peer(
            peer_id,
            limit=args.limit,
            mark_read=not args.keep_unread,
        )
        if messages:
            emit_json_line({"peer": peer, "messages": messages})
            if args.once:
                return
        if deadline is not None and time.time() >= deadline:
            print_json({"peer": peer, "messages": [], "timeout": True})
            return
        time.sleep(interval)


def handle_ask(args: argparse.Namespace) -> int:
    store = PeerStore(preserve_existing=True)
    payload = store.send_message_to_target(args.target, args.message, scope=args.scope)
    if not payload["ok"]:
        print_json(payload)
        return 2

    peer = payload["peer"]
    deadline = time.time() + max(0.0, float(args.timeout))
    interval = max(0.05, float(args.interval))
    while time.time() < deadline:
        replies = [
            message
            for message in check_messages_for_peer(
                store.peer_id,
                limit=50,
                mark_read=not args.keep_unread,
            )
            if message.get("from_peer_id") == peer["peer_id"]
        ]
        if replies:
            payload["reply"] = replies[0]
            print_json(payload)
            return 0
        time.sleep(interval)

    payload["ok"] = False
    payload["error"] = "reply_timeout"
    print_json(payload)
    return 2


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    session_peer_id = args.peer_id if hasattr(args, "peer_id") and args.peer_id else None
    if args.command == "poll":
        session_peer_id = session_peer_id or os.environ.get("AI_PEERS_SESSION_KEY")
        if not session_peer_id:
            raise SystemExit("Missing peer id. Set AI_PEERS_SESSION_KEY or pass --peer-id.")
        print(
            json.dumps(
                {
                    "peer": get_peer(session_peer_id),
                    "messages": check_messages_for_peer(
                        session_peer_id,
                        limit=args.limit,
                        mark_read=not args.keep_unread,
                    ),
                },
                indent=2,
            )
        )
        return

    if args.command in {"watch", "daemon"}:
        handle_watch(args)
        return

    if args.command == "ask":
        raise SystemExit(handle_ask(args))

    if args.command == "set-summary-for":
        session_peer_id = session_peer_id or os.environ.get("AI_PEERS_SESSION_KEY")
        if not session_peer_id:
            raise SystemExit("Missing peer id. Set AI_PEERS_SESSION_KEY or pass --peer-id.")
        print(
            json.dumps(
                {
                    "peer": set_summary_for_peer(
                        session_peer_id,
                        summary=args.summary,
                        active_files=args.active_file,
                    )
                },
                indent=2,
            )
        )
        return

    store = PeerStore(preserve_existing=args.command in {"send", "send-target", "message"})
    try:
        if args.command == "peers":
            print(
                json.dumps(
                    {
                        "peers": store.list_peers(
                            scope=args.scope,
                            include_self=args.include_self,
                            role=args.role,
                        )
                    },
                    indent=2,
                )
            )
            return

        if args.command == "route":
            print(
                json.dumps(
                    store.recommend_peer(
                        task_kind=args.task_kind,
                        difficulty=args.difficulty,
                    ),
                    indent=2,
                )
            )
            return

        if args.command == "send":
            print_json({"sent": store.send_message(args.peer_id, args.message)})
            return

        if args.command in {"send-target", "message"}:
            payload = store.send_message_to_target(args.target, args.message, scope=args.scope)
            print_json(payload)
            if not payload["ok"]:
                raise SystemExit(2)
            return

        if args.command == "inbox":
            print(
                json.dumps(
                    {
                        "messages": store.check_messages(
                            limit=args.limit,
                            mark_read=not args.keep_unread,
                        )
                    },
                    indent=2,
                )
            )
            return

        if args.command == "whoami":
            print(json.dumps(store.get_self(), indent=2))
            return

        if args.command == "cleanup":
            removed = cleanup_stale_peers()
            payload = {"removed": removed}
            print(json.dumps(payload, indent=2) if args.json else removed)
    finally:
        if args.command not in {"send", "send-target", "message"}:
            store.remove_self()


if __name__ == "__main__":
    main()
