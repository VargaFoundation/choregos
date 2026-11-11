---
title: Budgets and policies
description: See what work costs, cap it per work item, per stage, per day and per month, and bound what agents may write, run and reach.
sidebar_position: 7
---

# Budgets and policies

Every model call an agent makes is paid from a budget it cannot exceed, and every run is bounded by its
project's policy: the paths it may not write, the commands it may not run, the hosts it may not reach.
This page shows how to read what work costs, how to set the limits, and what happens when one is
reached.

**What you will be able to do**

- Read what a project, a workflow, an agent or a work item cost.
- Set budgets per work item size, per stage, per day, and for one work item.
- Bound what agents may write, run and reach.
- Know what happens when a budget runs out, and grant more when it is worth it.

## Before you start

- To read costs: any role on the project.
- To change a project's policy: **Project owner**.
- To set the organisation's ceiling: **Organisation admin**.

## See what work costs

- **Spend**, in the organisation's sidebar: **Spend over time**, then the **Top agents**, **Top
  workflows** and **Top work items**, the **Projects**, the spend **By model** and **By tool**, and
  **Knowledge distillations**.
- On a work item: its cost next to its title, and **Cost per stage**.
- On a run: its **Cost**, measured by the platform from the model gateway.
- The **Inbox** lists the **Budgets crossed**: those that reached their alert threshold today.

Model calls and catalogue tool calls land in the same ledger, with their project, work item, stage and
model.

## How a budget works

When a stage starts, Choregos gives its run a model key capped at what is **left**. What is left is the
smallest of:

- the stage's budget (by role and work item size);
- the work item's budget;
- the agent's own run and day budgets;
- what the project may still spend today;
- what the organisation may still spend this month.

Each is counted from what the cost ledger says was already spent, minus the keys still open. Two stages
starting at once cannot both take the same remainder. Below one cent left, the stage does not start.

When the gateway refuses the key because it is spent, the run ends with the reason `budget`. The work
item goes to a person (the workflow's `on_budget_exceeded` state, usually **Needs a human**). A stage
stopped on budget is **never retried on its own**: a retry would mint a fresh key.

## Change a project's policy

1. Open the project's **Settings › Guardrails**.
2. Open **Policy**. Its summary line says the current budget and release trains.
3. Edit the YAML, then press **Save the policy**. The server validates it; an error names the field.

Here is a policy for `billing-api`:

```yaml
apiVersion: choregos/v1
kind: Policy
metadata:
  name: billing-api
  version: 2
  description: "Billing API: modest budgets, no writes to CI or deployment files, a closed network."
budgets:
  ticket_usd: { S: 10, M: 30, L: 80, XL: 160 }
  stage_usd:
    default: { S: 3, M: 8, L: 18, XL: 30 }
    implement: { S: 5, M: 12, L: 25, XL: 45 }
  max_turns: { default: 80, implement: 120 }
  max_minutes: { default: 60, implement: 90 }
  tool_calls_per_run: 30
  daily_project_usd: 150
  alert_at_ratio: 0.8
attempts:
  by_role: { implement: 3 }
  max_stages_per_ticket: 40
scope:
  auto_grant_max_files: 3
  deny_paths: [".choregos/**", ".github/workflows/**", "deploy/**"]
  max_diff_files: 60
  max_diff_lines: 3000
sandbox:
  deny_commands: [kubectl, "terraform apply", "git push --force", sudo]
  network:
    allow_domains: [github.com, pypi.org, files.pythonhosted.org]
    deny_by_default: true
```

What each part does:

| Part | What it bounds |
| :-- | :-- |
| `budgets.ticket_usd` | What one work item may spend, by size (S, M, L, XL) |
| `budgets.stage_usd` | What one stage may spend, by role and size; `default` for the roles not named |
| `max_turns`, `max_minutes` | How long an agent may work, by role |
| `tool_calls_per_run` | How many catalogue tool calls one run may make |
| `daily_project_usd` | What the project may spend in a day, across all its work items |
| `alert_at_ratio` | When a budget shows in the inbox as crossed: here at 80 % |
| `attempts` | How many times a role may retry, and how many agent stages one work item may run in all |
| `scope.deny_paths` | Paths no run may write, whatever the work item allows |
| `scope.auto_grant_max_files` | How many extra files an agent may add to its scope without asking a person |
| `sandbox.deny_commands` | Commands a run may not execute |
| `sandbox.network` | The hosts a run may reach; everything else is closed |

Three presets are a good starting point: `solo`, `team` and `regulated`. A template ships its own.

### What a policy does not do

A policy does not name who approves. The keys `approvals` and `review.require_human_for_risk` are
accepted but nobody applies them. To make a person approve or review, put a human step in the
**workflow** (see [Workflows](workflows.md)), or an approval on the release train's environment.

## Give one work item its own budget

When you file a work item from the board, set **Budget in USD**. A workflow can also carry a budget of
its own. Either one can only **tighten** the others: a budget wider than the workflow's or the policy's
is refused.

## Grant more money

More money is a decision, and it is recorded. When a work item has spent its budget, **Replay the
stage** is refused unless you grant a top-up. It adds to the work item's budget, and the audit log
records who granted it:

```bash
choregos items action <item-id> rerun_stage --top-up 5
```

## The organisation's ceiling

An organisation admin sets a **ceiling** that every project policy can only tighten: the budgets per
size and per stage, max turns and minutes, tool calls per run, the daily spend of a project, and the
**monthly spend of the organisation**, the one cap that exists only here.

1. Open **Admin › Overview** and find **Policy ceiling**.
2. Fill in the fields you want to cap. An empty field is no cap; the grey figure is what applies when
   nobody sets one.
3. Save.

A project policy above the ceiling is refused when it is saved, and the error names each field. A
policy saved before the ceiling existed is not refused: what runs is brought down to the ceiling, and
**Settings › Guardrails** lists each field that was brought down.

The ceiling's network section bounds the hosts every project and agent may reach, with a deny list that
always wins. **Admin › Network** shows it with the hosts Choregos itself calls.

## What can go wrong

| Message or symptom | What it means |
| :-- | :-- |
| The work item is in **Needs a human** with the reason `budget` | A run spent its key. Read what it did; replay with a top-up, or stop the work item |
| "exceeds the organisation's ceiling" when saving | Lower the field the error names, or ask an organisation admin to raise the ceiling |
| The stage does not start, and no run appears | Less than a cent was left in one of the budgets: the project's day, the organisation's month or the work item |
| A run is refused a host | The host is not in the policy's `sandbox.network`, or the organisation's ceiling denies it |

## Go further

- [Where the money goes](../../concepts.md#where-the-money-goes), in depth.
- [Write the policy](../../usage.md#4-write-the-policy): every key of the policy and the ceiling.
- [Security](../../security.md): what bounds a run, and what is not a wall.
