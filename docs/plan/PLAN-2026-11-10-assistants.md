# Plan — 2026-11-10: assistants, a plugin-shipped capability (stream S63)

Source: the product owner's specification of 2026-11-10, "1:1 agent — business, agent and platform
specification" (tab A, *the Assistant capability*, is the part this repository implements; its first use, a
one-on-one coach, lives in a private plugin of a deployment repository and in the sources' own repositories,
not here). The specification is French and stays outside the repository (ADR 0046).

Checked against `main` at `8698a2c0`. Stream number: **S63**. ADRs: **0133-0140** (proposed). Story ids keep
the specification's numbers: `S63-NN` is its `AS-NN`.

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

## 0. What the audit found

| What an assistant needs | Where | Today |
|:--|:--|:--|
| A plugin content for it | `plugin.schema.json` `contents` (`agents, skills, templates, evals, procedures, servers, matrix_cases`), `choregos_core.plugins.load_directory`, `_declared_refs` | **missing**: no `assistants`, and no `workflows` or `knowledge` key either |
| Install and check | `services/plugin_installer.py` (`_prepare`, `install`, `upgrade`, `uninstall`) | **exists** for agents and servers; no readiness |
| Who may talk to an agent | `routers/agent_sessions.py`, `agent_governance.decide(Action.CONVERSE)`, `item:control` in a project | **exists**, project-bound |
| A person's own space | `projects` (`db/models/projects.py`), `GET /orgs/{org}/projects` | **missing** |
| Durable conversation | `services/agent_sessions.py`: `SESSION_TTL_S = 3600`, one key per session (`_reserve`, `run_id="session:<id>"`), no list, one SSE delta | **exists**, one hour, not resumable |
| Knowledge in a conversation | `knowledge/index.py` `search`; offered to runs only (`stage_tools._knowledge_of_agent`, `/internal/runs/{id}/knowledge/search`) | **missing** in sessions |
| Memory in a conversation | `memory_stores` / `memory_facts` (ADR 0075), `services/memory_access.py`; runs only | **missing** in sessions; no person or subject scope |
| A call as the person | `services/broker.py` `_call` → `plugin_servers.connector_client` (the connector's credential); `mcp_gateway_identity.identity_of_person` (gateway assertions only) | **missing**: S50-01, S50-02 not started |
| Writes in a conversation | `session_tools` keeps `not is_write and not needs_approval` | **missing** (read only) |
| MCP door | `mcp/transport.py` (`/mcp`, `/mcp/projects/{org}:{slug}`), `mcp/tools.py` `core_tools`, `mcp:read`/`mcp:write` | **exists**; no conversation, no prompts |
| Retention of sessions | none; the run purge does not reach `agent_sessions` | **missing** |

## 1. Departures from the specification (the code wins)

- ADRs **0133-0140**, not 0126-0133 (taken on `main` or by open pull requests on 2026-11-10).
- The table stays **`agent_sessions`** with new columns (ADR 0134); the specification's rename to
  `conversations` with a compatibility view is dropped.
- Projects are listed by **`GET /orgs/{org}/projects`** (there is no `GET /projects`).
- Task workflows and knowledge seeds are **files the assistant names**, packed with the plugin; no
  `contents.workflows` or `contents.knowledge` key (ADR 0133).

## 2. Stories

Lot 1 (core of a pilot): S63-01..07, 09, 11, 15, 16, 19; S63-17 and S63-18 in part. Lot 2: S63-08, 10, 12,
13, 14 and the rest of 17 and 18. The minimal subset for a pilot through the MCP door is S63-01, 02, 03,
04, 05, 07, 09, 11. Order of the first pull requests: S63-00, then S63-01, 03, 04, 07.

### S63-00 · docs(adr): ADRs 0133-0140, this plan, the backlog stories

### S63-01 · contract(schemas): `assistant.schema.json` and `contents.assistants` (AS-01)

The manifest of ADR 0133 and validation rules 1-8 in `choregos plugin validate`. **Accepted when** one
invalid fixture per rule is refused with the JSON path of the field, and `make contracts` is regenerated.

### S63-02 · feat(api): install an assistant, bind its needs, readiness (AS-02)

**Accepted when** an unbound required need gives `incomplete` and a readiness item; binding it gives
`ready`; a connector that does not serve the operation is refused (422).

### S63-03 · feat(api): the implicit personal space (AS-03)

**Accepted when** it is created at the first conversation, one per person and organisation, absent from
`GET /orgs/{org}/projects`, unreadable by another person (RLS, PostgreSQL test).

### S63-04 · feat(api): durable conversations (AS-04)

A key per turn, closing on inactivity, list, resume, SSE relayed from the gateway; `/responses` compatible.
**Accepted when** a conversation is resumed after 2 h (simulated clock); the `per_conversation_usd` cap
holds across turns; the existing tests of `test_agent_sessions.py` pass unchanged.

### S63-05 · feat(api): knowledge in a conversation (AS-05)

**Accepted when** the agent cites a passage of an installation's base; a base outside the installation is
never offered, even to a person who may read it.

### S63-06 · feat(api): offered knowledge, `origin`, proposed updates (AS-06)

**Accepted when** the four cases of the three-way merge (unchanged, changed locally, changed upstream, both)
behave as `upstream` says.

### S63-07 · feat(api): the `user_exchange` identity (RFC 8693) and `act` (AS-07)

**Accepted when**, with a fake identity provider, the source receives a token whose subject is the person and
whose audience is the connector's; without a subject token, `identity_unavailable` and no call under the
connector's identity; a live test against Keycloak (`make test-live`).

### S63-08 · feat(api): connected accounts, `user_consent` (AS-08, lot 2)

**Accepted when** a simulated OAuth flow works end to end; the token is sealed (ADR 0058), never returned;
revocation is immediate; `account_not_connected` carries the URL; the catalogue no longer opposes
`needs_oauth`.

### S63-09 · feat(api): memory spaces, shapes, write rules, subject resolver, memory tools (AS-09)

**Accepted when** `memory_search` on an unresolved subject is refused; resolved by a resolver tool, it
opens; `resolver: none` accepts a well-formed subject; `agent_proposes` writes `proposed`; `memory.enabled:
false` cuts everything.

### S63-10 · feat(api,web): inline writes (AS-10, lot 2)

**Accepted when** proposal, confirmation and execution happen with the person's identity; through the MCP
door, a `confirm_url` and no execution; owners cannot go below the floor.

### S63-11 · feat(api): the MCP door talks to assistants (AS-11)

`list_assistants`, `ask_assistant`, `get_conversation`, a door per assistant, prompts. **Accepted when** a
test MCP client (the official SDK) with OAuth makes a turn, gets `status: working` then the answer; a person
outside the audience does not see the assistant; prompts are listed by `prompts/list`.

### S63-12 · feat(web): the embedded component (AS-12, lot 2)

**Accepted when** a Playwright host page on an allowed origin opens the conversation with its context; on
another origin the frame is refused (`frame-ancestors`); `postMessage` events are received.

### S63-13 · feat(api,orchestrator): tasks — subscriptions, schedule per time zone, `poll`, delivery (AS-13, lot 2)

**Accepted when** a subscriber in Paris and one in Bogota get their output at their own hour; a `poll` never
starts the same object twice; the artifact is in the inbox.

### S63-14 · feat(api): the standing delegation (AS-14, lot 2)

**Accepted when** an offline read works while the delegation is valid, and is refused after expiry,
revocation, leaving the audience or a change of `manifest_digest`; a write under delegation becomes a
proposal.

### S63-15 · feat(api): `assistant:use`, `assistant:manage`, `assistant:audit`; the audience at every turn; three budget caps (AS-15)

**Accepted when** removing an IdP group cuts the next turn, and each cap refuses the turn that exceeds it.

### S63-16 · feat(api): retention, the reads journal, erasure by user and by subject, export (AS-16)

**Accepted when** the purge deletes an expired conversation and its events; every tool call writes a journal
line; an erasure by subject deletes its memories across users and redacts the conversations it was resolved
in.

### S63-17 · feat(evals): conversation evals and feedback (AS-17; deterministic in lot 1, judge in lot 2)

**Accepted when** a conversation suite blocks promotion below its threshold; a thumb down creates a
pseudonymised candidate case, not added automatically.

### S63-18 · feat(web): console — assistants, conversation, memory, accounts, tasks, installation (AS-18)

Memory and installation in lot 1, the rest in lot 2. **Accepted when** each screen has web tests (`pnpm
test`) and every route of the stream has a client.

### S63-19 · test(plugins): the `investment-assistant` fixture plugin on fakes (AS-19)

**Accepted when** it installs and converses with no code of its own in the core: the genericity guard, played
in CI.

## 3. Outside this repository

The identity provider's exchange and offline tokens, the sources' MCP servers and fakes, the private
plugin of the first assistant, the data protection impact assessment and the works council's opinion. The
stories above advance on fakes until they exist.

## 4. Schedule

Waves follow the backlog's dependencies; the stories of one wave run in parallel, each on its own branch
stacked on the wave's base, and the integrator chains their migrations (`down_revision`) and the STATUS
total when merging.

| Wave | Stories | Base | Needs |
|:--|:--|:--|:--|
| 0 | S63-00, S63-01, S63-03 | `main` | delivered (#1534, #1536, #1537) |
| A | S63-02 install and readiness · S63-04 durable conversations · S63-07 `user_exchange` · S63-17 conversation evals (deterministic) | S63-03 | ADRs, the contract, the personal space |
| B | S63-05 knowledge · S63-09 memory spaces · S63-11 MCP door · S63-15 rights and caps · S63-19 the genericity guard | wave A | installations and conversations |
| C | S63-06 offered knowledge · S63-16 retention, journal, erasure · S63-18 console (lot 1 part) | wave B | knowledge and memory in conversations |
| D (lot 2) | S63-08 connected accounts · S63-10 inline writes · S63-12 embedded component · S63-13 tasks · S63-14 delegation · S63-17 judge · S63-18 rest | wave C | the gates of lot 1 (CSE opinion, DPIA, security review of S63-07) |
