---
id: connector.mcp.mcp
title: Connect an MCP server
applies_to: {connector: mcp/mcp}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "MCP specification: transports (Streamable HTTP)", url: "https://modelcontextprotocol.io/specification/2025-06-18/basic/transports"}
egress: ["the server's host"]
fields:
  url: "the server's Streamable HTTP endpoint, https"
  auth_scheme: "bearer, or basic (username plus token)"
  username: "the account, for basic"
  token: "the token or key the vendor gave you, typed once"
---

## Before you start

- The server's **Streamable HTTP** endpoint, over https. A server that speaks only the older SSE
  transport is refused.
- An organisation administrator declares the connector. What an agent may then call is decided tool by
  tool, below.
- Read the vendor's terms: the server acts with the token's account, and everything an agent sends to
  it leaves the platform.

## At the vendor

Create the credential in the vendor's own settings: an API token, a service account key, or a personal
access token. The catalogue entry for a known server says where. Prefer a token of a dedicated account
whose rights are exactly what the agents should have, and the narrowest scopes the vendor offers.

## Permissions

- The connector's tools act with **that account's permissions**, nothing in Choregos can widen them.
- Tools are **born closed** (ADR 0034): after discovery each tool is `forbidden` until an administrator
  opens it. A read can be `allowed`; a write is a governed action that waits for approval.
- A project sees only the tools its groups are opened to, and can only tighten them.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **MCP**, type **MCP server**, a lowercase name.
2. `url`: the endpoint.
3. `auth_scheme`: `bearer` (the default) sends `Authorization: Bearer <token>`. `basic` sends
   `Authorization: Basic base64(username:token)`; it needs `username`, usually an email.
4. `token`: paste it in the password field. It is sealed and shown once; **Replace** rotates it.
5. Press **Discover**, then open the operations the agents need, one by one.

## Test it

**Discover** asks the server for its tools. A list of tools proves the URL, the scheme and the token.

## When it fails

- `401` or `403`: the token is wrong, expired, or sent with the wrong scheme (Bearer versus Basic).
- `406 Not Acceptable`: the server wants an `Accept: application/json, text/event-stream` header and
  rejected the request; check that the URL is the Streamable HTTP endpoint and not an SSE one.
- A timeout or a connection refused: the egress allowlist does not open the server's host.
- "No tools", or only a few: discovery has not run, or the token's scopes are too narrow for the server
  to list more.
