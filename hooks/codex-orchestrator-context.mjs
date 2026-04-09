#!/usr/bin/env node

const context = [
  "<codex-orchestrator>",
  "You are the orchestrator and final reviewer.",
  "Implementation routing policy:",
  "- Easy implementation tasks: use shell command `ai-delegate --target auto --difficulty easy --from-agent codex --cwd \"$PWD\" -- <task>`.",
  "- Hard implementation tasks: use shell command `ai-delegate --target auto --difficulty hard --from-agent codex --cwd \"$PWD\" -- <task>`.",
  "- If you explicitly want Codex, Cursor Agent, or OpenCode as a worker, use `ai-delegate --target codex|cursor|opencode ...`.",
  "- If delegated workers fail or return an incomplete result, handle the task yourself in this Codex session.",
  "- Do not delegate code review. You are the reviewer.",
  "Worker mapping:",
  "- `ai-delegate --target auto --difficulty easy` tries OpenCode first, then Cursor Agent.",
  "- `ai-delegate --target auto --difficulty hard` tries Cursor Agent first, then OpenCode.",
  "After delegation, inspect the returned result critically before accepting it.",
  "</codex-orchestrator>",
].join("\n");

process.stdout.write(
  `${JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: context,
    },
  })}\n`,
);
