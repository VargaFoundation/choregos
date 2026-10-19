# Connecting Claude, or any MCP client

Choregos has a door for the agent *you* use — Claude Code, Claude Desktop, Cursor, VS Code, or any
client that speaks the [Model Context Protocol](https://modelcontextprotocol.io). Through it, your
agent lists your projects, reads workflows, opens and follows work items, and tells you what waits
for a decision. It acts with **your** rights, never more, and it **never decides**: a decision needs
you, signed in again, in the console ([ADR 0030](adr/0030-an-mcp-door-for-external-clients.md)).

The console has the same instructions, pre-filled with your platform's address: **Integrations**
(in the sidebar's *Organisation* group; inside a project, *Configure → Integrations*).

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
| `create_work_item` | Opens a work item in a project (in its tracker first when external, ADR 0059) | `mcp:write` and the right to control work items |

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

### Signing in with your single sign-on

When the platform accepts the IdP's tokens and names the client the IdP registered for Claude Code
(`global.mcp.oauth.clients.claude-code`, [below](#claudeai-claude-mobile-chatgpt)), there is no
token to mint or paste — the Integrations page shows this command first:

```bash
claude mcp add --transport http --client-id choregos-claude-code --callback-port 33418 \
  choregos https://<console-host>/mcp
```

Then `/mcp` in Claude Code, `choregos`, *Authenticate*: your browser opens the IdP's sign-in page,
and Claude Code keeps the session. `--callback-port` is the port of the redirect URI the IdP
registered (`http://localhost:33418/callback`); the client is public and uses PKCE, so there is no
secret. Your role bounds what Claude sees, and signing out of the IdP ends its access.

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

| Chart value (`global.mcp.oauth.…`) | Setting | Value |
| :-- | :-- | :-- |
| `enabled` | `CHOREGOS_MCP_OAUTH_ENABLED` | `true` |
| `audience` | `CHOREGOS_MCP_OAUTH_AUDIENCE` | the audience your IdP puts in the door's tokens — default `choregos-mcp` |
| `issuer` | `CHOREGOS_MCP_OAUTH_ISSUER` | empty: the console's own OIDC issuer; set it only for another IdP |
| `clients` | `CHOREGOS_MCP_OAUTH_CLIENTS` | the clients your IdP registered, per client of the Integrations page — `{claude-code: {clientId, callbackPort}, claude-ai: {clientId}}` in the chart, `{"claude-code": {"client_id": …, "callback_port": …}}` as JSON; a name the page does not know stops the API at startup |

With `clients`, the Integrations page shows each registered client's exact setup: the
`claude mcp add --client-id` command for Claude Code, and for claude.ai the URL and client ID to
enter in *Settings, Connectors, Add custom connector, Advanced settings* — the client secret is
never shown there; your administrator holds it.

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

Each client above also has a short procedure, shown in the console and listed in
[the procedures](procedures/README.md).

Point it at `https://<console-host>/mcp` with the header `Authorization: Bearer <token>`, transport
Streamable HTTP. The door has no SSE stream and no session: a `GET` answers 405, which the protocol
allows.

## An external agent

A client of the door can be **registered as an agent** of the organisation
([ADR 0033](adr/0033-agent-registry-and-skill-library.md)): an agent of kind `external`,
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

## Atlassian's Rovo MCP Server, as a connector

The step-by-step setup (what to enable in Atlassian Administration, which credential goes with which
authentication scheme, the scopes) is the procedure
[Connect Atlassian's Rovo MCP Server](procedures/connector.mcp.mcp.atlassian-rovo.md), also shown in the
console when you add an MCP server. What follows keeps the concepts.

The other direction: an internal agent calling **Rovo's tools** (search, Jira, Confluence, JSM,
Bitbucket, Compass) through an `mcp` connector. This gives the agents Atlassian's tools under the
platform's per-operation policy and approvals; it does not invoke the Rovo agent itself.

1. In Atlassian Administration, allow API-token access to the Rovo MCP Server.
2. Create a service account (or a dedicated user) with the Jira and Confluence permissions the
   agents should have, and an API token with granular scopes, for example `read:jira-work`,
   `write:jira-work` (only if writes are wanted), `read:page:confluence`, `search:confluence`,
   `search:rovo:mcp`. Without the search scopes only some tools appear.
3. Declare the connector. The token is sent as `Authorization: Basic base64(email:token)`, which
   is what the server expects for an API token (Bearer is refused):

```bash
curl -X POST $API/orgs/acme/connectors -d '{"kind": "mcp", "name": "rovo", "type": "mcp",
  "config": {"url": "https://mcp.atlassian.com/v1/mcp", "auth_scheme": "basic",
             "username": "choregos-bot@example.com"},
  "secret_refs": {"token": "env:CONNECTOR_ROVO_API_TOKEN"}}'
```

   Declaring it opens `mcp.atlassian.com` in the organisation's egress allowlist: an administrator
   does it in the same call (`allow_egress`) or from the console's network-access modal; anyone else
   asks ([ADR 0066](adr/0066-the-organisation-egress-allowlist.md)). On a cluster with a restricted
   network, the platform team may also need to open the host (the modal shows the change).
4. *Discover* the connector, then open the tools the agents need: a new tool is born closed.

`auth_scheme` is `bearer` (the default, unchanged) or `basic`; `basic` needs `username`. The
server allows about 1 000 calls per hour on Standard and Premium plans.

## A remote agent: Atlassian Rovo over A2A

The step-by-step setup is the procedure [Add Atlassian Rovo as a remote agent](procedures/agent.remote.a2a.atlassian-rovo.md)
(and [Add a remote agent (A2A)](procedures/agent.remote.a2a.md) for any other), shown in the console when you
add one. What follows keeps the concepts and the limits.

The reverse of the door: instead of a client calling Choregos, a **workflow step calls an agent that
runs elsewhere**, over the A2A protocol ([ADR 0062](adr/0062-remote-agents-over-a2a.md)). Atlassian's
Rovo is the first one; any A2A agent is the same entry with another card.

What Rovo exposes, and what it does not (checked on 2026-10-15):

- **One agent**, "Atlassian Rovo", with two skills: `work-in-jira` and `work-in-confluence`. Custom
  agents built in Rovo Studio are **not** individually addressable; no public document says how.
- **A2A 0.3.0**, JSON-RPC at `https://a2a.atlassian.com/v1/rovo`, card at
  `https://a2a.atlassian.com/.well-known/agent.json`. No push notifications. A synchronous call is cut
  at about 30 s by Atlassian's CDN, so Choregos streams (or polls); nothing is lost.
- **Authentication is OAuth 2.0 authorization code with PKCE and Dynamic Client Registration, and
  nothing else**: no API token, no service account. The agent therefore acts **as the Atlassian user
  who authorised it**, within that user's permissions. Use a dedicated account (for example
  `choregos-bot@your-domain`) licensed for Rovo, with exactly the Jira and Confluence access the agent
  should have.
- No cost or token figure comes back; Rovo usage counts against the Atlassian subscription.

Two different things, both useful: Rovo's **tools** (search, create and update issues and pages) reach
your governed agents through an `mcp` connector on `https://mcp.atlassian.com/v1/mcp` and are live
queries under per-operation policy; the **A2A agent** below is Rovo itself, answering as a person.
Neither is a knowledge source: a Confluence knowledge base ingests documents, this asks questions.

### Set it up (an organisation administrator)

1. In Atlassian Administration, **allow A2A connections** (Security, AI settings).
2. In the console, **Administration → Remote agents → Connect**: the card URL
   `https://a2a.atlassian.com/.well-known/agent.json`. Choregos registers itself at the DCR endpoint
   the card advertises, then opens Atlassian's consent page: sign in **as the dedicated account** and
   accept. The credential shows `connected`, and who it is `authorised as`. The refresh token is sealed
   in the secret store and never shown again; reconnecting replaces it.
   The same, by API: `POST /orgs/{org}/remote-credentials` with `{"name": "atlassian-rovo", "type":
   "oauth2_authorization_code", "card_url": "https://a2a.atlassian.com/.well-known/agent.json"}`, then
   `POST …/atlassian-rovo/authorize` and open the `redirect_url` it returns.
3. **Agents → New → Remote agent (A2A)**: paste the card URL, pick the skill and the credential, set the
   timeout and the declared cost, publish. Publication reads the card and pins its digest.
4. Allow the host in each project that uses it: `sandbox.network.allow_domains: [a2a.atlassian.com]`
   (the stage is refused otherwise, naming the fix). On a platform with an egress allow-list, the
   runner pods must be allowed to reach `a2a.atlassian.com`, and the API and the orchestrator
   `auth.atlassian.com` (registration and token refresh).

```yaml
# a step of a workflow
- id: ask-rovo
  type: agent
  agent: rovo        # the registry entry; no `backend:`, no model
```

### What a step does

The pod re-reads the card and **refuses a card that changed** since publication (`remote_card_changed`:
publish a new version to accept it). It sends the agent's instructions and the task as one message,
and the declared inputs as files for an agent registered with `text+files`. The project's memory is not
sent. The answer is `response.md`, an artifact of the step; artifacts the agent returns become files
or, when named like a declared output, that output. Facts the agent states are recorded as **declared,
never measured**. The transition's gates read the result like any other.

A step ends `failed` with a reason a person can read: `remote_failed`, `remote_timeout` (the remote task
is cancelled), `remote_card_changed`, `remote_auth_required` (reconnect the credential),
`remote_input_required` (the agent asked a question: it is shown, and answering it is not supported
yet).

### Cost

A2A reports none. The registry entry **declares** `per_task_usd` and `per_minute_usd`; the stage reserves
that against the ticket and the day and books it as a ledger line of kind `external` (the costs page
shows it apart). With nothing declared the line says `unmeasured`: Choregos does not know what Rovo
costs.

### Limits, said plainly

Not built: Choregos **as** a Rovo agent inside Jira (it needs a Forge app and an A2A server); an agent
answering `input-required` from the console and resuming; push notifications; verification of signed
cards (A2A 1.0). The A2A 1.0 spelling is implemented from secondary sources and exercised against a
fake only; so is the Dynamic Client Registration exchange with Atlassian: neither has run against the
real service yet (`tests/live/test_rovo_a2a.py`, run by hand once the consent is given).

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
