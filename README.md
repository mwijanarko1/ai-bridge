# ai-bridge

Local multi-agent switchboard for coding agents and CLIs.

This bundle contains the shareable source for a Hermes-style delegation system:

- `codex` can orchestrate and review
- Cursor Agent can handle harder implementation tasks
- OpenCode can handle easier implementation tasks
- Claude Code can be used as an explicit worker target
- Goose is supported as an explicit worker target
- Gemini CLI, Aider, Amp, Cline, Droid, and other tools are supported through an adapter registry
- any supported target can be launched through the shared delegation command

Assumption: the target user already has the relevant CLIs installed and authenticated locally as needed.

## Contents

- `src/ai-peers/`
  - local MCP peer bus
  - SQLite-backed peer registry and message queue
  - tests and pinned Python requirements
- `hooks/`
  - prompt/session context injection hooks
- `bin/`
  - shareable wrapper and dispatcher commands
- `skills/agent-delegation/`
  - shared skill that teaches any of the four tools how to delegate to the others
- `examples/`
  - setup notes and config snippets

## Main Commands

- `ai-delegate`
  - explicit delegation to any supported target
- `ai-dispatch`
  - difficulty-based routing
- `codex-orchestrator`
  - launch Codex as orchestrator/reviewer
- `agent-hard`
  - launch Cursor Agent as the hard-task worker
- `opencode-easy`
  - launch OpenCode as the easy-task worker
- `claude-code-worker`
  - launch Claude Code as a worker
- `ai-peers`
  - shell helper for inspecting the peer bus

## Example Usage

```bash
ai-delegate --target codex --cwd "$PWD" --from-agent cursor-agent -- "Fix the failing parser tests"
ai-delegate --target cursor --cwd "$PWD" --from-agent opencode -- "Refactor the auth pipeline safely"
ai-delegate --target opencode --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
ai-delegate --target claude --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target goose --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target gemini --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target auto --difficulty hard --cwd "$PWD" --from-agent codex -- "Debug the race condition"
ai-delegate --target auto --difficulty hard --background --notify-on-complete --cwd "$PWD" --from-agent codex -- "Run the refactor and tell me when it's done"
```

## Support Matrix

- Native in this bundle:
  - `codex`
  - `cursor`
  - `opencode`
  - `claude`
  - `goose`
- Adapter-ready via `~/.config/ai-bridge/adapters.json`:
  - `gemini`
  - `aider`
  - `amp`
  - `cline`
  - `droid`
  - other shell-callable agents

## Notes

- This export intentionally does not include live config files with tokens or personal settings.
- It includes the actual source code and wrappers, plus safe documentation for wiring it up.
- Delegation is Hermes-style: one agent shells out to another agent CLI as a worker.
- Background delegation supports Hermes-style completion tracking and next-turn notifications.
- Non-native tools are supported through adapter commands rather than hardcoded assumptions about their CLI flags.
- This is not native in-process model handoff and not true mid-turn push messaging.
- Claude Code is included as an explicit worker target in the delegator.
