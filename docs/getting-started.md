# Getting started

This page answers one question: **I have work to run — a repository to develop, or a
business process to instruct — how do I get the platform to do it?**

Start on your own machine: one command, Docker only, a demo project with a work item that
runs. Then onboard a real project.

---

## 1. Run it on your machine — Docker only

```bash
git clone https://github.com/VargaFoundation/choregos.git
cd choregos
cp .env.example .env      # optional: every line has a default
docker compose up
```

When the console is ready, open **http://localhost:3000**. Development sign-in is filled in
with the demo administrator (`admin@choregos.local`): click *enter* and you land on the
**demo project**, with one work item, `DEMO-1`, already waiting for your approval. Approve
it, or send it back, from its page. A work item you create in the demo project runs the same way.

| Measured on 2026-10-12 (WSL2, 16 cores, images already built) | |
| :-- | :-- |
| `docker compose up` to every service healthy, empty volumes | 147 to 216 seconds |
| Memory at rest, the whole install | about 1.85 GB, LiteLLM alone 1.2 GB |
| A work item through its agent stage, fakes mode | 2 seconds |

The first start also builds the platform's images from source and pulls the others: count
several more minutes, depending on your machine and your connection (not measured on a cold
cache).

What runs: PostgreSQL (the platform connects through a non-superuser role, so row-level
security applies), Temporal, the migrations, the API, the workers, the console, LiteLLM, and a
one-shot seed of the demo organisation and project. Everything listens on `127.0.0.1` only.

### Fakes mode, and a real model

With no model key in `.env`, agents are **simulated**: each agent stage hands back the outputs
its transition declares, says in its summary that no model ran, and costs nothing. Everything
else is the real path — Temporal, the database, the gates, the human decision.

To run **real agents**, put a key in `.env` and start again:

```bash
# .env
ANTHROPIC_API_KEY=…       # or OPENAI_API_KEY=…
```

```bash
docker compose up -d
```

LiteLLM then routes the platform's model aliases to that provider; it is the only container
that sees the key, and each agent run gets its own virtual key, capped by the run's budget. Each
agent runs in the runner image (`ghcr.io/vargafoundation/choregos-runner`, more than a gigabyte,
pulled on the first run), started by the workers. With a key, the seeded work item waits for you
to start it: an install spends nothing before you open it. `LITELLM_REAL=0` keeps fakes even with
a key; `LITELLM_REAL=1` refuses to start without one.

To check it end to end, from the repository:

```bash
python3 tools/local_install_check.py      # fakes: sign-in, a work item, an approval
make test-live-local                      # with a key: fails unless the model was called
```

> **Security.** With a key, the workers start agent containers through the Docker socket,
> mounted into the `worker` container. Whoever controls that socket controls Docker on your
> machine, which amounts to root on it. That is acceptable on a laptop; never expose this
> install beyond one. Development sign-in has no password: the API refuses it outside the
> `dev` and `test` environments.

### Options

```bash
docker compose --profile memory up        # adds Ecphoria, the memory service (embeddings need a key)
docker compose --profile temporal-ui up   # adds the Temporal UI on http://localhost:8233
docker compose down -v                    # stops everything and deletes the data
```

Ports taken on your machine? `CHOREGOS_PORT_WEB`, `CHOREGOS_PORT_API` and
`CHOREGOS_PORT_TEMPORAL_UI` in `.env`. PostgreSQL runs without `fsync` here, for start-up time:
keep nothing in this install that you cannot recreate.

## 2. Other ways to look

```bash
make setup
make demo        # one ticket end to end, in memory, nothing installed but Python
```

`make demo` plays a software ticket on simulated connectors: specification, human approval,
implementation, verification, PR, merge, deployment. It is the shape of the journey, printed
in the terminal.

Closer to production, the single-node bench of [`demo/`](../demo/README.md) runs the chart on
kind with real agents: `make demo-up`, `make demo-images`, `make demo-seed`. To work on the
platform itself (hot reload, a development cluster), see [development](development.md).

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

The board's fields are `Status`, `Cost (€)`, `Size`, `Risk` and `Run`. A board provisioned by
Choregos 0.17 or earlier named three of them `Coût (€)`, `Taille` and `Risque`: provisioning again
renames them in place — the values already on the cards stay — and, until then, Choregos keeps
writing to the fields the board has.

And a **scaffolding PR** on your repository:

| File | Review before merging |
| :-- | :-- |
| `AGENTS.md` | the test, lint and type-check commands of *your* project |
| `.choregos/workflow.yaml` | state labels must match your board; keep the gates |
| `.choregos/policy.yaml` | budgets, allowed scope, sandbox, train rules (who approves a ticket is in the workflow) |
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
3. waits for human approval if the workflow names a human on that transition;
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
refused), its live journal and its pod logs (the log tail and how the pod ended, secrets masked).

---

## What is not proven yet

[`docs/plan/BLOCKERS.md`](plan/BLOCKERS.md) keeps the list, with the cause and the
workaround chosen for each. At the time of writing: the platform's high-availability values
need a staging-sized environment, Azure DevOps (Boards, Pipelines) has no reachable
organisation, and the single-node bench measures no cost unless a provider key is given to
the embedded gateway. The default backend is `claude-code`; OpenHands was removed
([ADR 0011](adr/0011-retrait-d-openhands.md)). The local install's real-model path was run
end to end up to the provider (which refused the test key): no work item has yet gone through it
with a valid key. It uses the `opencode` backend for both providers, because `claude-code` in the
published runner asks the gateway for a model of its own choosing.

## Where to go next

- [development.md](development.md) — set up a development environment
- [security.md](security.md) — what the platform guarantees, and by which mechanism
- [cli.md](cli.md) — the CLI reference, generated from the CLI itself
- [runbooks/](runbooks/) — operations
- [plan/02-orchestrateur-agents-runner.md](plan/02-orchestrateur-agents-runner.md) — agent backends and their invocations (French)
