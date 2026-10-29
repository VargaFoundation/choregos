---
id: connector.mcp.mcp-gateway
title: Connect an MCP server through your organisation's MCP gateway
applies_to: {connector: mcp/mcp-gateway}
audience: org_admin
version: 1
reviewed: 2026-10-27
vendor_docs:
  - {title: "MCP specification: transports (Streamable HTTP)", url: "https://modelcontextprotocol.io/specification/2025-06-18/basic/transports"}
egress: ["none of its own: the MCP gateway's host, opened when the gateway is set"]
fields:
  server: "the name your MCP gateway gives the server"
  timeout_s: "seconds before a call is abandoned; empty uses the gateway's timeout"
---

## Before you start

- Your organisation runs an **MCP gateway**, and an administrator has set it in Choregos (the
  gateway's address and how Choregos signs in to it). Without that, this connector cannot be used.
- The server is already registered at the gateway, under a name. Ask the gateway's owner for it.
- An organisation administrator declares the connector. What an agent may then call is decided tool by
  tool, below.

## At your MCP gateway

Register the server there under a name, and give the gateway the server's credential or OAuth setup.
**Choregos never holds that credential**: it asks the gateway for the server by its name, and the
gateway reaches the server with its own access. Prefer an account whose rights are exactly what the
agents should have.

## Permissions

- The connector's tools act with the permissions the **gateway** grants for that server, nothing in
  Choregos can widen them.
- Tools are **born closed**: after discovery each tool is `forbidden` until an administrator
  opens it. A read can be `allowed`; a write is a governed action that waits for approval.
- A project sees only the tools its groups are opened to, and can only tighten them.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **MCP**, type **MCP server through the
   organisation's MCP gateway**, a lowercase name.
2. `server`: the name your MCP gateway gives the server. There is no URL and no token to give.
3. `timeout_s`: optional; leave it empty to use the gateway's timeout.
4. Press **Discover**, then open the operations the agents need, one by one.

## Test it

**Discover** asks the server, through the gateway, for its tools. A list of tools proves the gateway
setting, Choregos's sign-in to the gateway and the server's name.

## When it fails

- `409 gateway_not_configured`: no gateway is set for the organisation. An administrator sets it first.
- `409 gateway_assertion_key_missing`: the gateway is set, but the key Choregos signs its requests
  with is not. An administrator finishes the gateway setting.
- `401` from the gateway: the gateway refused Choregos's sign-in, or the caller is not allowed to use
  that server. Check the gateway's own logs.
- `404` for an unknown server name: the name is not registered at the gateway; check the spelling
  against the gateway's list.
- "No tools", or only a few: discovery has not run, or the gateway's access to the server is too
  narrow to list more.
