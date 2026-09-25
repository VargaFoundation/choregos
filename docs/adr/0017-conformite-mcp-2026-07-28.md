# 0017 — MCP 2026-07-28 conformance: stateless tools, tasks, elicitation, resource-bound OAuth

- **Status**: proposed, 2026-09-25 — not implemented
- **Concerns**: `packages/tools-mcp`, the runner sidecar, the catalogue's external MCP servers (ADR 0014)

## Context

`choregos-tools` is a deliberately small MCP server: `initialize`, `tools/list`, `tools/call`,
`ping`, protocol version `2025-06-18`, JSON-RPC over HTTP on localhost in the runner sidecar.
It holds nothing but the run token and relays every call to the internal API. Eleven tools
(`validate_result`, `report_finding`, `request_scope_change`, `ask_human`, `get_ticket`,
`get_spec`, `get_plan`, `get_ci_logs`, `get_context`, `search_memory`, `propose_fact`), plus
the catalogue's tools loaded lazily.

The 2026-07-28 revision of MCP changed four things that touch us:

1. **Stateless streamable HTTP** is the normative transport; a server may answer any request
   without a prior `initialize` on the same connection. Our sidecar already behaves that way
   by accident (no session state); it does not say so.
2. **Tasks**: a call may return a task handle and be polled or awaited, instead of holding
   the HTTP request open. Two of our tools are slow by nature: `get_ci_logs` waits for a
   pipeline, `ask_human` waits for a person (today it ends the stage on `needs_human` and
   the stage is replayed after the answer, ADR 0013 Decision 3).
3. **Multi-turn elicitation**: a server may ask the *client* (the agent) for structured
   input mid-call. This is the protocol-level form of our `question`, which today travels
   as a free-text `ask_human` plus a `StageResult.questions[]` list the agents keep getting
   wrong (`result.repair` on the bench).
4. **OAuth resource server + RFC 8707 resource indicators**: a client must bind its token to
   the exact server it calls. For the catalogue's external MCP servers (`AppelMcp`), the
   platform holds the key (ADR 0014 Decision 2); the agent never sees it, so the binding is
   the platform's job, not the agent's.

## Decision

1. **Announce what is already true.** `choregos-tools` declares protocol `2026-07-28`,
   stateless transport, and keeps refusing anything it does not implement with `-32601`.
   A conformance test replays the published transport test vectors against `McpServer`.
2. **Tasks for the two slow tools, and only those.** `get_ci_logs` and `ask_human` return a
   task; the sidecar polls the internal API. `ask_human` stays *terminal for the stage* when
   the executor cannot suspend (ADR 0013), and becomes a genuine wait when it can (ADR 0016).
   The agent sees one contract in both cases; the executor's capability decides the cost.
3. **Elicitation replaces free-text questions.** `ask_human` elicits a schema (`question`,
   `options[]`, `blocking`) from the agent instead of parsing prose; `StageResult.questions`
   keeps its shape and is filled by the server from the elicitation. The `result.repair`
   counter on questions must reach zero on the bench before the old path is removed.
4. **Resource-bound tokens for external MCP servers.** When the catalogue calls an external
   MCP server with an OAuth credential, the platform requests the token with `resource=` set
   to that server's URL, and refuses a credential that cannot be bound. A static API key in
   `credential_env` stays allowed: it is the operator's choice, and it is logged as such in
   the access report.

## Conditions

- The internal API is the only thing behind the sidecar. Nothing in this ADR gives the agent
  a new way to reach the network; the egress proxy (P1-5) and the catalogue stay the
  boundary.
- Tasks need a durable place to live across a runner restart: the run's journal
  (`run_events`), not sidecar memory.

## Consequences

- One protocol version to maintain, tested against vectors instead of trusted.
- Questions become data the platform can render (options, blocking or not) and the human
  screen (P0-5 "Garanties", decision bar) gets a real form instead of a text block.
- The catalogue's external servers are called with tokens that cannot be replayed elsewhere.

## Alternatives discarded

- **Adopting the reference SDK's server** for the sidecar: it brings sessions, resources,
  prompts and sampling we do not want the agent to have. The explicit, minimal server is the
  security argument; it stays.
- **Letting the agent talk to external MCP servers directly**: ruled out by ADR 0014.
