# Choregos — English documentation

Choregos is a delivery platform for agent work. A request comes in as a ticket, agents move
it through a workflow you declare, and every step is bounded, evidenced and auditable.

> Since 2026-09-24 this documentation is the **reference**, in English. `docs/fr/` archives
> the earlier French guides; `docs/plan/` (status, blockers, execution plan) stays in French,
> like the code and its comments. Where a page and the code disagree, the code wins — and
> the page is a bug.

| Page | What it covers |
| :-- | :-- |
| [Getting started](getting-started.md) | From `make demo` to a real project: prerequisites, templates, the first ticket |
| [Concepts](concepts.md) | The model: projects, workflows, stages, gates, runs, evidence, tools |
| [Deployment](deployment.md) | Helm chart, dependencies, secrets, identity, the first organisation, a single-node bench |
| [Usage](usage.md) | Onboarding a project, writing a workflow, tools, policy, reading a run, day-2 knobs |
| [Development](development.md) | The three levels of a dev environment, the tests that touch the real world, pitfalls |
| [Security](security.md) | What stops an agent from doing damage, and what is *not* a wall |
| [CLI reference](cli.md) | Every command, generated from the CLI itself |
| [Contributing](CONTRIBUTING.md) | One story, one PR; the contract before the code; what CI checks |
| [ADRs](adr/README.md) | The structural decisions |
| [Positioning](positioning.md) | Where Choregos sits against runtimes, agent platforms and tool brokers — and what they do better |
| [Runbooks](runbooks/README.md) | Operations: how to know it is this, what to do, how to check |
| [State of the project](plan/STATE-OF-THE-PROJECT-2026-09-24.md) | The critical assessment of 2026-09-24 and the P0 → P3 plan |

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
  and is instructed in steps — see [ADR 0012](adr/0012-le-moteur-n-est-pas-lie-au-logiciel.md)
  for what was made generic and, just as important, what is still shaped by code.
- It does not hide what it cannot prove. A gate that cannot read what it is supposed to
  check **refuses** instead of passing.

## Licence

Apache 2.0.
