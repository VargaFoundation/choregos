// SPDX-License-Identifier: Apache-2.0
// Foundry's hosted-agent contract on `$PORT`, with Node's standard library only (ADR 0080).
//
// Every Node example of `examples/hosted/` carries this file unchanged (a test refuses a copy that
// differs, as for `responses_server.py`): no shared library (ADR 0063). It serves `GET /readiness`,
// `POST /responses` (the Responses API, as server-sent events when the body asks `stream: true`) and
// `POST /invocations` (a JSON body in, `{"output": ...}` out), and hands the text of the request to
// `answer(text)` of `./agent.mjs`, the one function an example writes, plain or `async`. On SIGTERM
// it stops accepting, lets the request in flight finish and exits 0.

import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { answer } from "./agent.mjs";

/** The text of a request: `input` as a string, or the text parts of its messages. */
export function requestText(body) {
  const value = body.input ?? "";
  if (typeof value === "string") return value;
  const parts = [];
  for (const item of Array.isArray(value) ? value : []) {
    const content = item && typeof item === "object" ? item.content : item;
    if (typeof content === "string") parts.push(content);
    else if (Array.isArray(content)) parts.push(...content.filter((c) => c && typeof c === "object").map((c) => String(c.text ?? "")));
  }
  return parts.filter(Boolean).join("\n");
}

async function runAgent(text) {
  const output = await answer(text);
  return typeof output === "string" ? output : JSON.stringify(output);
}

function responseObject(text, status = "completed", error = "") {
  const message = {
    type: "message",
    id: `msg_${randomUUID().replaceAll("-", "")}`,
    role: "assistant",
    status: "completed",
    content: [{ type: "output_text", text, annotations: [] }],
  };
  return {
    id: `resp_${randomUUID().replaceAll("-", "")}`,
    object: "response",
    created_at: Math.floor(Date.now() / 1000),
    status,
    model: process.env.CHOREGOS_MODEL ?? "",
    output: text ? [message] : [],
    error: error ? { code: "server_error", message: error } : null,
  };
}

function send(res, status, contentType, content) {
  const bytes = Buffer.from(content);
  res.writeHead(status, { "content-type": contentType, "content-length": bytes.length });
  res.end(bytes);
}

async function responses(res, body) {
  let response;
  try {
    response = responseObject(await runAgent(requestText(body)));
  } catch (error) {
    console.error(error);
    response = responseObject("", "failed", String(error?.message ?? error).slice(0, 500));
  }
  if (!body.stream) return send(res, 200, "application/json", JSON.stringify(response));
  const text = response.output.flatMap((item) => item.content.map((part) => part.text)).join("");
  const final = response.status === "completed" ? "response.completed" : "response.failed";
  const events = [
    { type: "response.created", response: { ...response, status: "in_progress", output: [] } },
    ...(text ? [{ type: "response.output_text.delta", output_index: 0, delta: text }] : []),
    { type: final, response },
  ];
  const stream = events.map((e, n) => `event: ${e.type}\ndata: ${JSON.stringify({ ...e, sequence_number: n })}\n\n`).join("");
  send(res, 200, "text/event-stream", stream);
}

async function invocations(res, body) {
  let output;
  try {
    output = await runAgent(requestText(body));
  } catch (error) {
    console.error(error);
    return send(res, 500, "application/json", JSON.stringify({ error: String(error?.message ?? error) }));
  }
  send(res, 200, "application/json", JSON.stringify({ output }));
}

const server = createServer(async (req, res) => {
  if (req.method === "GET") {
    if (req.url === "/readiness") return send(res, 200, "application/json", '{"status":"ready"}');
    return send(res, 404, "application/json", '{"error":"not found"}');
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  let body;
  try {
    body = JSON.parse(Buffer.concat(chunks).toString() || "{}");
  } catch {
    return send(res, 400, "application/json", '{"error":"the body is not JSON"}');
  }
  if (req.method === "POST" && req.url === "/invocations") return invocations(res, body);
  if (req.method === "POST" && req.url === "/responses") return responses(res, body);
  send(res, 404, "application/json", '{"error":"not found"}');
});

let stopping = false;
function stop() {
  if (stopping) return;
  stopping = true;
  // No new connection; the request in flight finishes, then the process exits 0 (since Node 19,
  // `close` also drops the idle keep-alive connections).
  server.close(() => process.exit(0));
}
process.on("SIGTERM", stop);
process.on("SIGINT", stop);

// Every interface of the container: the runner reaches it on the pod's loopback.
const host = process.env.HOST ?? "0.0.0.0";
const port = Number(process.env.PORT ?? "8088");
server.listen(port, host, () => console.log(`listening on ${host}:${port}`));
