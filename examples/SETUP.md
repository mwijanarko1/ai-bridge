# Setup Notes

These are the minimum wiring steps for the exported source. For open-source usage, the canonical install path is `pipx install .` from the repo root.

Assumption: the machine already has the relevant CLIs installed and authenticated. This document does not cover login flows.

## 1. Install the package

Recommended from the repo root:

```bash
pipx install .
```

If you are developing from a source checkout instead:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 2. Put the wrapper scripts on your PATH

With `pipx`, the commands are installed globally. In a source checkout, scripts also live in `bin/`:

- `ai-peers`
- `ai-peers-mcp`
- `ai-bridge-setup-hooks`
- `ai-delegate`
- `ai-dispatch`
- `codex-orchestrator`
- `agent-hard`
- `opencode-easy`
- `claude-code-worker`

## 3. Install peer inbox hooks

Run:

```bash
ai-bridge-setup-hooks
```

This writes generated hook files to:

```text
~/.config/ai-bridge/hooks/
```

It also merges Codex and Cursor hook config and installs an OpenCode plugin at:

```text
~/.config/opencode/plugins/ai-bridge-peers.ts
```

Restart Codex, Cursor Agent, and OpenCode after setup. Use `ai-bridge-setup-hooks --dry-run --json` to inspect planned writes without changing files.

## 4. Register the MCP server

The preferred MCP server command after install is:

```text
ai-peers-mcp
```

If you are developing from a local virtualenv, you can also use:

```text
python -m ai_peers.server
```

## 5. Understand the agent model

Primary agents:

- `codex`
- `claude`
- `cursor`
- `opencode`

Additional agents:

- explicit built-in targets: `goose`, `qwen`
- adapter-configured targets: `gemini`, `aider`, `amp`, `cline`, `droid`, and other shell-callable tools

`ai-delegate --target auto` routes only among the primary four unless optional agents are explicitly allowlisted in routing config.

## 6. Install the shared skill

The shareable skill is:

```text
skills/agent-delegation/SKILL.md
```

It teaches supported tools how to call:

```bash
ai-delegate --target codex|claude|cursor|opencode|goose|qwen|<adapter>|auto ...
```

## 7. Optional routing config

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

## 8. Optional verification config

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
      "command": ["python3", "-m", "unittest", "discover", "-s", "src/ai_peers/tests", "-p", "test_*.py"]
    }
  }
}
```

Use it with:

```bash
ai-delegate --target opencode --verify default --cwd "$PWD" --from-agent codex -- "Add a simple settings toggle"
```

## 9. Optional hook wiring

The `ai-bridge-setup-hooks` command is the supported way to install hooks. The `hooks/` directory also contains source-tree copies of:

- `ai-peers-context.mjs`
- `codex-orchestrator-context.mjs`

These are used for:

- automatic unread peer-message injection
- background-job completion summaries
- Codex startup orchestration guidance

## 10. Optional worktree isolation

Use one of:

```bash
ai-delegate --target cursor --worktree auto --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
ai-delegate --target cursor --worktree branch:auth-refactor --cwd "$PWD" --from-agent codex -- "Refactor the auth flow safely"
```

Worktrees are retained by default. Remove them manually with:

```bash
ai-dispatch cleanup-worktree <job_id>
```

## 11. Adapter registry

Non-primary additional targets are configured via:

```text
~/.config/ai-bridge/adapters.json
```

Use the example file in:

```text
examples/adapters.example.json
```

## 12. Portability env vars

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
