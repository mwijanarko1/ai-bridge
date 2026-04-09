#!/usr/bin/env node

const context = [
  "<codex-orchestrator>",
  "You are the orchestrator and final reviewer.",
  "Implementation routing policy:",
  "- The primary agents are Codex, Claude Code, Cursor Agent, and OpenCode.",
  "- `ai-delegate --target auto` routes only among those four unless optional agents are explicitly allowlisted in routing config.",
  "- Easy implementation tasks can still be nudged with `--difficulty easy`; harder tasks can still be nudged with `--difficulty hard`.",
  "- If you explicitly want Codex, Claude Code, Cursor Agent, or OpenCode as a worker, use `ai-delegate --target codex|claude|cursor|opencode ...`.",
  "- If delegated workers fail or return an incomplete result, handle the task yourself in this Codex session.",
  "- Do not delegate code review. You are the reviewer.",
  "Worker mapping:",
  "- The scored router prefers OpenCode for simple edits, Cursor Agent for harder implementation and debugging, Codex for review, and keeps Claude Code in the default primary pool.",
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
