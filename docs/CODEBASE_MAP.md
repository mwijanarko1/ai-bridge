---
last_mapped: 2026-04-09T00:00:00Z
---

# Codebase Map

## System Overview

`ai-bridge` is a local multi-agent switchboard. The primary product model is a four-agent core:

| Agent | Role |
|---|---|
| Codex | `orchestrator-reviewer` |
| Claude Code | `worker` |
| Cursor Agent | `hard-programmer` |
| OpenCode | `easy-programmer` |

Additional agents such as Goose or adapter-configured CLIs are supported explicitly, but they are not part of the default auto-routing pool.

Delegation is Hermes-style: one agent shells out to another agent CLI as a worker process, writes a JSON job record, and exposes the result through CLI lifecycle commands plus next-turn hook summaries.

## Directory Guide

```text
.
├── bin/
│   ├── ai-dispatch               Thin entrypoint → `src/ai_dispatch/cli.py`
│   ├── ai-delegate               Preferred user-facing alias → `ai-dispatch`
│   ├── ai-peers                  Wrapper → `src/ai-peers/cli.py`
│   ├── codex-orchestrator        Launches Codex with orchestrator-reviewer role
│   ├── agent-hard                Launches Cursor Agent with hard-programmer role
│   ├── opencode-easy             Launches OpenCode with easy-programmer role
│   └── claude-code-worker        Launches Claude Code as a worker
├── hooks/
│   ├── ai-peers-context.mjs      Injects peer messages and completed background-job summaries
│   └── codex-orchestrator-context.mjs  Injects orchestration policy into Codex session startup
├── src/ai_dispatch/
│   ├── cli.py                    CLI entrypoints, job lifecycle commands, dispatch execution
│   ├── jobs.py                   JSON job store helpers, state paths, summaries, repo detection
│   ├── adapters.py               Built-in worker commands, adapter registry loading, prompt assembly
│   ├── routing.py                Deterministic classification, scoring, route planning, routing config
│   ├── verify.py                 Verification config loading and post-run verification execution
│   ├── worktree.py               Opt-in git worktree creation and cleanup
│   ├── output.py                 Text and JSON-friendly render helpers
│   └── tests/                    Unit and CLI integration coverage for dispatcher behavior
├── src/ai-peers/
│   ├── server.py                 FastMCP stdio server for peer presence and coordination
│   ├── store.py                  SQLite-backed peer registry and message queue
│   ├── cli.py                    Shell-facing CLI for peers, inbox, summaries, and routing hints
│   ├── requirements.txt          Pinned MCP dependency
│   └── tests/test_store.py       Unit tests for peer store behavior
├── skills/agent-delegation/
│   └── SKILL.md                  Shared delegation instructions
├── examples/
│   ├── SETUP.md                  Setup and configuration notes
│   ├── adapters.example.json     Example adapter registry
│   └── ORIGIN_MAP.md             Maps exported files back to canonical local paths
└── README.md                     Product overview and operator docs
```

## Key Workflows

### 1. Delegate work

```bash
ai-delegate --target auto --difficulty hard --cwd "$PWD" --from-agent codex -- "Debug the race condition"
```

Default `auto` routing uses only:

- `codex`
- `claude`
- `cursor`
- `opencode`

Optional agents can join `auto` only through explicit routing config.

### 2. Inspect and retry jobs

```bash
ai-dispatch list
ai-dispatch show <job_id>
ai-dispatch retry <job_id> --feedback "Tighten the fix"
ai-dispatch watch <job_id>
```

Job state is file-backed under `~/.local/state/ai-dispatch/` by default.

### 3. Verify results

```bash
ai-delegate --target opencode --verify default --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
```

Verification is config-driven and runs only after a winning worker result.

### 4. Use worktree isolation

```bash
ai-delegate --target cursor --worktree auto --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
```

Worktrees are opt-in and retained by default.

### 5. Coordinate active sessions

Peers register through the local SQLite-backed peer bus. Hooks poll unread messages and finished background jobs and inject them into the next prompt cycle. This is next-turn delivery, not a push channel.

## Known Risks

- Shell-delegated workers are still full subprocesses with their own CLI quirks.
- Completion delivery is still next-turn only.
- Dispatch state is local JSON, not a distributed queue.
- Peer state is single-machine SQLite with WAL mode.
- There is no TUI, cost tracking, sandboxing, or auto-merge.
