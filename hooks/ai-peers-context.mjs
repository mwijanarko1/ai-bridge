#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.env.AI_BRIDGE_ROOT || path.resolve(HERE, "..");
const PYTHON = process.env.AI_BRIDGE_PEERS_PYTHON || "python3";
const CLI = process.env.AI_BRIDGE_PEERS_CLI || path.join(ROOT, "src", "ai-peers", "cli.py");
const DISPATCH = process.env.AI_BRIDGE_DISPATCH_BIN || path.join(ROOT, "bin", "ai-dispatch");
const TOOL = process.env.GUARDRAIL_TOOL || "generic";
const EVENT = process.env.GUARDRAIL_EVENT || "UserPromptSubmit";
const SESSION_KEY = process.env.AI_PEERS_SESSION_KEY || "";

function parseInput(raw) {
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function promptText(input) {
  return String(input.prompt || input.message || input.text || "").trim();
}

function summarizePrompt(text) {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return "";
  return clean.length <= 140 ? clean : `${clean.slice(0, 137)}...`;
}

function runCli(args) {
  if (!existsSync(CLI)) {
    return null;
  }
  const result = spawnSync(PYTHON, [CLI, ...args], {
    env: process.env,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    return null;
  }
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

function runDispatch(args) {
  const command = existsSync(DISPATCH) ? DISPATCH : "ai-dispatch";
  const result = spawnSync(command, args, {
    env: process.env,
    encoding: "utf8",
  });
  if (result.status !== 0 && !result.stdout.trim()) {
    return null;
  }
  try {
    return JSON.parse(result.stdout);
  } catch {
    return null;
  }
}

function formatContext(peer, messages) {
  const lines = [
    "<peer-messages>",
    "Unread coordination messages from other local AI sessions:",
  ];
  for (const message of messages) {
    const parts = [
      `from=${message.from_client || "unknown"}`,
      message.from_role ? `role=${message.from_role}` : "",
      message.from_cwd ? `cwd=${message.from_cwd}` : "",
      message.from_summary ? `summary=${message.from_summary}` : "",
    ].filter(Boolean);
    lines.push(`- [${message.from_peer_id}] ${parts.join(" | ")} | message=${message.body}`);
  }
  if (peer?.role === "orchestrator-reviewer") {
    lines.push("Routing policy: you are the orchestrator and reviewer. The primary agents are Codex, Claude Code, Cursor Agent, and OpenCode. Auto-routing defaults to those four only.");
  }
  lines.push("Treat these as fresh peer updates. Resolve conflicts before making overlapping edits.");
  lines.push("</peer-messages>");
  return lines.join("\n");
}

function formatCompletionContext(jobs) {
  const stripAnsi = (text) => String(text || "").replace(/\u001b\[[0-9;]*m/g, "");
  const lines = [
    "<delegation-completions>",
    "Completed delegated worker jobs:",
  ];
  for (const job of jobs) {
    const winner = job.winner || "none";
    const status = job.status || "unknown";
    const attempts = Array.isArray(job.attempts) ? job.attempts : [];
    const classifier = job.classifier || {};
    const verification = job.verification || {};
    const worktree = job.worktree || {};
    const detail = attempts
      .map((attempt) => {
        const output = stripAnsi(attempt.stdout || attempt.stderr || "").replace(/\s+/g, " ").trim();
        const short = output.length > 260 ? `${output.slice(0, 257)}...` : output;
        return `${attempt.worker}[exit=${attempt.exit_code}] ${short}`;
      })
      .join(" || ");
    const suffix = [
      classifier.category ? `category=${classifier.category}` : "",
      classifier.complexity ? `complexity=${classifier.complexity}` : "",
      verification.status ? `verify=${verification.status}` : "",
      worktree.path ? `worktree=${worktree.path}` : "",
    ]
      .filter(Boolean)
      .join(" ");
    lines.push(
      `- job=${job.job_id} status=${status} winner=${winner} difficulty=${job.difficulty} route=${(job.route || []).join(",")} ${suffix} | ${detail}`
    );
  }
  lines.push("These notifications come from background delegated jobs finishing. Use them before launching overlapping follow-up work.");
  lines.push("</delegation-completions>");
  return lines.join("\n");
}

function emit(context) {
  if (!context) return;
  if (TOOL === "cursor") {
    process.stdout.write(`${JSON.stringify({ additional_context: context })}\n`);
    return;
  }
  process.stdout.write(
    `${JSON.stringify({
      hookSpecificOutput: {
        hookEventName: EVENT,
        additionalContext: context,
      },
    })}\n`,
  );
}

const raw = readFileSync(0, "utf8");
const input = parseInput(raw);

if (SESSION_KEY) {
  const text = summarizePrompt(promptText(input));
  if (text) {
    runCli(["set-summary-for", text]);
  }
  const completions = runDispatch(["poll-completions", "--session-key", SESSION_KEY, "--limit", "8"]);
  const polled = runCli(["poll", "--limit", "8"]);
  const messages = polled?.messages || [];
  const completionJobs = completions?.jobs || [];
  const parts = [];
  if (messages.length > 0) {
    parts.push(formatContext(polled.peer, messages));
  }
  if (completionJobs.length > 0) {
    parts.push(formatCompletionContext(completionJobs));
  }
  if (parts.length > 0) {
    emit(parts.join("\n\n"));
  }
}
