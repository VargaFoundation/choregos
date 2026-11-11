// SPDX-License-Identifier: Apache-2.0
// The two fakes of the embedded component's journey (S63-12): a Choregos API that answers what the embed page
// asks, and two "host applications" (one on an allowed origin, one that is not). Plain node, no dependency.
//
//   fake API  127.0.0.1:4100   the installation `varga/coach` allows `http://localhost:3100` only
//   host      localhost:3100   an allowed origin   (`/?src=<embed url>`)
//   host      127.0.0.1:3200   NOT an allowed origin
//
// A frame under another site holds no console cookie: like the real API, the fake opens the component's routes
// only to the frame token (`X-Choregos-Embed`) that `POST .../embed-token` mints for a signed-in browser (the
// header `X-Fake-Session: ada` stands for the first-party cookie the sign-in popup carries). Every call is
// recorded with the token it carried.
//
// `GET /__calls` on the fake API returns what it was asked, so the test can see the opening context.
import { createServer } from "node:http";

const calls = [];
const TOKEN = "frame-token-ada";
const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};
const body = (req) =>
  new Promise((done) => {
    let text = "";
    req.on("data", (c) => (text += c));
    req.on("end", () => done(text ? JSON.parse(text) : null));
  });

const conversation = {
  id: "c1",
  agent: "coach",
  agent_version: 1,
  status: "open",
  turns: 0,
  cost_usd: 0,
  created_at: "2026-11-10T10:00:00Z",
  last_activity_at: "2026-11-10T10:00:00Z",
  messages: [],
};

createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname.replace("/api/v1", "");
  const payload = req.method === "POST" ? await body(req) : null;
  if (path === "/__calls") return json(res, 200, calls);
  const token = req.headers["x-choregos-embed"];
  calls.push({ method: req.method, path, body: payload, token: token ?? null });
  if (path === "/public/assistants/varga/coach/embed") return json(res, 200, { embed_origins: ["http://localhost:3100"] });
  if (path === "/me/assistants/varga/coach/embed-token" && req.method === "POST") {
    if (req.headers["x-fake-session"] !== "ada") return json(res, 401, { title: "Unauthorized" });
    return json(res, 200, { token: TOKEN, expires_in: 900 });
  }
  // The component's own routes: the cookie never comes from a frame, only the token.
  if (token !== TOKEN) return json(res, 401, { title: "Unauthorized" });
  if (path === "/me") return json(res, 200, { email: "ada@varga.test" });
  if (path === "/me/assistants/varga/coach/conversations") return json(res, 201, conversation);
  if (path === "/me/conversations/c1/actions") return json(res, 200, { items: [] });
  if (path === "/me/conversations/c1/messages") {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write('event: response.output_text.delta\ndata: {"delta":"Bonjour"}\n\n');
    res.end('event: response.completed\ndata: {"status":"completed","memory_updated":["notes"]}\n\n');
    return;
  }
  return json(res, 404, { title: "Not Found" });
}).listen(4100, "127.0.0.1");

// A host page: frames the embed URL it is given and records every message it receives in `window.__events`.
const hostPage = `<!doctype html><title>host</title><body>
<iframe id="assistant" title="assistant" width="480" height="420"></iframe>
<script>
  window.__events = [];
  window.addEventListener("message", (event) => {
    window.__events.push({ origin: event.origin, data: event.data });
  });
  document.getElementById("assistant").src = new URLSearchParams(location.search).get("src");
</script></body>`;
const host = (_req, res) => {
  res.writeHead(200, { "Content-Type": "text/html" });
  res.end(hostPage);
};
createServer(host).listen(3100, "localhost");
createServer(host).listen(3200, "127.0.0.1");
