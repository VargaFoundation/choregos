# 0062 — Remote agents over A2A

- **Status**: accepted, 2026-10-16
- **Concerns**: the stage input (`stage-input.schema.json`, `StageInput.remote`), the agent registry
  (`Agent.kind`, `AgentSpec.runtime`), the organisation's remote credentials (`/orgs/{org}/remote-credentials`),
  the orchestrator's stage preparation and run start, the runner, the cost ledger; builds on
  [ADR 0033](0033-agent-registry-and-skill-library.md) (what an agent version is),
  [ADR 0056](0056-a-deterministic-step.md) (a step that is not an ACP agent),
  [ADR 0058](0058-connector-secrets-are-write-only-values.md) (the sealed secret store) and
  [ADR 0063](0063-agents-written-in-code.md) (`AgentSpec.runtime`); amends `docs/positioning.md` (A2A)

## Context

Vincent asked to integrate external agents such as Atlassian's Rovo. Choregos had two notions of
"external": `Agent.kind = external` is a **client of the MCP door**, something that calls *into*
Choregos (ADR 0033 §7); nothing let Choregos call *out to* an agent it does not run.

What Rovo actually exposes (checked 2026-10-15):

1. **The Rovo MCP Server**: tools (search, Jira, Confluence…), not an agent. Reached through an `mcp`
   connector (a separate change gives that connector a Basic scheme for Atlassian's API tokens).
2. **The Atlassian A2A gateway** — the only documented way to invoke Rovo itself from outside. One
   agent, "Atlassian Rovo", with two skills (`work-in-jira`, `work-in-confluence`); A2A protocol
   0.3.0 over JSON-RPC (`message/send`, `message/stream`, `tasks/get`, `tasks/cancel`); **no push
   notifications**; synchronous calls are cut at about 30 s by the CDN, so streaming or polling is
   the rule. Authentication is **OAuth 2.0 authorization code + PKCE + Dynamic Client Registration
   only** (scopes `read:me`, `offline_access`, `full_access:chat:rovo`): no API token, no service
   account — everything runs **as the user who authorised**. Custom Rovo agents built in Rovo Studio
   are not individually addressable. An organisation administrator must enable A2A in Atlassian
   Administration. The real card is kept as a test fixture.

## Decision

1. **A new kind: `remote`.** `Agent.kind` is `internal` (run by the platform), `external` (a client of
   the MCP door, unchanged) or `remote` (called by the platform over A2A). A `remote` agent needs an
   `a2a` runtime and only a `remote` agent has one (422 otherwise, on creation and on publication).
   Creating or publishing it is `agent:manage` (org_admin), as for every registry agent: it uses an
   organisation credential. Skills, knowledge bases, MCP servers, `model` and `backend` mean nothing
   to an agent that runs elsewhere and are refused with an `a2a` runtime.
2. **`AgentSpec.runtime` gains `A2aRuntime`**: `{kind: a2a, card_url, skill?, credential?,
   timeout_minutes (1..480), declared_cost {per_task_usd?, per_minute_usd?}, input: text | text+files,
   pinned_card}`. The instructions of the version stay: they become the preamble of the message.
3. **The card is pinned at publication.** The API fetches the card (https only, no redirect to another
   host, 10 s, 512 KiB), checks that the named skill exists and that the credential's type is one the
   card accepts, and records `pinned_card {url, protocol_version, digest (SHA-256 of the canonical card),
   skills}`. A `pinned_card` sent by the caller is replaced by what was read. A card that changes is a
   new decision: publish a new version.
4. **Execution is in the runner pod, not in the orchestrator.** The orchestrator prepares the step
   (`StageInput.remote = RemoteCall {endpoint, card_url, protocol_version, skill, card_digest,
   timeout_s, input, declared_cost, agent_slug, credential}`, `agent.backend = "a2a"`, a placeholder
   model, no gateway key, no repository to clone, `memory_scope = none`, no skills or tools) and the
   runner calls the agent: it reads the card **again** and stops with `remote_card_changed` before any
   message if the digest moved; it sends one message (instructions and task; the declared inputs as
   file parts for `text+files`) with `metadata.choregos = {run_id, work_item, skill}`; it follows the
   task by streaming, or by `message/send` and polling `tasks/get` when the agent cannot stream; it
   cancels (`tasks/cancel`) at the step's time. **The project's memory is never sent to a remote
   agent.** *Rejected: an orchestrator activity calling A2A* — it would hold user tokens in the
   orchestrator, bypass the run lifecycle (admission, events, gates, the pod's egress policy) and, for
   long tasks, need workflow code (replays). The pod waits cheaply and everything else is reused. No
   change under `workflows/`: there is no new actor type and no new error type reaches the interpreter.
5. **The result is an ordinary `StageResult`.** The final message is `outputs/response.md`; artifacts
   are files in `.choregos/outputs/` (names sanitised) or, when their name is a declared inline output,
   its value; the single inline output nothing named takes the answer (a text-only agent has no other
   way). Facts the agent states in a `facts` data artifact are **declared, never measured**
   (`evidence.measured` does not list them; `artifacts.reports.remote_provenance = remote:<slug>`).
   The same gates read the result as for any step. Failures are results with a reason —
   `remote_failed`, `remote_timeout`, `remote_card_changed`, `remote_card_unreachable`,
   `remote_auth_required`, `remote_input_required` (the question goes to `questions[]`; answering it
   and resuming on the same `contextId` is phase 2), `remote_input_too_large`, `remote_result_invalid`.
6. **Declared cost, kept apart.** A2A reports no cost. The registry entry declares one; the stage
   reserves `per_task + per_minute × minutes` against the ticket and the day (`reserve_key`, no key is
   minted) and refuses the stage on budget when it does not fit; at the end `collect_spend` books the
   wall time actually spent as a ledger line `kind = external`, `provider = <endpoint host>`,
   `model = a2a:<slug>` (`(unmeasured)` when nothing was declared). The costs page shows it apart.
7. **Credentials are an organisation object, and the refresh token never leaves the store.**
   `remote_credentials` {name, type `oauth2_authorization_code | bearer | none`, public registration
   data, a `store:` reference, `authorised_as`, status}. OAuth: `POST` registers the platform at the
   card's DCR endpoint (RFC 7591, public client, redirect `{api_url}/api/v1/remote-credentials/oauth/callback`),
   `authorize` returns the provider URL with a PKCE challenge and sets a signed httpOnly cookie (the
   handshake pattern of the OIDC login), the callback exchanges the code and **seals the refresh
   token in the secret store** (ADR 0058, `remote/<name>/refresh_token`) — rewritten at every refresh,
   since providers like Atlassian rotate it. `start_run` exchanges it for a short-lived access token,
   commits the rotated refresh token **before** starting the executor (so a failure after the exchange
   cannot lose it), and puts only the access token in the run's Secret (`CHOREGOS_RUN_REMOTE_TOKEN`,
   next to the run token and the gateway key, in every executor). It is in no `runs.stage_input`, no
   Temporal payload, no journal. The credential row is locked during the exchange. **Who authorises
   matters**: the remote agent acts with that person's permissions. The recommendation is a dedicated,
   least-privileged account.
8. **Egress is admission.** The stage is refused (`AgentIndisponible`, the interpreter's existing
   configuration refusal: the work item is escalated, a person fixes the configuration) when the
   endpoint host is not in the project's `sandbox.network.allow_domains`, naming the fix; so is a
   credential that is missing, pending or revoked. On Diametral the platform's FQDN egress list must
   also contain the host (infra).
9. **The A2A client is ours** (`choregos_adapters/a2a`: httpx, SSE parsing), 0.3 and 1.0 method names
   chosen by the card's `protocolVersion`, with a scriptable `FakeA2aAgent`. *Rejected: the `a2a-sdk`
   package as a dependency* — its 0.3→1.0 transition and server weight are not needed for four client
   calls. 1.0 details come from secondary sources: that path is exercised against the fake only.
10. **Not built.** Plain-HTTP agents without A2A (wrap them as an MCP tool, or a later `webhook`
    runtime); push notifications; signed-card verification; **the inverse direction** — Choregos as a
    remote agent inside Jira through a Forge `rovo:agentConnector` (it needs a Forge app and an A2A 1.0
    server): a follow-up story, not a decision here.

## Consequences

- Contract change: `Agent.kind` (+ `remote`), `AgentSpec.runtime` (+ `a2a`), `StageInput.remote`,
  `StageJobSpec.remote_token`, six operations (`listRemoteCredentials`, `createRemoteCredential`,
  `authorizeRemoteCredential`, `remoteCredentialCallback`, `revokeRemoteCredential`,
  `discoverRemoteAgent`), table `remote_credentials`.
- The Rovo path through MCP tools (a service-account API token) and this one are different things: tools
  are live queries a governed agent makes under per-operation policy; the A2A agent is Rovo itself,
  as a person.
- Rovo usage counts against the Atlassian subscription; the ledger line is a declaration, not a bill.
- A remote step uses no repository: it commits and pushes nothing. What it hands over are its answer
  and its artifacts, typed and stored like any step's.
