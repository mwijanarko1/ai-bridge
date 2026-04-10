# ai-bridge

Local multi-agent switchboard for coding agents and CLIs.

`ai-bridge` is an opinionated Hermes-style delegation system: one agent shells out to another agent CLI as a worker, stores the job as local JSON state, and feeds the result back into the next prompt cycle through hooks.

## Install

`ai-bridge` is meant to be run from a local clone of this repository. It assumes the agent CLIs you want to delegate to are already installed and authenticated on your machine.

From the repo root:

```bash
cd ai-bridge

python3 -m venv src/ai-peers/.venv
src/ai-peers/.venv/bin/pip install -r src/ai-peers/requirements.txt

mkdir -p ~/.local/bin
ln -sf "$PWD/bin/ai-dispatch" ~/.local/bin/ai-dispatch
ln -sf "$PWD/bin/ai-delegate" ~/.local/bin/ai-delegate
ln -sf "$PWD/bin/ai-peers" ~/.local/bin/ai-peers
ln -sf "$PWD/bin/codex-orchestrator" ~/.local/bin/codex-orchestrator
ln -sf "$PWD/bin/agent-hard" ~/.local/bin/agent-hard
ln -sf "$PWD/bin/opencode-easy" ~/.local/bin/opencode-easy
ln -sf "$PWD/bin/claude-code-worker" ~/.local/bin/claude-code-worker
```

Add the wrappers and repo path to your shell config:

```bash
export PATH="$HOME/.local/bin:$PATH"
export AI_BRIDGE_ROOT="/absolute/path/to/ai-bridge"
export AI_BRIDGE_PEERS_PYTHON="$AI_BRIDGE_ROOT/src/ai-peers/.venv/bin/python"
```

Reload your shell, then smoke-test the install:

```bash
ai-dispatch --help
ai-peers --help
ai-delegate --help
```

## Primary Agents

These four are the default product model:

- `codex`
- `claude`
- `cursor`
- `opencode`

They are the only agents treated as first-class in:

- default docs and examples
- `--target auto` routing
- scoring and route explanations
- orchestration guidance
- hook summaries

## Additional Agents

Additional agents are supported, but they are second-class by design.

- Built-in explicit target:
  - `goose`
- Adapter-configured explicit targets:
  - `gemini`
  - `aider`
  - `amp`
  - `cline`
  - `droid`
  - other shell-callable tools

Additional agents do not participate in `--target auto` unless they are explicitly allowlisted in `.ai-bridge/routing.json` or `~/.config/ai-bridge/routing.json` and given scores.

## Main Commands

- `ai-delegate`
  - preferred user-facing command
- `ai-dispatch`
  - dispatcher engine and lifecycle commands
- `ai-peers`
  - local peer-bus CLI
- `codex-orchestrator`
  - launch Codex as orchestrator/reviewer
- `agent-hard`
  - launch Cursor Agent as the hard-task worker
- `opencode-easy`
  - launch OpenCode as the easy-task worker
- `claude-code-worker`
  - launch Claude Code as a worker

## Core Workflows

Explicit delegation:

```bash
ai-delegate --target codex --cwd "$PWD" --from-agent cursor-agent -- "Fix the failing parser tests"
ai-delegate --target cursor --cwd "$PWD" --from-agent codex -- "Debug the race condition in the sync engine"
ai-delegate --target opencode --cwd "$PWD" --from-agent codex -- "Rename the settings toggle label"
ai-delegate --target claude --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target goose --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target gemini --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
```

Auto-routing across the primary four only:

```bash
ai-delegate --target auto --difficulty hard --cwd "$PWD" --from-agent codex -- "Debug the race condition"
```

Background work with completion notification:

```bash
ai-delegate --target auto --difficulty hard --background --notify-on-complete --cwd "$PWD" --from-agent codex -- "Run the refactor and tell me when it's done"
```

Job lifecycle:

```bash
ai-dispatch list
ai-dispatch show <job_id>
ai-dispatch retry <job_id> --feedback "Tighten the fix and keep the diff smaller"
ai-dispatch watch <job_id>
```

Route inspection:

```bash
ai-dispatch classify "Debug the race condition in the sync engine" --json
ai-dispatch route "Rename the settings toggle label" --json
```

Verification:

```bash
ai-delegate --target opencode --verify default --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
```

Opt-in worktree isolation:

```bash
ai-delegate --target cursor --worktree auto --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
```

## Config Files

Adapter registry:

```text
~/.config/ai-bridge/adapters.json
```

Routing config:

```text
.ai-bridge/routing.json
~/.config/ai-bridge/routing.json
```

Verification config:

```text
.ai-bridge/verify.json
~/.config/ai-bridge/verify.json
```

Routing config example:

```json
{
  "auto_routing": {
    "enabled_agents": ["codex", "claude", "cursor", "opencode"],
    "optional_allowlist": ["goose"]
  },
  "agents": {
    "goose": {
      "scores": {
        "simple_edit": 5,
        "implementation": 6,
        "debugging": 6,
        "refactor": 5,
        "research": 5,
        "review": 4
      }
    }
  }
}
```

Verification config example:

```json
{
  "profiles": {
    "default": {
      "command": ["python3", "-m", "unittest", "discover", "-s", "src/ai-peers/tests", "-p", "test_*.py"]
    }
  }
}
```

## Portability

Use these env vars to override repo-relative path resolution when needed:

- `AI_BRIDGE_ROOT`
- `AI_BRIDGE_DISPATCH_BIN`
- `AI_BRIDGE_PEERS_PYTHON`
- `AI_BRIDGE_PEERS_CLI`
- `AI_BRIDGE_CONFIG_DIR`
- `AI_DISPATCH_STATE_ROOT`

## Notes

- Delegation is Hermes-style shell execution, not an in-process model handoff.
- Job state is stored as JSON under `~/.local/state/ai-dispatch/` by default.
- Background completion is next-turn only; there is no mid-turn push channel.
- Worktrees are opt-in and retained by default.
- Verification is opt-in and config-driven.
- This project does not include a TUI, cost tracking, sandboxing, or auto-merge.
