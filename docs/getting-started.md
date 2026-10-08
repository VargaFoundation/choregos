# Getting started

This page answers one question: **I have work to run — a repository to develop, or a
business process to instruct — how do I get the platform to do it?**

Three paths, from the least committing to the most real. Take the first if you want to see
what it looks like before installing anything.

---

## 1. See the whole journey without installing anything

```bash
make setup
make demo
```

`make demo` plays one ticket end to end on simulated connectors: specification, human
approval, implementation, verification, PR, merge, deployment. You will see the status
comment as it would be written in the ticket — stages, backend and model per stage, tokens,
cost, duration — and the summary:

```
· runs executed        : 3
· total cost           : 1.00 USD
· PR opened            : https://fake.scm/varga/billing-api/pull/1
· findings filed       : 1
· final ticket state   : deployed_prod
```

Nothing is called outside. It is the shape of the journey, not a proof that it works against
your tools.

## 2. On a development cluster

```bash
make dev-up      # kind + Tilt: the whole platform on your machine
make dev-seed    # an organisation, a demo project, ten tickets, one train
```

The front is on `http://localhost:3000`, the API on `:8000`. Or, closer to what a real agent
does, the single-node bench of [`demo/`](../demo/README.md): real agents, real Temporal,
real gates — `make demo-up`, `make demo-images`, `make demo-seed`.

Without Kubernetes, `docker compose -f dev/compose.yaml up -d` brings the dependencies
(Postgres, Temporal, LiteLLM, Keycloak, RustFS for S3) and the three processes start by hand — see
[development](development.md).

---

## 3. Onboard a real project

### Before you start

| | Why |
| :-- | :-- |
| An **installed platform** with its first organisation and administrators | [`deployment.md`](deployment.md): the chart bootstraps both (`global.bootstrap.*`) |
| An **API token** | issued by the person who will use it (`choregos tokens create`, or *Admin → Tokens*); the very first one from the server side (`choregos-admin tokens create`) |
| The **application repository** (GitHub, GitLab) — *if the work is code* | it is what agents modify, on a branch, through a PR. A business project has none |
| A **GitOps repository** — *for a templated stack* | provisioning writes the project's manifests there, Argo CD applies them |
| A **board** (GitHub Projects v2, Jira) — *or nothing* | with `tracker: internal` the requests are filed in Choregos itself |
| A **GitHub App** installed on the organisation — *for GitHub* | scoped installation tokens, webhooks; never a broad PAT |
| A **cluster** (or an Azure subscription) | depending on the template: Tekton runners, or Container Apps jobs |
| The **platform secrets** | `session-secret`, the run-token key pair, the webhook secrets, the App ID and its private key — in the vault, read by External Secrets (`charts/choregos/charts/api/templates/externalsecret.yaml`) |

Models go through the platform's LiteLLM gateway: a project has no provider key of its own,
it has a budget.

### Choose a template — or none

| Template | When |
| :-- | :-- |
| `github-tekton-argo-k8s` | you have a cluster and run the agents there |
| `github-aca` | you are on Azure and want no Kubernetes pool for agents: runs are Container Apps *jobs*, billed by the second |
| `github-software-delivery` | a software team on GitHub Actions, run by agents from the catalogue: a fix ships with no human (`dev-simple`), a study ends with a merged MADR 4 decision record (`study`), a feature waits for a product owner, a maintainer and a release captain (`dev-complex`) — see its [README](../templates/github-software-delivery/README.md) |
| `joiners-leavers` | HR, no repository: arrivals and departures, two workflows, two agents, two skills — every write into the directory, the device manager, the carrier or the badge readers is a governed action |
| *none* | a project with no repository, or one whose connectors you attach yourself |

`templates/<name>/manifest.yaml` lists a template's inputs, required connectors and steps. A
template can also ship **agents and skills** (`defaults.agents`, `defaults.skills`): they enter the
organisation when a project is born from it, in version 1 (`catalogue:<slug>` names one from the
platform's catalogue) — never rewritten when they are already
there, so what the organisation changed since stays. It can ship its own policy (`policy:
./policy.yaml`) instead of a preset. And it names the organisation connectors its
workflows call (`requires.org_connectors`: `annuaire: identity`…), for the administrator to declare.

What only a plugin understands, a template ships as an **extension** (`defaults.extensions`, a name →
a folder of the template): `joiners-leavers` ships its ontology as `ontology: ./ontology`. When the
project is born, the plugin that declared an installer under that name installs it — an invalid
package fails the creation, like an invalid workflow. Without such a plugin (the ontology plugin is a
trial, off unless `CHOREGOS_ESSAI_ONTOLOGIE=1`), the project is born without it, and the audit log
says so (`template.extension.skip`).

### Create the project

```bash
choregos login --api-url https://choregos.example.com --token chg_… --org acme
choregos projects create billing-api \
  --repo https://github.com/acme/billing-api \
  --language python \
  --template github-tekton-argo-k8s
```

`--template` chains into provisioning. To run it again and follow it:

```bash
choregos projects provision billing-api --follow
```

Provisioning is **idempotent**: rerunning it after a failure resumes where it stopped,
without redoing what was done.

A project without code takes its requests from Choregos itself:

```bash
choregos projects create staffing --name "Staffing"                    # no --repo → tracker `internal`
choregos items create staffing --title "Data PM for a 6-month mission" --size M
```

### What provisioning does

Installs the GitHub App, labels, a Projects v2 board with its fields, an issue template,
manifests written to the GitOps repository then synchronised by Argo CD, webhooks, the
project's memory tenant with an initial import (README, docs, ADRs, the year's issues and
PRs), a team and a budget at the gateway, and a test message on the notification channel.

And a **scaffolding PR** on your repository:

| File | Review before merging |
| :-- | :-- |
| `AGENTS.md` | the test, lint and type-check commands of *your* project |
| `.choregos/workflow.yaml` | state labels must match your board; keep the gates |
| `.choregos/policy.yaml` | budgets, approvals, allowed scope, train rules |
| `tekton/pipeline.yaml` | the project's CI |
| `deploy/kustomization.yaml` | the deployment target |
| `.github/PULL_REQUEST_TEMPLATE.md`, `CODEOWNERS` | review |

Validate the workflow before committing:

```bash
choregos workflow validate .choregos/workflow.yaml
```

### The first ticket

Open an issue, describe the intent, put the **`agent-ready`** label on it. That is the only
trigger. The platform:

1. classifies the ticket (size, risk) and picks the workflow;
2. runs a specification stage — the agent writes a spec with verifiable criteria;
3. waits for human approval if the project's policy requires it;
4. implements on a branch, within the paths the ticket allows;
5. verifies (tests, lint, types, coverage) and opens the PR;
6. mirrors everything into the ticket: state, cost, run link.

If a webhook is lost, a periodic catch-up picks the open `agent-ready` tickets nobody saw —
there is nothing for you to do. With `tracker: internal`, `choregos items create` (or *New
request* on the board) starts the ticket's interpreter at once.

### Follow, and take over

```bash
choregos items list billing-api --state running
choregos runs tail <run-id>        # the run's journal, live
choregos runs diff <run-id>        # what the agent changed
choregos items approve <item-id>   # release a human wait
choregos items reject  <item-id> --reason "…"
```

In the front: the **board** (columns are the workflow's states), a ticket's **timeline** and
cost per stage, a run's **guarantees** (which gates ran and what each decided), its
**evidence**, its **access card** (what the agent read, wrote, ran, reached — and what was
refused) and its live journal.

---

## What is not proven yet

[`docs/plan/BLOCKERS.md`](plan/BLOCKERS.md) keeps the list, with the cause and the
workaround chosen for each. At the time of writing: the platform's high-availability values
need a staging-sized environment, Azure DevOps (Boards, Pipelines) has no reachable
organisation, and the single-node bench measures no cost unless a provider key is given to
the embedded gateway. The default backend is `claude-code`; OpenHands was removed
([ADR 0011](adr/0011-retrait-d-openhands.md)).

## Where to go next

- [development.md](development.md) — set up a development environment
- [security.md](security.md) — what the platform guarantees, and by which mechanism
- [cli.md](cli.md) — the CLI reference, generated from the CLI itself
- [runbooks/](runbooks/) — operations
- [plan/02-orchestrateur-agents-runner.md](plan/02-orchestrateur-agents-runner.md) — agent backends and their invocations (French)
