---
last_mapped: 2026-04-09T00:00:00Z
---

# Codebase Map

## System Overview

The Conductor is a local multi-agent switchboard that enables Codex, Cursor Agent, OpenCode, and Claude Code to coordinate work through a shared peer registry and message bus. Delegation is Hermes-style: one agent shells out to another agent CLI as a worker process.

**Role split:**

| Agent | Role |
|---|---|
| Codex | `orchestrator-reviewer` |
| OpenCode | `easy-programmer` |
| Cursor Agent | `hard-programmer` |
| Claude Code | `hard-programmer` |

**Auto-routing order:** easy → OpenCode then Cursor; hard → Cursor then OpenCode; review → Codex.

## Directory Guide

```
.
├── bin/                          Executable wrappers (all on $PATH)
│   ├── ai-dispatch               Main dispatcher script (Python); difficulty-based routing, background job registry, and worker execution
│   ├── ai-delegate               Thin bash shim → ai-dispatch
│   ├── ai-peers                  CLI wrapper → src/ai-peers/cli.py
│   ├── codex-orchestrator        Launches Codex with orchestrator-reviewer role
│   ├── agent-hard                Launches Cursor Agent with hard-programmer role
│   ├── opencode-easy             Launches OpenCode with easy-programmer role
│   └── claude-code-worker         Launches Claude Code with hard-programmer role
├── src/ai-peers/                  Peer bus core (Python)
│   ├── server.py                 FastMCP stdio server — exposes whoami, list_peers, recommend_peer, set_summary, send_message, check_messages
│   ├── store.py                  PeerStore class + SQLite schema, peer lifecycle, message queue, routing logic
│   ├── cli.py                    Argument parser for shell use of peer bus (peers, route, send, inbox, poll, whoami, cleanup, set-summary-for)
│   ├── requirements.txt          Pinned dependency: mcp==1.27.0
│   └── tests/test_store.py       Unit tests: message round-trip, set_summary, recommend_peer lane preference
├── hooks/                         Agent context injection
│   ├── ai-peers-context.mjs      Reads unread peer messages via cli.py and emits them as additional context for Cursor/Codex/OpenCode
│   └── codex-orchestrator-context.mjs  Injects orchestration policy into Codex session startup
├── skills/agent-delegation/       Shared cross-tool skill
│   └── SKILL.md                  Delegation instructions: when/how to use ai-delegate, routing rules, worker mapping
├── examples/                      Setup documentation
│   ├── SETUP.md                  Installation steps: venv, PATH, MCP registration, skill install, hook wiring
│   └── ORIGIN_MAP.md             Maps exported files back to their canonical local paths
└── README.md                      Project overview, commands, usage examples
```

## Key Workflows

### 1. Delegate a task to another agent

```
ai-delegate --target <codex|cursor|opencode|claude|auto> --cwd "$PWD" --from-agent <caller> -- "<task>"
```

`ai-delegate` → `ai-dispatch` (Python). Builds a worker prompt, runs the target agent CLI as a subprocess, checks for failure markers, tries fallbacks in route order, saves a JSON run log to `~/.local/state/ai-dispatch/`.

For long-running jobs:

```
ai-delegate --target auto --difficulty hard --background --notify-on-complete --cwd "$PWD" --from-agent codex -- "<task>"
```

This creates a detached monitor-backed job under `~/.local/state/ai-dispatch/jobs/` and surfaces completion back into the caller's next prompt context.

### 2. Discover peers and coordinate

Agent inserts itself into `peers` table on startup via `PeerStore()`. Heartbeat thread keeps `last_seen` fresh. Stale peers (inactive >`ACTIVE_WINDOW_SECONDS` or dead PID) are cleaned automatically. Peers can `send_message` / `check_messages` for file-collision avoidance and status updates.

### 3. Context injection (hooks)

On each prompt submission, `ai-peers-context.mjs` polls the message bus and injects unread coordination messages into the agent's context. On session start, `codex-orchestrator-context.mjs` injects routing policy for Codex.

### 4. Difficulty-based routing

`ai-dispatch` normalises difficulty (auto-detects from task text using hard markers like "debug", "refactor", "race condition"). Route order:
- easy: `[opencode, cursor]`
- hard: `[cursor, opencode]`
- explicit target: `[target]` (no fallback)

## Known Risks

- **Shell-delegated workers**: No native in-process handoff. Each delegation forks a full agent subprocess with its own timeout (default 900s).
- **Background jobs**: Completion tracking is file-backed and local to one machine. It is robust enough for single-user workflows, but not a distributed queue.
- **Message polling, not push**: Peer messages are only delivered on next prompt cycle via hooks. No mid-turn injection.
- **SQLite store**: Single-machine, no concurrent-write guarantees beyond WAL mode. Sufficient for local single-user use.
- **Hardcoded paths in wrappers**: `bin/ai-peers` and `bin/ai-delegate` reference home-directory paths (`~/.agents/vendor/ai-peers/`, `/Users/mikhail/.local/bin/ai-dispatch`). Must be adjusted per-install.
- **PID-based liveness**: Default `SKIP_PID_CHECK=0` can incorrectly prune peers in containers where PID namespaces differ.
