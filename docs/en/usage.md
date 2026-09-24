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
its connectors yourself, from the UI (*Settings → Connectors*) or the API.

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

### Workflows outside software

The same engine carries work that has no repository. Name your own roles, bring your own
playbooks, and use the gates that do not speak of code:

```yaml
actors:
  sourcer: { type: agent, role: custom, playbook: sourcing, model: "profile:standard" }
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

- With a **fake SCM**, the gates that read a diff (`scope_respected`, `diff_size_max`,
  `no_secrets`) refuse rather than pass. That is deliberate.
- A ticket cannot be replayed under the same id — Temporal refuses to restart a completed
  workflow id. Replays use a fresh ticket key.
- `ProjectConfig.repo` is still mandatory, so a project with no repository declares one it
  does not use. See [ADR 0012](../adr/0012-le-moteur-n-est-pas-lie-au-logiciel.md) for the
  full list of what is still shaped by software.
