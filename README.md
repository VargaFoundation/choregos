# Choregos

> χορηγός — celui qui organise le chœur et lui donne les moyens de jouer.

**Choregos** est la plateforme de delivery agentique de la [Varga Foundation](https://github.com/VargaFoundation) :
un ticket entre, une mise en production maîtrisée sort. Elle dirige un chœur d'agents de code
(Claude Code, Codex, Gemini CLI, Goose, OpenCode…) derrière un protocole unique (ACP),
leur donne un workspace jetable, un modèle, un budget et une partition (le *workflow*), puis
sérialise les déploiements derrière un *release train*.

Licence : Apache 2.0. Plan d'exécution complet : [`docs/plan/00-index.md`](docs/plan/00-index.md).

> **English documentation** — [docs/en](docs/en/README.md) : concepts, deployment and
> usage. The reference documentation below is in French.

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

Si un port est déjà pris — un Postgres sur 5432, un Keycloak sur 8080 — chaque service
accepte une surcharge plutôt que de vous faire éditer le compose :

```bash
CHOREGOS_PORT_POSTGRES=15432 CHOREGOS_PORT_KEYCLOAK=18080 \
  docker compose -f dev/compose.yaml up -d
CHOREGOS_PORT=8001 CHOREGOS_FAKES=1 uv run choregos-api
```

`CHOREGOS_PORT_{POSTGRES,TEMPORAL,TEMPORAL_UI,LITELLM,KEYCLOAK,MINIO,MINIO_CONSOLE,ECPHORIA}`
pour la pile, `CHOREGOS_PORT` pour l'API, `PORT` pour le front (`PORT=3001 pnpm -C apps/web dev`).

L'image d'Ecphoria vit sur GHCR sous `VargaFoundation`. Si le paquet est privé, il faut
s'authentifier une fois avant le premier `up` :

```bash
echo "$GITHUB_TOKEN" | docker login ghcr.io -u <utilisateur> --password-stdin
```

## Une démonstration avec de vrais agents

`make demo` tourne en mémoire. Pour voir la plateforme travailler **pour de vrai** — un cluster
kind mono-nœud, des agents qui écrivent du code et poussent des branches — tout est dans
**[demo/README.md](demo/README.md)** : un projet de code et un projet RH, le second sur le même
moteur sans une ligne de code changée. Le README dit aussi ce que ce banc ne prouve pas.

## Intégrer un projet

Vous avez un dépôt et vous voulez que la plateforme le développe :
**[docs/demarrer-un-projet.md](docs/demarrer-un-projet.md)**. Le guide va du `make demo`
(le trajet complet en mémoire, sans rien installer) jusqu'au provisioning d'un vrai projet
— GitHub App, board, GitOps, scaffolding — et au premier ticket `agent-ready`.

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
| `demo/` | banc mono-nœud : deux workflows (code et RH), playbooks, git dans le cluster |
| `tests/` | e2e (kind) et conformance (backends ACP, templates) |

## Contribuer

Lire `AGENTS.md` (instructions agents, aussi valables pour les humains), puis
`docs/plan/07-plan-parallelisation-conventions.md`. Une story = une PR.
Les contrats (`packages/contracts`) ne changent que par une PR taguée `contract-change`.
