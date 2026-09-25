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
its connectors yourself, from the UI (*Settings → connecteurs*: type and configuration, secrets referenced never typed) or the API (`PUT /projects/{id}/connectors/{kind}`).

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
| `memory` | `ecphoria`, `pgvector`, `fake` |
| `notify` | `slack`, `fake` |

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

The web editor (`/p/<slug>/workflow`) validates as you type and draws the workflow as a map,
one lane per kind of actor. The map is keyboard-navigable: Tab reaches the states in reading
order, ← and → follow transitions, ↑ and ↓ move between states, Home and End jump to the
ends. The state under the cursor is described below the map, with its outgoing transitions,
actors and gates — that sentence is what a screen reader announces.

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
```

Three presets ship (`solo`, `team`, `regulated`) and are a reasonable starting point.

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
