---
name: agent-delegation
description: Delegate a task to another coding agent from any supported tool. The primary agents are Codex, Claude Code, Cursor Agent, and OpenCode. Additional tools such as Goose or adapter-configured CLIs are available explicitly and only join auto-routing when allowlisted in config.
---

# Agent Delegation

Use the shared shell entrypoint:

```bash
ai-delegate ...
```

## Product Model

Primary agents:

- `codex`
- `claude`
- `cursor`
- `opencode`

Additional agents:

- built-in explicit targets: `goose`, `qwen`
- adapter-configured explicit targets: `gemini`, `aider`, `amp`, `cline`, `droid`, and other shell-callable tools

`--target auto` uses only the primary four by default.

## When To Use

- The user explicitly says to ask another agent
- The user wants automatic routing among the primary four
- The user wants an explicit additional tool such as Goose, Qwen, or Gemini
- The user wants long-running work to continue in the background

## Routing Rules

- `codex`:
  - Use for final review, fallback, research-heavy work, and orchestration
- `claude`:
  - Use for explicit Claude Code delegation
- `cursor`:
  - Use for harder implementation, debugging, and refactor tasks
- `opencode`:
  - Use for straightforward implementation and simple edits
- `goose`:
  - Use only when the user explicitly asks for Goose or config makes it relevant
- `qwen`:
  - Use only when the user explicitly asks for Qwen Code or config makes it relevant
- adapter targets:
  - Use only when the user explicitly names them or repo/local config intentionally enables them
- `auto`:
  - Routes only among `codex`, `claude`, `cursor`, and `opencode` unless optional agents are allowlisted in routing config

## Commands

Explicit target:

```bash
ai-delegate --target codex --cwd "$PWD" --from-agent codex -- "Review the parser fix"
ai-delegate --target claude --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target cursor --cwd "$PWD" --from-agent codex -- "Debug the race condition in the sync engine"
ai-delegate --target opencode --cwd "$PWD" --from-agent codex -- "Rename the settings toggle label"
ai-delegate --target goose --cwd "$PWD" --from-agent codex -- "Investigate the flaky test"
ai-delegate --target qwen --cwd "$PWD" --from-agent codex -- "Investigate the flaky test"
ai-delegate --target gemini --cwd "$PWD" --from-agent codex -- "Investigate the flaky test"
```

Automatic routing:

```bash
ai-delegate --target auto --difficulty easy --cwd "$PWD" --from-agent codex -- "Rename the settings toggle label"
ai-delegate --target auto --difficulty hard --cwd "$PWD" --from-agent codex -- "Debug the race condition in the sync engine"
```

Lifecycle commands:

```bash
ai-dispatch list
ai-dispatch show <job_id>
ai-dispatch retry <job_id> --feedback "Tighten the fix"
ai-dispatch watch <job_id>
ai-dispatch route "Rename the settings toggle label" --json
```

Verification:

```bash
ai-delegate --target opencode --verify default --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
```

Worktrees:

```bash
ai-delegate --target cursor --worktree auto --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
```

## How To Use It Correctly

1. If the user names a specific agent, use `--target` with that exact agent.
2. If the user asks for automatic delegation, use `--target auto`.
3. Always pass `--cwd "$PWD"` so the delegated worker runs in the right repo.
4. Always pass your current tool name with `--from-agent ...`.
5. Read the returned result critically. Delegation is not automatic validation.
6. Use `--verify <profile>` only when the repo or user has supplied a matching verify config.
7. Use `--worktree auto` or `--worktree branch:<name>` when isolation matters.
8. If an optional agent should participate in `auto`, require explicit routing config and explicit scores.

## Notes

- `ai-delegate` launches the real agent CLI. This is Hermes-style delegation via shell command, not an internal model handoff.
- `ai-dispatch` stores local JSON job state and exposes lifecycle commands.
- `goose` and `qwen` are supported explicitly, but they are not part of the default primary-four mental model.
- Adapter-configured tools are supported through `~/.config/ai-bridge/adapters.json`.
- This project does not include a TUI, cost tracking, sandboxing, or auto-merge.
