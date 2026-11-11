// SPDX-License-Identifier: Apache-2.0
// The fake Choregos API of the assistants journeys (S63-18): what the assistants pages, a conversation and the
// installation form ask, with fixed answers. Plain node, no dependency. `GET /__calls` returns what it was asked.
//
//   fake API  127.0.0.1:4200   the signed-in person is a member of `varga` and `acme`; `coach` is of `acme`
import { createServer } from "node:http";

const calls = [];
const json = (res, status, body) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};
const readBody = (req) =>
  new Promise((done) => {
    let text = "";
    req.on("data", (c) => (text += c));
    req.on("end", () => done(text ? JSON.parse(text) : null));
  });

const spaces = [{ name: "preferred_sources", scope: "user", kind: "facts" }];
const coach = {
  org: "acme",
  slug: "coach",
  name: "one-on-one-coach",
  display_name: "One-on-one coach",
  description: "Prepares a one-to-one",
  status: "ready",
  memory_spaces: spaces,
  tasks: [],
  needs: [],
};
// A conversation says whose it is: `org` and `assistant`, so no `?assistant=` is needed to show its memory.
const conversation = {
  id: "c1",
  title: "Preparing Ada",
  agent: "coach",
  agent_version: 1,
  installation_id: "i1",
  org: "acme",
  assistant: "coach",
  status: "open",
  turns: 1,
  cost_usd: 0.01,
  created_at: "2026-11-10T10:00:00Z",
  last_activity_at: "2026-11-10T10:00:00Z",
  messages: [
    { role: "user", content: "How do I open the meeting?" },
    { role: "assistant", content: "Start with **a question** [Coaching guide, 2]." },
  ],
};
const memory = {
  space: "preferred_sources",
  scope: "user",
  kind: "facts",
  writes: "agent_proposes",
  subjects: [
    {
      subject: null,
      facts: [{ id: "f1", content: "Prefers short meetings", status: "proposed", pinned: false }],
      profiles: [],
    },
  ],
};
// A write the assistant proposes, with what the source's preview says it would change.
const action = {
  id: "a1",
  conversation_id: "c1",
  need: "crm",
  operation: "update_contact",
  arguments: { phone: "02" },
  summary: "Update Ada's phone",
  status: "proposed",
  diff: { phone: { from: "01", to: "02" } },
  created_at: "2026-11-10T10:00:00Z",
};
const shipped = {
  items: [
    {
      name: "one-on-one-coach",
      display_name: "One-on-one coach",
      description: "Prepares a one-to-one",
      config_schema: {
        type: "object",
        required: ["company_name", "hr_contact"],
        properties: {
          company_name: { type: "string", default: "Diametral" },
          hr_contact: { type: "string", description: "Who to refer sensitive matters to" },
          embed_origins: { type: "array", items: { type: "string", format: "uri" }, default: ["https://scorecard.example.org"] },
          tone: { oneOf: [{ const: "warm", title: "Warm" }, { const: "direct", title: "Direct" }] },
          review_days: { anyOf: [{ type: "integer" }, { type: "null" }] },
          topics: { type: "array", items: { $ref: "#/$defs/topic" } },
        },
        $defs: { topic: { type: "object", properties: { label: { type: "string" } } } },
      },
      suggested_groups: ["grp-managers"],
      needs: [],
    },
  ],
};

createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname.replace("/api/v1", "");
  const payload = req.method === "GET" ? null : await readBody(req);
  if (path === "/__calls") return json(res, 200, calls);
  calls.push({ method: req.method, path, body: payload });
  if (path === "/me") return json(res, 200, { email: "ada@acme.test", memberships: [{ org: "acme", role: "org_admin" }] });
  if (path === "/orgs") return json(res, 200, [{ slug: "varga", name: "Varga" }, { slug: "acme", name: "Acme" }]);
  if (path === "/me/assistants") return json(res, 200, { items: [coach] });
  if (path === "/me/assistants/acme/coach/conversations") return json(res, 201, conversation);
  if (path === "/me/conversations") return json(res, 200, { items: [conversation], meta: { has_more: false } });
  if (path === "/me/conversations/c1") return json(res, 200, conversation);
  if (path === "/me/conversations/c1/actions") return json(res, 200, { items: [action] });
  if (path === "/me/assistants/acme/coach/memory-export") {
    return json(res, 200, { installation: "coach", organisation: "acme", exported_at: "2026-11-11T08:00:00Z", spaces: [memory] });
  }
  if (path === "/me/assistants/acme/coach/memory/preferred_sources") return json(res, 200, memory);
  if (path === "/orgs/acme/agent-sessions/c1/feedback") return json(res, 201, { id: "fb1", rating: payload?.rating });
  if (path === "/orgs/acme/assistants") {
    if (req.method === "POST") return json(res, 201, { ...coach, slug: "coach", plugin: "coaching", plugin_version: "1.0.0", channel: "stable" });
    return json(res, 200, { items: [] });
  }
  if (path === "/orgs/acme/plugins") return json(res, 200, { items: [{ name: "coaching", version: "1.0.0" }] });
  if (path === "/orgs/acme/plugins/coaching/assistants") return json(res, 200, shipped);
  if (path === "/me/conversations/c1/messages") {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write('event: response.output_text.delta\ndata: {"delta":"Ask what "}\n\n');
    res.write('event: response.output_text.delta\ndata: {"delta":"went well."}\n\n');
    res.end('event: response.completed\ndata: {"status":"completed"}\n\n');
    return;
  }
  return json(res, 404, { title: "Not Found" });
}).listen(4200, "127.0.0.1");
