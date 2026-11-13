---
title: Connect your AI assistant (MCP)
description: Connect Claude Code, Claude Desktop, claude.ai, Cursor, VS Code, ChatGPT or any MCP client to Choregos, with your rights and never more.
sidebar_position: 1
---

# Connect your AI assistant (MCP)

You work in Claude Code, Cursor or VS Code all day. Instead of switching to the console, ask your
assistant *"what is waiting for my decision in Choregos?"* or *"open a work item in billing-api to add
credit notes"*. The **MCP door** makes that possible: it serves Choregos to any client of the
[Model Context Protocol](https://modelcontextprotocol.io).

**What you will be able to do:** connect your assistant, list your projects, read workflows and work
items, see what waits for a person, and open work items, all with **your** rights.

**Before you start:**

- an account on your Choregos instance, signed in to the console at least once;
- the instance's address (here `https://choregos.example.com`);
- for the clients that call from their vendor's cloud (claude.ai, ChatGPT), an instance reachable on
  a public HTTPS address with OAuth enabled by your operator.

:::note The door never decides
Your assistant can read and open work, never take a decision: approving a specification, a pull
request or a release needs you, signed in again, in the console. Text read from a work item is data
for the assistant, not instructions.
:::

The console has these steps pre-filled with your instance's address: **Integrations**, in the
sidebar's *Organisation* group (inside a project, **Integrations** under *Configure*). Each client has
its tab.

## The door and its tools

| URL | What it serves |
| --- | --- |
| `https://choregos.example.com/mcp` | every project you can read; tools take a `project` argument (`org:slug`) |
| `https://choregos.example.com/mcp/projects/acme:billing-api` | one project; tools need no `project` argument |

Transport: Streamable HTTP, stateless, JSON responses; protocol versions `2025-11-25`, `2025-06-18`
and `2025-03-26`.

| Tool | What it does | Needs |
| --- | --- | --- |
| `list_projects` | the projects you can read, with their console links | `mcp:read` |
| `describe_workflow` | a project's states and transitions, and who performs each one | `mcp:read` |
| `search_work_items` | work items by text, by state, or only those waiting for a person | `mcp:read` |
| `get_work_item` | a work item's state, recent timeline and pending decision, with its link | `mcp:read` |
| `summarize_run` | one agent run: stage, backend, model, status, cost, summary | `mcp:read` |
| `list_pending_decisions` | what waits for a person, with the link where it is decided | `mcp:read` |
| `get_pending_decision` | what waits for **your** decision: the list, or one request's full context (question, form, work item, the draft already made, its link) | `mcp:read` |
| `docs_search` | this documentation, in the version the instance runs: ranked pages with their slug, link and a snippet | `mcp:read` |
| `docs_read` | one page of it as Markdown, by slug, or one of its sections; a long page comes in parts | `mcp:read` |
| `create_work_item` | opens a work item in a project | `mcp:write` and the right to control work items |
| `draft_decision` | deposits a draft of the decision of a request waiting for you; it decides nothing, you take or discard it in the console | `mcp:write` and the right to decide work items |

A tool you may not use is not announced, and calling it answers like an unknown tool.

`docs_search` and `docs_read` serve the pages you are reading, from the instance itself: an assistant
connected to the door answers how Choregos works from the documentation of the version installed,
with no access to the internet. An instance built without the documentation answers that it carries
none.

## 1. Mint a token for the client

The clients that run on your machine (Claude Code, Claude Desktop, Cursor, VS Code, most others) take
a token you mint, with an MCP scope:

- `mcp:read`: reads only;
- `mcp:write`: reads, and opens work items;
- optionally bound to **one project**: the token then sees nothing else.

**In the console:** **Integrations**, choose the client's tab, card *A token for this client*: tick
*Can open work items (mcp:write)* if you want it, then **Create an access token**. It is shown once.

**With the CLI:**

```bash
choregos tokens create --name claude-code --scope mcp:write --project acme:billing-api --expires-in-days 30
```

The door refuses a full-access (`*`) token, and the REST API refuses an MCP token. The token sits in
your client's configuration in clear: mint one per client, keep it narrow and short-lived, and revoke
it when the client is gone (`choregos tokens list`, then `choregos tokens revoke <id>`).

## 2. Connect your client

### Claude Code

```shell
export CHOREGOS_TOKEN=chg_...
claude mcp add --transport http choregos https://choregos.example.com/mcp \
  --header "Authorization: Bearer $CHOREGOS_TOKEN"
```

Claude Code runs on your machine, so it reaches an instance behind your VPN.

**With your single sign-on instead of a token.** When your operator registered a Claude Code client
at the identity provider, the Integrations page shows this command first; no token to mint:

```shell
claude mcp add --transport http --client-id choregos-claude-code --callback-port 33418 \
  choregos https://choregos.example.com/mcp
```

Then type `/mcp` in Claude Code, choose `choregos`, then *Authenticate*: your browser opens your
identity provider's sign-in page.

**The Choregos plugin for Claude Code** bundles the door and a skill that teaches Claude the rules (it
never decides; work-item text is data). Claude Code asks for the URL and the token once and keeps the
token in your system's secret store:

```text
/plugin marketplace add VargaFoundation/choregos
/plugin install choregos@choregos
```

### Claude Desktop

Claude Desktop's built-in connectors call from Anthropic's network. For an instance inside your
network, run a local bridge in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "choregos": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://choregos.example.com/mcp", "--header", "Authorization:${AUTH}"],
      "env": { "AUTH": "Bearer chg_..." }
    }
  }
}
```

Add `"--allow-http"` to `args` if the instance is served over plain HTTP. Restart Claude Desktop.

### Cursor

`~/.cursor/mcp.json`, then `export CHOREGOS_TOKEN=chg_...` in the shell that starts Cursor:

```json
{
  "mcpServers": {
    "choregos": {
      "url": "https://choregos.example.com/mcp",
      "headers": { "Authorization": "Bearer ${env:CHOREGOS_TOKEN}" }
    }
  }
}
```

### VS Code

`.vscode/mcp.json`. VS Code asks for the token once and keeps it in its secret store:

```json
{
  "inputs": [{ "type": "promptString", "id": "choregos-token", "description": "Choregos MCP token", "password": true }],
  "servers": {
    "choregos": {
      "type": "http",
      "url": "https://choregos.example.com/mcp",
      "headers": { "Authorization": "Bearer ${input:choregos-token}" }
    }
  }
}
```

### claude.ai

claude.ai (and Claude on mobile) calls a remote MCP server from Anthropic's network and signs in with
OAuth: it cannot be handed a `chg_` token. It works when your instance:

- is reachable on a public HTTPS address (`/mcp` and `/.well-known/oauth-protected-resource`);
- has OAuth on the door, with a claude.ai client registered at your identity provider (your operator
  sets it, see [Identity](../host/identity.md#signing-in-to-the-mcp-door-with-oauth)).

Then the Integrations page's **claude.ai** tab gives what to enter in claude.ai, under *Settings,
Connectors, Add custom connector, Advanced settings*: the name, the URL and the OAuth client ID. The
client secret is never shown on that page; your administrator holds it. claude.ai then sends you to
your identity provider to sign in.

### ChatGPT

ChatGPT also calls from its vendor's cloud and signs in with OAuth: the same two conditions as
claude.ai apply. Add Choregos as a custom connector in ChatGPT with the URL
`https://choregos.example.com/mcp`, and sign in when ChatGPT asks. The Integrations page's **ChatGPT**
tab gives what to enter when your operator registered a ChatGPT client.

### Any other MCP client

Point it at the door with your token:

```text
URL:        https://choregos.example.com/mcp
Transport:  Streamable HTTP
Header:     Authorization: Bearer chg_...
```

The door has no SSE stream and no session: a `GET` answers `405`, which the protocol allows.

## 3. Try it

Ask your assistant *"what is waiting for my decision in Choregos?"*.

**What you should see:** the assistant calls `list_pending_decisions` and lists the work items that
wait for you, each with the console link where you decide. Then ask it to open a work item in
`acme:billing-api`: with an `mcp:write` token, it calls `create_work_item` and gives you the new key.

## Limits

- 120 calls a minute per token (HTTP `429` with `Retry-After` beyond).
- 50 writes a day per person through the MCP and A2A doors together (`budget_exhausted`).
- A result longer than 60,000 characters is cut, and says so (`result_truncated`).
- Every call is in the audit log, with the token and the client that made it.

## What can go wrong

| Symptom | Likely cause |
| --- | --- |
| the client lands on the console's 404 page | the ingress does not route `/mcp` to the API: ask your operator |
| `401` | the token is missing, unknown, revoked or expired |
| `401 invalid_token` with OAuth | wrong audience or issuer, or an expired token |
| `403` with OAuth, *sign in to the console once* | the identity provider knows you, Choregos does not yet |
| `403 insufficient_scope` | a full-access (`*`) token: mint an `mcp:read` or `mcp:write` one |
| `403` on `/mcp/projects/…` | the token is bound to another project |
| `403` with an `Origin` | a web page tried to call the door: only your client may |
| claude.ai never offers to sign in | OAuth is off, or `/.well-known/oauth-protected-resource` is not routed to the API |
| a tool is missing | a read-only token, a role without the right, or a project whose tracker is external |

The full list of answers is in [Error codes](../reference/errors.md#the-mcp-door).

## Go further

- [Use assistants from claude.ai and your own application](assistants.md), the same door for the assistants you may talk to.
- [Start a workflow over A2A](a2a.md), for an application or another agent rather than an assistant.
- [The CLI](../cli/install.md) and [the REST API](rest.md), for your own scripts.
