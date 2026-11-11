---
title: Workflows
description: Choose, read, change and publish the process a project follows, and decide which workflow each request takes.
sidebar_position: 2
---

# Workflows

A workflow is the process a project follows: the states a work item goes through, and who moves it
from one to the next. This page shows how to read one, change it safely, and route each request to the
right one.

**What you will be able to do**

- Read a workflow in the console as a graph, as steps in plain words, or as YAML.
- Adopt a workflow from the library, or start a new one.
- Change a workflow and publish a new version without disturbing work in progress.
- Write a workflow in YAML and validate it before you publish it.
- Route each incoming request to the right workflow.

## Before you start

- To read workflows: any role on the project.
- To publish or change one, and to change the routing: **Project owner**.

## Read a workflow

Open the project, then **Workflows** in the sidebar. Each workflow shows its **Active version**, its
**Open items** and how many requests were **Routed here**; one of them is marked **Default**.

Open a workflow. It has four tabs:

| Tab | What it shows |
| :-- | :-- |
| **Graph** | The map: one lane per kind of actor, the states, the transitions between them. Use the keyboard: Tab reaches the states, the arrow keys follow the transitions, Enter opens a state |
| **Steps** | Each transition in plain words: who acts, what it produces, the gates, what happens on failure |
| **YAML** | The definition itself, validated as you type |
| **History** | Every published version, who published it, and a comparison between two versions |

The same plain-words reading, from a file on your machine:

```bash
choregos workflow show --process .choregos/workflow.yaml
```

## Who acts at each step

Each transition names its actor with `by`:

- an **agent** does the work in a sandbox, under a budget, and the transition's gates check the
  result;
- a **person** of a group decides: the work item waits in a state of kind `wait` (see
  [Decisions](decisions.md));
- the **system**, the platform itself, does what the transition says it `does`: `open_pr` opens the
  pull request, `merge_pr` merges it.

When an agent step fails its gates, `on_fail` sends it back a few times (`max_attempts`), then to the
state named by `escalate_to`, where a person takes over.

## Adopt a workflow from the library

1. On the **Workflows** page, press **Browse the library**.
2. Filter by **Needs no repository** or **Delivers code from a repository**, and open an entry to read it.
3. Press **Adopt** next to the entry, give the workflow its name in the project, and press **Adopt as**. The workflow is copied into the project,
   and the agents it names are installed in the organisation if they are missing.

A copy is a copy: later changes to the library do not reach the project on their own.

## Start a new workflow

1. On the **Workflows** page, press **New workflow**.
2. Type a **Name** (lowercase letters, digits and dashes, for example `offboarding`).
3. Under **Start from**, pick a starting point.
4. Tick **Make it the project's default workflow** if requests that match no rule should go there.
5. Press **Publish**.

## Change a workflow

1. Open the workflow. Change it on the **Graph** (**Add a step**, or open a state to edit it), on the
   **Steps** tab, or in **YAML**. The three tabs are views of one draft.
2. Read the **Validation** card: it shows each error with its line. Nothing can be published while an
   error remains.
3. Press **Publish v2** (the number is the next version). **Discard** throws the draft away.

A work item in progress **finishes on the version it started with**. Only new work items take the new
version. If someone published while you edited, the console says so and offers to publish over their
version; theirs stays in the **History**.

## Write a workflow in YAML

Here is a small development workflow: an agent fixes, another tests, the platform opens the pull
request, a maintainer approves it, the platform merges it when the checks are green.

```yaml
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: fix-with-review
  version: 1
  description: "An agent fixes, another tests, a maintainer approves the pull request, the platform merges."
actors:
  developer: { type: agent, role: implement, agent: developer, model: "profile:standard" }
  tester: { type: agent, role: verify, agent: tester, model: "profile:standard" }
  platform: { type: system }
  maintainers: { type: human, group: maintainers, sla_hours: 24 }
states:
  todo: { display: To do, kind: wait }
  implemented: { display: Implemented }
  tested: { display: Tested }
  in_review: { display: Pull request to approve, kind: wait }
  approved: { display: Approved }
  merged: { display: Merged, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
  abandoned: { display: Abandoned, terminal: true, outcome: cancelled }
transitions:
  - id: t-implement
    from: todo
    to: implemented
    by: developer
    inputs: [human_feedback]
    gates: [scope_respected, no_secrets]
    on_fail: { to: todo, max_attempts: 3, escalate_to: needs_human }
  - id: t-test
    from: implemented
    to: tested
    by: tester
    outputs: [test_report]
    gates: [evidence_present]
    on_fail: { to: todo, max_attempts: 2, escalate_to: needs_human }
  - id: t-open-pr
    from: tested
    to: in_review
    by: platform
    does: open_pr
  - id: t-approve
    from: in_review
    to: approved
    by: maintainers
    on_reject: todo
    timeout_hours: 72
  - id: t-merge
    from: approved
    to: merged
    by: platform
    does: merge_pr
    gates: [ci_green]
    on_fail: { to: approved, max_attempts: 2, escalate_to: needs_human }
  - id: t-abandon
    from: needs_human
    to: abandoned
    by: maintainers
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
    on_timeout: needs_human
  needs_human:
    on_answer: resume
    on_abandon: abandoned
```

What to notice:

- **States** with `kind: wait` wait for a person. `terminal: true` ends the work item;
  `outcome: cancelled` shows it in grey rather than green.
- **`agent: developer`** names an agent of the organisation's registry. See [Agents](../build/agents.md).
- **Gates** are checks the platform runs, not instructions: `scope_respected` compares the diff with
  the allowed paths, `evidence_present` requires tests the platform saw run, `ci_green` reads the pull
  request's checks.
- **`on_reject: todo`** sends the work back when the maintainer presses **Send back**, with their reason
  as `human_feedback` for the developer.
- **`defaults`** say where a work item goes when an agent asks a question, runs out of budget or time,
  and what an answer does.

Validate it before you publish. Validation is local and needs no instance:

```bash
choregos workflow validate fix-with-review.yaml
```

Then publish it as the next version of that workflow in the project, and list the result:

```bash
choregos workflow push billing-api fix-with-review.yaml
choregos workflow list billing-api
```

The validator refuses more than syntax: a state no transition reaches, a retry that could loop forever,
an unknown gate, a gate with nothing to check, a condition on an output nobody produces.

### Choose the next step from what the agent answered

A transition can depend on the outputs of the step before it. The first transition whose `when` holds
is taken; the one without `when` is the default:

```yaml
transitions:
  - { id: t-develop, from: todo, to: checkpoint, by: developer, outputs: [done, confidence] }
  - { id: t-again, from: checkpoint, to: todo, by: platform, when: { done: false } }
  - { id: t-unsure, from: checkpoint, to: unsure, by: platform, when: { confidence: { lt: 0.6 } } }
  - { id: t-finish, from: checkpoint, to: done, by: platform }
```

## Route requests to the right workflow

A project can have several workflows. On the **Workflows** page, the **Routing** card decides which one
a new request takes:

1. Pick a **Strategy**: **Labels and types** (rules only), **Rules, then a small model**, or **A small
   model**.
2. Add rules: **Labelled (any of)**, **Labelled (all of)** or **Of type**, and the workflow it **Goes to**.
   The first matching rule wins.
3. Choose the **Default workflow**, for a request that matches no rule.
4. Check the result in **Try the routing** with a title, a description and labels, then press **Save the
   routing**.

A work item that landed in the wrong workflow can be moved: open it and press **Change workflow**.

## What can go wrong

| Message | What it means |
| :-- | :-- |
| "A workflow named … already exists" | Pick another name, or open that workflow to change it |
| `when.no_default` | Every exit of a state has a `when`: add one without, as the default |
| `gate.nothing_to_check` | A gate has nothing to read, such as `outputs_present` on a transition with no `outputs` |
| `workflow.implicit_effect` | The effect is carried by a state's name; write `does: open_pr` or `production: true` instead |
| The board shows a work item in an old state | It runs the version it started with; it will finish there |

## Go further

- [Work items](work-items.md): what happens to a request inside a workflow.
- [Write the workflow](../../usage.md#3-write-the-workflow), in depth: fan-outs, schedules, release trains.
- [Gates](../../concepts.md#gate): every gate and its parameters.
