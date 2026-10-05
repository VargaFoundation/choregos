# 0030 — A door for external MCP clients, inside the API

- **Status**: accepted, 2026-10-05
- **Concerns**: the API process, API tokens, the ontology plugin's generated tools, the console,
  the chart's ingress; follows [ADR 0029](0029-une-plateforme-d-agents-gouvernes.md)

## Context

Until now, the only MCP server in Choregos is the per-run sidecar `choregos-tools`
(`packages/tools-mcp`): it listens on `127.0.0.1` inside the runner pod and authenticates to the
API with the run token. Nothing lets a person connect *their* agent — Claude Code, Claude Desktop,
Cursor, VS Code — to the platform, list their projects, open a work item, follow it, or see what
waits for a decision. The product owner asked for exactly that on 2026-10-05, with a page that
explains how, the way Higgsfield documents its own connector.

Three facts constrain the answer:

- **Claude's hosted surfaces** (claude.ai, Desktop connectors, mobile) call a remote MCP server
  from Anthropic's network, `160.79.104.0/21`, and require OAuth (dynamic client registration or a
  client ID metadata document) or, in beta, a static header. A tenant behind a VPN, like the
  development one, is unreachable for them. **Claude Code** runs on the person's machine: it
  reaches an internal host and accepts a bearer token in a header.
- **The specification** (Peredur, R-SOC-MCP-01 and -02) wants the server at `/mcp` and
  `/mcp/projects/{P}`, as an OAuth 2.1 resource server whose clients are registered in the
  identity provider, and says the platform issues no secret.
- **ADR 0017** kept the sidecar on a small hand-written protocol rather than the `mcp` SDK, and
  ADR 0011 recorded critical findings in `fastmcp`.

## Decision

1. **Where.** `/mcp` and `/mcp/projects/{org}:{slug}`, at the root of the API process. The chart
   routes `/mcp` to the API next to `/api`; the console's development rewrite does the same. The
   route is out of the OpenAPI document, like `/metrics`: MCP is its own protocol, and its tool
   schemas are tested where they are defined.
2. **Protocol.** Hand-written, minimal and stateless, shared with the sidecar
   (`choregos_core/mcp.py`): `initialize`, `tools/list`, `tools/call`, `ping`; versions
   `2025-06-18`, `2025-03-26` and `2025-11-25`; JSON responses only; `POST` only (`GET` and
   `DELETE` answer 405); a notification answers 202; a body above 1 MiB answers 413; a foreign
   `Origin` is refused. The `mcp` SDK enters as a **test** dependency only, to prove that a
   reference client lists and calls our tools.
3. **Tokens first.** A `chg_` token now carries scopes — `*`, `mcp:read`, `mcp:write` — and,
   optionally, one project. The REST API refuses a token without `*`; the MCP door refuses `*`.
   A token stolen from an agent's configuration therefore can neither mint another token nor
   reach the REST API, and one bound to a project sees nothing else.
4. **What the door offers.** `list_projects`, `describe_workflow`, `create_work_item`,
   `search_work_items`, `get_work_item`, `summarize_run`, `list_pending_decisions`; on
   `/mcp/projects/{P}`, the tools generated from the project's ontology, filtered by the
   person's own rights (an action they may not propose is not announced). Every result carries a
   `console_url`; every pending decision a `decision_url`.
5. **No decision through MCP, ever.** A decision needs a re-authenticated session (the ontology
   plugin's `decision_requires_session`, the console's `reauth=1`). An MCP principal has no
   authentication time, so a decision refuses by construction; no tool name may match
   `decid|approv|reject|answer`.
6. **Guards.** Every call is audited (`mcp.call`). A token is rate-limited (120 calls a minute
   per replica); a person has 50 writes a day through the door; a result is truncated at 60 kB
   with an explicit marker; ticket texts are wrapped as data, not instructions.
7. **OAuth next.** The door becomes an OAuth resource server (RFC 9728) whose tokens are issued
   by the identity provider (Keycloak on the development tenant), with a mandatory audience on a
   shared realm. Until then, the platform issuing tokens is a recorded deviation from
   R-SOC-MCP-02.

## Consequences

- Backlog stream S15: scoped tokens, the door, the ontology's tools through it, deciding a
  proposal in the console, the Integrations page, a Claude Code plugin and skill, a trial on the
  development tenant, OAuth.
- The Integrations page tells each client how to connect, pre-filled with the platform's URL and
  a token minted for that client, and says plainly which clients cannot reach an internal host.
- The CLI keeps full tokens (`*`). Whether a full token may still decide through REST is a
  separate question (the specification says no); it is answered with the token scopes.

## Alternatives discarded

- **The `mcp` SDK in production.** Faster to write, but it brings sessions and a transport we
  would have to audit, for seven short tools; ADR 0017's reasoning holds.
- **Mounting the door under `/api/v1/mcp`.** It works behind the current ingress without a new
  path, but ties a protocol endpoint to the REST API's version, and the specification names
  `/mcp`.
- **OAuth before anything else.** Right for claude.ai, useless for the development tenant, which
  claude.ai cannot reach; Claude Code with a scoped token proves the door first.
