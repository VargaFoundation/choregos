# 4. Models, costs, memory

## 4.1 LiteLLM gateway (`choregos-gateway`)

Deployment: LiteLLM chart (2 replicas), dedicated Postgres (CNPG, `litellm` database) for keys and spend logs, Redis for the cache and routing, `master_key` as a secret. **Pinned** version and image verified by digest (compromised versions have existed: never follow `latest`).

Base configuration (`charts/choregos/values/gateway.yaml`):

```yaml
model_list:
  - { model_name: platform/strong,   litellm_params: { model: anthropic/claude-opus-5,     api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/strong,   litellm_params: { model: azure_ai/claude-opus-5,      api_base: os.environ/FOUNDRY_ENDPOINT, api_key: os.environ/FOUNDRY_KEY } }
  - { model_name: platform/standard, litellm_params: { model: anthropic/claude-sonnet-5,   api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/cheap,    litellm_params: { model: anthropic/claude-haiku-4-5,  api_key: os.environ/ANTHROPIC_API_KEY } }
  - { model_name: platform/aux,      litellm_params: { model: vertex_ai/gemini-flash-latest, vertex_project: os.environ/GCP_PROJECT } }
  - { model_name: platform/embed,    litellm_params: { model: openai/text-embedding-3-large, api_key: os.environ/OPENAI_API_KEY } }
  - { model_name: platform/local,    litellm_params: { model: openai/qwen-coder, api_base: http://vllm.choregos-gateway:8000/v1 } }
router_settings: { num_retries: 2, timeout: 600, fallbacks: [{ "platform/strong": ["platform/standard"] }] }
litellm_settings: { drop_params: true, callbacks: ["otel"], cache: true, cache_params: { type: redis } }
general_settings: { master_key: os.environ/LITELLM_MASTER_KEY, database_url: os.environ/DATABASE_URL, store_model_in_db: true }
```

Projects **add** their own entries (`model_list` per LiteLLM team, via the `/model/new` API); the platform exposes `GET /platform/models` = `GatewayAdapter.list_models()` + prices + capabilities (tool calling, vision, context).

Claude Code compatibility: the gateway serves `/v1/messages` and `/v1/messages/count_tokens` and relays `anthropic-beta`/`anthropic-version`; a conformance-suite test checks it at every version bump.

## 4.2 Profiles and resolution

```
profile requested by the actor ("profile:strong", "profile:by_size")
  → project.models.profiles[name]            (e.g. strong: anthropic/claude-opus-5)
  → otherwise platform.models.profiles[name] (platform defaults)
  → validation: the model is listed at the gateway, supports tool calling, is compatible with the backend (matrix), allowed by the policy
  → StageInput.model = { litellm_model, base_url, api_format(per backend), params }
```

`profile:by_size`: `S,M → standard; L → strong; XL → strong + max_turns×1.5`. A project can point at a local model (`platform/local`): the cost uses a configured **internal price** (€/M tokens) to stay comparable.

Backend × model matrix (`model_profiles.validated_backends`, fed by `EvalMatrix`): the front end refuses a combination that is not validated, with a link to the results; the admin can force it (`allow_unvalidated: true`).

## 4.3 Cost accounting

1. `mint_gateway_key`: LiteLLM `POST /key/generate` with `max_budget = budget.usd`, `duration = max_minutes + 30m`, `models = [litellm_model]`, `metadata = {project, work_item, run_id, transition, attempt, actor, backend}` and `tags`.
2. The agent backend receives **this** key; LiteLLM logs every request (spend logs: prompt/completion/cache tokens, cost from its price table or the internal price, latency, actual model after fallback) and **cuts off** at the cap (the agent receives a budget error → the runner ends `failed(reason=budget)`).
3. `collect_spend`: `GET /key/info` + `/spend/logs?request_id…` filtered by key → `Spend{tokens_in, tokens_out, tokens_cached, cost_usd, requests, models_used}`; write to `cost_ledger` (with the day's `fx_rate` for `cost_eur`); `revoke_key`.
4. Aggregates: `work_items.totals` updated per run; materialised views; `GET /costs`.
5. **Write to the ticket** (`update_ticket_status_comment`): a single comment identified by the `<!-- choregos:status -->` marker, rewritten at each stage, plus the board's structured fields (*Cost (€)*, *Size*, *Risk*, *Run* since S22-21).

```markdown
<!-- choregos:status -->
### Choregos — tracking
**Workflow** advanced v3 · **Size** M · **Risk** low · **State** Done, PR open · **PR** #456 · **Budget** €6.77 / €25

| # | Stage | Actor | Backend · model | Tokens in / out (cache) | Cost | Duration | Result |
|--:|:--|:--|:--|--:|--:|--:|:--|
| 1 | Proofreading/completion | agent refine | openhands · claude-sonnet-5 | 41 k / 3 k (28 k) | €0.31 | 2 min | spec ✓, 6 allowed paths |
| 2 | Approval | Augustin | — | — | — | 1 d 3 h | approved |
| 3 | In progress | agent implement | openhands · claude-sonnet-5 | 388 k / 22 k (301 k) | €2.74 | 21 min | 7 commits |
| 4 | Verification | agent verify | openhands · claude-sonnet-5 | 96 k / 4 k | €0.62 | 9 min | 412 tests ✓, coverage +1.2 |
| 5 | Review | agent review | claude-code · claude-opus-5 | 120 k / 6 k | €3.10 | 6 min | 2 remarks, fixed |
| 6 | Review | Marie (CODEOWNERS) | — | — | — | 4 h | approved |
| | **Total** | | | **645 k / 35 k** | **€6.77** | 1 d 8 h | estimated €8.20 (median M) |

Findings filed: #789 (perf, medium) · Run: https://choregos.acme.dev/p/billing-api/items/123 · Memory: 3 facts consulted, 1 proposed
```

Estimate before launch: median and p80 of tickets with the same (project, workflow, size) over 90 days; shown on the board and in the initial comment; alert if the actual exceeds p80.

## 4.4 Ecphoria — study

### State (README, September 2026)

Agentic memory platform in a single Rust binary (Apache 2.0): **bi-temporal** memories (`valid_from`/`valid_to`), **deterministic supersession** of contradictions (history kept), dedup/consolidation, BM25 + vector **hybrid search** by RRF (works without embeddings), forgetting by decay, optional LLM extraction, Mem0-compatible API, **native MCP**, PostgreSQL protocol, gRPC, Raft (3 nodes), RBAC/JWT/OIDC, GDPR (retention, erasure per tenant/user, backup/restore), Prometheus metrics, Python/TS/Go SDKs, LangChain/LlamaIndex integrations, Helm. Stores: episodic (DuckDB), memories (DuckDB), semantic (USearch HNSW), state (SQLite + DashMap). Advanced features: bi-temporal knowledge graph, per-fact provenance, feedback loop (`helpful/wrong/obsolete`), WebSocket CDC, HITL review of contradictions, session distillation, event tracing. Plus an "agentic platform" (durable runs, agent loop, sub-agents, HITL, triggers, tool gateway) and an auto-RAG LLM proxy. Reproducible LoCoMo bench, KB bench up to 200 k memories. 297 commits, 19 open PRs, no external adoption.

### What Choregos asks of it

| Need | Ecphoria feature | Status |
| :-- | :-- | :-- |
| One tenant per project, RBAC mapped onto the OIDC groups | tenants + OIDC | to verify/align (task E-03) |
| Typed facts with a normalised `subject` (decision, convention, incident, ticket summary, run lesson, flaky test, hotspot) | memories + supersession | business schema to add (E-04) |
| "Context pack" in one call: top-k memories + incidents on paths + linked tickets, under a token budget | hybrid search, graph | composite endpoint to add (E-05) |
| Idempotent ingestion from GitHub/GitLab/Jira/Argo/Tekton/Alertmanager | event ingest | connectors to write (E-06, on the Choregos side `MemoryIngestion` + on the Ecphoria side upsert by `external_id`) |
| Facts proposed by agents in a validation queue | HITL review of contradictions | generalise into a `pending` queue (E-07) |
| Embeddings via an OpenAI-compatible endpoint (LiteLLM) | Ollama / OpenAI | generalise `ECPHORIA_EMBEDDING__BASE_URL` (E-08) |
| Read < 300 ms p95, moderate concurrent write | embedded DuckDB, Raft | bench on our profile (E-09) |
| Mandatory provenance | provenance | already there; make it mandatory per tenant (E-10) |
| K8s operation: Helm in our umbrella, backups, alerts | Helm, backup/restore, Prometheus | integrate and harden (E-11) |

### Decisions

1. **Ecphoria = memory + knowledge base.** The "agentic platform" and the auto-RAG proxy are not used by Choregos and leave the default binary (Cargo feature flags `agentic`, `llm-proxy` disabled in the `ecphoria:memory` image). They remain maintainable separately if another use justifies them.
2. **Pull, not push**: agents consult memory via MCP (`search_memory`, `get_context`); the orchestrator injects a bounded context pack **marked untrusted**; never in the system message.
3. **Governed writes**: the orchestrator writes deterministic facts (ticket end, rollback, finding created, run lesson) with provenance; agents **propose** (`propose_fact`); a human (or a rule: fact confirmed by ≥ 2 runs) validates.
4. **Proof before dependency**: A/B over 4 weeks (projects with/without a context pack) on the first-pass merged-PR rate and the cost per ticket; if memory does not pay off, it stays optional.

### Improvements to implement in `VargaFoundation/ecphoria` (stream S11)

| ID | Task | Detail | Size |
| :-- | :-- | :-- | :-- |
| E-01 | Triage of the 19 open PRs | For each PR: (a) CI green? (b) memory/KB/ops scope → **merge**; agentic platform/proxy scope → **park** (label `deferred/agentic`); (c) conflicts → rebase by an agent, one PR at a time; (d) dependency PRs → merge if tests pass. Produce `docs/PR-TRIAGE-2026-09.md` | M |
| E-02 | Cargo feature flags | `agentic`, `llm-proxy` optional; `ecphoria:memory` image without them; `ecphoria:full` image with them; docs | M |
| E-03 | Tenant = project | Tenant creation/deletion via admin API; mapping of OIDC groups → roles per tenant; isolation test | M |
| E-04 | Business fact schemas | `kind` enum + JSON Schema validation per kind; normalised `subject` (`decision:<area>:<slug>`, `incident:<service>:<date>`, `flaky_test:<path>::<name>`…); migration; docs | M |
| E-05 | `POST /api/v1/context-pack` endpoint | `{ query, paths[], kinds[], budget_tokens, k }` → `{ memories[], incidents[], related_items[], tokens_estimated, provenance }`; ordered by RRF then freshness; truncation to the budget; latency test | M |
| E-06 | Idempotent upsert | unique `external_id` + `source`; `PUT /memories/by-external-id`; used by the Choregos connectors | S |
| E-07 | Generalised `pending` queue | `POST /memories?status=pending`; `GET /pending`; `POST /pending/{id}/accept|reject`; configurable auto-accept rule; CDC event | M |
| E-08 | OpenAI-compatible embeddings | `ECPHORIA_EMBEDDING__PROVIDER=openai_compatible`, `BASE_URL`, `MODEL`, automatic dims; test against LiteLLM | S |
| E-09 | Choregos-profile bench | Corpus: 20 k facts, 200 k events; 50 reads/s, 5 writes/s; measure p50/p95, memory, Raft behaviour on loss of a node; report | M |
| E-10 | Mandatory provenance per tenant | `require_provenance: true` option; 422 refusal otherwise | S |
| E-11 | Hardened Helm chart | `StatefulSet` with 3 Raft replicas, PDB, `NetworkPolicy`, probes, `PodSecurity` restricted, `CronJob` backup to S3/Blob with integrity manifest, documented restore, `ServiceMonitor`, basic alerts | M |
| E-12 | Releases | Semver, `cargo-release`, changelog (`cliff` already present), signed multi-arch images (cosign), SBOM | S |
| E-13 | Security | Review of the PostgreSQL wire (auth, limits), fuzzing of the ingestion parsers, `cargo audit`/`deny` in CI (already), `SECURITY.md` up to date | M |
| E-14 | Choregos integration docs | `docs/integrations/choregos.md`: MCP, context pack, ingestion, schemas | S |

Order: E-01 → E-02 → E-06/E-08 (unblockers) → E-03/E-04/E-05 (core) → E-07/E-10 → E-11/E-12 → E-09 → E-13/E-14. E-01 can start on day 1, in parallel with everything else.

### Integration on the Choregos side (stream S10)

| Component | Detail |
| :-- | :-- |
| `packages/adapters/src/choregos_adapters/memory/ecphoria.py` | `MemoryAdapter` via REST + MCP; tenant = `project.slug`; short timeouts (300 ms read, 2 s write); circuit breaker → empty context pack rather than a blocked stage |
| `MemoryIngestion` (Temporal, 15 min cron + events) | tracker: closed tickets, merged PRs (title, description, review summary) → `ticket_summary`; SCM: `docs/adr/*.md`, `docs/**` → `decision`/`convention`; CD: deployments, rollbacks → `incident`; Alertmanager → `incident`; orchestrator: end of run → `run_lesson` (recurring CI failure, flaky test, time per stage); created findings → `finding` |
| `build_context_pack` (activity) | Query = title + spec; `paths` = `allowed_paths`; `kinds` per role (`refine`: decision, incident, ticket_summary; `implement`: convention, run_lesson, hotspot; `review`: incident, hotspot, decision); budget per role (2 k / 4 k / 3 k tokens); stored with the run |
| Memory MCP in the workspace | Ecphoria MCP server mounted read-only (`search_memory`, `get_memory_history`) + `propose_fact` via `choregos-tools` |
| Front end `/memory` | Search, bi-temporal history, `pending` queue, reimport |
| Eval | `EvalMatrix` includes the `with_memory` / `without_memory` variant on the reference tickets |

### Fallback

`packages/adapters/memory/pgvector.py`: facts in `memory_facts(project_id, kind, subject, content, embedding, valid_from, valid_to, provenance)` with supersession by `subject` in an SQL trigger; `ts_rank` + cosine search. Less clever, zero extra services; same interface.
