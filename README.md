# Tranibor

Tranibor runs governed agents: workflows from any trade — software delivery, operations, joiners and
leavers, data change — that agents and people move forward under budgets and checks you can read.

It drives agents (Claude Code, Codex, Gemini CLI, Goose, OpenCode…) behind one protocol (ACP), gives
each run a disposable sandbox, a model, a budget and a workflow to follow, and makes every write to a
third-party system wait for a re-authenticated human approval. For software, it serialises deployments
behind a *release train*.

Tranibor is published by [GRAAL.SYSTEMS SAS](https://github.com/graalsystems) under the Apache 2.0
licence. It is the open core of the graal Agentic Platform.

**Documentation:** [`docs/`](docs/README.md), also published as the project website (linked from the
repository's About panel).

## Principles

1. **The tracker is the human interface.** GitHub Issues/Projects, Jira or GitLab stays where
   decisions are made; Tranibor writes the state, the cost and the evidence there.
2. **The orchestrator plans, it never calls a model.** Temporal workflows decide which stage to
   launch; only the runners talk to the agents.
3. **The runner is disposable; memory and results are durable.**
4. **Every guarantee is a mechanism** (gate, sandbox, diff check), never a sentence in a prompt.
5. **Production is a lock:** merge queue, release train, declarative guardrails.

## Architecture

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

With Docker only:

```bash
cp .env.example .env    # optional: ANTHROPIC_API_KEY or OPENAI_API_KEY for real agents
docker compose up       # console on http://localhost:3000, with a demo project and work item
```

[Getting started](docs/getting-started.md) goes from there to a real project: GitHub App, board,
GitOps, scaffolding and the first `agent-ready` ticket. [demo/](demo/README.md) runs real agents
on a single-node kind cluster, with a code project and an HR project on the same engine.

## Development

```bash
make setup      # uv sync + pnpm install
make ci         # lint, typing, unit tests: everything that blocks a PR
make demo       # end-to-end scenario in memory (fakes, no cluster)
make dev-up     # kind + Tilt: the complete dev cluster
make dev-seed   # demo organisation, project, tickets and a train
```

Front-end work on fakes (the API records the workflows it would start and runs none):

```bash
make api                   # API on :8000
pnpm -C apps/web dev       # console on :3000
```

[Development](docs/development.md) covers the dependencies-only stack (`make compose-up`), port
overrides and the tests that touch the real world.

## Repository map

| Path | Content |
| :-- | :-- |
| `packages/contracts` | **Source of truth**: JSON Schemas, OpenAPI 3.1, generated Python/TS types |
| `packages/core` | Domain, workflow DSL, policy engine, gates, model resolution |
| `packages/adapters` | Tracker, SCM, CI, CD, executor, memory, gateway, notify — and their fakes |
| `packages/runner` | Headless ACP client, guardrails, DoD loop, stage result publication |
| `packages/tools-mcp` | MCP sidecar exposed to the agent |
| `packages/playbooks` | Prompts per role, and their evals |
| `packages/cli` | `tranibor` CLI |
| `apps/api` | FastAPI: REST, webhooks, SSE, internal API, OIDC, RLS |
| `apps/orchestrator` | Temporal workers and workflows |
| `apps/web` | Next.js console: board, runs, trains, findings, memory, admin |
| `charts/` | Helm charts: the platform and the outpost |
| `templates/` | Stack templates used to provision a project |
| `dev/` | kind, Tilt, docker compose, seed |
| `demo/` | Single-node bench with real agents |
| `tests/` | e2e (kind) and conformance (ACP backends, templates) |

## Contributing

Read [`AGENTS.md`](AGENTS.md) (it applies to humans too) and
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md). One story, one PR; contracts in
`packages/contracts` change only through a PR labelled `contract-change`.

## Licence

Apache 2.0 — see [LICENSE](LICENSE). Names and marks: [docs/TRADEMARK.md](docs/TRADEMARK.md).
