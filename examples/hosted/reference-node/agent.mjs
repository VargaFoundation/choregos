// SPDX-License-Identifier: Apache-2.0
// The reference hosted agent on Node: a tool-calling loop over chat completions, no dependency.
//
// It reads what the platform gives every hosted container (ADR 0080) and nothing else: the model at
// `OPENAI_BASE_URL` with `OPENAI_API_KEY`, the MCP servers at `CHOREGOS_MCP_URL<name>` for each name
// of `CHOREGOS_MCP_SERVERS`. Node's `fetch` never goes through `HTTPS_PROXY`: the loopback is
// reached directly. The same image runs anywhere these variables point to a model and to
// streamable-HTTP MCP servers.

const INSTRUCTIONS =
  'You triage tickets. Use the tools to read the ticket, then answer one JSON object: {"status": "done", "summary": "<one sentence>"}.';
const MAX_STEPS = 8;

async function post(url, body, headers) {
  const answer = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(300_000),
  });
  let raw = await answer.text();
  if (!answer.ok) throw new Error(`${url} answered ${answer.status}: ${raw.slice(0, 300)}`);
  if ((answer.headers.get("content-type") ?? "").startsWith("text/event-stream")) {
    const data = raw.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim());
    raw = data.at(-1) ?? "{}";
  }
  return [raw ? JSON.parse(raw) : {}, answer.headers];
}

/** One streamable-HTTP MCP server, spoken to in JSON-RPC. */
class McpServer {
  constructor(name, url) {
    this.name = name;
    this.url = url;
    this.headers = { accept: "application/json, text/event-stream" };
    this.ids = 0;
  }

  async rpc(method, params) {
    this.ids += 1;
    const [answer, headers] = await post(this.url, { jsonrpc: "2.0", id: this.ids, method, params }, this.headers);
    const session = headers.get("mcp-session-id");
    if (session) this.headers["mcp-session-id"] = session;
    if (answer.error) throw new Error(`${this.name}: ${JSON.stringify(answer.error)}`);
    return answer.result ?? {};
  }

  async tools() {
    await this.rpc("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "choregos-reference-node-agent", version: "1" },
    });
    await fetch(this.url, {
      method: "POST",
      headers: { "content-type": "application/json", ...this.headers },
      body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
    });
    return (await this.rpc("tools/list", {})).tools ?? [];
  }

  async call(tool, args) {
    const result = await this.rpc("tools/call", { name: tool, arguments: args });
    return (result.content ?? []).map((part) => String(part.text ?? "")).join("\n");
  }
}

export async function answer(text) {
  const base = process.env.OPENAI_BASE_URL.replace(/\/+$/, "");
  const headers = { authorization: `Bearer ${process.env.OPENAI_API_KEY}` };
  const model = process.env.CHOREGOS_MODEL ?? "default";
  const names = (process.env.CHOREGOS_MCP_SERVERS ?? "").split(",").filter(Boolean);
  const owners = new Map();
  const tools = [];
  for (const name of names) {
    const server = new McpServer(name, process.env.CHOREGOS_MCP_URL + name);
    for (const tool of await server.tools()) {
      owners.set(tool.name, server);
      tools.push({
        type: "function",
        function: { name: tool.name, description: tool.description ?? "", parameters: tool.inputSchema ?? { type: "object" } },
      });
    }
  }
  const messages = [
    { role: "system", content: INSTRUCTIONS },
    { role: "user", content: text },
  ];
  for (let step = 0; step < MAX_STEPS; step += 1) {
    const [completion] = await post(`${base}/chat/completions`, { model, messages, ...(tools.length ? { tools } : {}) }, headers);
    const message = completion.choices[0].message;
    const calls = message.tool_calls ?? [];
    if (calls.length === 0) return String(message.content ?? "");
    messages.push(message);
    for (const call of calls) {
      const server = owners.get(call.function.name);
      const output = server
        ? await server.call(call.function.name, JSON.parse(call.function.arguments || "{}"))
        : `no tool named ${call.function.name}`;
      messages.push({ role: "tool", tool_call_id: call.id, content: output });
    }
  }
  throw new Error(`no answer after ${MAX_STEPS} model calls`);
}
