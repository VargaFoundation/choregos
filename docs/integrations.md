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
| `https://<console-host>/mcp/assistants/<org>:<installation>` | One assistant as its own connector (see below) |

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
| `docs_search` | Searches this platform's user documentation: ranked pages with title, slug, public URL and a snippet | `mcp:read` |
| `docs_read` | One documentation page as Markdown, by slug, or one of its sections by heading anchor; a long page comes in parts (`offset`) | `mcp:read` |

A tool you are not allowed to use is not announced, and calling it answers like an unknown tool.

**The documentation, from the platform itself** ([ADR 0150](adr/0150-the-instance-serves-its-own-documentation-over-mcp.md)).
The API image carries the Markdown of the user documentation (`docs/site/` in the repository) of its
own version, at `/opt/choregos/docs` (`CHOREGOS_DOCS_DIR` overrides). `docs_search` and `docs_read`
read it, so your assistant answers from the documentation of the version you run, behind your firewall,
without internet. The links they give point to the public site, `CHOREGOS_DOCS_URL` (default
`https://docs.varga.foundation/choregos`). Every token that opens the door may use them, bound to a
project or not; they are audited (`mcp.call`) and rate limited like the other reads. A build without
documentation answers so, with the public URL. Ask *"how do gates work in Choregos?"*: the client
should call `docs_search`, then `docs_read`.

### Assistants (ADR 0139)

An assistant installed for a group is reachable from the door by the people of that group (its audience), with
`mcp:read` alone. On `/mcp`, `list_assistants`, `ask_assistant` and `get_conversation` are announced only to
someone in the audience of at least one ready assistant. `/mcp/assistants/<org>:<installation>` is one assistant
as its own connector: tools `ask` and `get_conversation`, and the manifest's `surfaces.mcp.prompts` as MCP
prompts (`prompts/list`, `prompts/get`, `{argument}` substituted in the message). Outside the audience, that
path answers `404`. A conversation opened here is one of your `/me/conversations`, in your personal space.

An answer is `{conversation_id, status, text, citations, pending_actions, missing_accounts}`. A turn longer than
`CHOREGOS_MCP_ASSISTANT_WAIT_S` (25 s) answers `status: working` and goes on in the background; call
`get_conversation` again until it is `completed` (or `failed`). The assistant never decides for you: a proposal
is confirmed in the console. `citations` are the knowledge documents the turn's knowledge tools returned
(`document_id`, `title`, `version`, `base`); `pending_actions` the writes it proposed and you have not decided;
`missing_accounts` the accounts a tool needed and you have not connected (`need`, `connect_url`). Each list is
the latest turn's, read from the conversation's journal.

### Embedding an assistant in your application (ADR 0139 point 6)

A host application (an internal web app) shows an assistant in an iframe. The host holds no token, no model and
no transcript: the person signs in to Choregos, and the conversation is theirs (`/me/conversations`).

```html
<iframe id="assistant" title="Assistant" width="480" height="520"
  src="https://choregos.example.com/embed/assistants/<org>/<installation>?context=<base64url JSON>"></iframe>
```

- **`context`** (optional) is a JSON object, base64url-encoded (`btoa(JSON.stringify(obj))` with `+` `/` `=`
  replaced by `-` `_` and nothing). Choregos validates it against the manifest's `surfaces.embed.context_schema`
  (422 shown in the frame when it does not match, or when the assistant declares none) and gives it to the agent
  as data, never as an instruction or a resolved subject. The frame resumes the person's conversation for the same
  installation and context while the browser tab lives; otherwise it opens a new one.
- **Who may frame it.** An administrator or owner of the installation sets `config.embed_origins` (a list of
  origins, `https://app.example.com`: no path, no `*`; `http` only for `localhost`). The console answers the embed
  page with `Content-Security-Policy: frame-ancestors <those origins>`; with none, or for an unknown installation,
  `'none'`. Every other console page always refuses framing. The origins are served, to anyone, by
  `GET /api/v1/public/assistants/{org}/{slug}/embed` (origins only: the console needs them before the visitor is
  signed in).
- **Sign-in.** If the frame has no session it asks the identity provider once, silently (`prompt=none`); if the
  provider needs the person it shows a **Sign in** button that opens the provider in a popup. The console's session
  cookie is `SameSite=Lax`, and Choregos does not weaken it for the embed. When the host is on the **same site** as
  the console (for example `app.example.com` and `choregos.example.com`) the frame carries that cookie. When the host
  is on **another site** the browser does not send it, so the popup (a first-party page, signed in with the ordinary
  cookie) asks `POST /api/v1/me/assistants/{org}/{slug}/embed-token` and hands the answer to the frame that opened it
  (`postMessage`, to the console's own origin). The frame keeps that **frame token** in its memory and sends it as
  `X-Choregos-Embed`. The token lives fifteen minutes, is bound to the person, their browser session (signing out ends
  it), the organisation and the installation, opens only the routes the component calls (open and read its
  conversation, send a turn, confirm or refuse an action, read a memory space) and carries a role in that organisation
  only; it never reaches the host application, and when it expires the frame shows the Sign in button again. A
  third-party cookie (`SameSite=None; Partitioned`) was not chosen: a cookie set by the popup is stored under the
  console's partition, not the host's, and Safari refuses third-party cookies whatever their attributes. The browser
  journey (`pnpm e2e:embed`, and `pnpm e2e:assistants` for the console's own screens) runs against fake APIs in CI.
- **Events.** The frame posts messages to the host with `postMessage`, to the allowed origin that framed it (never
  `*`; your listener should check `event.origin` is the console's and `event.data.source === "choregos.assistant"`).
  Every message is `{ source: "choregos.assistant", type, org, installation, ...payload }`:

| `type` | When | Payload |
|---|---|---|
| `conversation.started` | The conversation is open (new or resumed) | `conversation_id`, `resumed` |
| `action.confirmed` | The person confirmed a write the assistant proposed | `conversation_id`, `action_id`, `operation`, `status` |
| `memory.updated` | A turn wrote or proposed in a memory space (`?memory=space1,space2` narrows it to those) | `conversation_id`, `spaces` |
| `resize` | The content's height changed: size the iframe to it | `height` (pixels) |

`memory.updated` comes from the turn itself: its final event carries `memory_updated`, the spaces the turn wrote or
proposed in (a proposal counts: the person has something to confirm). `?memory=` only narrows it. The write actions
the assistant proposes appear in the frame with what the source's preview says they would change (`diff`) and Confirm and Refuse
buttons (the same routes as the console: the broker executes with the person's identity).

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

## The CLI

The `choregos` CLI is installed from the instance itself (S62-08): **Integrations › CLI** shows the
one-line install for the browser's system (`curl -fsSL <instance>/cli/install.sh | sh`, or
`irm <instance>/cli/install.ps1 | iex` on Windows), the version, every file with its sha256, and the
first commands (`choregos login --api-url <instance> --token <full-access token> --org <org>`, or the
`CHOREGOS_API_URL` / `CHOREGOS_TOKEN` / `CHOREGOS_ORG` variables). `choregos self update` replaces the
binary with the instance's current one. A build that bundles no CLI says so there and links the GitHub
release of the version it runs. The routes and why they are public: [deployment.md](deployment.md#the-cli-served-by-the-instance-s62-08).

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

## Another agent, over A2A

A workflow can be served to an outside agent as an **A2A skill** (A2A 0.3.0 and 1.0,
[ADR 0078](adr/0078-governed-workflows-as-a2a-skills.md)). Nothing is served by default: the
workflow opts in. This section is the reference; for complete calls, in order, read
[Starting a workflow from outside](starting-a-workflow-from-outside.md).

```yaml
a2a:
  skill: ship-a-change            # the skill id is `<project>/ship-a-change`
  description: Ship a change through review and release.
  input_modes: [text/plain]       # add application/json to take one data part as the fields,
                                  # a file's media type (application/pdf…) to take that file
```

The organisation's card is at `/a2a/orgs/<org>/.well-known/agent-card.json`, its JSON-RPC endpoint at
`/a2a/orgs/<org>`. Both take a token with the `a2a` scope, minted like an MCP one (a `*` token is
refused, and an `a2a` token does not open the REST API):

```bash
choregos tokens create --name partner-agent --scope a2a --project acme:billing-api --expires-in-days 30
```

The card lists the opted-in workflows of the projects the token's person may read. A skill that takes
`application/json` publishes its fields, the JSON Schema of the workflow's `metadata.inputs`, in the card's
`capabilities.extensions` (`https://choregos.dev/a2a/extensions/skill-fields/v1`, `params.skills.<id>`).
Fields the workflow refuses answer `-32602` with `error.data.errors`, each with its `loc` and `msg`. What
the door does:

- `message/send` opens a work item through the same path as the console, under an id derived from the
  organisation, the client and the `messageId`: a message sent twice is one work item. Text parts make
  its body, one data part its fields; name the skill in `message.metadata.skill` when there are several.
  A file part (`file.bytes` in base64, `file.mimeType` among the skill's `input_modes`) becomes an
  **input artefact** of the work item, named after `file.name` (`attachment-<n>` when it has none):
  a step that lists that name in its transition's `inputs` finds it in `.choregos/inputs/`. The
  project bounds them with `a2a.max_file_bytes` in its configuration, **10 MiB by default** (50 MiB
  at most), for each file and for all the files of one message together; it is set in the project's
  **Settings › A2A**, in MiB. A file sent by `uri` is
  refused: the door fetches nothing on a caller's behalf. The interpreter starts once the files are
  stored, and a store that fails creates nothing.
- `tasks/get` reads the work item: `working`, `input-required` while a person must decide (with the
  console's `decision_url` — a decision is never taken over A2A, and a reply to a task is refused),
  `completed` with the latest revision of each artefact, `failed`, `canceled`. An artefact's content
  is in its part while it is small — markdown as text, a JSON object as data, anything else as a file
  in base64 — up to 256 KiB each and 1 MiB per task, and only when its bytes still match its digest;
  otherwise the part describes it (`content` says why) and the console serves it. Its description is
  in the artefact's `metadata.choregos` either way.
- `tasks/cancel` stops the work item, for a person who may control it.
- `message/stream` (a `message/send` that stays open) and `tasks/resubscribe` answer server-sent
  events: the task, then a `status-update` each time its state, its workflow state or its pending
  request changes, and at the end each artefact as an `artifact-update` before a `final` status. An
  `input-required` status is not final: the person decides in the console and the stream goes on.
  The token is read again at each change (revoked, the stream ends with an error); five streams per
  token at most, an hour each, then resubscribe.
- `tasks/pushNotificationConfig/set|get|list|delete` register an `https` URL to hear of a task, with
  a `chg_` token (an OAuth token is refused: it cannot be read again at delivery). The host must be
  open in the organisation's egress list ([ADR 0066](adr/0066-the-organisation-egress-allowlist.md)) when it is
  registered. The answer to `set` carries `signing.secret`, **shown once**: `get` and `list` never
  return it, and a `set` on the same id draws a new one. Each notification POSTs the task as it is
  now, after its event is persisted, with `X-Choregos-Event-Id` (also `Idempotency-Key`: a receiver
  may see one twice), your `token` in `X-A2A-Notification-Token`, and
  `X-Choregos-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">`. Events in between
  are coalesced. A non-2xx answer is retried with a growing delay, eight times at most; a revoked or
  expired token ends the registration; so does the final state, once delivered. Registering inline
  in `message/send` is refused: its answer has no place for the secret.

The console walks through the same steps with the instance's own values: **Integrations › An
application or another agent (A2A)** (`/integrations/a2a`, or `/p/<project>/integrations/a2a` for one
project). It shows the organisation's card and endpoint, then:

1. **the workflows exposed**: per project, which workflows carry `a2a:` (skill id, input modes,
   required fields from `metadata.inputs`) and which do not, with the block to add, prefilled with the
   workflow's name, and a link to its YAML tab; each project's file limit, with a link to the setting;
2. **who calls**: an application's own account (create one or pick one, then issue its token, shown
   once), which takes `member:manage` on the project, and says so to whoever lacks it; and, second, an
   `a2a` token of one's own (30 days, shown once) to try the door;
3. **the calls**, in three tabs (the CLI, `curl` in A2A 0.3.0, A2A 1.0) with the instance's address,
   the organisation, the chosen skill and its required fields, and the token just issued, or a
   placeholder; `tests/web/test_a2a_snippets_match_the_door.py` holds every method and command they
   name to the door's tables and the CLI;
4. **the follow-up**: each task is a work item of the project, started by `machine:<address>` for an
   application; a decision is taken in the inbox; a token is revoked in **Settings › People ›
   Applications** or **My account › Access tokens**.

Each step links to the public documentation (`/integrate/a2a`, `/cli/install`).

The card needs the token too, since its skills depend on who asks. Calls are audited (`a2a.call`),
each stored file too (`workitem.input_file`, with its name, size and digest); they are rate-limited
per token, and count in the person's daily write budget with the MCP door's.

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
3. Declare the connector on `https://mcp.atlassian.com/v2/mcp` (Atlassian's API token page,
   2026-10-28). The credential decides how it is sent:
   - a **service account API key** travels as `Authorization: Bearer <key>`: install "Atlassian
     Rovo" from the MCP catalogue, with the key as its credential;
   - a **personal API token** travels as `Authorization: Basic base64(email:token)` (as Bearer it is
     refused): declare it by hand.

```bash
curl -X POST $API/orgs/acme/connectors -d '{"kind": "mcp", "name": "rovo", "type": "mcp",
  "config": {"url": "https://mcp.atlassian.com/v2/mcp", "auth_scheme": "basic",
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
your governed agents through an `mcp` connector on `https://mcp.atlassian.com/v2/mcp` and are live
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

### E-mail notifications

A project can send its notifications by e-mail: add a `notify` connector of type **E-mail (SMTP)**
(host, port, TLS mode, sending address, and the password as a write-only secret). Every notification
the orchestrator sends, the release train's included, then goes to the project's e-mail addresses
(Project settings, Notifications). When the project sets none, the connector's `to` list is used. A
failed send is logged and never fails the work that notifies.

SMTP does not go through the HTTP egress guard: the connector declares its host and port, and the
operator's network policy must open them. The setup procedure is `docs/procedures/connector.notify.email.md`.

## Limits, said plainly

Not built: Choregos **as** a Rovo agent inside Jira (it needs a Forge app and an A2A server); an agent
answering `input-required` from the console and resuming; push notifications; verification of signed
cards (A2A 1.0). The A2A 1.0 spelling is implemented from secondary sources and exercised against a
fake only; so is the Dynamic Client Registration exchange with Atlassian: neither has run against the
real service yet (`tests/live/test_rovo_a2a_live.py`, run by hand once the consent is given).

## Your organisation's MCP gateway (ADR 0105)

Some MCP servers cannot be called directly: they need hosting, or OAuth, or their credential must
stay in one place. If your organisation already runs an **MCP gateway** in front of them, Choregos
consumes it ([ADR 0105](adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md)): it calls the
gateway like any MCP server, and never is one. It does not route, aggregate or re-serve third-party
servers. Any gateway product can be that gateway; none is required or recommended here.

**What Choregos needs from the gateway.**

- MCP over Streamable HTTP.
- A way to name the server: in the path (`{server}` in the endpoint) or in a header.
- One of four ways to sign in: `bearer`, `header`, `oauth_client_credentials`, or `jwt_assertion`
  (`services/mcp_gateway.py`).
- Optionally, that it accepts the identity assertion Choregos signs for each call: an ES256 JWT of
  60 seconds, checked against the organisation's public JWKS URL
  (`services/mcp_gateway_identity.py`; the claims and their personal data are in
  [Security](security.md#mcp-gateway)).

**Steps** (an organisation administrator; the full walk-through is the procedure
[Connect an MCP server through your organisation's MCP gateway](procedures/connector.mcp.mcp-gateway.md)).

1. Register the server at the gateway, with its own credential or OAuth setup. Choregos never holds it.
2. **Administration → MCP gateway**: set the mode, the endpoint, the routing, the sign-in and its secret
   (sealed, write-only), then **Test the gateway**. Give the gateway the issuer and the JWKS URL the
   page shows. The issuer is stored with the first key, so renaming the organisation does not change it
   (ADR 0090; the former JWKS URL keeps resolving through `org_alias_middleware.py`).
3. **Library → MCP servers** (*Through your MCP gateway*) or **Connectors** (type
   `mcp-gateway`, with the server's name at the gateway).
4. Operators: the chart values and egress rules are in
   [Deployment](deployment.md#an-mcp-gateway-adr-0105); a local try-out with a fake gateway is in
   [Development](development.md#an-mcp-gateway-in-the-local-install-adr-0105); the console
   page is described in [Usage](usage.md).

**What changes for agents, and what does not.** An agent names the same tools as before. Policies,
the approval of a write, the per-tool permissions, the result cap and the injection guard apply as for
a direct connector (`services/broker.py`); the call is journaled in the cost ledger under the provider
`mcp:<connector>`. What changes is who holds the upstream credential (the gateway) and the extra hop
the gateway sees. In mode `required`, a direct `mcp` connector is refused with `gateway_required`
before anything is created: no action, no ledger line. The same goes for `gateway_not_configured`
(no gateway, or mode `off`) and `gateway_assertion_key_missing` (a lost key fails closed); removing a
gateway that connectors use is refused with `gateway_in_use`.

A plugin's server ([ADR 0096](adr/0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md): an
organisation connector with `plugin_server` set, run by the platform in-cluster in the plugins
namespace; `services/plugin_servers.py`) is not an upstream. It is never reached through the gateway
and `required` mode does not refuse it. See [Servers](plugins.md#servers) and
[Plugin servers](deployment.md#plugin-servers).

## A tool server at an outpost (ADR 0149)

Some servers live where nothing outside may connect: a production network, an internal tracker or
forge, a database behind a firewall whose rule is "no inbound". If your organisation already exposes
them through its own MCP gateway, use the section above: it needs nothing more. Otherwise an
**outpost** is installed inside that network ([ADR 0147](adr/0147-an-outpost-only-dials-out.md)): a
daemon that only dials out to Choregos and makes the calls it is handed. A connector of type
`mcp-outpost` is how an organisation uses a server through one
([ADR 0149](adr/0149-a-tool-may-live-at-an-outpost.md)).

**What it is declared with.** An outpost's name, the alias of an endpoint in that outpost's policy
file, and a timeout (`McpOutpostConnectorConfig` in `packages/contracts/openapi.yaml`). No URL and no
secret: the address and the credential are the outpost's and exist in its file only. A declaration
that carries either is a 422, and so is the name of an outpost the organisation does not have. The
walk-through is the procedure
[Connect an MCP server that lives at an outpost](procedures/connector.mcp.mcp-outpost.md).

**What changes for agents, and what does not.** An agent names the same tools as before. The agent's
selection, the operation's policy and groups, its argument schema, the grants that bind its
arguments, the approval, the result cap and the injection guard apply as for a direct connector
(`services/broker.py`): they are decided before a call leaves, and what the platform refuses writes
nothing an outpost could claim. A call is booked in the cost ledger under `mcp:<connector>` and its
`tool.called` event carries the same digests. What changes is the transport: instead of an HTTP call,
the call waits in the platform's database until an instance of the outpost claims it at its next poll
(`services/outpost_route.py`), sealed to that instance and signed by the door. The outpost is told
who the call is made for, as names it may log and decides nothing on; it reads from its own server
whether a tool writes. Each call is a line of the network activity under `outpost:<name>/<alias>`:
the platform opened no connection, so the organisation's allowed hosts and per-host rate do not
apply to it; the outpost's own ceilings and the door's quotas do.

**What it can refuse**, each a tool error that names the outpost, never a crash:

| Code | When | Booked |
| :-- | :-- | :-- |
| `outpost_paused` | the outpost is paused or revoked, or outposts are switched off | no |
| `outpost_offline` | no instance that serves the alias polled in the last minute, or none took the call within the connector's timeout. An instance announces its policy file's aliases at every poll: an alias the file does not hold is answered this way | no |
| `outpost_refused` | the outpost's own policy refused; `refusal` is its code (`tool_not_allowed`, `write_denied`, `endpoint_unavailable`…) | no |
| `outpost_not_admitted` | a quota of calls in flight or per minute is reached, or the call is too large to cross the door | no |
| `outpost_timeout` | an instance took the read and its server did not answer in time | yes: it may have been executed |
| `action_in_doubt` | an instance took the **write** and never answered: its action ended `in_doubt` | yes: it may have been executed, and it is never sent again |

At declaration and at discovery the same refusals are a 409 that carries the code (a quota is a 429).
A call that reached no server is not booked.

**A write is delivered at most once, and may end in doubt** (ADR 0149 §5-6). Choregos does not see
the outpost's server: once an instance has taken a write, an answer that never comes back leaves one
thing unknown, whether the server made it. So:

- the write is sent once. Whatever is retried finds that first call and reads what became of it;
- a write no instance took reached no server: the action retries it and it is delivered when the
  outpost is back (then fails, saying why, if the outpost stays away);
- a write an instance took and never answered ends the action **`in_doubt`**: it is not sent again
  and nothing the action had done is undone
  ([Usage](usage.md#4-bis-governed-actions), "In doubt", says what a person then does).

That is weaker than what a direct connector gives, so it is never implied. A write operation of an
`mcp-outpost` connector is set to `allowed` or `approval` only when its rule says one of two things,
in the same request or before it (`PATCH /orgs/{org}/connectors/{name}/operations/{operation}`):

| The rule says | Meaning | After a doubt |
|:--|:--|:--|
| `"platform_arguments": ["action_key"]` | the tool has an argument `action_key` and makes a call harmless when it arrives twice with the same key. Choregos fills it (the same for one action and call, whatever an agent wrote there) | the write is delivered again with the same key; the action is in doubt only if every delivery was |
| `"in_doubt_accepted": true` | an in-doubt outcome is accepted for this tool, explicitly | the action ends `in_doubt` |

In the console, **Administration → Connectors** shows the two statements under each write of a
connector at an outpost (**How it is delivered**), and nowhere else: the first is offered only when
the tool's input schema has an `action_key` argument. What is ticked leaves with the policy that
opens the write, in the same request, or alone with **Set**; the API's refusal is shown with its own
words, on each statement it names.

A request that says neither is a 422 that names both fields, and so is one that would withdraw the
last of the two from a write that is open. `platform_arguments` is the list a plugin's server
declares for its own operations ([Plugins](plugins.md)); here the organisation states it for a
discovered tool, which declares nothing, and it is refused for a tool whose input schema has no such
argument. A tool whose schema changes is closed again and loses both statements. Choregos does not
verify that a server honours the key: the statement is the administrator's.

**Limits of this version.**

- **An action in doubt is not closed in Choregos.** A person checks the system behind the outpost
  and records what they found elsewhere.
- **The connector's own identity only.** The call reaches the server under the credential the
  outpost holds for the alias. A need that must be called as the person cannot be bound to such a
  connector.
- **Mode `required` of the MCP gateway does not refuse it** (ADR 0149 §8): through an outpost
  Choregos holds no upstream credential and opens no upstream connection.
- **An outpost does not keep data where it is.** A tool's result travels to Choregos, is bounded and
  screened there, and is read by a model.

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
