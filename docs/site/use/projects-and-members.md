---
title: Projects and members
description: Create a project, check what it still lacks, invite people and give them the right role.
sidebar_position: 1
---

# Projects and members

A project is where a team's work runs: its workflows, its connectors, its policy, its people. This
page shows how to create one and who can do what in it.

**What you will be able to do**

- Create a project, with or without a template, with or without a code repository.
- Read the readiness list and fix what the project still lacks.
- Invite people and give them a role on the organisation or on one project.
- Give an application its own account on a project.

## Before you start

- To create a project: the **Organisation admin** role.
- To invite people to a project or change its settings: the **Project owner** role on it.

## Create a project

### From the console

1. Open **Projects** in the sidebar and press **New project**.
2. Answer **What does this project deliver?**:
   - **Software, from a code repository**: agents plan, change and deliver the code of a repository.
     Pick a **Stack template**, or **None — I wire my connectors myself**.
   - **A business process, no code repository**: HR onboarding, purchasing, legal, support. Pick a
     **Workflow** from the library; it becomes the project's default.
3. **Identifier (slug)**: lowercase letters, digits and dashes, for example `billing-api`. It is in
   every address and cannot be changed later. **Display name**: what people read.
4. For software: the **Repository** (`owner/name` or an https address) and the **Main language**.
5. **Tracker (where tickets come from)**: **Choregos (requests are filed here)**, **GitHub Issues /
   Projects**, **Jira** or **GitLab**. A Slack channel for notifications is optional.
6. Read the summary and press **Create and provision** (with a template) or **Create**.

If a step fails half-way, the wizard keeps what was already created: press **Retry**, and it goes on
from there.

### From the command line

```bash
choregos projects create billing-api --name "Billing API" --repo https://github.com/acme/billing-api --template github-software-delivery
choregos projects provision billing-api --follow
```

A project without a repository files its requests in Choregos itself:

```bash
choregos projects create onboarding --name "Employee onboarding"
```

### Which template

| Template | When |
| :-- | :-- |
| `github-software-delivery` | A software team on GitHub Actions: fixes, studies that end in a decision record, features with three human approvals |
| `software-delivery-governed` | Changes that must pass everything: an approved decision record, mutation testing, security and compliance reviews decided by people |
| `github-tekton-argo-k8s` | You run your own CI (Tekton) and deployment (Argo CD) on Kubernetes |
| `github-aca` | You are on Azure and run agents as Container Apps jobs |
| `joiners-leavers` | HR arrivals and departures, with no repository; every write into another system is approved |
| none | You attach the connectors and the workflow yourself |

A template brings the workflows, the policy and the agents those workflows name. Agents enter the
organisation the first time a project is born from the template; an agent the organisation already
has is never overwritten.

## Check what the project still lacks

Open the project's **Settings**. The first page shows the **readiness** list: one line per thing the
project needs, its state, and who can fix it.

| State | Meaning |
| :-- | :-- |
| Done | Configured and working |
| To check | It works, but deserves a look: a demo stand-in, a connector not tested for a week |
| To do | The project cannot run as intended until this is fixed |
| Not needed | No workflow of the project uses it |

The card's header sums it up: **Ready to run**, or how many lines are left to do.

Each open line has a **Fix** link to the page that fixes it. When you cannot fix it yourself, the line
says whom to ask: a project owner, the organisation admin, the platform operator, or someone outside
Choregos (your identity provider, a vendor). The project's **Overview** shows the same list as a
**Getting started** card until nothing is left to do.

```bash
choregos projects readiness billing-api
```

The readiness list is read from what is stored: the last connector tests, the policy, the members, the
workflows. It does not call the outside systems. Press **Test** on a connector to refresh it.

## The settings, group by group

| Group | What it holds |
| :-- | :-- |
| **General** | Name, status, repository, the commands that prove the work (tests, lint, type checks) and the facts that prove production is healthy |
| **Connections** | The tracker, the repository, the deployment, the agent runtime; notifications |
| **Agents** | The backend and models agents run on, the tools they may call, A2A, knowledge and memory |
| **Guardrails** | The policy, and the hosts a run may reach |
| **People** | Who works on the project, and the applications that call it |
| **Danger zone** | Suspend or archive the project |

Each section saves on its own. If someone else saved the same settings in between, the console reads
them again and merges; if it cannot, it asks you to reload the page.

## Invite people and give them roles

Roles go from least to most: **Viewer**, **Developer**, **Release captain**, **Project owner**,
**Organisation admin**. See [Members and roles](../concepts.md#members-and-roles) for what each one
adds.

### On one project

1. Open the project, then **Members** in the sidebar.
2. In **Members of this project**, type the person's **Member e-mail**, pick the **Role**, and press
   **Invite**.

### On the whole organisation

1. Open **Admin › Members**.
2. Type the **Member e-mail**, choose the **Scope**: **The whole organisation** or one project, pick
   the **Role**, and press **Invite**.

A project role **adds** to the organisation role: someone who is a developer of the organisation is
at least a developer on every project. To give someone less on one project, give them less on the
organisation.

### Groups

People also carry groups from your identity provider, read at each sign-in. Workflows address their
human steps to groups (`maintainers`, `product-owners`). The readiness list warns when a workflow names
a group that nobody has ever carried at sign-in. Groups are managed in your identity provider, not in
Choregos; when your provider withdraws a role it granted, Choregos removes it at the person's next
sign-in.

## Give an application its own account

When another system starts work in a project (a portal, a script, another agent), give it an
**application** rather than a person's token. It acts under its own name, has its own daily budget,
and keeps working when the person who created it leaves.

1. Open **Settings › People**. In the **Applications** card, type a short name under **New
   application** (lowercase letters, digits and hyphens) and press **Create**.
2. Press **Issue a token** on its line. The token is shown **once**: copy it into the calling
   system's secret store.

```bash
choregos machines create billing-api invoicing-portal --display-name "Invoicing portal"
choregos machines token billing-api invoicing-portal --name production --expires-in-days 90
```

An application's token opens the A2A door only, on that one project. It starts and follows work; it
never takes a decision. See [A2A](../integrate/a2a.md).

## What can go wrong

| Message or symptom | What it means |
| :-- | :-- |
| **New project** is missing or refused | You are not an organisation admin |
| "Lowercase letters, digits and dashes only" | The identifier has another character in it |
| "The repository is not valid yet" | Write `owner/name` or an https address |
| A readiness line stays to do after you fixed it | Press **Test** on the connector: readiness reads the last test |
| A person cannot approve a step | They lack the role (Developer or above) or the step's group |

## Go further

- [Connectors](connectors.md): connect the tracker, the repository and the other systems.
- [Workflows](workflows.md): the process the project follows.
- [Project settings and readiness](../../usage.md#project-settings-and-readiness), in depth.
