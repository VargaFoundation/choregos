// SPDX-License-Identifier: Apache-2.0
// The ticket triager on the Claude Agent SDK for TypeScript, as a hosted agent (ADR 0080).
//
// The SDK drives the Claude Code CLI it carries. The CLI reads the platform's model from
// `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` (the loopback key) and is given the step's MCP
// servers (`CHOREGOS_MCP_URL<name>`, HTTP) and none of its built-in tools: a hosted triager reads,
// it does not edit files or run commands. The CLI's own traffic (telemetry, error reports, updates)
// is turned off: a step's network does not reach it (the conformance suite's `no_egress` check fails
// otherwise).

import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { query } from "@anthropic-ai/claude-agent-sdk";

const INSTRUCTIONS =
  'You triage tickets. Use the tools to read the ticket, then answer one JSON object and nothing else: {"status": "done", "summary": "<one sentence>"}.';
const QUIET = {
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1",
  DISABLE_TELEMETRY: "1",
  DISABLE_ERROR_REPORTING: "1",
  DISABLE_AUTOUPDATER: "1",
};

export async function answer(text) {
  const names = (process.env.CHOREGOS_MCP_SERVERS ?? "").split(",").filter(Boolean);
  const home = await mkdtemp(join(tmpdir(), "claude-"));
  try {
    const messages = query({
      prompt: text,
      options: {
        systemPrompt: INSTRUCTIONS,
        model: process.env.CHOREGOS_MODEL,
        tools: [],
        mcpServers: Object.fromEntries(
          names.map((name) => [name, { type: "http", url: process.env.CHOREGOS_MCP_URL + name }]),
        ),
        strictMcpConfig: true,
        allowedTools: names.map((name) => `mcp__${name}`),
        settingSources: [],
        maxTurns: 8,
        cwd: home,
        // `env` replaces the CLI's environment rather than adding to it, hence `process.env` first;
        // the CLI keeps its state in a directory of its own, never in the image's home.
        env: { ...process.env, ...QUIET, CLAUDE_CONFIG_DIR: home },
      },
    });
    let final = "";
    for await (const message of messages) {
      if (message.type !== "result") continue;
      if (message.is_error) throw new Error(`the agent stopped: ${message.subtype}: ${message.result ?? ""}`);
      final = message.result ?? "";
    }
    return final;
  } finally {
    await rm(home, { recursive: true, force: true });
  }
}
