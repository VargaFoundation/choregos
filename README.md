# Choregos

> χορηγός — the one who leads the chorus and gives it the means to perform.

**Choregos** is the governed-agent platform of the [Varga Foundation](https://github.com/VargaFoundation)
(ADR 0029): workflows from any trade — software delivery, operations, joiners and leavers,
data change — that agents and humans move forward under measured guarantees. It
directs a chorus of agents (Claude Code, Codex, Gemini CLI, Goose, OpenCode…) behind a single
protocol (ACP), gives them a disposable space, a model, a budget and a score (the *workflow*),
and makes every write to a third-party system wait for a re-authenticated human approval. For
software, it serialises deployments behind a *release train*.

Licence: Apache 2.0. Complete execution plan: [`docs/plan/00-index.md`](docs/plan/00-index.md).

> **Documentation** — [docs/README.md](docs/README.md) (the reference): concepts, deployment and
> usage.

## Principles

1. **The tracker is the human interface.** GitHub Issues/Projects, Jira or GitLab remains
   the place where decisions are made; Choregos writes the state, the cost and the evidence there.
2. **The orchestrator plans, it never calls a model.** The Temporal workflows decide
   which stage to launch; only the runners talk to the agents.
3. **The runner is disposable; memory and results are durable.**
4. **Every guarantee is a mechanism** (gate, sandbox, diff check), never a sentence
   in a prompt.
5. **Production is a lock**: merge queue, release train, declarative guardrails.

## Architecture on one map

```
tracker ──webhooks──▶ apps/api ──signals──▶ apps/orchestrator (Temporal)
                         │                        │
                         │                        ├─ WorkflowInterpreter  (one ticket)
                         │                        ├─ ReleaseTrain         (one env)
                         │                        ├─ FindingsTriage       (one project)
                         │                        ├─ ProjectProvisioning  (one project)
                         │                        ├─ MemoryIngestion / EvalMatrix
                         │                        ▼
                         │                  Executor (Tekton | K8s Job | local Docker)
                         │                        ▼
                         └──internal API◀── packages/runner ──ACP──▶ agent ──MCP──▶ tools-mcp
                                                   │                    │
                                                   ▼                    ▼
                                             object store          LiteLLM ──▶ models
```

## Quick start

On one machine with Docker only (see [getting started](docs/getting-started.md)):

```bash
cp .env.example .env    # optional: ANTHROPIC_API_KEY or OPENAI_API_KEY for real agents
docker compose up       # console on http://localhost:3000, a demo project, a work item
```

To develop the platform:

```bash
make setup          # uv sync + pnpm install
make ci             # lint + typing + unit tests
make dev-up         # kind + Tilt (complete dev cluster)
make dev-seed       # org varga, demo project, 10 tickets, one train
make demo           # end-to-end scenario in memory (fakes, no cluster)
```

Without Kubernetes, the whole platform from your working copy — see `docs/getting-started.md`:

```bash
docker compose up                             # API, workers, Temporal, Postgres, LiteLLM, front on :3000
```

For front-end work only, on fakes (the API records the workflows it would start and runs none):

```bash
make api                                      # API on :8000, CHOREGOS_FAKES=1
pnpm -C apps/web dev                          # front on :3000
```

The dependencies alone, for running the API and the workers by hand against them:

```bash
make compose-up                               # Postgres, Temporal, LiteLLM, Keycloak, Ecphoria
```

If a port is already taken — a Postgres on 5432, a Keycloak on 8080 — every service
accepts an override rather than making you edit the compose file:

```bash
CHOREGOS_PORT_POSTGRES=15432 CHOREGOS_PORT_KEYCLOAK=18080 \
  docker compose -f dev/compose.yaml up -d
CHOREGOS_PORT=8001 CHOREGOS_FAKES=1 uv run choregos-api
```

`CHOREGOS_PORT_{POSTGRES,TEMPORAL,TEMPORAL_UI,LITELLM,KEYCLOAK,MINIO,MINIO_CONSOLE,ECPHORIA}`
for the stack, `CHOREGOS_PORT` for the API, `PORT` for the front end (`PORT=3001 pnpm -C apps/web dev`).

The Ecphoria image lives on GHCR, `ghcr.io/vargafoundation/ecphoria`. If the package is private, you need to
authenticate once before the first `up`:

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <user> --password-stdin
```

## A demonstration with real agents

`make demo` runs in memory. To see the platform work **for real** — a single-node
kind cluster, agents that write code and push branches — everything is in
**[demo/README.md](demo/README.md)**: a code project and an HR project, the second on the same
engine without a line of code changed. The README also says what this bench does not prove.

## Onboarding a project

You have a repository and you want the platform to develop it:
**[docs/getting-started.md](docs/getting-started.md)**. The guide goes from `make demo`
(the complete journey in memory, with nothing to install) to the provisioning of a real project
— GitHub App, board, GitOps, scaffolding — and to the first `agent-ready` ticket.

## Repository map

| Path | Content |
| :-- | :-- |
| `packages/contracts` | **Source of truth**: JSON Schemas, OpenAPI 3.1, generated Python/TS types |
| `packages/core` | Domain, workflow DSL (parse/validate/select), policy engine, gates, model resolution |
| `packages/adapters` | tracker, scm, ci, cd, executor, memory, gateway, notify — and their `Fake*` |
| `packages/runner` | Headless ACP client, guardrails, DoD loop, `StageResult` publication |
| `packages/tools-mcp` | `choregos-tools` MCP sidecar exposed to the agent |
| `packages/playbooks` | Prompts per role + evals |
| `packages/cli` | `choregos` CLI |
| `apps/api` | FastAPI: REST, webhooks, SSE, internal API, OIDC, RLS |
| `apps/orchestrator` | Temporal workers and workflows |
| `apps/web` | Next.js 15: board, runs, trains, findings, memory, admin |
| `charts/choregos` | Platform Helm umbrella |
| `templates/` | Stack templates (provisioning of a project) |
| `dev/` | kind, Tilt, docker compose, seed |
| `demo/` | single-node bench: two workflows (code and HR), playbooks, git inside the cluster |
| `tests/` | e2e (kind) and conformance (ACP backends, templates) |

## Contributing

Read `AGENTS.md` (agent instructions, equally valid for humans), then
`docs/plan/07-delivery-parallelisation-conventions.md`. One story = one PR.
The contracts (`packages/contracts`) change only through a PR tagged `contract-change`.
