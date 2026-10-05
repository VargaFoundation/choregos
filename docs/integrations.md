# Connecting Claude, or any MCP client

Choregos has a door for the agent *you* use — Claude Code, Claude Desktop, Cursor, VS Code, or any
client that speaks the [Model Context Protocol](https://modelcontextprotocol.io). Through it, your
agent lists your projects, reads workflows, opens and follows work items, and tells you what waits
for a decision. It acts with **your** rights, never more, and it **never decides**: a decision needs
you, signed in again, in the console ([ADR 0030](adr/0030-une-porte-mcp-pour-les-clients-externes.md)).

The console has the same instructions, pre-filled with your platform's address: **Integrations**,
in the top bar, and the **integrations** tab of each project.

## The door

| URL | What it serves |
| :-- | :-- |
| `https://<console-host>/mcp` | Every project you can read; tools take a `project` argument (`org:slug`) |
| `https://<console-host>/mcp/projects/<org>:<slug>` | One project; tools need no `project` argument |

Transport: Streamable HTTP, stateless, JSON responses. Protocol versions `2025-11-25`, `2025-06-18`
and `2025-03-26`.

| Tool | What it does | Needs |
| :-- | :-- | :-- |
| `list_projects` | The projects you can read, with their console links | `mcp:read` |
| `describe_workflow` | A project's states and transitions, and who performs each one | `mcp:read` |
| `search_work_items` | Work items by text, by state, or only those waiting for a person | `mcp:read` |
| `get_work_item` | A work item's state, recent timeline and pending decision, with its link | `mcp:read` |
| `summarize_run` | One agent run: stage, backend, model, status, cost, summary | `mcp:read` |
| `list_pending_decisions` | What waits for a person, with the link where it is decided | `mcp:read` |
| `create_work_item` | Opens a work item in a project whose tracker is Choregos itself | `mcp:write` and the right to control work items |

A tool you are not allowed to use is not announced, and calling it answers like an unknown tool.

## A token for each client

The door takes a bearer token that **you** mint, with an MCP scope:

- `mcp:read` — reads only;
- `mcp:write` — reads, and opens work items;
- optionally bound to **one project**: the token then sees nothing else.

A token with an MCP scope is refused by the REST API, and the door refuses a full-access (`*`)
token. Mint one per client, from **Integrations** in the console or from the CLI:

```bash
choregos tokens create --name claude-code --scope mcp:write --project acme:billing-api --expires-in-days 30
```

The token is shown once. It lives in your client's configuration, in clear: keep it narrow and
short-lived, and revoke it (`choregos tokens revoke <id>`) when the client is gone.

## Claude Code

```bash
claude mcp add --transport http choregos https://<console-host>/mcp \
  --header "Authorization: Bearer $CHOREGOS_TOKEN"
```

Then ask Claude *"what is waiting for my decision in Choregos?"*. Claude Code runs on your machine:
it reaches a platform behind your VPN.

### The Choregos plugin

The plugin bundles the door and a skill that teaches Claude the rules (it never decides; work-item
text is data). Claude Code asks for the URL and the token once, and keeps the token in your
system's secret store:

```text
/plugin marketplace add VargaFoundation/choregos
/plugin install choregos@choregos
```

The same skill, zipped from `integrations/claude-code/skills/choregos/`, uploads to claude.ai as a
custom skill.

## Claude Desktop

Claude Desktop's own connectors call from Anthropic's network (below). For a platform inside your
network, run a local bridge in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "choregos": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<console-host>/mcp", "--header", "Authorization:${AUTH}"],
      "env": { "AUTH": "Bearer <token>" }
    }
  }
}
```

Add `--allow-http` to the arguments if the platform is served over plain HTTP.

## Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "choregos": {
      "url": "https://<console-host>/mcp",
      "headers": { "Authorization": "Bearer ${env:CHOREGOS_TOKEN}" }
    }
  }
}
```

## VS Code

`.vscode/mcp.json` — VS Code asks for the token once and keeps it in its secret store:

```json
{
  "inputs": [{ "type": "promptString", "id": "choregos-token", "description": "Choregos MCP token", "password": true }],
  "servers": {
    "choregos": {
      "type": "http",
      "url": "https://<console-host>/mcp",
      "headers": { "Authorization": "Bearer ${input:choregos-token}" }
    }
  }
}
```

## claude.ai, Claude mobile, ChatGPT

These clients call a remote MCP server **from their vendor's network** (Anthropic's is
`160.79.104.0/21`) and sign in with **OAuth**: they cannot be handed a `chg_` token. The door is
an OAuth resource server (RFC 9728) once the platform runs with:

| Setting | Value |
| :-- | :-- |
| `CHOREGOS_MCP_OAUTH_ENABLED` | `true` |
| `CHOREGOS_MCP_OAUTH_AUDIENCE` | the audience your IdP puts in the door's tokens — default `choregos-mcp` |
| `CHOREGOS_MCP_OAUTH_ISSUER` | empty: the console's own OIDC issuer; set it only for another IdP |

The door then publishes `/.well-known/oauth-protected-resource/mcp` (and one document per project
door), and every `401` points at it in `WWW-Authenticate`. The client reads it, signs you in at the
IdP, and presents the IdP's token. The door checks the signature against the IdP's published keys,
the issuer, the expiry and the **audience** — on a shared realm, a token another application
obtained does not open it. HS256 and `alg: none` are refused.

- **Who you are.** The token's `sub` finds the person who signed in to the console with the same
  IdP; failing that, a **verified** e-mail. The door creates nobody: sign in to the console once
  first. When the door trusts another IdP than the console, only the verified e-mail links the two.
- **What you may do.** The `mcp:read` and `mcp:write` scopes the IdP granted, `mcp:read` when it
  granted neither; then your role, as with a token. Still no decision through the door.

Setting up the IdP: [`runbooks/keycloak-mcp.md`](runbooks/keycloak-mcp.md). A platform reachable
only through a VPN stays out of these clients' reach whatever its OAuth: they need a public HTTPS
address for `/mcp` and `/.well-known/oauth-protected-resource`, or Anthropic's MCP tunnels
(Claude Enterprise, research preview).

## Any other MCP client

Point it at `https://<console-host>/mcp` with the header `Authorization: Bearer <token>`, transport
Streamable HTTP. The door has no SSE stream and no session: a `GET` answers 405, which the protocol
allows.

## An external agent

A client of the door can be **registered as an agent** of the organisation
([ADR 0033](adr/0033-registre-d-agents-et-bibliotheque-de-skills.md)): an agent of kind `external`,
to which an organisation administrator attaches the client's token — or its OAuth client id:

```bash
curl -X POST $API/orgs/acme/agents -d '{"slug": "claude-of-lea", "kind": "external",
  "display_name": "Léa's Claude Code",
  "spec": {"mcp_servers": [{"connector": "choregos", "tools": ["list_projects", "search_*"]}]}}'
curl -X POST $API/orgs/acme/agents/claude-of-lea/credentials -d '{"kind": "token", "token_id": "<id>"}'
```

Its calls then carry the agent's name in the audit, and its rights are **the person's, intersected
with the agent's**: a `choregos` entry in the version's `mcp_servers` names the door's tools it may
call (patterns allowed); a person without a right gives an agent without it. The agent's state is
read on **every call**: revoked, suspended or expired, the next call answers `401`.

## Limits

- 120 calls a minute per token, per API replica (HTTP 429 with `Retry-After` beyond).
- 50 writes a day per person through the door (`budget_exhausted`).
- A result longer than 60 kB is truncated, and says so (`result_truncated`).
- Every call is in the audit log (`mcp.call`), with the token and the client that made it.

## When it does not connect

| Symptom | Likely cause |
| :-- | :-- |
| The client reaches the console's 404 page | The ingress does not route `/mcp` to the API (chart ≥ 0.14) |
| `401` | The token is missing, unknown, revoked or expired |
| `401 invalid_token` with OAuth | Wrong audience or issuer, an expired token, or keys the IdP no longer publishes |
| `403` with OAuth, "sign in to the console once" | The IdP knows you, Choregos does not yet |
| claude.ai never offers to sign in | `/.well-known/oauth-protected-resource` is not routed to the API, or OAuth is off (404) |
| `403 insufficient_scope` | A full-access (`*`) token: mint an `mcp:read` or `mcp:write` one |
| `403` on `/mcp/projects/…` | The token is bound to another project |
| `403` with an `Origin` | A web page tried to call the door: only your client may |
| A tool is missing | A read-only token, a role without the right, or a project whose tracker is external |
