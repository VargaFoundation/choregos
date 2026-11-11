---
title: Work items
description: File work, follow what agents do with it, read what came out, and take over when it is stuck.
sidebar_position: 3
---

# Work items

A work item is one request moving through a workflow: an issue, a ticket, a request filed in Choregos.
This page shows how to start one, follow it, read what each step produced, and step in.

**What you will be able to do**

- Start a work item from your tracker, from the board or from the command line.
- Follow it on the board and on its own page, live or as a replay.
- Read a run: its cost, its evidence, what the agent did and what was refused.
- Pause, resume, stop, replay a stage or move a work item to another workflow.

## Before you start

- To read work items: any role on the project.
- To file, pause, stop or replay: **Developer** or above.

## Start a work item

### From your tracker

On GitHub, GitLab or Jira, open the issue and add the **`agent-ready`** label. That label is the only
trigger; other labels (`feature`, `bug`) choose the workflow through the project's routing. If a
webhook is lost, a catch-up pass finds the labelled issues nobody saw, within about a minute.

### From the board

1. Open the project, then **Board**, and press **New work item**.
2. Pick the **Workflow** (the project's routing chooses it when you leave the default), then fill in
   the **Title**, the **Description**, the **Size**, and the **Fields of the work item** the workflow
   asks for.
3. Optionally, set a **Budget in USD**. It can only tighten the workflow's own budget.
4. Press **Create and start**.

With GitHub, Jira or GitLab as the tracker, Choregos creates the issue there first, then follows it.
With Choregos as the tracker, it gives the work item its key itself.

### From the command line

On a project whose tracker is Choregos itself:

```bash
choregos items create onboarding --title "Arrival of a data engineer on 3 March" --size M
choregos items create onboarding --title "Quarterly access review" --workflow access-review --field team=data
```

Work can also start on a schedule, from a webhook or from another system: see
[Triggers](../integrate/triggers.md) and [A2A](../integrate/a2a.md).

## Follow a work item

### On the board

The **Board**'s columns are the workflow's states. A card shows the work item's key and title; open it for the rest. Columns with no work item are hidden; press **Show empty columns** to see
them.

The board on your tracker (GitHub Projects, Jira) follows Choregos, not the reverse: moving a card
there does not move the work item. Move it from the console.

### On its page

Open a work item. From top to bottom:

| Card | What it shows |
| :-- | :-- |
| **Decision awaited** | What a person must decide now, and the buttons to do it (see [Decisions](decisions.md)) |
| **About this ticket** | Its size, its risk, and the facts its workflow chose to show |
| **Journey** | The workflow as a path. Each step says what it is doing: in progress, waiting for a person, sent back, done, failed |
| **Cost per stage** | What each stage cost, from the cost ledger |
| **Timeline** | Every event, in order |
| **Artifacts** | What the steps produced: a specification, a plan, a test report, a deck |
| **Hand-offs** | What one step passed to the next |

On the **Journey**, click a step to open its panel: one tab per attempt, the gates, what the agent read
and wrote, and its log, live while it runs. **Replay this ticket** plays the work item's life back,
event by event.

From the command line:

```bash
choregos items list billing-api --state needs_human
choregos items show <item-id>
choregos runs list <item-id>
```

## Read a run

Each agent step is a run. Open it from the journey, or from the work item's timeline:

| Card | What it tells you |
| :-- | :-- |
| **Cost** and **Duration** | What the run spent and how long it took, measured by the platform |
| **Scope** | The paths the agent was allowed to change |
| **Evidence** | Tests run and passed, lint, types, coverage, diff size, and the facts the workflow names |
| **Agent plan** and **Activity** | What the agent said it would do and did |
| **Gates** | Each check, and what it decided |
| **Access** | What the agent read, wrote, ran and reached, with what was refused first |
| **ACP journal** and **Pod logs** | Every message between the runner and the agent; the last lines of the sandbox's log and how it ended |
| **Diff** | What the agent changed |

Cards are marked **observed** (the platform measured it) or **declared** (the agent said it). A gate
reads only what was observed.

From the command line:

```bash
choregos runs tail <run-id>
choregos runs diff <run-id>
```

## Take over

| You want to | Do this |
| :-- | :-- |
| Hold a work item while you look | **Pause** on its page; **Resume** later |
| Give up on it | **Stop**. Its run is cancelled and it leaves the workflow; it does not come back, it has to be filed again |
| Run the step that escalated again | **Replay the stage**. It is a new run, billed, with one more attempt |
| Send it to another workflow | **Change workflow**. It restarts at that workflow's first state |
| Answer the question that stopped it | See [Decisions](decisions.md) |

The same from the command line:

```bash
choregos items action <item-id> pause
choregos items action <item-id> resume
choregos items action <item-id> rerun_stage
choregos items action <item-id> stop
```

**More money is a decision.** Replaying a stage whose work item has spent its budget is refused unless
you grant more. The top-up adds to the work item's budget and is recorded in the audit log with your
name:

```bash
choregos items action <item-id> rerun_stage --top-up 5
```

## When work comes back

A work item that ended starts again when its issue comes back: the issue is reopened, its Jira status
goes back to work, or the `agent-ready` label is put on it again. It is a **new pass of the same work
item**, never a second one: it goes back to the workflow's first state, and the earlier passes stay on
its page.

## Problems outside the scope

An agent that spots a problem outside what it was asked to change does not fix it. It files a
**finding**. Findings are listed under **Findings** in the project's sidebar: triage them there: a
finding turned into a ticket becomes a new work item, the others are marked duplicate or dismissed.

## What can go wrong

| Symptom | Likely cause | What to do |
| :-- | :-- | :-- |
| The work item is in **Needs a human** | A step failed its gates too many times, an agent asked a question, the budget ran out, or a step timed out | Open the step's run to see which; answer, replay the stage, or stop it |
| A `/choregos approve` comment on the issue did nothing | Decisions are taken in the console, not in tracker comments | Follow the link Choregos replies with |
| "Replay the stage" is refused | The work item's budget is spent | Replay with a top-up, from the command line |
| The run failed before the agent said anything | The sandbox did not start: an image, memory, the network | Read **Pod logs**: they say how the sandbox ended |

## Go further

- [Decisions](decisions.md): approve, send back, answer, complete a task.
- [Budgets and policies](budgets-and-policies.md): what bounds a run.
- [Let a ticket run](../../usage.md#5-let-a-ticket-run) and [Read what came out](../../usage.md#6-read-what-came-out), in depth.
