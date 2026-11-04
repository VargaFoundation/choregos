# Choregos — complete execution plan (v3)

> Current plans (English): [PLAN-2026-10-12](PLAN-2026-10-12.md), [PLAN-2026-10-15](PLAN-2026-10-15.md) (stream S39), [PLAN-2026-10-16](PLAN-2026-10-16.md) (stream S40), [PLAN-2026-10-27-servicenow](PLAN-2026-10-27-servicenow.md) (stream S43, agents as plugins), [PLAN-2026-10-27](PLAN-2026-10-27.md) (streams S45-S47: English, MCP gateway, hardening).

> Agentic delivery platform of the **Varga Foundation**: a ticket goes in, a controlled production release comes out. This document is written to be **handed as is to Claude Code** (or to any ACP agent) as the single source: architecture, contracts, backlog, conventions, deployment. Version 3 — 18 September 2026.

## 0.1 How to use this plan

1. Create the `VargaFoundation/choregos` monorepo and copy this plan into `docs/plan/` (one file per section, `08-backlog.yaml` as is).
2. Copy `AGENTS.md` and `CLAUDE.md` from section 7.6 to the root.
3. Start **one Claude Code session per work stream** (section 7.2), each in its own `git worktree`, with this instruction: "Read `docs/plan/00-index.md`, then the section of your stream, then your stories in `docs/plan/08-backlog.yaml` (filter `stream: Sx`). Work story by story, one PR per story, tests included. Never modify `packages/contracts` without a dedicated PR tagged `contract-change`."
4. An **integrator** session reviews, runs the CI, merges through the merge queue and updates `docs/plan/STATUS.md`.
5. The sections are self-contained: an agent that reads only its own and the contracts (section 1) must be able to deliver.

## 0.2 The name

The foundation names its projects with a Greek word full of meaning: **Argus** (the guardian with a hundred eyes — the driver that sees into every warehouse), **Ecphoria** (ἐκφορία, recall into memory). The third project directs agents; it needs a name from the same family.

| Name | Meaning | Why | Pronunciation | Collision |
| :-- | :-- | :-- | :-- | :-- |
| **Choregos** (χορηγός) — **recommended** | The one who organises and leads the chorus in the theatre of Athens; the one who provides the means for the performance | The platform directs a chorus of agents (OpenHands, Claude Code, Codex, Gemini…) and gives them a workspace, a model, a budget and a score; a role, like Argus | ko-RÉ-gos | Low in tech; "sponsor" in modern Greek | <!-- lang: fr-ok -->
| **Teleia** (τελεία) | The full stop; completion | The original problem ("the task is never really finished"); same -ia ending as Ecphoria | té-LÉ-ya | Close to "Telia" (telecom) | <!-- lang: fr-ok -->
| **Poiesis** (ποίησις) | The act of bringing into being | From the idea to production | poï-É-sis | Art collectives | <!-- lang: fr-ok -->
| **Poreia** (πορεία) | The walk, the journey | The ticket's journey to production | po-RÉ-ya | Low | <!-- lang: fr-ok -->
| **Eunomia** (εὐνομία) | Good order, good governance | The policy layer and the serialised train | eu-no-MI-a | Low | <!-- lang: fr-ok -->
| **Entelecheia** (ἐντελέχεια) | The realisation of a potential (Aristotle) | An idea made real | en-té-lé-KHÉ-ya | Low, but long | <!-- lang: fr-ok -->

Working decision: **Choregos**. Optional vocabulary for communication (never in the code, which keeps technical English): *Skene* (σκηνή, the backstage: the workspace), *Stolos* (στόλος, the fleet that sails together: the release train batch), *Kleis* (κλείς, the key: the production lock), *Heurema* (εὕρημα, the find: a finding), *Taxis* (τάξις, the ordering: the workflow DSL). To check before announcing: INPI/EUIPO (classes 9, 42), domains `choregos.dev` / `.io`, GitHub org, PyPI `choregos`, npm `@choregos/*`, Docker Hub / ghcr. Renaming = one `sed` over the monorepo; nothing in this plan depends on it structurally.

## 0.3 Decisions made (do not reopen without a `decision` PR)

| # | Decision | Value |
| :-- | :-- | :-- |
| D1 | Licence | Apache 2.0 (aligned with Argus and Ecphoria) |
| D2 | Agent contract | ACP (Agent Client Protocol); default agent OpenHands (replaced by Claude Code, [ADR 0011](../adr/0011-openhands-removed.md)); Claude Code, Codex, Gemini CLI, Goose, OpenCode, Copilot CLI as optional backends |
| D3 | Orchestration engine | Temporal (self-hosted via Helm + CloudNativePG; Temporal Cloud possible without changing the code) |
| D4 | Languages | Python 3.12 (`uv`, FastAPI, Temporal SDK, pydantic v2) for API, orchestrator, runner, adapters, CLI; TypeScript (Next.js 15, React 19) for the front end; Rust for Ecphoria (existing) |
| D5 | Day-1 executor | Tekton Pipelines on Kubernetes; abstract `Executor`, K8s Job as fallback |
| D6 | Model gateway | LiteLLM proxy; one virtual key per run; cost counted at the gateway |
| D7 | Memory | Ecphoria (refocused on memory + knowledge base); pgvector fallback |
| D8 | Day-1 tracker / SCM / CI / CD | GitHub Issues + Projects v2 / GitHub / Tekton / Argo CD + Argo Rollouts |
| D9 | Platform deployment | Kubernetes, Helm umbrella `charts/choregos`, GitOps Argo CD app-of-apps from `choregos-infra` |
| D10 | Database | PostgreSQL 16 via CloudNativePG, `pgvector` extension |
| D11 | Auth | OIDC (Keycloak in dev, the organisation's IdP in prod); RBAC with 5 roles |
| D12 | Observability | OpenTelemetry → Prometheus / Loki / Tempo / Grafana (kube-prometheus-stack); Azure Monitor as an export option; Langfuse **optional** (phase 2) |
| D13 | Repository instructions | `AGENTS.md` (standard); `CLAUDE.md` = symbolic link |
| D14 | Default workflow | `default-simple` for a project with a repository, `default-request` without one (ADR 0110); production always through the release train |
| D15 | Monorepo | `VargaFoundation/choregos`; GitOps infra in `VargaFoundation/choregos-infra`; Ecphoria stays in its own repository |

## 0.4 Map of the document

| Section | File | Content | Streams concerned |
| :-- | :-- | :-- | :-- |
| 1 | `01-architecture-and-contracts.md` | Architecture, repository, data model, DSL, StageInput/StageResult, events, API, adapter interfaces | all |
| 2 | `02-orchestrator-agents-runner.md` | Temporal workflows, ACP client, backends, runner, Tekton Task, sandbox, playbooks | S1, S2 |
| 3 | `03-connectors-templates-front.md` | Connectors, templates, provisioning, front end, API | S3, S5, S6, S8 |
| 4 | `04-models-costs-memory.md` | Gateway, profiles, costs in tickets, Ecphoria (study, improvements, integration) | S4, S10, S11 |
| 5 | `05-release-train-findings.md` | Release train, GitOps, findings | S9, S10 |
| 6 | `06-kubernetes-deployment.md` | Deployment of the platform on Kubernetes: charts, namespaces, HA, backups, security, environments | S7 |
| 7 | `07-delivery-parallelisation-conventions.md` | Parallel streams, milestones, sprints, Claude Code operating mode, `AGENTS.md`/`CLAUDE.md`, DoD, risks | all |
| 8 | `08-backlog.yaml` | Machine-readable epics and stories | all |

## 0.5 Glossary

- **WorkItem**: canonical representation of a ticket, whatever the tracker.
- **Workflow (DSL)**: state machine declared in YAML per project; states, transitions, actors, gates.
- **Stage / run**: execution of an "agent" transition by a runner; has an ID, a cost, a transcript, a `StageResult`.
- **Gate**: deterministic condition on a transition (green CI, scans OK…).
- **Release train**: singleton workflow per environment that serialises deployments.
- **Finding**: problem discovered out of scope, turned into a linked ticket.
- **Context pack**: selection of memory, linked tickets and incidents injected into a stage under a token budget.
- **Agent backend**: ACP implementation (OpenHands, Claude Code…) launched in the workspace.
- **Executor**: what runs a run (Tekton PipelineRun, K8s Job, ACA Job).
- **Template (stack)**: combination of connectors + workflow + policy + scaffolding, instantiable into a project.
