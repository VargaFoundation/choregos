---
id: agent.door.other
title: Connect another MCP client to Choregos
applies_to: {door_client: other}
audience: everyone
version: 1
reviewed: 2026-10-16
link: /integrations/other
vendor_docs:
  - {title: "MCP specification: transports", url: "https://modelcontextprotocol.io/specification/2025-06-18/basic/transports"}
values: [public_url]
egress: []
---

## Before you start

another MCP client connects **to** Choregos through the MCP door, with **your** rights and never more. It lists your
projects, reads workflows and work items, and tells you what waits for a decision. It never decides: a
decision needs you, signed in again, in the console. The door is `{{public_url}}/mcp`.

Any client that speaks the Model Context Protocol over Streamable HTTP will do.

## At the client

The Integrations page of this console is the procedure: it gives the exact snippet for another MCP client, with this
platform's address filled in. Open it with the link below the panel.

## Permissions

A token with an MCP scope that you mint yourself, sent as `Authorization: Bearer <token>`. The door refuses a full-access token. A token can be bound to one project, and the door's tools
announce only what your role allows.

## In Choregos

Open **Integrations**, choose another MCP client and copy the snippet. Point the client at the door with that header; the door has no SSE stream and no session, and a `GET` answers 405, which the protocol allows.

## Test it

Ask the client "what is waiting for my decision in Choregos?". It should call `list_pending_decisions`.

## When it fails

- `401`: the token is missing, unknown, revoked or expired.
- `403 insufficient_scope`: a full-access token was used; mint an `mcp:read` or `mcp:write` one.
- A tool is missing: a read-only token, a role without the right, or a project whose tracker is external.
- A client that insists on SSE or a session fails: use its Streamable HTTP mode.
