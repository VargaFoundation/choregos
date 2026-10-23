// SPDX-License-Identifier: Apache-2.0
// The ticket triager on the GitHub Copilot SDK for Node, as a hosted agent (ADR 0080).
//
// The SDK drives the Copilot CLI runtime, which npm installs from the SDK's platform package: there
// is nothing to fetch when the container starts. In BYOK mode (`provider`), the runtime speaks chat
// completions to the platform's model at `OPENAI_BASE_URL` with `OPENAI_API_KEY` (the loopback key),
// without a GitHub account. It is given the step's MCP servers (`CHOREGOS_MCP_URL<name>`, HTTP) and
// none of its built-in tools: a hosted triager reads, it does not edit files or run commands.

import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { approveAll, CopilotClient, ToolSet } from "@github/copilot-sdk";

const INSTRUCTIONS =
  'You triage tickets. Use the tools to read the ticket, then answer one JSON object and nothing else: {"status": "done", "summary": "<one sentence>"}.';

export async function answer(text) {
  const names = (process.env.CHOREGOS_MCP_SERVERS ?? "").split(",").filter(Boolean);
  const home = await mkdtemp(join(tmpdir(), "copilot-"));
  const client = new CopilotClient({ workingDirectory: home, baseDirectory: home, useLoggedInUser: false });
  try {
    await client.start();
    const session = await client.createSession({
      onPermissionRequest: approveAll,
      model: process.env.CHOREGOS_MODEL ?? "default",
      provider: {
        type: "openai",
        wireApi: "completions",
        baseUrl: process.env.OPENAI_BASE_URL,
        apiKey: process.env.OPENAI_API_KEY,
      },
      systemMessage: { mode: "replace", content: INSTRUCTIONS },
      mcpServers: Object.fromEntries(
        names.map((name) => [name, { type: "http", url: process.env.CHOREGOS_MCP_URL + name, tools: ["*"] }]),
      ),
      // The MCP tools only: none of the runtime's shell, file and web tools.
      availableTools: new ToolSet().addMcp("*"),
      workingDirectory: home,
    });
    // The runtime connects the MCP servers in the background: the first model call can leave before
    // the tools are known, and the model then answers without reading the ticket (S41-23: seen
    // through a slower loopback). `mcp.list` starts them and waits for their status.
    const { servers } = await session.rpc.mcp.list();
    console.error("mcp servers:", JSON.stringify(servers.map(({ name, status, error }) => ({ name, status, error }))));
    const reply = await session.sendAndWait(text, 300_000);
    await session.disconnect();
    return String(reply?.data?.content ?? "");
  } finally {
    await client.stop();
    await rm(home, { recursive: true, force: true });
  }
}
