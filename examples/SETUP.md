# Setup Notes

These are the minimum wiring steps for the exported source.

Assumption: the machine already has the relevant CLIs installed and authenticated. This document does not cover login flows.

## 1. Install the peer bus dependency

From `src/ai-peers/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Put the wrapper scripts on your PATH

Scripts live in `bin/`:

- `ai-peers`
- `ai-delegate`
- `ai-dispatch`
- `codex-orchestrator`
- `agent-hard`
- `opencode-easy`
- `claude-code-worker`

## 3. Register the MCP server

The peer bus MCP server is:

```text
src/ai-peers/server.py
```

Use the Python interpreter from the local virtualenv:

```text
src/ai-peers/.venv/bin/python src/ai-peers/server.py
```

## 4. Understand the agent model

Primary agents:

- `codex`
- `claude`
- `cursor`
- `opencode`

Additional agents:

- explicit built-in target: `goose`
- adapter-configured targets: `gemini`, `aider`, `amp`, `cline`, `droid`, and other shell-callable tools

`ai-delegate --target auto` routes only among the primary four unless optional agents are explicitly allowlisted in routing config.

## 5. Install the shared skill

The shareable skill is:

```text
skills/agent-delegation/SKILL.md
```

It teaches supported tools how to call:

```bash
ai-delegate --target codex|claude|cursor|opencode|goose|<adapter>|auto ...
```

## 6. Optional routing config

Routing config is read from:

```text
.ai-bridge/routing.json
~/.config/ai-bridge/routing.json
```

Example:

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

## 7. Optional verification config

Verification config is read from:

```text
.ai-bridge/verify.json
~/.config/ai-bridge/verify.json
```

Example:

```json
{
  "profiles": {
    "default": {
      "command": ["python3", "-m", "unittest", "discover", "-s", "src/ai-peers/tests", "-p", "test_*.py"]
    }
  }
}
```

Use it with:

```bash
ai-delegate --target opencode --verify default --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
```

## 8. Optional hook wiring

The `hooks/` directory contains:

- `ai-peers-context.mjs`
- `codex-orchestrator-context.mjs`

These are used for:

- automatic unread peer-message injection
- background-job completion summaries
- Codex startup orchestration guidance

## 9. Optional worktree isolation

Use one of:

```bash
ai-delegate --target cursor --worktree auto --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
ai-delegate --target cursor --worktree branch:auth-refactor --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
```

Worktrees are retained by default. Remove them manually with:

```bash
ai-dispatch cleanup-worktree <job_id>
```

## 10. Adapter registry

Non-primary additional targets are configured via:

```text
~/.config/ai-bridge/adapters.json
```

Use the example file in:

```text
examples/adapters.example.json
```

## 11. Portability env vars

- `AI_BRIDGE_ROOT`
- `AI_BRIDGE_DISPATCH_BIN`
- `AI_BRIDGE_PEERS_PYTHON`
- `AI_BRIDGE_PEERS_CLI`
- `AI_BRIDGE_CONFIG_DIR`
- `AI_DISPATCH_STATE_ROOT`

## Safety

- Do not copy your real config files with API tokens into a public repo.
- Recreate config wiring from safe snippets instead of publishing live local config.
- This project does not include a TUI, cost tracking, sandboxing, or auto-merge.
