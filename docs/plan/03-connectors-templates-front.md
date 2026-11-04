# 3. Connectors, templates, front end

## 3.1 Connectors (`packages/adapters`)

| Kind | Day 1 | Phase 5–6 | Implementation notes |
| :-- | :-- | :-- | :-- |
| `tracker` | **GitHub Issues + Projects v2** | Jira Cloud, GitLab Issues, Azure Boards | GitHub: GraphQL for Projects v2 (`updateProjectV2ItemFieldValue`), REST for issues/comments; fields *Status*, *Cost (€)*, *Size*, *Risk*, *Run* (French names from before S22-21, renamed at provisioning); webhooks `issues`, `issue_comment`, `projects_v2_item`, `label`. Fallback polling every 60 s (`list_candidates`) to catch up on lost webhooks |
| `scm` | **GitHub** (App) | GitLab, Azure Repos | GitHub App `choregos-bot`: Contents/Issues/Pull requests/Checks RW, Metadata R permissions; installation tokens per repository; merge queue via `enablePullRequestAutoMerge`; check-runs `choregos/scope`, `choregos/evidence` |
| `ci` | **Tekton** | Jenkins, GitHub Actions, GitLab CI, Azure Pipelines | Tekton: `PipelineRun` triggered by Triggers (GitHub `EventListener`); state via CloudEvents `dev.tekton.event.pipelinerun.{successful,failed}.v1` posted to `/webhooks/tekton`; logs via Tekton Results |
| `cd` | **Argo CD + Argo Rollouts** | Azure Container Apps, Azure DevOps Releases, Flux | Argo: one `Application` per env; promotion = PR on the GitOps repo; health via the Argo API + notification webhooks; Rollouts: `AnalysisTemplate`, `abort`, `promote` via the API |
| `runtime` (executor) | **Tekton PipelineRun**, K8s Job | Azure Container Apps Jobs, ACI | Section 2.3 |
| `memory` | **Ecphoria** | pgvector (fallback) | Section 4 |
| `gateway` | **LiteLLM** | — | Section 4 |
| `notify` | **Slack** | Teams, email | Slack Blocks with *Approve / Send back* buttons → `POST /decisions` |

Each connector: `config` (JSON schema per type), `secret_ref` (External Secrets reference), `test()` (connection, permissions), periodic `health()`, quotas and backoff (GitHub 429: one queue per installation).

DSL states → tracker mapping (`TrackerStateMapping`): GitHub Projects v2 *Status* (option created if absent) + label `choregos:<state>`; Jira: workflow transition by name; GitLab: scoped labels `choregos::state`.

## 3.2 Templates (`templates/`)

A template's `manifest.yaml`:

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
  agent: claude-code         # OpenHands removed, ADR 0011
inputs:                      # wizard questions
  - { name: repo, type: github-repo, required: true }
  - { name: default_branch, type: string, default: main }
  - { name: language, type: enum, values: [python, node, go, java, dotnet, other] }
  - { name: envs, type: list, default: [dev, staging, prod] }
  - { name: cluster, type: cluster-ref }
steps:                       # run by ProjectProvisioning, idempotent
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

Planned templates: `github-actions-argo-k8s` (phase 5), `gitlab-ci-argo-k8s`, `jira-github-jenkins-k8s`, `azure-devops-aca`, `github-azure-aci` (phase 6). A template is **tested** by `tests/conformance/templates/`: provisioning on kind, a size-S ticket end to end.

## 3.3 Provisioning (`ProjectProvisioning`)

Temporal workflow: one activity per `step`, idempotent, with `provision.status` sent back to the front end over SSE (current step, log, error, suggested corrective action). A project's Kubernetes resources are **not** created by a direct call: the `gitops.write_project_manifests` activity writes into `choregos-infra/projects/<slug>/` (Kustomize) and Argo CD applies it (one `ApplicationSet` per directory). Advantages: auditable, reversible, no cluster-admin right in the API. Deleting a project = deleting the directory + `prune`.

## 3.4 Front end (`apps/web`)

Stack: Next.js 15 (App Router, RSC for lists, client components for real time), strict TypeScript, Tailwind + shadcn/ui, TanStack Query, React Flow (workflow editor), Monaco (YAML with schema), `eventsource` for SSE, next-intl (FR/EN), Auth.js (OIDC), Playwright tests.

Design: sober, dense, operator-oriented; light/dark theme; the DSL states use the project's labels (`display`) everywhere.

### Screens

| Route | Content | API |
| :-- | :-- | :-- |
| `/` | Projects (cards: state, cost of the month, active tickets, trains) | `GET /orgs/{org}/projects` |
| `/projects/new` | Wizard: template → inputs → connectors (GitHub App OAuth, cluster) → workflow (template or YAML) → models/budgets → policy → summary → provisioning (SSE) | `POST /projects`, `PUT connectors`, `POST provision`, `GET provision` (SSE) |
| `/p/{slug}` | Overview: throughput, cycle time, cost vs budget, first-pass PR rate, trains, alerts | `GET costs`, `GET work-items?stats` |
| `/p/{slug}/board` | Kanban = workflow states; card: live agent activity (stage, attempt, current cost), buttons *Approve / Send back / Answer*, tracker link | `GET work-items`, `POST decisions`, SSE events |
| `/p/{slug}/items/{id}` | Ticket: timeline (states, runs, decisions), cost table per stage, filed findings, PR, transcripts | `GET work-items/{id}`, `GET timeline`, `GET runs` |
| `/p/{slug}/runs/{id}` | Run: ACP log (messages, tool calls, permissions granted/refused), diff, tests, cost, *Replay the stage* | `GET runs/{id}`, `GET events` (SSE), `GET diff` |
| `/p/{slug}/trains` | Per env: pending batch, planned departure, lock, freeze, approval, canary, history | `GET releases`, `POST depart/freeze/approve` |
| `/p/{slug}/findings` | Triage: dedup, severity, *Make agent-ready*, *Duplicate*, *Ignore* | `GET findings`, `POST actions` |
| `/p/{slug}/memory` | Hybrid search, pending facts (proposed by agents), bi-temporal history of a fact, reimport | `GET memory/search`, `GET/POST pending` |
| `/p/{slug}/workflow` | YAML editor (Monaco + schema) + React Flow rendering (columns = states, icons = actors, gates on the edges); *Validate*; *Open a PR* | `POST workflows/validate`, `PUT workflow` |
| `/p/{slug}/settings/*` | Connectors (test, rotation), models (profiles, eval matrix), policy, members and roles, notifications, danger zone | `PUT connectors/models/policy`, `members` |
| `/admin/*` | Templates, agent backends (enable/disable, conformance results), executors/clusters, gateway (keys, budgets), users, audit | `platform/*`, `audit` |

Cross-cutting components: `StateBadge` (colour per kind), `CostChip` (€, token tooltip), `ActorIcon` (agent/human/system), `LiveLog` (virtualised), `DecisionBar`.

Demo mode: `NEXT_PUBLIC_API_MODE=mock` serves fixtures generated from the OpenAPI (Prism) — the front-end stream moves forward without the API.

## 3.5 API (`apps/api`)

FastAPI + pydantic v2 (models generated from `packages/contracts`), SQLAlchemy 2 async + Alembic, `authlib` OIDC, Postgres RLS per organisation, rate limiting (slowapi), SSE (`sse-starlette`), OTel auto-instrumentation, structlog JSON.

Modules: `auth/`, `projects/`, `connectors/`, `workflows/`, `workitems/`, `runs/`, `releases/`, `findings/`, `memory/`, `costs/`, `templates/`, `admin/`, `webhooks/` (one router per source, signature verification, normalisation → `InboundEvent` → Temporal signal or creation of a `WorkflowInterpreter`), `internal/` (run JWT).

Rules: every mutation writes to `audit_log`; every external error (GitHub, Argo) is wrapped with `retry_after`; webhooks answer in < 500 ms (asynchronous processing via Temporal or a background task).

CLI (`packages/cli`): `choregos login`, `projects list|create --template`, `items list|approve|reject|answer`, `runs tail <id>`, `trains status|depart|freeze`, `findings list`, `dev up|down|seed` (kind + Tilt), `workflow validate`.
