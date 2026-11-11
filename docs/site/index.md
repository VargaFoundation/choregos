---
title: Choregos
description: What Choregos is, who it is for, a five-minute tour, and a map of this documentation.
sidebar_position: 1
---

# Choregos

**Choregos runs your team's work through agents and people, under rules you can check.** A request
comes in: an issue, a ticket, a form, a call from another system. Choregos moves it through a
**workflow** you declare. Agents do the steps they are given. People decide where the workflow says a
person decides. Every step has a budget it cannot exceed and leaves evidence you can read. Nothing
moves on because an agent says it is done: the platform checks.

Choregos is open source (Apache 2.0), self-hosted, and works with any agent. It is published by the
Varga Foundation.

## Who it is for

- **A team lead** who wants agents to take a share of the work without losing control of what ships:
  who approved it, what it cost, what it changed.
- **A developer** who files work, follows what an agent does, reviews its result and takes over when
  it gets stuck.
- **An operator or administrator** who connects the tools the team already uses (GitHub, Jira,
  Confluence, Slack, an identity provider), sets the budgets and decides what agents may reach.

It is not tied to software. The same engine carries an HR onboarding, a purchasing request or a data
change: anything that arrives as a request and is done in steps.

## What it is not

- **Not a chat.** Agents work unattended. People step in at the points the workflow names, with a
  deadline.
- **Not a coding agent.** Choregos runs the coding agents you choose, in disposable sandboxes, and
  gives them only what one step needs.
- **Not a promise in a prompt.** A check (a **gate**) is code: tests that really ran, a diff inside the
  allowed paths, the documents a step must produce. A gate that cannot see what it must check refuses.

## A five-minute tour

Here is the life of one request in a classic development project, `billing-api`, created from the
software-delivery template.

1. **A developer opens an issue** on GitHub, "Export invoices as CSV", and labels it `agent-ready`
   and `feature`. That label is the only trigger.
2. **Choregos picks the workflow.** The `feature` label routes it to the `dev-complex` workflow. The
   work item appears on the project's **Board**, in the first column.
3. **An agent triages it**, sizes it (S, M, L or XL) and estimates the risk. **Another agent writes
   the specification**, with criteria that can be checked.
4. **The work item waits for a person.** The specification goes to the product owners. It shows in
   the **Inbox** with a deadline. One of them reads it and presses **Approve**, or **Send back** with
   a reason; the agent then tries again with that reason.
5. **Agents plan, implement, test and document**, on a branch, inside the paths the specification
   allows. After each step a gate checks the result: the tests were really run, no secret was written,
   the diff stays in scope. A failed check sends the work back, a few times at most, then to a person.
6. **Agents review the change**, then the platform opens the pull request. A maintainer approves it
   in Choregos. When the repository's checks are green, the platform merges it.
7. **A release train deploys it**, first to staging, then to production after a release captain
   approves the departure. An agent checks production from measured facts.
8. **Everything stays readable.** The work item's page draws its journey, step by step. Each run shows
   its cost, its evidence, what the agent read, wrote and was refused, and its full log.

## How to read this documentation

Read it in order the first time: what it is, the ideas, a first project, then the daily tasks.

| Section | Read it to |
| :-- | :-- |
| [Concepts](concepts.md) | Learn the words: organisation, project, workflow, run, gate, evidence, budget |
| [Get started](get-started.md) | Try Choregos on your machine, then run a first real project end to end |
| **Use Choregos** | Do the daily work in the console |
| ▸ [Projects and members](use/projects-and-members.md) | Create a project, invite people, give them roles |
| ▸ [Workflows](use/workflows.md) | Choose, read, change and publish the process a project follows |
| ▸ [Work items](use/work-items.md) | File work, follow it, take over when it is stuck |
| ▸ [Decisions](use/decisions.md) | Approve, send back, answer a question, complete a task |
| ▸ [Knowledge](use/knowledge.md) | Give agents documents to read: uploads, Confluence, SharePoint, git, the web |
| ▸ [Connectors](use/connectors.md) | Connect the tracker, the repository, the deployment and the other systems |
| ▸ [Budgets and policies](use/budgets-and-policies.md) | Cap what work costs and bound what agents may do |
| **Build agents** | Write what agents carry |
| ▸ [Skills](build/skills.md) | Package know-how an agent loads |
| ▸ [Agents](build/agents.md) | Create, version and release agents of every kind |
| ▸ [Plugins](build/plugins.md) | Ship templates, agents and connectors as one package |
| ▸ [Tools](build/tools.md) | Give agents tools they cannot misuse |
| **Integrate** | Reach Choregos from outside |
| ▸ [MCP](integrate/mcp.md) | Connect an AI assistant (Claude, Cursor, VS Code) to Choregos |
| ▸ [A2A](integrate/a2a.md) | Start and follow a workflow from another agent or an application |
| ▸ [Triggers](integrate/triggers.md) | Start work from a schedule, a webhook or a tracker |
| ▸ [REST API](integrate/rest.md) | Script anything the console does |
| **Command line** | Use the `choregos` command |
| ▸ [Install](cli/install.md) | Install the command on your machine |
| ▸ [Reference](cli/reference.md) | Look up every command and option |
| [Host Choregos](host/index.md) | Deploy, secure and upgrade an instance |
| [Reference](reference/index.md) | The workflow YAML, the errors, the API |

## Editions

This documentation describes the **community edition**: Apache 2.0, one organisation per instance,
everything in this documentation included. An **enterprise edition**, published separately, adds
several organisations on one instance and the identity features that come with them. The console
shows which edition runs under **About**, at the bottom of the sidebar.

## Where the deep detail lives

These pages are written for people who use Choregos. The complete reference, written for the people
who run and extend it, is in the repository: the [reference documentation](../README.md), the
[concepts in depth](../concepts.md), [usage in depth](../usage.md), [security](../security.md) and the
[architecture decisions](../adr/README.md). Where a page and the code disagree, the code is right and
the page is a bug: [report it](https://github.com/VargaFoundation/choregos/issues).
