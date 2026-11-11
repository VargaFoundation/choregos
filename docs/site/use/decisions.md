---
title: Decisions
description: Find what waits for you, approve or send back, answer an agent's question, complete a task, and approve governed actions and releases.
sidebar_position: 4
---

# Decisions

A workflow names the points where a person decides. Choregos never guesses past them: the work item
waits, with a deadline, until someone with the right role and group decides. This page shows where to
find what waits for you and how to answer each kind of request.

**What you will be able to do**

- Find everything that waits for a person, across your projects.
- Approve or send back, answer a question, complete a task.
- Approve a governed action and a production release.
- Know who may decide what, and what happens when nobody does.

## Before you start

- To decide a step of a work item: **Developer** or above on the project, and membership of the group
  the step is addressed to (for example `maintainers`). Groups come from your identity provider, as of
  your last sign-in.
- To approve a production release: **Release captain**.
- To approve a governed action: the role and group its approval rule names, and a recent sign-in.

## Find what waits for you

Open **Inbox** in the sidebar. Its count is the number of things waiting for a person. It has four
parts:

| Card | What waits |
| :-- | :-- |
| **Tickets** | Work items stopped on a person: an approval, a question, a change of scope, a task. Overdue first, then the nearest deadline |
| **Actions to approve** | Governed actions: writes into other systems, waiting for their approvers |
| **Network access** | Requests to open a host for agents, waiting for an organisation admin |
| **Budgets crossed** | Budgets that reached their alert threshold today |

Inside a project, its own **Inbox** shows the same for that project only. You can decide a work item
from the inbox, or open it and use its **Decision awaited** card.

## Approve or send back

1. Open the work item. Read what the step produced in **Artifacts**: the specification, the plan, the
   diff.
2. To accept it, press **Approve**.
3. To refuse it, type what is wrong in **Reason for sending back** and press **Send back**. The workflow
   goes where it says on a rejection, usually back to the agent, which receives your reason.

From the command line:

```bash
choregos items approve <item-id>
choregos items reject <item-id> --reason "The export must include credit notes."
```

## Answer a question

When an agent is blocked, it does not guess: it asks. The work item moves to a waiting state (often
**Needs a human**) and the people of the step's group are notified.

1. Open the work item. The question is in **Decision awaited**.
2. Type your reply in **Answer** and press **Answer**.

The agent's stage resumes where it stopped, with your answer and one more attempt.

```bash
choregos items answer <item-id> "Use the customer's billing currency."
```

## Accept or refuse a change of scope

An agent that needs to touch a path outside its allowed scope asks for it. A small request (a few
files, as the project's policy allows) is granted on its own. A larger one waits for a person: press
**Approve** to widen the scope for this work item, or **Send back** with a reason.

## Complete a task

Some steps are done by a person in the real world: hand over a badge, set up a workstation, sign a
contract. The step shows a form and, often, a sentence to attest.

1. Open the work item. The task's instructions are in **Decision awaited**.
2. Fill in the form. Required fields are listed under "To fill" until they are filled.
3. If the task asks for it, tick **I attest** and the sentence it shows.
4. Press **Done**. The values become fields of the work item, and the next step reads them.

If the task cannot be done, write why in **Why it cannot be done** and press **Cannot do it**.

## Approve a governed action

A write into a system that is not Choregos (create an account, order a laptop, deactivate a badge) is a
**governed action**. An agent or a workflow proposes it; the people its approval rule names decide; then
the platform runs it, step by step, and undoes what it did if a step fails.

1. Open **Inbox**, then the action under **Actions to approve**. Its page lists what it will do, effect
   by effect, and with which parameters.
2. Press **Approve**, or write a **Reason to reject** and press **Reject**.

Three rules apply:

- **A recent sign-in.** An approval rule may require that you signed in a few minutes ago. If you did
  not, the console sends you through your identity provider again, then brings you back.
- **Not your own proposal.** The person who proposed the action, or who owns the agent that proposed
  it, does not decide it.
- **Several approvers.** A rule may ask for two different people.

The action's page then shows its journal: every effect, its attempts, its answer or its error.

## Approve a production release

A release train batches what is ready and deploys it. When the project's policy or the workflow asks
for an approval before production:

1. Open the project's **Trains**.
2. Find the batch waiting for approval and press **Approve**. The confirmation names the batch and how
   many work items it carries.

```bash
choregos trains status billing-api
choregos trains approve <release-id> --note "Checked with support"
```

## Deadlines and escalations

Every human step can carry a deadline (`sla_hours` on the group, `timeout_hours` on the transition).
The inbox puts overdue requests first. When a step times out, the work item goes where its workflow
says, usually **Needs a human**, and from there a person decides to resume or abandon it.

## Who decides when nobody can

A step addressed to a group that nobody carries would wait forever. An **organisation admin** may answer
any step; doing so outside the group is recorded in the audit log as an override, with the group it
bypassed.

## Decisions stay in the console

A `/choregos approve`, `reject` or `answer` comment on an issue does not decide anything. Choregos
replies on the issue with a link to the inbox. A decision is taken by a signed-in person, in the
console or with their own token.

## What can go wrong

| Message | What it means |
| :-- | :-- |
| "deciding needs at least the developer role" | You are a viewer on this project |
| "this step is addressed to the group … only its members answer it" | You do not carry the step's group at your last sign-in; sign in again after your identity provider adds you, or ask an organisation admin |
| "no human request is pending on this work item" | Someone decided before you, or the work item moved on |
| "approving a release needs the release_captain role" | Ask a project owner for the role |

## Go further

- [Work items](work-items.md): what happens after your decision.
- [Workflows](workflows.md): where a workflow names who decides.
- [Governed actions](../../usage.md#4-bis-governed-actions), in depth.
