---
id: connector.mcp.mcp.atlassian-rovo
title: Connect Atlassian's Rovo MCP Server
applies_to: {connector: mcp/mcp, preset: atlassian-rovo}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Configuring authentication via API token (Rovo MCP Server)", url: "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-authentication-via-api-token"}
  - {title: "Rovo MCP Server only returns Teamwork Graph tools when using an API token", url: "https://support.atlassian.com/rovo/kb/rovo-mcp-server-only-returns-teamwork-graph-tools-when-using-api-token/"}
  - {title: "Manage API tokens for service accounts", url: "https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/"}
egress: [mcp.atlassian.com]
fields:
  url: "https://mcp.atlassian.com/v1/mcp"
  auth_scheme: "basic for a personal API token, bearer for a service account API key"
  username: "the account's email, for basic"
  token: "the API token or key, typed once"
---

## Before you start

- An Atlassian organisation administrator, because **API token access to the Rovo MCP Server is off
  until an admin enables it**.
- This gives agents Atlassian's tools (search, Jira, Confluence, and more) under Choregos's per-tool
  policy. It does not call the Rovo agent itself; for that, see the remote agent procedure.
- The server allows about 1 000 calls an hour on Standard and Premium plans.

## At Atlassian

1. Atlassian Administration: enable **API token authentication** for the Rovo MCP Server.
2. Create the credential. Two kinds exist and they are sent differently:
   - a **personal API token with scopes** (id.atlassian.com, Security, API tokens), sent as **Basic**
     with the account's email;
   - a **service account API key** (Directory, Service accounts), sent as **Bearer**.
3. The server's endpoint is `https://mcp.atlassian.com/v1/mcp`. The older `/v1/sse` endpoint stopped on
   30 June 2026. The MCP server catalogue may list a newer `/v2/mcp` address that signs in with OAuth
   instead: this procedure is the API-token route on `/v1/mcp`; check Atlassian's documentation for the
   address your site supports.

## Permissions

- Give the token granular scopes. A starting set for reading: `read:jira-work`, `read:page:confluence`,
  `search:confluence`, `search:rovo:mcp`. Add `write:jira-work` and the like only if agents must write.
- With classic or broad scopes, only the **Teamwork Graph** tools appear (Atlassian's own knowledge-base
  article says so). If you see only those, the token is the cause.
- Every tool acts with the account's own Jira and Confluence permissions.

## In Choregos

1. Install it from the MCP catalogue ("Atlassian Rovo MCP"), or Administration, **Connectors**,
   **Declare**: capability **MCP**, type **MCP server**.
2. `url`: `https://mcp.atlassian.com/v1/mcp`.
3. Personal token: `auth_scheme` `basic`, `username` the account's email. Service account key:
   `auth_scheme` `bearer`. A personal token sent as Bearer fails.
4. `token`: the token or key, in the password field.
5. **Discover**, then open the tools the agents need.

## Test it

Press **Discover**. The list should include Jira and Confluence tools, not only Teamwork Graph ones.

## When it fails

- `401`: wrong scheme for the credential (a personal token needs `basic`, a service account key
  needs `bearer`), a wrong email, or API token access is not enabled by an administrator.
- Only a few Teamwork Graph tools: the token's scopes are classic or too broad; create a token with
  granular scopes.
- `429`: the hourly limit.
- A timeout: the egress allowlist must open `mcp.atlassian.com`.
