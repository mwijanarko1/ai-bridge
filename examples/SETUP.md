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

Additional explicit targets supported natively by the dispatcher:

- `goose`

## 3. Register the MCP server

The peer bus MCP server is:

```text
src/ai-peers/server.py
```

Use the Python interpreter from the local virtualenv:

```text
src/ai-peers/.venv/bin/python src/ai-peers/server.py
```

## 4. Install the shared skill

The shareable skill is:

```text
skills/agent-delegation/SKILL.md
```

It teaches supported tools how to call:

```bash
ai-delegate --target codex|cursor|opencode|claude|goose|gemini|aider|amp|cline|droid|auto ...
```

For long-running work it also supports:

```bash
ai-delegate --target auto --difficulty hard --background --notify-on-complete ...
```

## 5. Optional hook wiring

The `hooks/` directory contains:

- `ai-peers-context.mjs`
- `codex-orchestrator-context.mjs`

These are used for:

- automatic unread peer-message injection
- Codex startup orchestration guidance

## 6. Claude Code

`Claude Code` is supported as an explicit delegation target:

```bash
ai-delegate --target claude --cwd "$PWD" --from-agent codex -- "Investigate the bug"
```

## 7. Adapter Registry

Non-native targets are configured via:

```text
~/.config/ai-bridge/adapters.json
```

Use the example file in:

```text
examples/adapters.example.json
```

This is the path for targets like `gemini`, `aider`, `amp`, `cline`, and `droid`.

## Safety

- Do not copy your real config files with API tokens into a public repo.
- Recreate config wiring from safe snippets instead of publishing live local config.
