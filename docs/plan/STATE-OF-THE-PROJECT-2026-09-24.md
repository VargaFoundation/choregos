# State of the project — 2026-09-24

A critical assessment, written to be disagreed with. Three independent audits (product and
documentation; engineering, security and operability; competitive landscape) were crossed with
**the real bench** — the single-node kind cluster where two HR tickets ran with real agents on
the afternoon of 2026-09-24. The five most serious findings were re-verified by hand on the
source before being written here.

The verdict in one sentence: **the core of the product — a statically validated workflow DSL,
guarantees that are mechanisms and refuse when blind, business-named evidence, a cost ledger
that charges models and tools alike, an agent that never holds a credential, findings and
release trains — is genuinely original and well built; but a fresh install is unusable, the
multi-tenant security is decorative, the observability is scenery, and the four features
shipped this week have still never met a real agent.** `STATUS.md` says 96 stories delivered;
several of those ✅ were false and are corrected today.

This document has two parts: what is true today (A), and the work plan that follows from it
(B), ordered so that nothing is claimed before it is proven.

---

## A. What is true today

### A1. What the bench showed (facts from the cluster, not from the code)

1. **Both HR tickets died and nobody could see it.**
   - `wi-staffing-RH-1`: Temporal `FAILED` in `collect_run_artifacts` with "the project has no
     repository". The guard exists in `apps/orchestrator/.../activities/scm.py:100` — but **the
     orchestrator image running on the cluster predates it** (the string is absent from the
     pod). The run is `succeeded`; the ticket is frozen in `demande`.
   - `wi-staffing-RH-2`: Temporal `FAILED` on the **heartbeat timeout** of `await_run`
     (`workflows/interpreter.py:248-258`: 5 minutes, `retry_policy=NO_RETRY`). The worker was
     restarted by a `helm upgrade` while the run was in flight. **Upgrading the platform while
     an agent works kills the ticket.** `await_run` only polls; it is idempotent and should
     retry.
   - The API never reads Temporal's state (`temporal_wf_id` is stored, never described). **A
     FAILED workflow is invisible in the product**: no event, no alert, no screen — the ticket
     just stays where it was.
2. **The tool catalogue has never reached an agent.** `global.toolCatalog` is absent from the
   deployed values and from `demo/values-demo.yaml`; the `demo-outils` ConfigMap exists but
   nothing mounts it; the deployed runner image does not contain `outils_locaux.py`. The ledger
   has zero `kind=tool` rows.
3. **Cost per run — the product's first argument — has never produced a non-zero number.**
   Twelve ledger rows, `tokens_in=0, tokens_out=0, cost=0`, on every run including the code
   ones. Cause: the demo runs with `gateway: direct` (OAuth credentials); nothing meters,
   nothing caps. The demo "proves" a spending cap that never measured a spend.
4. **Agents miss the `StageResult` contract on the first try**: 5 `result.repair` events over
   8 runs (`questions` as strings, `findings` without `title`/`type`). The runner repairs — but
   the contract is not guided enough.
5. **The runner's write guardrail never applies to Claude Code.** 91 `session/request_permission`
   decisions, 0 refusals; the 23 `Write`/`Edit` calls were classified `read`. Verified on the
   raw payload: Claude Code's ACP adapter sends `toolCall{title, rawInput{file_path}, toolCallId}`
   **without `kind`**; `packages/runner/.../guardrails.py:77` requires `kind` in
   `{edit, write, …}` and falls through to line 81 ("read or search: allowed"). The path scope is
   only enforced after the fact (`scope.reverted`). `check_command` is a denylist alone: a shell
   command that writes bypasses the scope entirely. The guardrails are fail-open by construction.

### A2. Usability — a fresh install cannot be used

- **No way to create an organisation.** `Organization(` is constructed only by
  `dev/scripts/seed.py` and `demo/seed.py`. No endpoint, no CLI command, no screen.
  `POST /orgs/{org}/projects` → `_org()` → 404. The web front hard-codes `DEFAULT_ORG = "varga"`.
- **No way to obtain an API token.** `generate_api_token()` (`apps/api/.../security.py:141`) is
  called from nowhere. The docs say `choregos login --token` — that token cannot exist.
- **No first-user bootstrap.** `_map_groups_to_roles` (`routers/auth.py:101`) iterates existing
  organisations; with zero organisations it grants zero roles, so `PROJECT_CREATE` is refused
  even after a successful OIDC login.
- **"Any OIDC issuer works" is false.** `_oidc_urls` (`routers/auth.py:54`) hard-codes Keycloak
  paths; no `.well-known` discovery; no PKCE; `state` is used as the redirect target, not as a
  CSRF nonce (open redirect + login CSRF). The required group names (`org-admins`,
  `product-owners`, `release-captains`, `developers`) are documented only in French.
- **The documented demo does not start.** `demo/values-demo.yaml` expects
  `local/choregos-*:demo` with `imagePullPolicy: Never`; `make images` builds `choregos-*:dev`;
  neither README mentions `make images` or `kind load`. Result: `ErrImageNeverPull` on every pod.
- **Web front.** No login, logout or 401 handling (`api.me()` is called only on `/admin`).
  Cannot: create or edit a connector (the docs say "Settings → Connectors" — that screen does not
  exist), edit the policy (read-only `<pre>`), edit model profiles, pick a template (one
  hard-coded `<option>`), manage members, backends, executors or gateway keys, suspend or delete
  a project, follow provisioning, abort a release, view a transcript, or **create a work item**
  (`tracker: internal` has no creation endpoint at all). The project wizard and the CLI **both
  require a repository** — ADR 0012 was not followed. The board re-parses the workflow YAML with
  a regex instead of the API's `report.graph`. **No screen shows which gates ran and what each
  decided** — the product's central claim is invisible. `next-intl` is declared and never
  imported (STATUS S5-01 said "FR/EN" — false). `@varga/design-system` is a git dependency.
  Playwright e2e runs in mock mode and not in CI.
- **CLI**: 30 commands, 0 tests, and no end-to-end path (login impossible, org impossible,
  `--repo` mandatory, no `items create`).
- **English docs** say `repo` is still mandatory (false since ADR 0012), teach `role: custom`
  (superseded), point to a catalogue that is never mounted, pin `--version 0.2.0` while
  `values.yaml` says `imageTag: 1.0.0`; "LiteLLM needs its database or no run starts" exists
  only in French; Infisical has zero occurrences (the secrets story is External Secrets);
  ADRs 0013 and 0014 are missing from `docs/adr/README.md`; "9 runbooks" vs 11 indexed.

### A3. Security

- **P0 — Authentication backdoor on by default.** `dev_login_enabled: bool = True`
  (`apps/api/.../config.py:36`), never set by the chart. `GET /auth/callback?code=dev:admin@x`
  creates a user and, because the e-mail starts with `admin`, grants `ORG_ADMIN` on **every**
  organisation — in production, with the official chart.
- **P0 — Row-level security is fail-open and never armed.** The policy is
  `choregos_current_org() IS NULL OR …`; the GUC is set only by `session_scope(org_slug=…)`,
  and **no production code path passes `org_slug`** (`deps.py:get_db`, the orchestrator's
  `session_scope` calls). `SECURITY.md` advertises RLS.
- **P0 — Tenant crossing by two independent routes.** The OIDC group mapping grants the role on
  **all** organisations; `resolve_project` drops the org part of the slug and searches by slug
  alone (`deps.py:129-133`) while `Principal.role_for()` indexes by slug without org.
- **P1** — Open redirect and no nonce/PKCE on the OIDC flow; API tokens: `expires_at`, `scopes`,
  `last_used_at` are never read; domain egress is an annotation only (the `choregos-egress`
  proxy exists in no chart); the Kyverno non-root rule is a conditional anchor (a pod with no
  `securityContext` passes); `SECURITY.md` promises an SBOM and a nightly CRITICAL block —
  neither exists.
- **P2** — No CSRF token on cookie mutations; hand-rolled session HMAC truncated to 128 bits
  while `itsdangerous` is a declared dependency; `generic_webhook_secret` defaults to a value
  published in the repo and the chart does not inject it (ArgoCD, Alertmanager, Jira, GitLab
  webhooks); the GitHub webhook skips signature verification when the secret is empty; run
  tokens are not revocable; `.seed-test.db-{shm,wal}` are committed; CSP allows
  `'unsafe-inline'`.
- **Sound**: human tokens and run tokens are strictly separated (`/internal` accepts only
  `RunAuth`); catalogue provider keys stay in the API and the run token never leaves; runner pods
  are hardened (`automountServiceAccountToken: false`, non-root, drop ALL, seccomp,
  `activeDeadlineSeconds`); images are cosign-signed and Kyverno verifies them by digest.

### A4. Operability

- **P0 — No metrics, no traces.** `prometheus_client` appears nowhere; there is no `/metrics`
  route. Yet the chart ships a ServiceMonitor scraping `/metrics`, 4 alerts on metrics that are
  never emitted, and 6 Grafana dashboards referencing 16 non-existent `choregos_*` series. The
  OTel collector and Tempo are deployed to receive nothing.
- **P0 — The SSE event bus is in-process memory.** `events.py:21` promises Postgres
  LISTEN/NOTIFY for multi-replica; it is implemented nowhere. With 2+ API replicas, or as soon as
  an event is born in the orchestrator (almost all of them), both live streams are silent.
  `emit()` runs before commit.
- **P1** — `rate_limit_per_minute` is a phantom setting; Temporal down = bare 500, `/readyz`
  does not check it; Postgres pool unbounded across 3–12 replicas; PDB for the API only; no
  Temporal backup; chart pinned at `1.0.0` and rewritten by `sed` at release; no request-id and
  none of the `project/work_item/run_id/stage` log keys promised by `AGENTS.md`.

### A5. Tests and code health

- **Temporal replay**: `tests/replay/histories/` is empty; `make replay-record` does not exist;
  `WorkflowInterpreter` (675 lines, the core) is never replayed.
- No multi-org test; RLS never exercised (SQLite); the real LiteLLM adapter untested (the fake
  validates the fake); run-token expiry/renewal untested; RBAC: 1 test for 18 permissions × 5
  roles; real OIDC callback untested; 13 of 19 adapters without tests; CLI 0 tests;
  `values/local.yaml` (the 4 embedded sub-charts) never rendered in CI; `fail_under=80` excludes
  `apps/api` and `packages/adapters`.
- Dependency direction: `adapters/memory/pgvector.py` imports `choregos_api` (undeclared); the
  orchestrator writes through the API's models, bypassing services and audit; `runner ⇄ tools-mcp`
  is a package cycle; the CLI pulls in the orchestrator; `TEMPLATES_DIR = parents[5]`.
- Ruff: every complexity rule is ignored; 79 `type: ignore`; `S105` ignored precisely on
  `config.py` and `security.py`; dead code (`generate_api_token`, `RunClaims.expired`,
  `rate_limit_per_minute`, `object_store_url`); cross-backend logic duplicated in API and
  orchestrator; three GitHub clients; `Makefile` pins pnpm 9.15 while `package.json` says
  12.4.2; the commands in `AGENTS.md` do not match CI.
- CI/CD: no SBOM, no SLSA provenance, no Trivy on release images, nightly scan `exit-code: 0`
  and outside the alert job's `needs`, no secret scanning, `github.actor` for registry login, no
  version bump tooling, Python packages never published, coverage not gated, 20 helm-unittest
  assertions for 8 sub-charts.
- Front: `mocks/data.ts` (464 lines) ships in the production bundle; 13 `aria-*` in 3,090
  lines; `DEFAULT_ORG` hard-coded; 4 vitest files; nothing measures the bundle.

### A6. Competitive landscape (researched 2026-09-24)

- **google/ax** (Apache 2.0, ~10k stars in nine days, on Agent Substrate/GKE) is a *runtime*
  — Task/Workspace/Gateway/Model primitives, BYO agent image, egress allowlist — with **no**
  stages, roles, gates, evidence, trains or cost ledger. It will define the category's
  vocabulary. The right posture: *Choregos is the delivery layer; ax and agent-sandbox are
  runtimes it can sit on.*
- **OpenHands**: per-project budgets, built-in gateway, **prompt-injection detection with
  automatic halt**. **Tembo**: the philosophical twin (multi-harness, ticket-triggered), SaaS, no
  gates, no security or pricing documentation. **Copilot / Codex / Devin / Factory / Cursor /
  Amp / Qodo**: locked agent; governance = SSO + credits; no self-host (Codex, Jules) or
  Enterprise-gated (Cursor). **kubernetes-sigs/agent-sandbox** (v1beta1): `SandboxWarmPool`,
  suspend/resume, `RuntimeClass` — solves ADR 0013 upstream. **treg**: ~2,900 endpoints.
  **Obot / Docker MCP Gateway**: credential brokers; neither hides the real tool name nor refuses
  auto-discovery. **Temporal Replay 2026**: Workflow Streams (durable streaming for live UIs).
  **Microsoft Agent Framework 1.0**: declarative YAML agents + first-class HITL.
- Standards: **ACP** — >50 agents, a registry, Devin Desktop adopted it (ADR 0002 aged well);
  **MCP 2026-07-28** — stateless core, `tasks` extension, multi-round-trip elicitation, servers
  as OAuth Resource Servers with RFC 8707, DCR deprecated; **A2A** under the AAIF with MCP,
  250+ members — Choregos has zero surface; **OTel GenAI** — all attributes still
  "Development", moved to `semantic-conventions-genai`; **AGENTS.md** — read natively by Claude
  Code since 2026-09-18, so `CLAUDE.md` is now redundant; **SLSA / `ossf/tac#628`** — a proposed
  "AI authorship" attestation predicate (model, prompt fingerprint, acceptance contract,
  sign-offs) that nobody has shipped; **EU AI Act Article 50** — transparency and traceability
  obligations live since 2026-08-02, high-risk deferred to 2027-12 / 2028-08.
- **What few others have**: a statically validated DSL; gates with refusal semantics; business
  evidence; BYO agent over an open protocol; a unified model+tool ledger capped per run; an
  agent that sees neither key, URL nor real tool name; findings + trains + freeze with reason;
  the same engine for non-code work; a fake for every connector; Apache 2.0, self-hosted,
  GitOps-only.
- **What most others have and Choregos lacks**: sandbox suspend/resume/snapshot; kernel
  isolation (no `RuntimeClass`/seccomp in the charts); prompt-injection detection;
  trajectory-level evals; a mature HITL UI; SPIFFE identity; A2A; catalogue breadth; English
  reference docs; a visual workflow builder.

---

## Decisions taken on 2026-09-24

- **English becomes the reference language of the documentation** (ADRs, runbooks, getting
  started, security, concepts). French stays for code, comments, STATUS/BLOCKERS and commits.
- **Scope: everything, P3 included**, in the order P0 → P1 → P2 → P3, over several sessions.
  Each PR updates STATUS saying what it proves and what it does not. Nothing is marked ✅
  without a test that fails in its absence — and for P0, without the bench.

## B. Work plan

### P0 — Without this, the product lies

- **P0-0** This document; STATUS and BLOCKERS corrected; ADR index fixed.
- **P0-1 The bench tells the truth.** Demo values mount the catalogue and **embed the LiteLLM
  gateway** (otherwise no spend is ever measured); `make demo-images` builds and loads
  `local/choregos-*:demo`; the README says how long it takes and how to tell it worked. Replay
  with fresh ticket keys; require `kind=tool ≥ 1` and `cost_eur > 0` in the ledger and both HR
  tickets at `a_valider`.
- **P0-2 Workflow resilience.** `await_run` retried (it only polls); post-run activities
  tolerant; a FAILED/TERMINATED workflow surfaces as `workflow_status` on the work item, a
  persisted `workflow.failed` event, a red banner, and an alert. Replay histories recorded
  (`make replay-record`) for `WorkflowInterpreter`, `ProjectProvisioning`, `ReleaseTrain`; no
  PR touches `workflows/` without a replayed history.
- **P0-3 Real multi-tenant security.** `dev_login_enabled` false by default, set explicitly by
  the chart, refused at startup in staging/prod, no `admin*` escalation. RLS armed from
  `get_db()` and the orchestrator, policy fail-closed, tested on PostgreSQL with two
  organisations. `resolve_project` requires `org/slug`; `role_for(org, slug)`; OIDC groups
  scoped per org (`choregos:<org>:<role>`, plus a configurable map). OIDC discovery, signed
  random `state`, PKCE, validated `redirect_to`. API tokens issued, listed, revoked, expiring.
  Webhook secrets injected by the chart; GitHub webhook refuses an empty secret outside dev;
  rate limiting implemented or removed; `.seed-test.db-*` removed; `SECURITY.md` promises only
  what exists. Runner guardrails fall back on the ACP title and on `file_path` when `kind` is
  missing, tested with the real Claude Code payload; the access card shows "N decisions, M by
  fallback".
- **P0-4 Real observability.** `/metrics` on API and orchestrator emitting exactly the series
  the dashboards and alerts use (or those panels are deleted); a test that no dashboard
  references an unexported metric. Event bus on Postgres LISTEN/NOTIFY, `emit()` after commit,
  tested across two buses. `/readyz` checks Temporal; request-id and `project/work_item/run_id/
  stage` bound in every log line.
- **P0-5 Usable from the first install.** Bootstrap org + admins from settings; `POST /orgs`
  for platform admins; `POST /projects/{id}/work-items` + `choregos items create` + a "New
  request" screen; repository optional in the wizard and the CLI; real connector step; `tools`
  and `groups` editable; templates from the API. Front: `/login`, logout, 401 interception, org
  selector, a **Guarantees** screen per run and transition (gate, verdict, reason), editable
  policy and model profiles, members, provisioning progress, transcript, release abort, board
  from `report.graph`. `next-intl` wired (fr + en) or removed — no half state. Mocks out of the
  production bundle; Playwright against the real API (fakes) in CI.

### P1 — The product deploys and documents itself honestly

- **P1-1 Docs**: English becomes the reference (getting started, dev, security, runbooks, ADRs
  translated; French archived under `docs/fr/`); every factual error above corrected; a generated
  CLI reference; `CLAUDE.md` merged into `AGENTS.md`; `AGENTS.md` commands aligned with CI.
- **P1-2 Delivery chain**: SBOM + provenance on image builds, `cosign attest`; Trivy on release
  images; nightly scan blocking and wired to the alert; secret scanning; fixed registry actor;
  `tools/bump_version.py`; Python wheels published; coverage gate including `apps/api` and
  `adapters`; `values/local.yaml` rendered in CI; helm-unittest per embedded sub-chart.
- **P1-3 Missing tests**: generated RBAC matrix; run-token expiry/audience/renewal; real LiteLLM
  in nightly `live`; Alembic downgrade and migrations on PostgreSQL; the 13 bare adapters
  (HTTP contract via `respx`); the CLI (`CliRunner`).
- **P1-4 Architecture**: `pgvector.py` without `choregos_api`; the `runner ⇄ tools-mcp` cycle
  broken (subprocess entrypoint, not import); CLI without the orchestrator; packaged
  `TEMPLATES_DIR`; one cross-backend computation (in `core`); one GitHub client.
- **P1-5 Operability**: PDBs for web and orchestrator; anti-affinity; bounded pool / optional
  PgBouncer; "embedded forbidden in prod" guard in the chart; Temporal backup documented; domain
  egress either delivered (`choregos-egress` in the chart) or the promise removed.
- **P1-6 Agent contract**: the `StageResult` JSON Schema and a valid example in the context
  pack, plus `choregos-tools validate_result` — target: zero `result.repair` on the bench.

### P2 — Consolidation

Complexity rules re-enabled with realistic thresholds; `stage.py`, `interpreter.py`,
`schemas.py`, `services.py` split; a Protocol for the memory adapter; avoidable `type: ignore`
removed; remaining dead code deleted. Front: a11y (`jsx-a11y`, axe in Playwright, keyboard on
board and graph), bundle budget, CSP without `unsafe-inline`, design system published. Runner:
fail-closed guardrails for out-of-scope writes (the agent receives a `reject` with the
explanation already written), basic prompt-injection detection. Kyverno: strict non-root rule;
optional gVisor/Kata `RuntimeClass` for jobs.

### P3 — Evolutions (one ADR each, by strategic value)

1. **ADR 0015 — Signed "AI authorship" attestation** (in-toto/DSSE, `ossf/tac#628` predicate)
   per merged PR, built from `StageResult` + gates + ledger; an Article 50 evidence pack per
   project.
2. **ADR 0016 — `agent-sandbox` executor**: warm pools, suspend/resume across a human gate,
   `RuntimeClass`; the `suspend/resume/snapshot` capabilities become real; ADR 0013 updated.
3. **Positioning against google/ax**: an honest comparison page; an `ax` executor as proof that
   Choregos is the delivery layer on top of a runtime.
4. **MCP 2026-07-28 conformance**: stateless `tools-mcp`, the `tasks` extension for long stages,
   elicitation ↔ human question, RFC 8707 for the external catalogue.
5. **OpenTelemetry GenAI spans** (`invoke_workflow` / `invoke_agent` / `execute_tool`) from the
   runner and the gateway, into the collector already deployed — labelled Development, honestly.
6. **Temporal Workflow Streams** for the live log (replaces the home-made SSE).
7. **Trajectory-level evals** in EvalMatrix.
8. **SPIFFE** identity behind the run token; the policy engine as the authorization answer.
9. **Federated catalogue**: entries proposed from Obot/treg, merged by a human in a PR.
10. A visual workflow builder (read-only first, from `report.graph`).

## Verification

- `make ci` green; `mypy --strict`; contracts regenerated; helm-unittest.
- **Bench** (P0-1/P0-2): two HR tickets to `a_valider`, one code ticket to `done`, ledger with
  `kind=tool ≥ 1` and `cost_eur > 0`, a `helm upgrade` **during** a run with no ticket lost, a
  provoked FAILED workflow visible in the UI and as an alert.
- **Security** (P0-3): the two-organisation PostgreSQL test; `curl '/auth/callback?code=dev:admin@x'`
  → 404 in prod; `helm template` for prod contains no `DEV_LOGIN_ENABLED=true`.
- **Observability** (P0-4): `curl /metrics` lists every series the dashboards use; an SSE
  event received from a replica other than the emitter.
- **Usability** (P0-5): a fresh path on an empty kind cluster — bootstrap → login → create a
  project with no repository → file a request → run → Guarantees screen → ticket at
  `a_valider` → approval — **without touching the database**.
