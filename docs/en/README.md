# Choregos — English documentation

Choregos is a delivery platform for agent work. A request comes in as a ticket, agents move
it through a workflow you declare, and every step is bounded, evidenced and auditable.

> The reference documentation of this repository is in French (`docs/`, `docs/plan/`,
> `docs/adr/`). These pages are the English entry points for **deploying** and **using** the
> platform. Where the two disagree, the French pages and the code are authoritative.

| Page | What it covers |
| :-- | :-- |
| [Concepts](concepts.md) | The model: projects, workflows, stages, gates, runs, evidence |
| [Deployment](deployment.md) | Helm chart, dependencies, secrets, sizing, a single-node bench |
| [Usage](usage.md) | Onboarding a project, writing a workflow, reading a run, day-2 knobs |

## What it is, in one paragraph

A **workflow** is a state machine you write in YAML: states, transitions, and who performs
each transition — an `agent`, a `human`, or the `system`. When a ticket enters, the
orchestrator (Temporal) runs one **stage** per transition. A stage is an agent in a
disposable sandbox, given only what it needs: a workspace, a playbook, a short-lived token
scoped to that single run, and a model budget it cannot exceed. What the agent produces is
checked by **gates** — mechanical guarantees, not prompts — before the ticket moves on.

## What it is not

- It is not a chat interface. Agents run unattended; humans intervene at the points the
  workflow declares, with a deadline and an escalation path.
- It is not tied to software delivery. The engine carries any work that arrives as requests
  and is instructed in steps — see [ADR 0012](../adr/0012-le-moteur-n-est-pas-lie-au-logiciel.md)
  for what was made generic and, just as important, what is still shaped by code.
- It does not hide what it cannot prove. A gate that cannot read what it is supposed to
  check **refuses** instead of passing.

## Licence

Apache 2.0.
