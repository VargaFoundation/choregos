# 0061 — Agent ownership scopes and forks

> **Amended by [ADR 0071](0071-an-agent-version-reaches-production-through-channels.md)**: the matrix
> has one more action, `release` (send a revision to testing or production, roll back), on the row of
> publish, edit and life.

- **Status**: accepted, 2026-10-15
- **Concerns**: the `agents` table (`scope`, `project_id`, `forked_from_*`), the agent registry API
  (`AgentCreate`, `AgentDto`, `forkAgent`, `changeAgentScope`, the `scope` and `project` filters of
  `listAgents`), `choregos_api.services.agent_governance`, `rbac.py` (two permissions), workflow
  publication, the orchestrator's agent resolution; amends
  [ADR 0033](0033-agent-registry-and-skill-library.md) (§1-2: every agent is an organisation's) and
  [ADR 0040](0040-a-built-in-agent-catalogue.md) (§4: catalogue updates concern common agents only)

## Context

ADR 0033 made every agent an object of the organisation, and `agent:manage` — an `org_admin` — the
only right to create, publish or suspend one. Two things followed that the product owner asked to
change (S39, request 9: *not everyone may modify agents; common agents are not modifiable except by
an admin; there are personal agents and project-owned agents; an agent can be forked to extend it*):

- a project owner cannot have an agent of their own, and a developer cannot experiment: the only way
  to a variant of `developer` is to ask an administrator;
- nothing tells a common agent (installed from the catalogue, ADR 0040) from one an administrator
  wrote, except the author of its version 1; an administrator who publishes v2 of `developer`
  changes the agent of every project that does not pin;
- extending an agent means re-typing it as a new one: nothing records the origin, nothing says that
  the original moved.

The console, meanwhile, offers editing to whoever opens the page and lets the API answer 403.

## Decision

1. **Four scopes, one slug namespace.** An agent has a `scope`:

   | scope | owner | what it is |
   | :-- | :-- | :-- |
   | `platform` | the organisation, curated | a **common** agent installed from the built-in catalogue |
   | `org` | the organisation | an agent shared across all projects, written by the organisation |
   | `project` | one project (`project_id`) | an agent that belongs to one project and runs only there |
   | `personal` | one user (`owner_id`) | someone's own agent: drafts, experiments, a personal assistant |

   Slugs stay unique per organisation (`uq_agents_org_slug`). The workflow DSL keeps
   `agent: slug[@version]`: **no workflow contract change, no interpreter change, no replay history.**
   A fork therefore takes a new slug.

2. **The permission matrix is computed by one server-side function**,
   `services/agent_governance.decide(principal, org, agent, action, …) -> Decision(allowed, reason)`.
   The routers ask it; the DTO carries its answers (`can`, `cannot_reason`) so the console disables a
   button with the server's own sentence rather than guessing roles. Its docstring and
   `docs/usage.md` hold the matrix; in short:

   - **common** and **organisation** agents are changed by an `org_admin` only; the refusal says to
     fork instead;
   - a **project** agent is changed by that project's `project_owner` (or an `org_admin`);
   - a **personal** agent is its owner's (and an `org_admin`'s), created by any member holding
     `agent:create_personal` (developer, release captain, project owner) in the organisation or in a
     project;
   - what a caller may **see**: common and organisation agents, any member; a project's agents, the
     members of that project; a personal agent, its owner and an `org_admin`. What the caller may
     not see answers **404**, never 403: its existence is not revealed.

   Two permissions are added to `rbac.py`: `agent:create_personal` and `agent:project_manage`
   (checked against the agent's project). `agent:manage` keeps its meaning — organisation and common
   agents. No `maintainer` role is added: the five roles are a contract enum and `project_owner`
   already is the project's maintainer.

3. **A personal agent never runs in a project's workflow; a project agent runs only in its project.**
   A workflow run by a whole team must not depend on an agent one person can change at will, or take
   away when they leave. Enforced at three points: the **pin** (`PUT /projects/{id}/agents/{slug}`,
   422), the **publication of a workflow** that names it (422 naming the agent, its scope and the fix:
   promote it, or fork it into the project), and at **run time** (`resoudre_l_agent` raises the
   existing non-retryable `AgentIndisponible`; nothing changes under `workflows/`). A personal agent
   runs in **trials** (S32-03), which already exist, and is promoted or forked to reach a workflow.

4. **Fork** = `POST /orgs/{org}/agents/{slug}/fork {slug, display_name?, scope, project?, version?}`:
   a new agent in the target scope whose **v1 is a byte-for-byte copy** of the source version's spec
   (default: the latest; same checksum), recording `forked_from_agent_id`, `forked_from_slug` and
   `forked_from_version`, audited `agent.fork`. Who may: whoever can see the source **and** create
   in the target scope. The source is never modified. The fork inherits nothing else — no pin, no
   client, no metrics. A fork of a fork records its immediate parent only. Nothing is forked into
   `platform`.

5. **The upstream is reported, never followed.** `AgentDto.forked_from = {slug, version, scope,
   latest_version, behind, available}`; `behind` is `latest_version > version`; a source deleted or no
   longer visible reports `{slug, version, available: false}`. Re-basing on the upstream's newer
   version is a **person's click** that publishes the fork's next version — never automatic (same rule as
   ADR 0040 §4: a catalogue update is never applied silently).

6. **Promotion** = `POST /orgs/{org}/agents/{slug}/scope {scope, project?}`: personal → a project
   (its owner **and** that project's owner, or an `org_admin`) or → the organisation (`org_admin`); a
   project's → the organisation (`org_admin`); organisation → one project (`org_admin`, refused 409
   while another project pins it or names it in an active workflow). A common agent never moves;
   nothing is promoted into `platform`; nothing is made personal. Audited `agent.scope`.

7. **Catalogue updates** (ADR 0040) are unchanged and concern `platform` agents only. **External
   agents** (`kind: external`, MCP-door clients) are organisation agents: a personal external agent is
   out of scope. The MCP door itself is unchanged; only creation and management go through `decide`.

8. **Migration of existing agents**: `scope = 'platform'` when version 1's `created_by` starts with
   `catalogue:` (the very test `catalogue_d_agents` applies), else `'org'`; `project_id` and the fork
   columns NULL. No agent changes hands and no pin moves: behaviour is unchanged for organisation
   administrators, and project owners gain rights only on agents created after the change. A CHECK
   constraint ties the columns: `(scope = 'project') = (project_id IS NOT NULL)`.

## Alternatives rejected

- **Namespaced slugs** (`@user/slug`, `project/slug`): changes the DSL pattern (a contract change of
  `workflow.schema.json`), touches every resolver and the replayed histories; org-unique slugs plus a
  scope column give the same governance.
- **A per-agent ACL** (an editors list): the request asks for ownership scopes; ACLs can come later on
  top of `decide()`.
- **Letting personal agents run in the workflows of projects their owner belongs to**: a shared
  process would depend on one person's private object; promotion makes the hand-over explicit.
- **Copy-on-write forks that follow the upstream automatically**: contradicts ADR 0033's immutability
  and ADR 0040's "never silently".
- **A `maintainer` role**: see decision 2.

## Consequences

- An `org_admin` keeps every right he had; the existing suites run unchanged.
- A project owner or a developer no longer needs an administrator to extend an agent: they fork.
- Choices that follow the plan with a stated bound: a trial needs `item:control` on the trial project
  and the right to create at least a personal agent (so, a developer and above) — it used to need
  `agent:manage`; a personal agent does not appear among a skill's users; `unpin` stays on
  `project:write` for any scope, so a stale pin can always be removed.
- The console (S39-09b) reads `can`, `cannot_reason`, `scope` and `forked_from`; until it lands, the
  API alone enforces the matrix.

## Update — 2026-10-16 (S40-10)

A fork copies the source's **tags** (§4, "inherits nothing else", is amended by this one line): they are
metadata, not a version, and the fork's owner edits them afterwards.

## Amendment 2026-10-16 (S39-25, #674): provenance of a catalogue agent installed by a template

The catalogue's provenance is the author of version 1, `catalogue:<slug>@<version>` (decision 1, and
[ADR 0040](0040-a-built-in-agent-catalogue.md) §4). A project template that lists `catalogue:<slug>`
used to import the agent authored `template:<name>@<v>` and scoped `org`, so the catalogue's upgrade
answered 409 "does not come from the catalogue" and the agent was frozen at the template's version.

- A template import records the **same provenance as a direct install**: version 1 authored
  `catalogue:<slug>@<catalogue version>`, scope `platform`. The template stays visible in the audit
  trail (`via: template:<name>@<v>`).
- A data migration (`f3a5c7e9b1d2`) backfills agents already imported: version 1 authored `template:*`,
  slug in the catalogue, scope `org`, not a fork. When the latest version still equals the catalogue's
  text, version 1 becomes `catalogue:<slug>@<version>`. When it does not — the organisation published
  its own text since — the provenance is recorded with a `!diverged` suffix (`@0` when the catalogue
  version it came from cannot be told). The scope becomes `platform` in both cases.
- An upgrade of a **diverged** agent whose latest text differs from the catalogue's is refused with a
  409 that says why: the upgrade proposes, it never publishes over what the organisation wrote. The
  organisation publishes the catalogue's text by hand (or forks) if that is what it wants.
