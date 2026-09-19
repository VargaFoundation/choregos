# Choregos

> χορηγός — celui qui organise le chœur et lui donne les moyens de jouer.

**Choregos** est la plateforme de delivery agentique de la [Varga Foundation](https://github.com/VargaFoundation) :
un ticket entre, une mise en production maîtrisée sort. Elle dirige un chœur d'agents de code
(OpenHands, Claude Code, Codex, Gemini CLI, Goose, OpenCode…) derrière un protocole unique (ACP),
leur donne un workspace jetable, un modèle, un budget et une partition (le *workflow*), puis
sérialise les déploiements derrière un *release train*.

Licence : Apache 2.0. Plan d'exécution complet : [`docs/plan/00-index.md`](docs/plan/00-index.md).

## Principes

1. **Le tracker est l'interface humaine.** GitHub Issues/Projects, Jira ou GitLab reste
   l'endroit où l'on décide ; Choregos y écrit l'état, le coût et les preuves.
2. **L'orchestrateur planifie, il n'appelle jamais un modèle.** Les workflows Temporal décident
   quelle étape lancer ; seuls les runners parlent aux agents.
3. **Le runner est jetable ; la mémoire et les résultats sont durables.**
4. **Toute garantie est un mécanisme** (gate, sandbox, vérification de diff), jamais une phrase
   de prompt.
5. **La prod est un verrou** : merge queue, release train, garde-fous déclaratifs.

## Architecture en une carte

```
tracker ──webhooks──▶ apps/api ──signaux──▶ apps/orchestrator (Temporal)
                         │                        │
                         │                        ├─ WorkflowInterpreter  (un ticket)
                         │                        ├─ ReleaseTrain         (un env)
                         │                        ├─ FindingsTriage       (un projet)
                         │                        ├─ ProjectProvisioning  (un projet)
                         │                        ├─ MemoryIngestion / EvalMatrix
                         │                        ▼
                         │                  Executor (Tekton | Job K8s | Docker local)
                         │                        ▼
                         └──internal API◀── packages/runner ──ACP──▶ agent ──MCP──▶ tools-mcp
                                                   │                    │
                                                   ▼                    ▼
                                             object store          LiteLLM ──▶ modèles
```

## Démarrage rapide

```bash
make setup          # uv sync + pnpm install
make ci             # lint + typage + tests unitaires
make dev-up         # kind + Tilt (cluster de dev complet)
make dev-seed       # org varga, projet demo, 10 tickets, un train
make demo           # scénario bout en bout en mémoire (fakes, sans cluster)
```

Sans Kubernetes :

```bash
docker compose -f dev/compose.yaml up -d      # Postgres, Temporal, LiteLLM, Ecphoria, Keycloak, MinIO
CHOREGOS_FAKES=1 uv run choregos-api          # API sur :8000
CHOREGOS_FAKES=1 uv run choregos-orchestrator # workers Temporal
pnpm -C apps/web dev                          # front sur :3000
```

## Carte du dépôt

| Chemin | Contenu |
| :-- | :-- |
| `packages/contracts` | **Source de vérité** : JSON Schemas, OpenAPI 3.1, types générés Python/TS |
| `packages/core` | Domaine, DSL de workflow (parse/validate/select), policy engine, gates, résolution de modèles |
| `packages/adapters` | tracker, scm, ci, cd, executor, memory, gateway, notify — et leurs `Fake*` |
| `packages/runner` | Client ACP headless, guardrails, boucle DoD, publication du `StageResult` |
| `packages/tools-mcp` | Sidecar MCP `choregos-tools` exposé à l'agent |
| `packages/playbooks` | Prompts par rôle + évals |
| `packages/cli` | CLI `choregos` |
| `apps/api` | FastAPI : REST, webhooks, SSE, API interne, OIDC, RLS |
| `apps/orchestrator` | Workers et workflows Temporal |
| `apps/web` | Next.js 15 : board, runs, trains, findings, mémoire, admin |
| `charts/choregos` | Helm umbrella de la plateforme |
| `templates/` | Templates de stack (provisioning d'un projet) |
| `dev/` | kind, Tilt, docker compose, seed |
| `tests/` | e2e (kind) et conformance (backends ACP, templates) |

## Contribuer

Lire `AGENTS.md` (instructions agents, aussi valables pour les humains), puis
`docs/plan/07-plan-parallelisation-conventions.md`. Une story = une PR.
Les contrats (`packages/contracts`) ne changent que par une PR taguée `contract-change`.
