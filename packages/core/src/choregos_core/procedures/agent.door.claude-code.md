---
id: agent.door.claude-code
title: Connect Claude Code to Choregos
applies_to: {door_client: claude-code}
audience: everyone
version: 1
reviewed: 2026-10-16
link: /integrations/claude-code
vendor_docs:
  - {title: "Claude Code: connect to MCP servers", url: "https://docs.claude.com/en/docs/claude-code/mcp"}
values: [public_url]
egress: []
---

## Before you start

Claude Code connects **to** Choregos through the MCP door, with **your** rights and never more. It lists your
projects, reads workflows and work items, and tells you what waits for a decision. It never decides: a
decision needs you, signed in again, in the console. The door is `{{public_url}}/mcp`.

Claude Code runs on your machine: it reaches a platform behind your VPN.

## At the client

The Integrations page of this console is the procedure: it gives the exact snippet for Claude Code, with this
platform's address filled in. Open it with the link below the panel.

## Permissions

A token with an MCP scope (`mcp:read` or `mcp:write`) that you mint yourself, or your single sign-on when the platform names a Claude Code client. The door refuses a full-access token. A token can be bound to one project, and the door's tools
announce only what your role allows.

## In Choregos

Open **Integrations**, choose Claude Code and copy the snippet. With single sign-on the page shows a `claude mcp add --client-id …` command and you authenticate from `/mcp` in Claude Code.

## Test it

Ask the client "what is waiting for my decision in Choregos?". It should call `list_pending_decisions`.

## When it fails

- `401`: the token is missing, unknown, revoked or expired.
- `403 insufficient_scope`: a full-access token was used; mint an `mcp:read` or `mcp:write` one.
- A tool is missing: a read-only token, a role without the right, or a project whose tracker is external.
- A `403` with an `Origin`: a web page tried to call the door; only your client may.
