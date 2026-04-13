from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


CODEX_ORCHESTRATOR_HOOK = """#!/usr/bin/env node
const context = [
  "<codex-orchestrator>",
  "You are the orchestrator and final reviewer.",
  "Implementation routing policy:",
  "- For live agent-to-agent coordination with an already-active session, prefer `ai-peers message <target> -- \\"...\\"`; it does not spawn a worker.",
  "- Use `ai-peers ask <target> -- \\"...\\"` for synchronous request/reply.",
  "- Use `ai-peers watch` / `ai-peers daemon` for push-style inbox streaming.",
  "- Use `ai-delegate --target ...` only when you explicitly need a fresh subprocess worker, background job, retry/watch lifecycle, or persisted job artifacts.",
  "</codex-orchestrator>",
].join("\\n");

process.stdout.write(`${JSON.stringify({
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: context,
  },
})}\\n`);
"""


AI_PEERS_CONTEXT_HOOK = """#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const PEERS = process.env.AI_BRIDGE_PEERS_COMMAND || "ai-peers";
const DISPATCH = process.env.AI_BRIDGE_DISPATCH_COMMAND || "ai-dispatch";
const TOOL = process.env.GUARDRAIL_TOOL || "generic";
const EVENT = process.env.GUARDRAIL_EVENT || "UserPromptSubmit";
const SESSION_KEY = process.env.AI_PEERS_SESSION_KEY || "";

function parseInput(raw) {
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function promptText(input) {
  return String(input.prompt || input.message || input.text || "").trim();
}

function summarizePrompt(text) {
  const clean = text.replace(/\\s+/g, " ").trim();
  return clean.length <= 140 ? clean : `${clean.slice(0, 137)}...`;
}

function runJson(command, args) {
  const result = spawnSync(command, args, {
    env: process.env,
    encoding: "utf8",
  });
  if (result.status !== 0 && !result.stdout.trim()) return null;
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

function formatPeerContext(peer, messages) {
  const lines = [
    "<peer-messages>",
    "Unread coordination messages from other local AI sessions:",
  ];
  for (const message of messages) {
    const parts = [
      `from=${message.from_client || "unknown"}`,
      message.from_role ? `role=${message.from_role}` : "",
      message.from_cwd ? `cwd=${message.from_cwd}` : "",
      message.from_summary ? `summary=${message.from_summary}` : "",
    ].filter(Boolean);
    lines.push(`- [${message.from_peer_id}] ${parts.join(" | ")} | message=${message.body}`);
  }
  if (peer?.role === "orchestrator-reviewer") {
    lines.push("Routing policy: you are the orchestrator and reviewer. Prefer peer messages for live coordination; use delegate only for subprocess jobs.");
  }
  lines.push("Treat these as fresh peer updates. If a message asks for a reply, answer with `ai-peers message <source-peer-id> ...`.");
  lines.push("</peer-messages>");
  return lines.join("\\n");
}

function formatCompletionContext(jobs) {
  const stripAnsi = (text) => String(text || "").replace(/\\u001b\\[[0-9;]*m/g, "");
  const lines = ["<delegation-completions>", "Completed delegated worker jobs:"];
  for (const job of jobs) {
    const attempts = Array.isArray(job.attempts) ? job.attempts : [];
    const detail = attempts
      .map((attempt) => {
        const output = stripAnsi(attempt.stdout || attempt.stderr || "").replace(/\\s+/g, " ").trim();
        const short = output.length > 260 ? `${output.slice(0, 257)}...` : output;
        return `${attempt.worker}[exit=${attempt.exit_code}] ${short}`;
      })
      .join(" || ");
    lines.push(`- job=${job.job_id} status=${job.status || "unknown"} winner=${job.winner || "none"} route=${(job.route || []).join(",")} | ${detail}`);
  }
  lines.push("</delegation-completions>");
  return lines.join("\\n");
}

function emit(context) {
  if (!context) return;
  if (TOOL === "cursor") {
    process.stdout.write(`${JSON.stringify({ additional_context: context })}\\n`);
    return;
  }
  process.stdout.write(`${JSON.stringify({
    hookSpecificOutput: {
      hookEventName: EVENT,
      additionalContext: context,
    },
  })}\\n`);
}

const input = parseInput(readFileSync(0, "utf8"));
if (SESSION_KEY) {
  const text = summarizePrompt(promptText(input));
  if (text) runJson(PEERS, ["set-summary-for", text]);
  const polled = runJson(PEERS, ["poll", "--limit", "8"]);
  const completions = runJson(DISPATCH, ["poll-completions", "--session-key", SESSION_KEY, "--limit", "8"]);
  const parts = [];
  if ((polled?.messages || []).length > 0) parts.push(formatPeerContext(polled.peer, polled.messages));
  if ((completions?.jobs || []).length > 0) parts.push(formatCompletionContext(completions.jobs));
  emit(parts.join("\\n\\n"));
}
"""


OPENCODE_PEERS_PLUGIN = """import type { Plugin } from "@opencode-ai/plugin";
import { spawnSync } from "node:child_process";

function runAiPeers(args: string[], cwd: string) {
  const command = process.env.AI_BRIDGE_PEERS_COMMAND || "ai-peers";
  const result = spawnSync(command, args, {
    cwd,
    encoding: "utf8",
    env: process.env,
  });
  if (result.status !== 0 && !result.stdout.trim()) return null;
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

function peerContextBlock(payload: any) {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  if (messages.length === 0) return "";
  const lines = [
    "<peer-messages>",
    "Unread coordination messages from other local AI sessions:",
  ];
  for (const message of messages) {
    const parts = [
      message.from_client ? `from=${message.from_client}` : "",
      message.from_role ? `role=${message.from_role}` : "",
      message.from_cwd ? `cwd=${message.from_cwd}` : "",
      message.from_summary ? `summary=${message.from_summary}` : "",
    ].filter(Boolean);
    lines.push(`- [${message.from_peer_id}] ${parts.join(" | ")} | message=${message.body}`);
  }
  lines.push("Treat these as fresh peer updates. If a message asks for a reply, answer with `ai-peers message <source-peer-id> ...`.");
  lines.push("</peer-messages>");
  return lines.join("\\n");
}

export const AiBridgePeers: Plugin = async ({ directory, worktree }) => {
  const baseDirectory = worktree || directory;
  return {
    "chat.message": async (_input, output) => {
      const promptText = output.parts
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join("\\n")
        .trim();
      if (!process.env.AI_PEERS_SESSION_KEY) return;
      const summary = promptText.replace(/\\s+/g, " ").trim().slice(0, 140);
      if (summary) runAiPeers(["set-summary-for", summary], baseDirectory);
      const polled = runAiPeers(["poll", "--limit", "8"], baseDirectory);
      const peerContext = peerContextBlock(polled);
      if (peerContext) {
        output.message.system = output.message.system
          ? `${output.message.system}\\n\\n${peerContext}`
          : peerContext;
      }
    },
  };
};
"""


def merge_hook_command(
    config: dict[str, Any],
    event: str,
    command: str,
    *,
    cursor_shape: bool,
    matcher: str | None = None,
    status_message: str | None = None,
) -> None:
    hooks = config.setdefault("hooks", {})
    entries = hooks.setdefault(event, [])
    if cursor_shape:
        if not any(item.get("command") == command for item in entries if isinstance(item, dict)):
            entries.append({"command": command})
        return

    target_entry = None
    for item in entries:
        if not isinstance(item, dict):
            continue
        if matcher is None or item.get("matcher") == matcher:
            target_entry = item
            break
    if target_entry is None:
        target_entry = {"hooks": []}
        if matcher is not None:
            target_entry["matcher"] = matcher
        entries.append(target_entry)
    target_hooks = target_entry.setdefault("hooks", [])
    if any(item.get("command") == command for item in target_hooks if isinstance(item, dict)):
        return
    payload = {"type": "command", "command": command}
    if status_message:
        payload["statusMessage"] = status_message
    target_hooks.append(payload)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def install_hooks(*, home: Path, config_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    hook_dir = config_dir / "hooks"
    peers_hook = hook_dir / "ai-peers-context.mjs"
    codex_hook = hook_dir / "codex-orchestrator-context.mjs"
    opencode_plugin = home / ".config" / "opencode" / "plugins" / "ai-bridge-peers.ts"

    peers_command = f"GUARDRAIL_EVENT=UserPromptSubmit node {peers_hook}"
    codex_peers_command = f"GUARDRAIL_TOOL=codex {peers_command}"
    cursor_peers_command = f"GUARDRAIL_TOOL=cursor {peers_command}"
    codex_orchestrator_command = f"node {codex_hook}"

    planned: list[str] = []
    if not dry_run:
        write_executable(peers_hook, AI_PEERS_CONTEXT_HOOK)
        write_executable(codex_hook, CODEX_ORCHESTRATOR_HOOK)
        opencode_plugin.parent.mkdir(parents=True, exist_ok=True)
        opencode_plugin.write_text(OPENCODE_PEERS_PLUGIN, encoding="utf-8")
    planned.extend([str(peers_hook), str(codex_hook), str(opencode_plugin)])

    codex_hooks = home / ".codex" / "hooks.json"
    cursor_hooks = home / ".cursor" / "hooks.json"

    codex_payload = read_json(codex_hooks)
    merge_hook_command(
        codex_payload,
        "SessionStart",
        codex_orchestrator_command,
        cursor_shape=False,
        matcher="startup|resume",
        status_message="Loading ai-bridge orchestration policy",
    )
    merge_hook_command(
        codex_payload,
        "SessionStart",
        "GUARDRAIL_TOOL=codex GUARDRAIL_EVENT=SessionStart node " + str(peers_hook),
        cursor_shape=False,
        matcher="startup|resume",
        status_message="Checking ai-bridge peer inbox",
    )
    merge_hook_command(
        codex_payload,
        "UserPromptSubmit",
        codex_peers_command,
        cursor_shape=False,
        status_message="Checking ai-bridge peer inbox",
    )

    cursor_payload = read_json(cursor_hooks)
    cursor_payload.setdefault("version", 1)
    merge_hook_command(
        cursor_payload,
        "SessionStart",
        "GUARDRAIL_TOOL=cursor GUARDRAIL_EVENT=SessionStart node " + str(peers_hook),
        cursor_shape=True,
    )
    merge_hook_command(
        cursor_payload,
        "UserPromptSubmit",
        cursor_peers_command,
        cursor_shape=True,
    )

    if not dry_run:
        write_json(codex_hooks, codex_payload)
        write_json(cursor_hooks, cursor_payload)
    planned.extend([str(codex_hooks), str(cursor_hooks)])

    return {
        "ok": True,
        "dry_run": dry_run,
        "config_dir": str(config_dir),
        "written": planned,
        "notes": [
            "OpenCode loads local plugins from ~/.config/opencode/plugins on startup; restart OpenCode after setup.",
            "Codex and Cursor read hook config on session startup; restart sessions after setup.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-bridge-setup-hooks")
    parser.add_argument("--home", default=str(Path.home()), help="Home directory to configure. Defaults to current user home.")
    parser.add_argument(
        "--config-dir",
        default=os.environ.get("AI_BRIDGE_CONFIG_DIR", "~/.config/ai-bridge"),
        help="ai-bridge config directory for generated hook files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned writes without modifying files.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = Path(args.home).expanduser()
    config_dir = Path(args.config_dir).expanduser()
    if not config_dir.is_absolute():
        config_dir = home / config_dir
    payload = install_hooks(home=home, config_dir=config_dir, dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("ai-bridge hook setup")
        for item in payload["written"]:
            print(f"- {item}")
        for note in payload["notes"]:
            print(f"note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
