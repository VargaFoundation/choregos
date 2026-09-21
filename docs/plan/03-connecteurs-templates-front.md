# 3. Connecteurs, templates, front

## 3.1 Connecteurs (`packages/adapters`)

| Kind | Jour 1 | Phase 5–6 | Notes d'implémentation |
| :-- | :-- | :-- | :-- |
| `tracker` | **GitHub Issues + Projects v2** | Jira Cloud, GitLab Issues, Azure Boards | GitHub : GraphQL pour Projects v2 (`updateProjectV2ItemFieldValue`), REST pour issues/commentaires ; champs *Status*, *Coût (€)*, *Taille*, *Risque*, *Run* ; webhooks `issues`, `issue_comment`, `projects_v2_item`, `label`. Polling de secours toutes les 60 s (`list_candidates`) pour rattraper les webhooks perdus |
| `scm` | **GitHub** (App) | GitLab, Azure Repos | App GitHub `choregos-bot` : permissions Contents/Issues/Pull requests/Checks RW, Metadata R ; tokens d'installation par dépôt ; merge queue via `enablePullRequestAutoMerge` ; check-runs `choregos/scope`, `choregos/evidence` |
| `ci` | **Tekton** | Jenkins, GitHub Actions, GitLab CI, Azure Pipelines | Tekton : `PipelineRun` déclenché par Triggers (`EventListener` GitHub) ; état via CloudEvents `dev.tekton.event.pipelinerun.{successful,failed}.v1` postés sur `/webhooks/tekton` ; logs via Tekton Results |
| `cd` | **Argo CD + Argo Rollouts** | Azure Container Apps, Azure DevOps Releases, Flux | Argo : `Application` par env ; promotion = PR sur le repo GitOps ; santé via API Argo + webhooks de notification ; Rollouts : `AnalysisTemplate`, `abort`, `promote` via API |
| `runtime` (executor) | **Tekton PipelineRun**, Job K8s | Azure Container Apps Jobs, ACI | Section 2.3 |
| `memory` | **Ecphoria** | pgvector (repli) | Section 4 |
| `gateway` | **LiteLLM** | — | Section 4 |
| `notify` | **Slack** | Teams, e-mail | Blocks Slack avec boutons *Approuver / Renvoyer* → `POST /decisions` |

Chaque connecteur : `config` (schéma JSON par type), `secret_ref` (référence External Secrets), `test()` (connexion, permissions), `health()` périodique, quotas et backoff (429 GitHub : file par installation).

Mapping des états DSL → tracker (`TrackerStateMapping`) : GitHub Projects v2 *Status* (option créée si absente) + label `choregos:<state>` ; Jira : transition de workflow par nom ; GitLab : labels scopés `choregos::state`.

## 3.2 Templates (`templates/`)

`manifest.yaml` d'un template :

```yaml
apiVersion: choregos/v1
kind: Template
metadata: { name: github-tekton-argo-k8s, version: 1.0.0, display: "GitHub · Tekton · Argo CD · Kubernetes" }
requires:
  connectors: [tracker: github-issues, scm: github, ci: tekton, cd: argocd, runtime: kubernetes]
  cluster_capabilities: [tekton>=1.9, argocd>=3.0, argo-rollouts>=1.8, gvisor?]
defaults:
  workflow: template:default-simple@1
  policy: preset:solo
  models: platform-defaults
  agent: claude-code         # OpenHands retiré, ADR 0011
inputs:                      # questions du wizard
  - { name: repo, type: github-repo, required: true }
  - { name: default_branch, type: string, default: main }
  - { name: language, type: enum, values: [python, node, go, java, dotnet, other] }
  - { name: envs, type: list, default: [dev, staging, prod] }
  - { name: cluster, type: cluster-ref }
steps:                       # exécutés par ProjectProvisioning, idempotents
  - github.install_app
  - github.ensure_labels
  - github.ensure_project_board: { fields: [Status, Cost, Size, Risk, Run] }
  - github.ensure_issue_template
  - gitops.write_project_manifests: { path: projects/{{slug}}/ }        # namespaces, quotas, netpol, SA, Tekton EventListener, Argo Applications
  - gitops.open_pr_or_commit
  - argocd.wait_synced: { apps: [choregos-project-{{slug}}] }
  - github.ensure_webhooks: { events: [issues, issue_comment, projects_v2_item, pull_request, check_suite] }
  - repo.scaffold_pr: { files: [AGENTS.md, .choregos/workflow.yaml, .choregos/policy.yaml, tekton/pipeline.yaml, deploy/kustomization.yaml, .github/PULL_REQUEST_TEMPLATE.md, CODEOWNERS] }
  - memory.create_tenant
  - memory.initial_import: { sources: [readme, docs, adr, closed_issues:365d, merged_prs:365d] }
  - gateway.create_team_and_budget
  - notify.test_message
scaffold: ./scaffold         # Jinja2
```

Templates planifiés : `github-actions-argo-k8s` (phase 5), `gitlab-ci-argo-k8s`, `jira-github-jenkins-k8s`, `azure-devops-aca`, `github-azure-aci` (phase 6). Un template est **testé** par `tests/conformance/templates/` : provisioning sur kind, ticket S de bout en bout.

## 3.3 Provisioning (`ProjectProvisioning`)

Workflow Temporal : une activité par `step`, idempotente, avec `provision.status` renvoyé au front en SSE (étape courante, log, erreur, action corrective proposée). Les ressources Kubernetes d'un projet ne sont **pas** créées par appel direct : l'activité `gitops.write_project_manifests` écrit dans `choregos-infra/projects/<slug>/` (Kustomize) et Argo CD applique (`ApplicationSet` par répertoire). Avantages : auditable, réversible, aucun droit cluster-admin dans l'API. La suppression d'un projet = suppression du répertoire + `prune`.

## 3.4 Front (`apps/web`)

Stack : Next.js 15 (App Router, RSC pour les listes, client components pour le temps réel), TypeScript strict, Tailwind + shadcn/ui, TanStack Query, React Flow (éditeur de workflow), Monaco (YAML avec schéma), `eventsource` pour SSE, next-intl (FR/EN), Auth.js (OIDC), tests Playwright.

Design : sobre, dense, orienté opérateur ; thème clair/sombre ; les états du DSL utilisent les libellés du projet (`display`) partout.

### Écrans

| Route | Contenu | API |
| :-- | :-- | :-- |
| `/` | Projets (cartes : état, coût du mois, tickets actifs, trains) | `GET /orgs/{org}/projects` |
| `/projects/new` | Wizard : template → inputs → connecteurs (OAuth GitHub App, cluster) → workflow (template ou YAML) → modèles/budgets → politique → récapitulatif → provisioning (SSE) | `POST /projects`, `PUT connectors`, `POST provision`, `GET provision` (SSE) |
| `/p/{slug}` | Vue d'ensemble : débit, cycle time, coût vs budget, taux de PR au premier passage, trains, alertes | `GET costs`, `GET work-items?stats` |
| `/p/{slug}/board` | Kanban = états du workflow ; carte : activité agent en direct (étape, tentative, coût courant), boutons *Approuver / Renvoyer / Répondre*, lien tracker | `GET work-items`, `POST decisions`, SSE events |
| `/p/{slug}/items/{id}` | Ticket : timeline (états, runs, décisions), tableau de coûts par étape, findings déposés, PR, transcripts | `GET work-items/{id}`, `GET timeline`, `GET runs` |
| `/p/{slug}/runs/{id}` | Run : journal ACP (messages, appels d'outils, permissions accordées/refusées), diff, tests, coût, *Rejouer l'étape* | `GET runs/{id}`, `GET events` (SSE), `GET diff` |
| `/p/{slug}/trains` | Par env : batch en attente, départ prévu, verrou, freeze, approbation, canary, historique | `GET releases`, `POST depart/freeze/approve` |
| `/p/{slug}/findings` | Triage : dédup, sévérité, *Rendre agent-ready*, *Doublon*, *Ignorer* | `GET findings`, `POST actions` |
| `/p/{slug}/memory` | Recherche hybride, faits en attente (proposés par agents), historique bi-temporel d'un fait, réimport | `GET memory/search`, `GET/POST pending` |
| `/p/{slug}/workflow` | Éditeur YAML (Monaco + schéma) + rendu React Flow (colonnes = états, icônes = acteurs, gates sur les arêtes) ; *Valider* ; *Ouvrir une PR* | `POST workflows/validate`, `PUT workflow` |
| `/p/{slug}/settings/*` | Connecteurs (test, rotation), modèles (profils, matrice d'évals), politique, membres et rôles, notifications, danger zone | `PUT connectors/models/policy`, `members` |
| `/admin/*` | Templates, backends agent (activer/désactiver, résultats de conformité), exécuteurs/clusters, gateway (clés, budgets), utilisateurs, audit | `platform/*`, `audit` |

Composants transverses : `StateBadge` (couleur par kind), `CostChip` (€, tooltip tokens), `ActorIcon` (agent/humain/système), `LiveLog` (virtualisé), `DecisionBar`.

Mode démo : `NEXT_PUBLIC_API_MODE=mock` sert des fixtures générées depuis l'OpenAPI (Prism) — le flux front avance sans l'API.

## 3.5 API (`apps/api`)

FastAPI + pydantic v2 (modèles générés depuis `packages/contracts`), SQLAlchemy 2 async + Alembic, `authlib` OIDC, RLS Postgres par organisation, rate limiting (slowapi), SSE (`sse-starlette`), OTel auto-instrumentation, structlog JSON.

Modules : `auth/`, `projects/`, `connectors/`, `workflows/`, `workitems/`, `runs/`, `releases/`, `findings/`, `memory/`, `costs/`, `templates/`, `admin/`, `webhooks/` (un routeur par source, vérification de signature, normalisation → `InboundEvent` → signal Temporal ou création de `WorkflowInterpreter`), `internal/` (JWT de run).

Règles : toute mutation écrit dans `audit_log` ; toute erreur externe (GitHub, Argo) est encapsulée avec `retry_after` ; les webhooks répondent en < 500 ms (traitement asynchrone via Temporal ou tâche de fond).

CLI (`packages/cli`) : `choregos login`, `projects list|create --template`, `items list|approve|reject|answer`, `runs tail <id>`, `trains status|depart|freeze`, `findings list`, `dev up|down|seed` (kind + Tilt), `workflow validate`.
