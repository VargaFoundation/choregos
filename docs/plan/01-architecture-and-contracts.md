# 1. Architecture and contracts

The contracts of this section are **frozen at the end of week 1** (milestone M0). They live in `packages/contracts/`: JSON Schemas (`schemas/*.json`), OpenAPI (`openapi.yaml`), generated types (`python/choregos_contracts/`, `ts/`). Any change goes through a PR tagged `contract-change`, reviewed by the integrator stream, and triggers the regeneration of the types.

## 1.1 Overview

```mermaid
flowchart LR
  subgraph FRONT[Front end + API]
    UI[web — Next.js]
    API["api — FastAPI<br/>REST, webhooks, SSE, internal"]
  end
  UI --> API
  subgraph TRK[Trackers / SCM]
    GH[GitHub Issues + Projects v2 + PR]
    JI[Jira]
    GL[GitLab]
  end
  GH -->|webhooks| API
  JI -->|webhooks| API
  GL -->|webhooks| API
  subgraph ORCH[orchestrator — Temporal workers]
    WF[WorkflowInterpreter]
    RT[ReleaseTrain]
    FT[FindingsTriage]
    PROV[ProjectProvisioning]
  end
  API <-->|Temporal client| ORCH
  PG[("PostgreSQL + pgvector")]
  API --> PG
  ORCH --> PG
  subgraph EXEC[Tekton on Kubernetes]
    TR[PipelineRun agent-stage]
    CI[PipelineRun CI]
  end
  WF -->|creates| TR
  TR -->|internal API| API
  GH -->|Tekton Triggers| CI
  CI -->|CloudEvents| API
  subgraph WS[Workspace]
    RUNNER[runner — ACP client]
    AG["agent ACP<br/>OpenHands, Claude Code, Codex, Gemini, Goose, OpenCode"]
    MCP[choregos-tools MCP sidecar]
  end
  TR --> RUNNER
  RUNNER -->|stdio JSON-RPC| AG
  AG -->|MCP| MCP
  MCP --> API
  GW[LiteLLM]
  AG --> GW
  GW --> LLM["Anthropic, OpenAI, Foundry, Vertex, vLLM"]
  MEM[Ecphoria]
  AG -->|MCP| MEM
  ORCH --> MEM
  RT --> GITOPS[GitOps repo]
  GITOPS --> ARGO[Argo CD + Rollouts]
  ARGO -->|webhooks| API
  OTEL[OTel Collector] --> OBS["Prometheus, Loki, Tempo, Grafana"]
  RUNNER --> OTEL
  API --> OTEL
  ORCH --> OTEL
  GW --> OTEL
```

Principles: the tracker is the human interface; the orchestrator plans and never calls a model; the runner is disposable, memory and results are durable; every guarantee is a mechanism (gate, sandbox, diff check), never a sentence in a prompt; production is a lock.

## 1.2 Monorepo `VargaFoundation/choregos`

```
choregos/
├── AGENTS.md  CLAUDE.md -> AGENTS.md  README.md  LICENSE  Makefile  justfile
├── docs/plan/                      # this plan
├── packages/
│   ├── contracts/                  # SOURCE OF TRUTH of the interfaces (schemas, openapi, generated types)
│   ├── core/                       # Python: domain models, DSL (parse/validate), policy engine, model resolution
│   ├── adapters/                   # Python: tracker/, scm/, ci/, cd/, executor/, memory/, gateway/, notify/ (+ fakes for tests)
│   ├── runner/                     # Python: ACP client, backends (launch specs), guardrails, result validation → `choregos-runner` image
│   ├── tools-mcp/                  # Python: `choregos-tools` sidecar MCP server (report_finding, ask_human…)
│   ├── playbooks/                  # prompts per role, variables, evals
│   └── cli/                        # `choregos` CLI (typer): projects, runs, trains, dev
├── apps/
│   ├── api/                        # FastAPI: REST, webhooks, SSE, internal, auth OIDC
│   ├── orchestrator/               # Temporal workers: workflows + activities
│   └── web/                        # Next.js: front end
├── templates/
│   └── github-tekton-argo-k8s/     # manifest.yaml, tekton/, argo/, k8s/, scaffold/
├── charts/
│   └── choregos/                   # Helm umbrella (api, web, orchestrator, tools, migrations, runner config)
├── tests/
│   ├── e2e/                        # end-to-end scenarios on kind
│   └── conformance/                # ACP backend + template conformance suite
├── dev/                            # kind config, Tiltfile, docker-compose (Postgres, Temporal dev, LiteLLM, Ecphoria)
└── .github/workflows/              # monorepo CI (lint, tests, images, charts, nightly e2e)
```

Separate repository `VargaFoundation/choregos-infra`: Argo CD app-of-apps bootstrap, per-environment values, manifests of the provisioned projects (section 6).

## 1.3 Data model (PostgreSQL)

Conventions: UUID v7 as primary key, `created_at`/`updated_at` everywhere, `jsonb` for schema-validated documents, Alembic migrations in `apps/api/src/choregos_api/migrations/` (run by `python -m choregos_api.migrer`). All tables carrying project data have `project_id` and are covered by RLS (`SET app.current_org`).

| Table | Key columns | Notes |
| :-- | :-- | :-- |
| `organizations` | id, slug, name, settings jsonb | |
| `users` | id, oidc_sub, email, display_name, last_login_at | |
| `memberships` | user_id, org_id, project_id nullable, role enum(org_admin, project_owner, developer, release_captain, viewer) | project_id null = org role |
| `projects` | id, org_id, slug, name, template_ref, config jsonb (`Project` schema), status enum(draft, provisioning, active, suspended, archived) | |
| `connectors` | id, project_id, kind enum(tracker, scm, ci, cd, runtime, memory, notify, gateway), type, config jsonb, secret_ref, status, last_check_at, last_error | secrets never in clear text |
| `workflow_defs` | id, project_id, name, version int, source enum(repo, platform, template), yaml text, json jsonb, checksum, is_active | pinned by work_item |
| `policies` | id, project_id, version, yaml, json, is_active | |
| `model_profiles` | id, scope enum(platform, project), project_id nullable, name, litellm_model, params jsonb, validated_backends text[] | |
| `templates` | id, name, version, manifest jsonb, repo_url, is_published | |
| `work_items` | id, project_id, tracker_key, title, body_snapshot, size enum(S,M,L,XL), risk enum(low,medium,high), state, workflow_def_id, policy_id, temporal_wf_id, created_by, closed_at, totals jsonb (tokens_in, tokens_out, tokens_cached, cost_usd, cost_eur, duration_s) | `state` = DSL state name |
| `runs` | id, work_item_id, project_id, transition_id, stage_role, attempt, actor, backend, model, executor_kind, executor_ref, status enum(queued, running, succeeded, failed, cancelled, timed_out), started_at, ended_at, tokens jsonb, cost_usd, gateway_key_id, result jsonb (`StageResult`), transcript_url, context_pack_url, playbook_checksum | |
| `run_events` | id, run_id, seq, type, payload jsonb, ts | ACP log (append-only, partitioned by month) |
| `human_requests` | id, work_item_id, transition_id, kind enum(approval, question, scope_change), payload, requested_at, due_at, decided_by, decided_at, decision jsonb | |
| `events` | id, project_id, work_item_id nullable, type, payload jsonb, ts | internal bus (append-only) |
| `findings` | id, project_id, origin_work_item_id, origin_run_id, title, type, severity, evidence, suggested_fix, estimate, status enum(pending, created, duplicate, dismissed), created_work_item_id, duplicate_of, embedding vector(1536) | index HNSW |
| `releases` | id, project_id, env, batch_no, status enum(collecting, departing, staging, awaiting_approval, promoting, verifying, done, rolled_back, frozen), items jsonb, started_at, ended_at, approved_by, verdict jsonb, notes text | |
| `deployments` | id, release_id, env, revision, cd_ref, status, started_at, ended_at, analysis jsonb | |
| `cost_ledger` | id, project_id, work_item_id, run_id, provider, model, tokens_in, tokens_out, tokens_cached, cost_usd, cost_eur, fx_rate, ts | source of the aggregates |
| `audit_log` | id, actor_id, actor_kind enum(user, agent, system), action, target_type, target_id, payload, ts | |
| `api_tokens` | id, user_id, name, hash, scopes, expires_at | CLI |

Materialised views refreshed by cron: `v_project_costs_daily`, `v_workitem_cycle_time`, `v_first_pass_merge_rate`, `v_dora`.

## 1.4 The workflow DSL — schema

File `schemas/workflow.schema.json` (normative summary):

```yaml
apiVersion: const "choregos/v1"
kind: const "Workflow"
metadata: { name: slug, version: int, description?: string, extends?: "template:<name>@<ver>" }
actors: map<actorId, Actor>
  Actor (agent): { type: "agent", role: enum[triage, refine, plan, implement, verify, review, fix_ci, address_review, release_notes, verify_prod, custom],
                   model: "profile:<name>" | "profile:by_size" | "<litellm_model>", backend?: string, fresh_context?: bool,
                   max_turns?: int, max_minutes?: int, playbook?: string }
  Actor (human): { type: "human", group: string, sla_hours?: int, escalate_to?: string }
  Actor (system): { type: "system" }
states: map<stateId, State>
  State: { display: string, tracker?: { status?: string, label?: string }, terminal?: bool, kind?: enum[work, wait, terminal] }
transitions: list<Transition>
  Transition: { id?: string, from: stateId | "*agent", to: stateId,
                by?: actorId,            # exclusive with via
                via?: "release_train",   # → train: { env, approval?: actorId, auto_sync?: bool }
                gates?: list<GateRef>, outputs?: list<string>, inputs?: list<string>,
                on_fail?: Retry, on_reject?: stateId, on_changes_requested?: Retry,
                review?: { agents?: list<actorId>, humans?: { group, required: "always"|"by_policy"|"never" } },
                timeout_hours?: int }
  Retry: { to: stateId, max_attempts: int, escalate_to: stateId }
  GateRef: string | { name: string, params: map }
defaults: { from_any_agent_state?: { on_question: stateId, on_budget_exceeded: stateId, on_timeout: stateId },
            needs_human?: { on_answer: "resume", on_abandon: stateId } }
```

Static validation rules (blocking errors): exactly one initial state (the first declared); at least one reachable terminal state; no orphan state; every return transition carries `max_attempts`; every `deployed_prod*` state is reachable only `via: release_train`; the referenced actors exist; the gates exist in the registry; the referenced `profile:` is resolved at pinning time. Warnings: no `needs_human` state, no `verify` before `pr_open`.

Registry gates (`packages/core/gates/`): `ci_green`, `scans_ok`, `evidence_present`, `scope_respected`, `review_approved`, `coverage_delta_min(x)`, `diff_size_max(n)`, `no_secrets`, `provenance_signed`, `external(url)`.

Delivered templates: `full-auto`, `default-simple`, `advanced` (complete definitions in `packages/core/dsl/templates/`).

## 1.5 `StageInput` (orchestrator → runner)

```json
{
  "run_id": "01J…", "attempt": 1,
  "project": { "slug": "billing-api", "org": "acme" },
  "work_item": { "key": "acme/billing-api#123", "title": "…", "body": "…", "url": "…", "size": "M", "risk": "low",
                 "links": { "spec_comment": "…", "plan_comment": "…", "pr": null } },
  "transition": { "id": "t-implement", "role": "implement", "from": "ready", "to": "in_progress",
                  "outputs": ["branch", "commits"], "inputs": ["spec", "allowed_paths"] },
  "repo": { "url": "https://github.com/acme/billing-api.git", "base_branch": "main", "work_branch": "choregos/123-slug",
            "clone_depth": 50 },
  "agent": { "backend": "openhands", "launch": { "command": ["openhands", "acp"], "env": {}, "files": {} } },
  "model": { "litellm_model": "anthropic/claude-sonnet-5", "base_url": "http://litellm.choregos-gateway:4000",
             "api_format": "openai", "params": { "temperature": 0 } },
  "gateway_key": "sk-…",                       # the run's virtual key, budget = the stage's budget
  "budget": { "usd": 12.0, "max_turns": 80, "max_minutes": 60 },
  "allowed_paths": ["src/orders/**", "tests/orders/**"],
  "context_pack_url": "s3://…/runs/01J…/context.json",
  "playbook": { "ref": "implement@sha256:…", "prompt_url": "s3://…" },
  "tools": { "mcp": { "choregos": { "url": "http://localhost:7777/mcp" }, "memory": { "url": "http://ecphoria.choregos-memory:8432/mcp" } } },
  "permissions": { "write_paths": ["src/orders/**", "tests/orders/**"], "deny_commands": ["kubectl", "terraform apply", "git push --force"] },
  "callbacks": { "api_url": "https://api.choregos.acme.dev/internal", "run_token": "eyJ…" }
}
```

## 1.6 `StageResult` (runner → orchestrator)

```json
{
  "schema": "choregos/StageResult/v1",
  "status": "done | blocked | needs_human | failed",
  "summary": "…",
  "outputs": { "size": "M", "risk": "low", "allowed_paths": ["…"], "spec_markdown": "…", "plan_markdown": "…" },
  "artifacts": { "branch": "…", "commits": ["…"], "pr_url": "…", "transcript_url": "…", "reports": { "tests": "…", "coverage": "…" } },
  "evidence": { "tests_passed": true, "tests_run": 412, "tests_failed": 0, "coverage_delta": 1.2, "lint": "ok", "typecheck": "ok", "security_scan": "ok" },
  "questions": [ { "text": "…", "options": ["…"] } ],
  "findings": [ { "title": "…", "type": "perf|bug|security|tech-debt|docs|flaky-test", "severity": "low|medium|high|critical",
                  "evidence": "path:line", "suggested_fix": "…", "estimate": "S|M|L" } ],
  "scope_changes_requested": [ { "paths": ["…"], "justification": "…" } ],
  "diagnostics": { "turns": 43, "tool_calls": 118, "permission_denials": 2, "duration_s": 1260, "agent_exit": "normal" }
}
```

The cost is **not** in `StageResult`: the orchestrator reads it from the gateway (section 4). The agent writes `.choregos/result.json`; the runner validates it, completes it (`artifacts`, `evidence`, `diagnostics`) and posts it.

## 1.7 Events

CloudEvents 1.0 format (`type`, `source`, `subject`, `time`, `data`). Persisted in `events`, published over SSE to the front end, exported as OTel logs.

| Type | Emitter | Data |
| :-- | :-- | :-- |
| `choregos.workitem.created` / `.state_changed` / `.closed` | orchestrator | key, from, to, by |
| `choregos.workitem.human_requested` / `.human_decided` | orchestrator / API | kind, payload, decision |
| `choregos.run.queued` / `.started` / `.progress` / `.finished` | orchestrator / runner | run_id, stage, status, tokens, cost |
| `choregos.finding.reported` / `.created` / `.duplicate` / `.dismissed` | runner / triage | finding |
| `choregos.release.collected` / `.departed` / `.staged` / `.approval_requested` / `.promoted` / `.verified` / `.rolled_back` / `.frozen` | train | release |
| `choregos.cost.recorded` | orchestrator | ledger entry |
| `choregos.project.provisioning.step` / `.completed` / `.failed` | provisioning | step, status |

Inbound events normalised by the adapters (`InboundEvent`): `tracker.item.created|updated|moved|commented|labeled`, `scm.pr.opened|synchronized|review_submitted|merged|closed`, `scm.check.completed`, `ci.run.started|succeeded|failed`, `cd.app.synced|degraded|rollout.completed|rollout.aborted`, `alert.fired|resolved`.

## 1.8 API (OpenAPI — summary)

Base `/api/v1`. Auth: OIDC session (cookie) or `Authorization: Bearer <api_token>`. Cursor pagination. RFC 9457 errors.

| Resource | Endpoints |
| :-- | :-- |
| Session | `GET /me`, `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` |
| Projects | `GET/POST /orgs/{org}/projects`, `GET/PATCH/DELETE /projects/{id}`, `POST /projects/{id}/provision`, `GET /projects/{id}/provision`, `POST /projects/{id}/suspend` |
| Connectors | `GET /projects/{id}/connectors`, `PUT /projects/{id}/connectors/{kind}`, `POST /projects/{id}/connectors/{kind}/test`, `GET /connectors/types` |
| Workflow / policy | `GET/PUT /projects/{id}/workflow`, `POST /workflows/validate`, `GET /workflows/templates`, `GET/PUT /projects/{id}/policy` |
| Models | `GET /platform/models`, `GET/PUT /projects/{id}/models`, `GET /projects/{id}/models/matrix` (backend × model evals) |
| Work items | `GET /projects/{id}/work-items`, `GET /work-items/{id}`, `GET /work-items/{id}/timeline`, `POST /work-items/{id}/decisions` (approve, reject, answer, scope_change), `POST /work-items/{id}/actions` (pause, resume, stop, migrate, rerun_stage, mark_agent_ready) |
| Runs | `GET /work-items/{id}/runs`, `GET /runs/{id}`, `GET /runs/{id}/events` (SSE), `GET /runs/{id}/transcript`, `GET /runs/{id}/diff` |
| Trains | `GET /projects/{id}/releases`, `GET /releases/{id}`, `POST /projects/{id}/trains/{env}/depart`, `POST /projects/{id}/trains/{env}/freeze`, `POST /projects/{id}/trains/{env}/unfreeze`, `POST /releases/{id}/approve`, `POST /releases/{id}/abort` |
| Findings | `GET /projects/{id}/findings`, `POST /findings/{id}/actions` (create_ticket, mark_duplicate, dismiss, agent_ready) |
| Memory | `GET /projects/{id}/memory/search?q=`, `GET/POST /projects/{id}/memory/pending`, `POST /projects/{id}/memory/reimport` |
| Costs | `GET /projects/{id}/costs?group_by=day|stage|model|backend|size`, `GET /orgs/{org}/costs` |
| Templates | `GET /templates`, `GET /templates/{name}`; admin `POST/PUT` |
| Admin | `GET/PUT /platform/backends`, `GET /platform/executors`, `GET /platform/gateway/keys`, `GET/POST /orgs/{org}/members`, `GET /audit` |
| Webhooks (inbound) | `POST /webhooks/github` (HMAC), `POST /webhooks/jira`, `POST /webhooks/gitlab`, `POST /webhooks/tekton` (CloudEvents), `POST /webhooks/argocd`, `POST /webhooks/alertmanager` |
| Internal (runner → API, run JWT) | `GET /internal/runs/{id}/input`, `POST /internal/runs/{id}/events` (batch), `POST /internal/runs/{id}/result`, `POST /internal/runs/{id}/findings`, `POST /internal/runs/{id}/scope-change`, `POST /internal/runs/{id}/question`, `GET /internal/runs/{id}/context`, `GET /internal/runs/{id}/ticket`, `GET /internal/runs/{id}/ci-logs` |

The front end consumes only this API; the CLI too. The orchestrator does not go through the API for the database (direct access via `packages/core`), but inbound webhooks go through the API, which signals Temporal.

## 1.9 Adapter interfaces (Python `Protocol`, `packages/adapters/base.py`)

```python
class TrackerAdapter(Protocol):
    async def fetch_item(self, key: str) -> WorkItemData: ...
    async def create_item(self, data: NewItem) -> str: ...
    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None: ...  # status and/or label
    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None: ...
    async def comment(self, key: str, markdown: str) -> str: ...
    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None: ...
    async def set_fields(self, key: str, fields: dict[str, Any]) -> None: ...  # cost, size, risk, run_url
    async def list_candidates(self, project: ProjectConfig) -> list[str]: ...  # fallback polling
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]: ...
    def verify_webhook(self, headers, body) -> bool: ...


class ScmAdapter(Protocol):
    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str: ...
    async def ensure_branch(self, repo: str, name: str, base: str) -> None: ...
    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef: ...
    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None: ...
    async def get_pr(self, ref: PrRef) -> PrState: ...  # checks, reviews, mergeable, diff files
    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None: ...
    async def enqueue_merge(self, ref: PrRef) -> None: ...
    async def compare(self, repo: str, base: str, head: str) -> DiffSummary: ...
    def parse_webhook(self, headers, body) -> list[InboundEvent]: ...


class CiAdapter(Protocol):
    async def status_for(self, repo: str, sha: str) -> CiStatus: ...
    async def logs(self, run_ref: str, tail: int = 500) -> str: ...
    async def trigger(self, repo: str, ref: str, pipeline: str) -> str: ...
    def parse_event(self, headers, body) -> list[InboundEvent]: ...


class CdAdapter(Protocol):
    async def current_revision(self, app: str) -> str: ...
    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef: ...  # GitOps PR
    async def health(self, app: str) -> Health: ...
    async def rollout_status(self, app: str) -> RolloutState: ...
    async def abort_rollout(self, app: str) -> None: ...
    async def set_sync_window(self, app: str, windows: list[Window]) -> None: ...


class Executor(Protocol):
    async def start(self, spec: StageJobSpec) -> ExecRef: ...
    async def status(self, ref: ExecRef) -> ExecStatus: ...
    async def logs(self, ref: ExecRef) -> AsyncIterator[str]: ...
    async def cancel(self, ref: ExecRef) -> None: ...


class AgentBackend(Protocol):
    name: str
    capabilities: frozenset[str]  # {"acp", "mcp", "agents_md", "structured_output"}

    def launch_spec(self, ctx: LaunchContext) -> LaunchSpec: ...  # command, env, files to write, MCP config
    def model_env(self, model: ModelRef) -> dict[str, str]: ...  # LLM_MODEL/LLM_BASE_URL or ANTHROPIC_*


class MemoryAdapter(Protocol):
    async def context_pack(
        self, project: str, query: str, paths: list[str], budget_tokens: int
    ) -> ContextPack: ...
    async def write_fact(self, project: str, fact: Fact) -> str: ...
    async def propose_fact(self, project: str, fact: Fact, provenance: Provenance) -> str: ...
    async def ingest_events(self, project: str, events: list[dict]) -> None: ...
    async def search(self, project: str, query: str, k: int) -> list[Memory]: ...


class GatewayAdapter(Protocol):
    async def mint_key(
        self, metadata: dict, budget_usd: float, ttl_s: int, models: list[str]
    ) -> VirtualKey: ...
    async def spend(self, key_id: str) -> Spend: ...  # tokens in/out/cached, cost_usd, requests
    async def revoke(self, key_id: str) -> None: ...
    async def list_models(self) -> list[GatewayModel]: ...


class Notifier(Protocol):
    async def send(self, channel: str, message: Message) -> None: ...
```

Each adapter has an in-memory `Fake*` implementation (`packages/adapters/fakes/`) used by the orchestrator and front-end tests (through the API in `CHOREGOS_FAKES=1` mode): this is what lets the streams work in parallel without waiting for the real connectors.

## 1.10 Security of the contracts

- Webhooks: HMAC (GitHub), shared secret (Jira/GitLab), signed CloudEvents or internal network (Tekton/Argo). Replay protected by a unique `delivery_id`.
- `run_token`: JWT signed by the API (ES256), `aud=internal`, `sub=run_id`, TTL = `max_minutes + 15`, scope limited to the run; the runner holds nothing else.
- Any write by an agent to the tracker goes through the internal API (never a broad GitHub token in the workspace).
