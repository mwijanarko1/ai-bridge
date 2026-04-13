# ai-bridge

Local multi-agent switchboard for coding agents and CLIs.

`ai-bridge` is an opinionated Hermes-style delegation system: one agent shells out to another agent CLI as a worker, stores the job as local JSON state, and feeds the result back into the next prompt cycle through hooks.

## Install

`ai-bridge` is meant to be run with Python 3.11+ on your machine. The agent CLIs you delegate to (Codex, Cursor `agent`, OpenCode, Claude Code, and so on) must already be installed and authenticated separately.

### pipx (recommended)

From any directory, with the repo as the source tree (or after publishing to an index):

```bash
pipx install /absolute/path/to/ai-bridge
# or: pipx install .
# or: pipx install git+<repo-url>
```

This installs `ai-dispatch`, `ai-delegate`, `ai-peers`, `ai-peers-mcp`, `ai-bridge-setup-hooks`, `codex-orchestrator`, `agent-hard`, `opencode-easy`, and `claude-code-worker` on your PATH.

After install, wire the peer inbox hooks:

```bash
ai-bridge-setup-hooks
```

This writes portable hook files under `~/.config/ai-bridge/hooks`, merges Codex and Cursor hook config, and installs an OpenCode peer plugin under `~/.config/opencode/plugins/ai-bridge-peers.ts`. Restart Codex, Cursor Agent, and OpenCode after setup so they load the new hooks.

Optional: point hooks or scripts at this checkout with:

```bash
export AI_BRIDGE_ROOT="/absolute/path/to/ai-bridge"
```

You do not need a separate `ai-peers` venv when using pipx; dependencies come from the pipx environment.

### Editable install from a clone

```bash
cd ai-bridge
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then either use `.venv/bin/ai-dispatch` (and the other entry points) or add `.venv/bin` to `PATH`.

### Legacy: run from a clone without installing

```bash
cd ai-bridge

mkdir -p ~/.local/bin
ln -sf "$PWD/bin/ai-dispatch" ~/.local/bin/ai-dispatch
ln -sf "$PWD/bin/ai-delegate" ~/.local/bin/ai-delegate
ln -sf "$PWD/bin/ai-peers" ~/.local/bin/ai-peers
ln -sf "$PWD/bin/ai-bridge-setup-hooks" ~/.local/bin/ai-bridge-setup-hooks
ln -sf "$PWD/bin/codex-orchestrator" ~/.local/bin/codex-orchestrator
ln -sf "$PWD/bin/agent-hard" ~/.local/bin/agent-hard
ln -sf "$PWD/bin/opencode-easy" ~/.local/bin/opencode-easy
ln -sf "$PWD/bin/claude-code-worker" ~/.local/bin/claude-code-worker
```

The `bin/*` wrappers add `src/` to `PYTHONPATH` so `ai_dispatch` and `ai_peers` resolve without an install. Peer coordination code lives in the import package `ai_peers` under `src/ai_peers/`; `src/ai-peers/` only holds thin shims for older paths (for example `AI_BRIDGE_PEERS_CLI` defaults in some hooks).

Add the wrappers and repo path to your shell config:

```bash
export PATH="$HOME/.local/bin:$PATH"
export AI_BRIDGE_ROOT="/absolute/path/to/ai-bridge"
```

If you override the peers CLI, you can still use a dedicated interpreter:

```bash
export AI_BRIDGE_PEERS_PYTHON="/path/to/python3"
export AI_BRIDGE_PEERS_CLI="/path/to/custom/cli.py"
```

Reload your shell, then smoke-test the install:

```bash
ai-dispatch --help
ai-peers --help
ai-delegate --help
ai-bridge-setup-hooks --dry-run
command -v ai-peers-mcp
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

- Built-in explicit targets:
  - `goose`
  - `qwen`
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
- `ai-peers-mcp`
  - launch the peer-bus MCP server
- `codex-orchestrator`
  - launch Codex as orchestrator/reviewer
- `agent-hard`
  - launch Cursor Agent as the hard-task worker
- `opencode-easy`
  - launch OpenCode as the easy-task worker
- `claude-code-worker`
  - launch Claude Code as a worker

## Core Workflows

Agent-to-agent messages through active peers:

```bash
ai-peers message codex --scope machine -- "Can you review the latest diff?"
ai-peers message cursor --scope repo -- "Please avoid src/router.py; I am editing it."
ai-peers ask opencode --timeout 60 -- "Can you reply with opencode-ok?"
ai-peers watch --peer-id "$AI_PEERS_SESSION_KEY" --once
```

Use peer messages when the target agent is already active and you want a short conversation or coordination note. This does not spawn a new worker process. It fails fast with JSON when no active matching peer exists or when more than one matching peer is active.

`ai-peers watch` is the push-style inbox mode: it blocks, streams JSONL message batches as they arrive, and can run as a daemon with `ai-peers daemon`. It is implemented as lightweight polling of the local peer database, so it still does not start a target agent.
`ai-peers ask` sends a peer message and waits for the first reply from that target peer, returning `reply_timeout` if the peer never answers.

Explicit delegation:

```bash
ai-delegate --target codex --cwd "$PWD" --from-agent cursor-agent -- "Fix the failing parser tests"
ai-delegate --target cursor --cwd "$PWD" --from-agent codex -- "Debug the race condition in the sync engine"
ai-delegate --target opencode --cwd "$PWD" --from-agent codex -- "Rename the settings toggle label"
ai-delegate --target claude --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target goose --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target qwen --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
ai-delegate --target gemini --cwd "$PWD" --from-agent codex -- "Investigate the migration bug"
```

Use delegation when you want to launch a fresh subprocess worker, keep a job record, run in the background, or use retry/watch lifecycle commands. Do not use `ai-delegate --target codex` as a substitute for messaging an already-running Codex peer.

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

Autonomous multi-turn orchestration (MVP): run up to `--max-turns` sequential delegations on the same PRD, feeding the previous worker output back as follow-up context until the task succeeds, a verification step fails, the worker asks for a user decision, a permission prompt needs a human decision, or the turn budget is exhausted. Each turn is still a normal on-disk job (chain via `parent_job_id`). `--background` is not supported on this subcommand.

```bash
ai-dispatch orchestrate --target cursor --max-turns 5 --cwd "$PWD" --from-agent codex -- "Implement section 3 of the PRD"
```

Foreground non-JSON mode prints the live handoff as a transcript: `Codex -> Cursor Agent`, then streamed worker output under `Cursor Agent -> Codex`. Use `--json` when another tool needs machine-readable output instead of the live transcript.

The orchestrated worker prompt asks each turn to end with `AI_BRIDGE_STATUS: done`, `AI_BRIDGE_STATUS: continue`, or `AI_BRIDGE_STATUS: blocked`. `continue` creates the next autonomous turn; `blocked` pauses and surfaces `AI_BRIDGE_USER_QUESTION` to the operator. Older workers that do not emit a status line are treated like normal successful `run` jobs.

Exit codes: `0` completed successfully, `1` failed or verification failed or max turns exhausted, `2` pending permission (use `permission-response`), `3` paused for a likely user question, `130` interrupted.

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
      "command": ["python3", "-m", "unittest", "discover", "-s", "src/ai_peers/tests", "-p", "test_*.py"]
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
