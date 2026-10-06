# Usage

This page takes a project from nothing to a ticket that an agent carries through to the end.

## 1. Try it without installing anything

```bash
make setup     # uv sync + pnpm install
make demo      # one ticket travels the whole platform, in memory, no cluster
make ci        # lint, strict typing, the test suite, contracts, charts
```

`make demo` uses the `fake` connectors. It proves the machinery, not the integrations.

## 2. Create a project

Three ways, same result: the web wizard (`/projects/new`), the CLI, or the API.

```bash
choregos login --api-url https://api.example --token "$CHOREGOS_TOKEN" --org acme
choregos projects create billing-api --name "Billing API" --org acme
choregos projects list
```

`--template <name>@<version>` on `create` also provisions the project: repository, board,
GitOps manifests and scaffolding. Without it, the project is created empty and you attach
its connectors yourself, from the UI (*Settings → connectors*: type and configuration, secrets referenced never typed) or the API (`PUT /projects/{id}/connectors/{kind}`).

A project needs, at minimum: a **tracker** (where humans look), a **workflow** and a
**policy**. Everything else has a default or can be added later.

### Connectors

| Kind | Implementations |
| :-- | :-- |
| `tracker` | `github-issues`, `jira`, `gitlab-issues`, `internal`, `fake` |
| `scm` | `github`, `fake` |
| `ci` / `cd` | `tekton` / `argocd`, `fake` |
| `runtime` | `tekton`, `k8s_job`, `aca`, `local_docker`, `fake` |
| `gateway` | `litellm`, `direct`, `fake` |
| `memory` | `ecphoria`, `lexical`, `fake` |
| `notify` | `slack`, `fake` |

A project only needs what its workflows read ([ADR 0034](adr/0034-connecteurs-par-capacites.md)).
`GET /projects/{id}/requirements` derives it — a guarantee says what it reads (`ci_green` the CI,
`scope_respected` a diff), an agent whose role works in a repository needs an `scm`, a release
train or a `deployed_prod*` state needs a `cd` — and says why for each. An HR project shows
neither `scm`, nor `ci`, nor `cd`; the settings page lists what is required, what is configured,
and *add a connector* for the rest. `GET /connectors/types` reads the adapter registry, plugins
included: each type gives its capabilities, the JSON Schema of its configuration (the console
draws the form from it) and its **secret fields**.

A secret is never written in `config` — a secret field there is refused (`422`). It is named by
**reference**, field by field, and resolved when the adapter is built, in the process that uses it:

```bash
curl -X PUT $API/projects/acme:hr/connectors/tracker -d '{"type": "jira",
  "config": {"base_url": "https://acme.atlassian.net", "email": "bot@acme.test", "project_key": "RH"},
  "secret_refs": {"api_token": "env:JIRA_TOKEN"}}'
```

The core reads `env:NAME`; a plugin declares other schemes (a vault) with
`choregos_core.secrets.declarer_un_resolveur`. A reference the process cannot resolve fails the
connector test with its name — never a silent fallback to a default value.

**The organisation's connectors.** A directory, a device manager, an MCP server is declared once
by the organisation's administrator (*Administration → connectors*, `POST /orgs/{org}/connectors`),
named, and shared by its projects. It is born with the operations its type declares — a read
`allowed`, a write `approval` (a governed action, [ADR 0035](adr/0035-actions-gouvernees-dans-le-coeur.md)) —
and each operation carries its **policy** (`allowed`, `approval`, `forbidden`), the **project
groups** it opens to (none: every project) and a price. Only the administrator decides them
(`tools:grant`). A project sees the operations its groups open (`GET /projects/{id}/operations`)
and may only **tighten** one: `PUT /projects/{id}/operations/{connector}/{operation}` refuses an
`approval` turned into `allowed` (`422`); `DELETE` returns to the organisation's policy, which a
later hardening by the organisation always overrides.

**An MCP server** is a connector of type `mcp`: its URL, and its key by reference
(`secret_refs: {"token": "env:SUPPLIER_KEY"}`). It declares no operation — they are
**discovered**: `POST /orgs/{org}/connectors/{name}/discover` (or *discover* in the console) runs
`initialize` and `tools/list` over Streamable HTTP — JSON or SSE answers, the session the server
gives, every page of the list — and returns the diff. **A new tool is born closed**
(`forbidden`): a server does not add a capability behind the administrator's back. A tool whose
input schema **drifts** — `order_laptop` suddenly taking a quantity — is closed again; one the
server stops offering is removed, with what projects tightened of it. A tool annotated
`readOnlyHint` is a read; any other is a write. Only the connector's key reaches the server —
never a person's token, never a run's; a key the process cannot resolve answers `502` naming it,
and the connector shows the error.

**Runs reach these servers through the platform.** An agent's version names the servers it uses
and the tools it takes from each — `mcp_servers: [{"connector": "supplier", "tools": ["track_*"]}]`
(`["*"]` for all; a server named without patterns opens nothing). A run of that agent sees, as
`supplier__track_order`, the tools that are **in its selection, `allowed`** for its project
(after the project's tightening) and open to its groups — an `approval` operation is a governed
action, not a tool. The agent calls it through `choregos-tools`; the platform checks the
arguments against the tool's schema (`400`, nothing billed), counts the call against the run's
`tool_calls_per_run` (`429`), calls the server **with the connector's key, resolved in the API**
— the run's pod holds only its run token — records the cost (`provider: mcp:<connector>`,
the operation's price) and a `tool.called` event, and passes what the server returns through the
injection guard: `warn` adds the suspicions to the answer, `block` withholds it (`451`). A tool
outside the selection, `forbidden` or awaiting approval does not exist for the run (`404`).

**A directory: `entra`** (Microsoft Entra ID through Graph, kind `identity`) is declared like any
connector of the organisation — tenant, application, the **administrative unit** the platform may
touch, and the application secret by reference. Its six operations come with their input schema:
`get_user` (a read, `allowed`), `create_user`, `add_to_group`, `remove_from_group`,
`disable_user`, `revoke_sessions` (writes, `approval`). Each is **idempotent**, because a governed
action replays: an account is looked up by UPN before it is created, "already a member" is a
success, removing a missing member too. An account the platform creates enters the administrative
unit; a gesture on an account outside it is refused, named, before anything is written. Graph's
`Retry-After` is honoured. With `CHOREGOS_FAKES=1`, a scriptable Graph answers instead.

**Business families: `mdm`, `shipping`, `access_control`.** A device manager (enrol a device for
its user, wipe it when it comes back, read it), a carrier (ship a parcel, book a collection — one
per reference —, track it) and badge readers (activate a badge for its holder, deactivate it, read
it) are declared like a directory, with `kind` and the key by reference (`api_key`). Reads are
`allowed`, writes `approval`. Every write is **idempotent**, and one that contradicts the state is
refused, named: a device enrolled for someone else, a reference already taken by another parcel, a
badge the readers do not know — a mistyped UID would leave the real badge opening doors. These
families have **no real type yet**: type `demo` keeps them in memory, each process its own — a
demonstration, refused in staging and prod. Where the API and the orchestrator are several
processes, give the `demo` connector a `url`: it then reaches the fakes served by **one** pod
(`demoFakes.enabled` in the chart, `python -m choregos_adapters.fakes.serveur`), which also serves a
fake Microsoft Graph for an `entra` connector whose `graph_url` and `login_url` point to it. A supplier's agent needs no family: it is an MCP
server like any other, its tools discovered and born closed.

`memory: lexical` keeps the project's memory in Choregos's own database — no extra service,
and a lexical, not semantic, search: there is no `vector` extension behind it. It was called
`pgvector` until 2026-09-26, which promised something the code does not do; that name still
works as a deprecated alias.
a lexical ranking instead of embeddings, the same governed writes. The connector type decides
for both the API and the orchestrator; on PostgreSQL the memory is read under the project's
organization, like everything else under row-level security.

`gateway: direct` lets agents use credentials they bring themselves. It is the honest choice
for a bench, and it says what it costs: **no spend is measured and no cap applies** — only
the turn and minute budgets still hold.

## 3. Write the workflow

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata: { name: default-simple, version: 1 }
actors:
  dev:      { type: agent, role: implement, model: "profile:standard", max_turns: 40, max_minutes: 30 }
  reviewer: { type: agent, role: verify,    model: "profile:standard", max_turns: 20, max_minutes: 15 }
  owner:    { type: human, group: maintainers, sla_hours: 24 }
states:
  inbox:       { display: Inbox, kind: wait }
  in_progress: { display: In progress }
  verifying:   { display: Verifying }
  done:        { display: Done, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
transitions:
  - id: t-implement
    from: inbox
    to: in_progress
    by: dev
    gates: [evidence_present]
    on_fail: { to: inbox, max_attempts: 2, escalate_to: needs_human }
  - id: t-verify
    from: in_progress
    to: verifying
    by: reviewer
    gates: [evidence_present]
    on_fail: { to: in_progress, max_attempts: 2, escalate_to: needs_human }
  - id: t-accept
    from: verifying
    to: done
    by: owner
    timeout_hours: 48
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
```

Validate before you commit it:

```bash
choregos workflow validate my-workflow.yaml   # add --remote to validate server-side
```

The validator refuses more than syntax: an unreachable state, a retry that can loop forever,
an unknown gate, or **a gate that would have nothing to check** — `outputs_present` on a
transition that declares no `outputs:`. If you mean it, say so: `params: { allow_empty: true }`.

The console's **workflows** tab (`/p/<slug>/workflows/<name>`) shows each workflow four ways:
the process (each transition in plain words), the map (one lane per kind of actor), the YAML
editor (it validates as you type) and the version history. The map is keyboard-navigable: Tab
reaches the states in reading order, ← and → follow transitions, ↑ and ↓ move between states,
Home and End jump to the ends, Enter opens the state for editing. The state under the cursor is
described below the map, with its outgoing transitions, actors and gates — that sentence is what
a screen reader announces.

### Several workflows in one project

A project runs as many workflows as it needs — onboarding and offboarding in an HR project, a
delivery flow and a maintenance flow in a platform team ([ADR 0031](adr/0031-plusieurs-workflows-par-projet.md)):

```bash
curl -X PUT  $API/projects/acme:hr/workflows/onboarding  -d '{"yaml": "..."}'   # publishes the next version
curl -X PUT  $API/projects/acme:hr/workflow-routing -d '{"default": "onboarding",
  "rules": [{"when": {"labels_any": ["leaver"]}, "workflow": "offboarding"}]}'
curl         $API/projects/acme:hr/workflows                                    # one active version per name
```

- One version is active per name; every publication adds a version, none is overwritten, and
  restoring an old version publishes it as the next one.
- A work item is bound at birth — an explicit `workflow`, else the first routing rule that
  matches its labels or its tracker type, else the default — and **pinned to that version**:
  publishing a new version never moves a running item.
- `PUT /projects/{id}/workflow` remains, as the alias of the default workflow.
- A stack template ships them to every project born from it, in its `manifest.yaml`:

  ```yaml
  defaults:
    workflows: [workflows/onboarding.yaml, workflows/offboarding.yaml]   # or template:<name>@<v>
    default_workflow: onboarding
    routing:
      - { when: { labels_any: [leaver] }, workflow: offboarding }
    policy: preset:team
  ```

  A path is read from the template's folder and may not leave it; a template published through
  `POST /templates` has no folder, so it ships core workflows only (`template:<name>@<v>`). A
  template that names a workflow it does not ship, or ships an invalid one, creates no project
  (`422`). The singular `workflow:` of older manifests still works.
- The default and a routing target cannot be deactivated; a deactivated workflow takes no new
  item, and its pinned items finish on their version.
- **Moving a running item to another version** is explicit:

  ```bash
  curl -X POST $API/work-items/ACME-12/actions \
    -d '{"action": "migrate", "workflow_def_id": "<id>", "state_mapping": {"triage": "request"}}'
  ```

  The API checks that the version belongs to the project and that the item's current state exists
  in it, or is mapped — `422` otherwise, and nothing is sent. An item waiting on a human decision
  or running an agent answers `409`: pause it, let the run finish, then migrate. A parked or
  paused item moves at once, its pin follows the new version, and its timeline shows
  `workitem.migrated`; if its state changed in between, the migration is refused
  (`workitem.migration_refused`) and the item carries on, on its own version.

### Editing a workflow without rewriting it

The console's map and process view change a workflow through typed operations, grafted into the
YAML text: everything else — comments, quoting, flow or block style — stays byte for byte
([ADR 0031](adr/0031-plusieurs-workflows-par-projet.md)). The same call is open to any client:

```bash
curl -X POST $API/workflows/edit -d '{"yaml": "...", "operations": [
  {"op": "add_gate", "transition": "t-implement", "gate": "ci_green"},
  {"op": "rename_state", "from": "triage", "to": "intake"}]}'
```

| Operations | On |
| :-- | :-- |
| `add_state`, `remove_state`, `rename_state`, `set_state` | states — a rename follows every reference |
| `add_transition`, `remove_transition`, `set_transition` | transitions, by `id` |
| `add_gate`, `remove_gate` | a transition's guarantees |
| `add_actor`, `remove_actor`, `set_actor` | actors |

The answer carries the edited `yaml`, its unified `diff`, the validation and the graph, and the
`inverse` operations, which give back the original bytes. Nothing is saved: the console saves
with `PUT /projects/{id}/workflows/{name}` and the `base_version` it read. A state or an actor
still named elsewhere cannot be removed (`422`); renaming a state whose name carries an effect
(`pr_*`, `merged*`, `deployed_prod*`) is allowed, and said in `notices`.

In the console, a click on a state or a transition of the map — or **edit** on a step of the
process view — opens its panel. A state has its label, its name, a new transition (to a state
that exists, or to a new one) and its removal; a transition has who moves it, its guarantees, its
time limit and its removal. Each change is one operation. The map and the process view redraw
from the same draft, which shows its last diff and undoes change by change (undo replays the
inverse). Nothing is saved before **publish**, which sends the text with the version that was
read: if someone published meanwhile, the console says so (`409`) instead of overwriting.

### Workflows outside software

The same engine carries work that has no repository. Name your own roles, bring your own
playbooks, and use the gates that do not speak of code:

```yaml
actors:
  sourcer: { type: agent, role: sourcing, model: "profile:standard" }
transitions:
  - id: t-sourcing
    from: request
    to: sourcing
    by: sourcer
    outputs: [profiles]
    gates:
      - name: outputs_present
      - name: evidence_facts
        params: { keys: [profiles_kept], min: { profiles_kept: 1 } }
```

`evidence_facts` reads what the agent **counted**, not what it narrated, and `min` refuses a
polite zero: finding nobody is said by failing the step. `demo/workflows/staffing.yaml` is a
complete example that runs on the same deployment as the software one, with no code change.

The `joiners-leavers` template goes further, with no agent writing anything itself: an agent
prepares the access plan with its skills, a person from HR validates it, and every write is a
**governed action** at a date — the account at D-10, the sensitive group on a re-authenticated
approval, the laptop ordered from the supplier's agent at D-7, the badge a **task** with its proof,
the check at D+1; at departure, D0 cuts the account, its sessions and the badge in one gesture.
See [§4 bis](#4-bis-governed-actions).

## 3 bis. Give agents tools they cannot misuse

Agents get their platform tools over MCP on localhost — `report_finding`, `ask_human`,
`request_scope_change` — and, if the deployment declares one, the **tool catalogue**: third
party APIs and MCP servers the platform calls *for* them.

What a project has to say, in its own configuration:

```yaml
tools: [verifier_adresse, rechercher_entreprise]   # what it uses; [] means none
groups: [rh]                                      # what it is entitled to
```

Those two lines are not redundant. `tools` is **what this project uses** and its team can
edit it. `groups` is **what the deployment opens to it**, and it is matched against the
`groups` declared on each catalogue tool. A project that lists a tool reserved to a group it
does not belong to simply does not get it — the listing is not the control.

Three properties worth knowing before you wire an external provider:

| | |
| :-- | :-- |
| The agent never holds a provider credential | The platform makes the call; the agent's egress stays closed |
| Your run token never reaches the provider | It authenticates the agent *to Choregos*, nowhere else |
| The agent chooses neither URL nor method | It names a catalogue tool; the rest is written in the catalogue |

An unauthorised tool answers **404, not 403**: an agent has no business discovering the
deployment's catalogue by guessing names.

Spend is bounded per run (`budgets.tool_calls_per_run`) and every call lands in the cost
ledger under `kind: tool` — visible on the project overview and in the run's access record.

**Announcing a tool is not using it.** On the 2026-09-24 bench the sourcing playbook said
"verify the location with `verifier_adresse` before anything else", the agent recorded
`lieu_verifie: true`, and the ledger held ten catalogue listings and zero calls. When a
step *must* use a tool, say so with a gate, which reads what the platform counted:

```yaml
gates:
  - { name: tool_called, params: { tools: [verifier_adresse] } }
```

Agents also get `validate_result`, which checks their `.choregos/result.json` against the
contract *before* they finish — the same validator the runner applies afterwards.

## 3 ter. Register your agents

An agent is an object of the organisation ([ADR 0033](adr/0033-registre-d-agents-et-bibliotheque-de-skills.md)):
`internal` when the platform runs it, `external` when it is a client of the MCP door. What it *is*
— instructions, model, limits, budget, skills, MCP servers and the tools it may call — lives in
**versions that never change**: a change publishes the next one.

```bash
curl -X POST $API/orgs/acme/agents -d '{"slug": "onboarding-coordinator",
  "display_name": "Onboarding coordinator",
  "spec": {"instructions": "Prepare the access plan of a newcomer…",
           "budget": {"run_usd": 2, "daily_usd": 20},
           "mcp_servers": [{"connector": "entra", "tools": ["entra_read_*"]}]}}'
curl -X POST $API/orgs/acme/agents/onboarding-coordinator/versions -d '{...}'   # v2; v1 is kept as is
curl -X PUT  $API/projects/acme:hr/agents/onboarding-coordinator \
  -d '{"version": 2, "overrides": {"budget": {"run_usd": 1}}}'
```

An agent carries **skills** — a folder with a `SKILL.md` whose header gives its `name` and
`description` — from the organisation's library:

```bash
curl -X POST $API/orgs/acme/skills/import -H 'Content-Type: application/zip' --data-binary @onboarding.zip
```

A skill **declares no permission**: an `allowed-tools` field is refused, because what an agent may
call is decided by its version and its project, never by a file it reads. An archive that leaves
its folder (`../`), holds a symbolic link, more than 64 files or 512 KiB, or a file that is not
UTF-8 text is refused. Versions never change; a skill says which agents carry it.

A workflow actor **names** an agent — `{type: agent, role: implement, agent: onboarding-coordinator}`,
or `…@2` for a given version. The run then takes the project's pinned version (else the latest),
overrides applied: its model, its limits and its budget, which only tighten the actor's own. Its
instructions **replace the playbook**: they are a Jinja template rendered in a **sandbox**
(`{{ ''.__class__ }}` is refused, at publication already), with the playbook's variables
(`ticket`, `spec`, `inputs`…), and the platform appends a frame the author cannot remove — the output
contract and the invariants. The run records the agent and its version; a revoked, suspended or
expired agent does not start, and neither does one that has spent its `daily_usd` since midnight
(UTC) — models **and** tool calls. `GET /orgs/{org}/agents/{slug}/metrics?days=30` gives its runs,
their success rate, their cost by kind and by project, and what it spent today.

A project **pins** a version and may only **tighten** it — a lower budget or limit, fewer tools;
an override that widens is refused (`422`). Creating, publishing and revoking need
`agent:manage` (organisation administrators); a revocation is final. Agents are under the same
row-level security as everything else: an agent of another organisation does not exist for you.

In the console, **agents** (top bar) lists the registry and **your MCP clients** — your `mcp:*`
tokens, each with its last call and the client that made it. A Claude Code that has called the door
shows as *connected*; **register as an external agent** creates the agent (with its human's tools,
or read-only ones) and attaches the token, and its calls then carry the agent. An agent's page gives
its last 30 days (runs, success rate, cost, today against its daily budget), its versions, a new
version started from the latest, and — for an external agent — the clients acting as it. **skills**
is the library: import a zip, read each version's files, see which agents carry it. A project's
**agents** tab shows what it pins and its **implicit agents**: workflow actors that run as agents
without naming one, which you can register, then name in the workflow (`agent: <slug>`).

## 4. Write the policy

```yaml
apiVersion: choregos/v1
kind: Policy
metadata: { name: team, version: 1, preset: team }
budgets:
  ticket_usd: { default: 8 }
  max_turns:  { implement: 40, verify: 20 }
  max_minutes: { implement: 30 }
  tool_calls_per_run: 20        # catalogue tools; omit for no cap
  daily_project_usd: 50
approvals:
  merge: { required: by_size, sizes: [L, XL], group: maintainers }
scope:
  max_diff_files: 60
sandbox:
  unknown_requests: reject      # refuse what the runner cannot classify (default: allow + journal)
  prompt_injection: block       # warn (default) journals; block stops the stage before any run
```

Three presets ship (`solo`, `team`, `regulated`) and are a reasonable starting point.

## 4 bis. Governed actions

A write into a system that is not ours — create an account, order a laptop, deactivate a badge —
is a **governed action** ([ADR 0035](adr/0035-actions-gouvernees-dans-le-coeur.md)): proposed,
decided by people, then run by the platform in Temporal, **never inside the request that
approves it**.

```bash
curl -X POST $API/projects/acme:hr/actions -d '{"kind": "entra.create_user",
  "title": "Account for Léa", "params": {"upn": "lea@acme.example"},
  "effects": [
    {"effect": "connector.call",
     "with": {"connector": "entra-acme", "operation": "create_user",
              "arguments": {"upn": "{{ params.upn }}", "display_name": "Léa Martin"}},
     "compensate": {"effect": "connector.call",
                    "with": {"connector": "entra-acme", "operation": "disable_user",
                             "arguments": {"upn": "{{ params.upn }}"}}}},
    {"effect": "connector.call",
     "with": {"connector": "entra-acme", "operation": "add_to_group",
              "arguments": {"upn": "{{ params.upn }}", "group_id": "devs"}}}],
  "approval": {"approvers": [{"role": "project_owner", "min": 1}], "step_up_minutes": 10}}'
curl -X POST $API/projects/acme:hr/actions/<id>/decision -d '{"decision": "approve"}'
```

- **Deciding** takes a human session (an API token or an MCP client proposes, never decides), at
  least the rank an approver rule asks for, and — to approve — an authentication younger than
  `step_up_minutes` (else `401 step_up_required`, and the console re-authenticates). Whoever
  proposed, or owns the agent that proposed, does not decide. A rejection gives its reason. With
  `min: 2`, two different people approve. Of two concurrent approvals, one starts the action; the
  other gets `409`.
- **Running**: the approval starts `ActionWorkflow` (`action-<id>`) and answers at once. Each effect
  is recorded under its key (`<action>:<n>`) **before** it is attempted and confirmed after: a
  retried effect finds its key done and does nothing twice; one left started by a dead worker is
  attempted again — effects are idempotent by construction. A transient failure (`429`, `503`,
  network) is retried with backoff; a refusal (an account outside the administrative unit, a
  forbidden operation) stops the action, and what was done is **compensated in reverse** — the
  action says what it could not undo. Parameters are Jinja (sandboxed) over `params` and the results
  of earlier `effects`; a compensation also sees its own effect's `result`.
- **Proving**: an effect can make the action wait for the proof that it served — the next report
  of a collector, say — until a deadline: the action shows `awaiting_evidence`; a contrary proof, or
  none in time, fails it and compensates what was done.
- **Reading**: `GET /projects/{id}/actions/{id}` gives the decisions (who, when, how fresh their
  authentication was) and the **journal** — every effect's key, attempts, answer or error. Events
  `choregos.action.*` tell the same story on the ticket. In the console, every action has its page,
  `/p/<project>/actions/<id>`, where it is decided — an ontology proposal too: it is a core action
  under the same id, and its page says what it acts on (action type, targets, parameters), what its
  effects returned and what its evidence collected. The former `/p/<project>/proposals[/<id>]` links
  redirect there.

The core ships two effects. `connector.call` is an operation of a connector of the organisation,
its key resolved by the platform, its arguments checked against the operation's schema; what an MCP
tool returns **structured** (`structuredContent`) is its result, so a later effect cites
`effects[0].serial` as it would for a typed connector. An `approval` operation runs here — the
action was approved; one this **project** cannot use — the organisation forbids it, the project
tightened it to `forbidden`, or its groups are not the project's — never does, and an action
calling it is refused when it is proposed (`422`). `verifier` is a check that touches nothing
outside: a condition, rendered with the earlier effects' results, that must be true — otherwise the
action fails, named by its `motif` (`condition: "{{ effects[1].active }}"`); it is `allowed`.

**An action at a date, from a workflow.** A system transition can propose the action itself. Its
title, justification and parameters are rendered (sandboxed Jinja) with the item's `fields` and the
`work_item`; `not_before` waits for a date read from a field the workflow declares as a date:

```yaml
metadata:
  inputs:
    type: object
    properties:
      upn: { type: string }
      start_date: { type: string, format: date }      # a date: midnight UTC; a date-time keeps its zone
transitions:
  - id: t-accounts
    from: planned
    to: accounts_ready
    by: platform                                      # an actor of type `system`
    action:
      kind: onboarding.accounts
      title: "Accounts for {{ fields.upn }}"
      params: { upn: "{{ fields.upn }}" }
      effects:
        - effect: connector.call
          with: { connector: entra-acme, operation: create_user,
                  arguments: { upn: "{{ params.upn }}", display_name: "{{ params.upn }}" } }
      not_before: fields.start_date - 10d             # d, h or m; + or -
    on_fail: { to: planned, max_attempts: 2, escalate_to: needs_review }
    on_reject: needs_review
```

- **The date moves, the timer follows.** `PATCH /work-items/{id}` with `{"fields": {...}}` replaces
  those fields (`null` removes one), validated by the item's own workflow, and tells its
  interpreter: a later date re-arms the timer, a date already past starts the action at once. A
  field not filled yet means waiting for it — no date, no action. A paused item does not start its
  action; it starts it when resumed.
- **Who approves.** When every operation the action calls — its compensations included — is
  `allowed` for the project and the workflow asks for no `approval`, the policy decides: the action
  is born approved (`by: policy`) and runs. Otherwise a person decides it in **Approvals**, like any
  other. A workflow can add an approval, never remove one. A plugin effect declares what it needs
  (`declarer_un_effet(name, fn, politique="allowed")`; `approval` by default).
- **What follows.** The transition passes when its action **succeeded** (`action_succeeded`, an
  implicit guarantee). Rejected, it follows `on_reject` when there is one; failed — compensated —,
  rejected without `on_reject`, or impossible to propose (a missing field, a forbidden operation),
  it follows `on_fail`, which such a transition must declare: without it the item would propose the
  same action forever. An unknown effect is refused when the workflow is **published**.
- **How the item learns.** The action runs in its own `ActionWorkflow`; when it settles, it signals
  the interpreter that proposed it (`action_settled`), and a rejection does too. The item also
  re-reads the action every six hours, in case a signal was lost.

**A task, with its proof.** Some steps are gestures a person makes — handing over a badge,
unpacking a laptop. A human transition can be a **task**: what to do, a form whose properties are
fields of the item, and a sentence the person attests.

```yaml
  - id: t-badge
    from: badge_due
    to: badge_handed
    by: front_desk                                    # an actor of type `human`
    task:
      title: Hand the badge over
      instructions: In person, then tap it on the reader.
      form:
        type: object
        required: [badge_uid]
        properties: { badge_uid: { type: string, minLength: 8 } }
      attest: I handed the badge to its holder in person
```

The item page shows the form; **done** stays closed until every required field is filled and the
sentence is attested. `POST /work-items/{id}/decisions` with `{"kind": "complete", "values": {...},
"attested": true}` accepts only what the form describes, refuses a missing or malformed field (`422`,
with its path) and a missing attestation, and writes the values into the item's **fields** — checked
again by `metadata.inputs`, which keeps the last word. The next action reads them:
`params: { uid: "{{ fields.badge_uid }}" }`. The decision keeps the values and the attested sentence
as it was shown, and the timeline tells it: that is the proof. A task that cannot be done is sent
back with its reason (`reject`). The validator wants a `human` actor, an object form, and every
form property declared in `metadata.inputs`.

**A tool under approval becomes an action.** A run sees an `approval` operation like any tool,
its description saying it needs a human approval. Calling it reaches **nothing**: it proposes a
governed action in the agent's name (`agent:<slug>`, origin `tool`), returns its id and the
console path where it is decided, and counts against the run's `tool_calls_per_run` — an agent
in a loop does not fill the box. The agent's **owner** does not approve its agent's writes.

**Approvals** (top bar) lists what waits across the organisation's projects
(`GET /orgs/{org}/actions?status=pending_approval`); a project's **actions** tab lists its actions,
and each one shows its parameters, effects and their compensations, its decisions — who, when, how
long after signing in — and the journal, key by key.

## 5. Let a ticket run

Label a ticket `agent-ready` in your tracker (or create it in Choregos with
`tracker: internal`). The platform starts one workflow per ticket, with a deterministic id —
so a webhook delivered twice starts nothing twice. If a webhook is lost, a reconciliation
pass every 60 seconds catches up.

```bash
choregos items list billing-api
choregos runs tail <run-id>        # live ACP log over SSE
```

## 6. Read what came out

In the UI, a run shows its cost, its duration, its **evidence**, its scope, its full ACP
journal, the diff and the artefacts. Evidence is what gates read: tests actually executed,
lint, typing, coverage — or, for work that has none of those, the facts the business named.

When an agent is blocked it does not guess: it asks, the ticket moves to a waiting state, a
human is notified with an SLA, and the answer resumes the workflow where it stopped.

## 7. Day-2 knobs

| Symptom | Knob |
| :-- | :-- |
| Bursts of agent pods | `choregos-orchestrator.runner.maxActive` |
| An agent pod refused with no message | `runner.limits` above the namespace `LimitRange` |
| Spend drifting | `budgets.daily_project_usd`, `ticket_usd`, alert at `alert_at_ratio` |
| Third-party API spend | `budgets.tool_calls_per_run`, cost report grouped by `kind` |
| An agent wandering outside its scope | `scope.max_diff_files`, gate `scope_respected` |
| Too many findings from one run | `findings.max_per_run` |

Costs export as CSV from the project overview, grouped by day, stage, model, backend, size
or kind.

## Known limits, stated rather than discovered

- The engine is still shaped by software in two places (ADR 0012): `StageOutputs` is typed
  for development — business outputs travel by name in `outputs`, are stored on the ticket
  and handed to the next stage as `inputs`, but are not typed —, and most gates read a
  diff. `outputs_present` and `evidence_facts` are the business-side gates.
- The tool catalogue is listed to agents and callable, but nothing *requires* a call: a
  playbook can announce a tool the agent ignores. A `tool_called` gate reading the ledger is
  the mechanism to write (P1-6).
- Human-in-the-loop is a state with a deadline and an escalation, and a decision bar in the
  front; there is no checkpoint/resume of the agent's own context across the wait.
- The single-node bench measures **no cost** unless the embedded gateway gets a provider
  key: in *direct* mode the agent brings its own credentials and nothing is metered.
- `docs/plan/BLOCKERS.md` (French) keeps the rest, with causes and workarounds.
