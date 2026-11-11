---
title: Get started
description: Try Choregos on your machine in ten minutes, then take a real development project from an issue to a merged pull request.
sidebar_position: 3
---

# Get started

This page has two parts. First, try Choregos on your own machine: one command, nothing to configure,
a demo work item waiting for your approval. Then run a real development project end to end: create it
from a template, connect its repository, open an issue, let agents work, approve, merge.

**What you will be able to do**

- Run Choregos locally and approve your first work item.
- Create a development project from the `github-software-delivery` template.
- Send an issue through the workflow and take the decisions it asks of you.

## Part 1: try it on your machine

### Before you start

- Docker with Compose, and about 2 GB of free memory.
- Git, to fetch the source.
- No model key: without one, agents are simulated and cost nothing. Everything else is real: the
  orchestrator, the database, the gates, the human decision.

### Steps

1. Fetch the source and start everything:

   ```bash
   git clone https://github.com/VargaFoundation/choregos.git
   cd choregos
   cp .env.example .env
   docker compose up
   ```

   The first start builds the images and pulls the others. Count a few minutes, more on a slow
   connection.

2. Open **http://localhost:3000**. The sign-in page shows a development sign-in with the demo
   administrator, `admin@choregos.local`, already filled in. Press **Enter**.

3. You land on the **demo** project. Open **Board** in the sidebar. One work item, "Plan the team
   offsite", waits in the column **Plan to approve**: an agent has written a plan.

4. Open the work item. The **Decision awaited** card shows what is asked. Read the plan in
   **Artifacts**, then press **Approve**. To see the other path, type a reason and press **Send back**
   instead: the work item goes back to the agent with your reason.

### What you should see

- After **Approve**, the work item moves to **Approved**, a terminal state, and its **Journey** draws
  each step: the agent's plan, then your decision.
- On the run page (open the agent's step from the journey), the summary says that no model ran. That
  is the simulated mode.

### What can go wrong

| Symptom | What to do |
| :-- | :-- |
| A port is already in use | Set `CHOREGOS_PORT_WEB` or `CHOREGOS_PORT_API` in `.env`, then start again |
| The console does not answer after several minutes | Run `docker compose ps`: the one-shot services (the migrations, the seed) have exited, every other one is healthy |
| You want real agents | Put an `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env` and start again; see the [local install in depth](../getting-started.md#fakes-mode-and-a-real-model) |

This install is for a laptop only. With a model key, the workers start agent containers through the
Docker socket, which amounts to root on the machine. To stop everything and delete the data:

```bash
docker compose down -v
```

## Part 2: a real development project

The running example of this documentation: the team behind `acme/billing-api` wants agents to take
features from an issue to production, with a person deciding at the points that matter. They use the
`github-software-delivery` template, which brings three workflows:

| Workflow | For | People who decide |
| :-- | :-- | :-- |
| `dev-simple` | a bug or a small fix (labels `bug`, `fix`, `chore`) | none, unless something goes wrong |
| `study` | a study that ends with one decision record (labels `study`, `adr`, `spike`) | the architects |
| `dev-complex` | a feature (labels `feature`, `epic`, `complex`) | a product owner on the specification, a maintainer on the pull request, a release captain on production |

### Before you start

- **An instance of Choregos** your organisation runs. To install one, see
  [Host Choregos](host/index.md).
- **The organisation admin role.** Only an organisation administrator creates a project. Ask one if
  you are not.
- **The Choregos GitHub App installed** on the repository's owner. You can do it from the console, on
  the way (step 4).
- **Groups from your identity provider** for the people who decide: `product-owners`, `maintainers`,
  `release-captains` and `architects`. A human step is answered only by a member of its group.
- **A deployment connector** for the release train: Argo CD for real, or the `demo` type for a trial
  where nothing is deployed.
- **Branch protection** on the repository's default branch: required status checks, but no required
  review. People decide in Choregos; the platform merges.

### 1. Create the project

In the console:

1. Open **Projects** in the sidebar and press **New project**.
2. Under **What does this project deliver?**, choose **Software, from a code repository**. Under
   **Stack template**, choose **GitHub · software delivery (fix, study, feature)**. Press **Next**.
3. Fill in **Identifier (slug)** with `billing-api`, **Display name** with "Billing API",
   **Repository** with `acme/billing-api`, and the **Main language**. Press **Next**.
4. Under **Tracker (where tickets come from)**, choose **GitHub Issues / Projects**. Press **Next**.
5. Check the summary and press **Create and provision**.

Or from the command line, once you are signed in (see [Install the CLI](cli/install.md)):

```bash
choregos login --api-url https://choregos.example.com --token chg_xxx --org acme
choregos projects create billing-api --name "Billing API" --repo https://github.com/acme/billing-api --template github-software-delivery
choregos projects provision billing-api --follow
```

Provisioning creates the routing labels (`bug`, `feature`, `study` and the others) and the webhooks on
the repository, and installs the template's workflows, policy and agents. It is safe to run again: it
resumes where it stopped.

### 2. Finish the setup with the readiness list

Open the project's **Settings**. The readiness list at the top says what the project still lacks, item
by item, and who can fix each one. Work through it:

1. **Settings › Connections**: the **Source code** card shows "The Choregos App is not installed" until
   the App is there; press **Install on GitHub**, install it on the repository, and come back. Press
   **Test** on each card until it reads **Connected**.
2. On the **Deployment (CD)** card, press **Configure** and choose Argo CD, or `demo` for a trial.
3. **Settings › People**: make sure someone holds the **Project owner** role, and someone the
   **Release captain** role. See [Projects and members](use/projects-and-members.md).
4. **Settings › General**: set the command that proves production is healthy (a `smoke_ok` fact, for
   example a `curl` on the health page). The production check fails without it.

The same list, from the command line (it exits with 1 while something is left to do):

```bash
choregos projects readiness billing-api
```

### 3. Open the first work item

On GitHub, open an issue on `acme/billing-api`: "Export invoices as CSV", with a description of what
you expect. Add two labels: `feature` (it routes the issue to `dev-complex`) and `agent-ready` (it
starts the work). Nothing starts without `agent-ready`.

Within a minute, the work item appears on the project's **Board**, and the issue gets a status comment
that Choregos keeps up to date.

### 4. Follow the agents

Open the work item from the board. Its **Journey** shows the workflow as a path: a circle for an agent
step, a circle with a person for a decision, a square for the platform. The current step pulses.

The triager sizes the issue, then the specification writer writes a specification with criteria that
can be checked and the paths the change may touch. Open a step to see its run: its cost, its evidence,
what it read and wrote, and its live journal.

### 5. Approve the specification

The work item stops in **Specification to approve** and appears in the **Inbox** of the
`product-owners`. As one of them:

1. Open the work item. Read the specification in **Artifacts**.
2. Press **Approve**. Or write what is wrong and press **Send back**: the specification writer starts
   again with your reason.

### 6. Let it be built, then approve the pull request

Agents plan, implement, test, document and review the change. After each step, gates check the
result: the tests really ran, the diff stays in the allowed paths, no secret was written, the
documentation changed with the code. A failed check sends the work back to the agent a few times, then
to a person.

The platform then opens the pull request, and the work item waits in **Pull request to approve** for
the `maintainers`. Read the diff on GitHub or in the run's **Diff**, then press **Approve** in
Choregos. When the repository's checks are green, the platform merges the pull request and the work
item reaches **Merged**.

### 7. Release

The release train takes the change to staging, then waits for a release captain to approve the
production departure (see [Decisions](use/decisions.md)). After the release, an agent checks
production from measured facts, and the work item ends in **Verified in production**.

### What you should see

- On the issue: a status comment with the state, the cost so far and a link to the work item.
- On the work item's page: every step of the journey green, the cost per stage, the artifacts (the
  specification, the plan, the test report, the release notes).
- In **Spend**: what the work item cost, by agent and by model.

### What can go wrong

| Symptom | Likely cause | What to do |
| :-- | :-- | :-- |
| The issue is labelled but nothing appears | The App is not installed, or the webhook does not reach the instance | **Settings › Connections**: test the cards; a periodic catch-up also picks up labelled issues |
| The work item went to `dev-simple` | The `feature` label was missing | Open the work item and press **Change workflow** |
| "Only its members answer it" when you approve | You are not in the step's group at your last sign-in | Ask your identity provider administrator; an organisation admin can answer instead, and it is audited |
| The work item is in **Needs a human** | A step failed its gates too many times, an agent asked a question, or the budget ran out | Read the step's run, then answer, replay the stage or stop the work item: see [Work items](use/work-items.md) |
| The merge never happens | A required check is red, or the merge queue setting does not match the repository | Read the pull request's checks; set the source code connector's merge queue option to match |

## Go further

- [Concepts](concepts.md), if a word on this page was new.
- [Work items](use/work-items.md) and [Decisions](use/decisions.md), for the daily work.
- [Workflows](use/workflows.md), to change the process the template brought.
- The [template's own page](../../templates/github-software-delivery/README.md), for every detail of its
  three workflows.
