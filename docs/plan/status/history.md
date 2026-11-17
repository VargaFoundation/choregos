# STATUS — the narrative until 2026-11-17

Moved unchanged from [STATUS.md](../STATUS.md) on 2026-11-17 (S80-02): the page had grown to
2,200 lines and 1.5 MB. The dated paragraphs and sections below are records of their day; the rows
of every story are in the stream files listed by [STATUS.md](../STATUS.md).

The repository has lived on `github.com/VargaFoundation/choregos` since 2026-09-19. The first
CI run anywhere other than the workstation found six things that no local
run could see: a GitHub action referenced by a tag that does not exist,
a mypy glob that caught TypeScript, `helm unittest` that had never run (and
whose four tests were wrong), the workers image that started from the previous commit,
the web image that did not build, and the `ci-ok` gate that did not look at the images.
That is the value of a remote repository, measured in one hour.

**2026-10-05 — ADR 0029: Choregos becomes a platform of governed agents, for general use; 0028 is
superseded.** The review of the dev console found five gaps (a single workflow per project, no visible
enterprise function, agents and MCP servers invisible, settings typed for development, no connection from Claude);
the product owner chose to widen the scope rather than narrow it. Six streams open, S15 to S20: their
stories enter the table at the start of each batch. What this PR proves: nothing functional; it records the
decision, its invariants (no secret in a pod, a discovered tool stays closed, no decision through MCP) and what
would make us change our minds. What it does not prove: that a single one of these streams works — each will prove it through its
tests. The S14 stream remains valid: data work still enters through the catalog and the guarantees.

**2026-10-02 — ADR 0028: Choregos governs the execution of change.** The S14 stream opens data work through the
catalog and the guarantees, without merging with graal. What this PR proves: nothing functional; it records a
decision and opens four ⬜ stories, each with its verifiable criterion. What it does not prove: that an agent has
already driven a data platform under these guarantees — that is the object of S14-03.

Since 2026-09-23, the repository has received material that matches no story of the
initial backlog — it came from use: optional embedded dependencies, single-node bench (`demo/`),
tool catalog, run admission queue, executor capabilities, English documentation.
ADRs 0012 to 0014 carry the decisions.

## What holds up today

- `make demo`: a ticket goes through the platform to production, without a cluster.
- `make ci`: lint, strict typing, tests with coverage, contracts verified, procedures checked, charts rendered. The page used to quote "465 tests"; the count moves with every PR and is no longer copied here.
- Coverage: `make test-cov` fails below 80 % (`fail_under` in `pyproject.toml`) over the packages that file lists; the figure is measured by each CI run. The page used to quote 84 % on `packages/core`, `packages/runner` and `apps/orchestrator`, three packages out of a longer list.
- 24 e2e scenarios M1–M5 green, without a cluster.
- **On a real cluster** (kind + Calico): a runner's egress is really blocked, the
  Postgres backup restores with its data, the generated manifests are accepted by the
  API server, and the API + workers run from the repository image — killed worker included.
- **Against the real services**: GitLab (complete cycle on gitlab.com), Ecphoria (context pack,
  validation queue, idempotent upsert), LiteLLM (run key, budget as a hard ceiling, cost
  measured at the gateway, Anthropic format) and **Azure Container Apps** (a job created, triggered,
  observed to its terminal state, cancelled — on a real subscription).
- The 7 ACP backends pass the 7 conformance checks; the 2 templates pass
  template conformance.
- **A single-node demonstration, with real agents** (2026-09-23, `demo/`): three code tickets
  go through the workflow to `done` — one agent implements, a **second** verifies and
  adds edge-case tests, the `evidence_present` guarantee covers tests actually
  run, and the branches are pushed. Two HR tickets follow the **same engine** without a line of code
  changed: lacking a profile database, the agents refuse to invent candidates,
  the ticket goes up to `needs_human` and their findings become tickets.
- The dependencies (PostgreSQL, Temporal, LiteLLM, Ecphoria) are embedded or plugged in
  externally, in the manner of the Bitnami charts: `global.<dependency>.embedded`. Default unchanged
  (external); `charts/choregos/values/local.yaml` mounts a complete bench without providing anything.
- **The engine carries a trade without a repository or tests** (2026-09-24, [ADR 0012](../../adr/0012-the-engine-is-not-tied-to-software.md)):
  `repo` is optional — the agent then works in an empty directory, tracked by a local
  git so that its work remains measurable; evidence is named by the trade
  (`Evidence.facts` + `evidence_facts` guarantee, which refuses a polite zero); and roles are
  names in their own right (`sourcing`, `qualification`) and no longer `custom`. The demonstration's HR project
  no longer has any repository.
- **A spike of tickets no longer makes a spike of pods** ([ADR 0013](../../adr/0013-agent-task-density.md)):
  beyond `runner.maxActive`, the Job is created suspended — no pod, no image pulled — and
  admitted in arrival order. An executor declares its capabilities (`queue`, `suspend`,
  `resume`, `snapshot`) and conformance refuses that it announces one it does not implement:
  it is the seam through which a snapshotting sandbox will enter.
- **A tool catalog kept by the platform** ([ADR 0014](../../adr/0014-a-platform-held-tool-catalogue.md)):
  third-party APIs called FOR the agent, key on the server side, capped per run and recorded in the
  cost ledger. The agent chooses neither the URL nor the method, and has no
  provider identifier.
- **English documentation** for deployment and usage (`docs/`), whose YAML examples
  are re-read by the DSL parser and the policy model — a test guards them.

## What the 2026-09-24 bench showed

Two HR tickets ran in the afternoon with real agents. What the cluster says, and that
the 492 tests did not say:

1. **Both tickets died without anyone seeing it.** RH-1: Temporal workflow `FAILED`
   on `collect_run_artifacts` — the "no repository" guard exists in the code but **the deployed
   image did not have it**. RH-2: `FAILED` on `await_run`'s heartbeat (`NO_RETRY`) because
   a `helm upgrade` restarted the worker during the run: **upgrading the platform kills
   the tickets in progress.** And the API never reads Temporal's state: the ticket stays in `demande`,
   with no event or screen (P0-2).
2. **The tool catalog has still never reached an agent**: not mounted in the demo,
   runner image without `outils_locaux.py`, 0 `kind=tool` rows in the ledger (P0-1).
3. **The cost per run has never produced a non-zero figure** — 12 rows, all at zero, code
   included: the demo runs through a direct gateway, nothing counts (P0-1).
4. **The runner's write guardrail does not apply to Claude Code**: its ACP does not send
   `kind`, and `guardrails.py` falls back to "read: allowed". 91 decisions, 0 refusals (P0-3).
5. 5 `result.repair` out of 8 runs: the `StageResult` contract is not guided enough (P1-6).

## What is missing to say "in production"

0. **A new installation is unusable, and multi-tenant security is decorative**
   (stocktaking, § A2–A4): no organization creation, no API token, no first
   admin; `dev_login_enabled=True` by default and never set by the chart; RLS never armed;
   OIDC mapping crossing orgs; zero metrics; in-memory SSE bus. That is P0-3 to P0-5.
   **Progress (evening of 2026-09-24)**: P0-2 delivered (#23 — `await_run` replays, a dead ticket
   carries its cause, the API reads Temporal's state); P0-3a delivered (development login
   off by default and refused in prod, `admin*` escalation removed, OIDC discovery + PKCE +
   signed `state` + bounded redirect, groups `choregos:<org>:<group>` attached to THEIR
   organization, project roles indexed by `org/slug`, `resolve_project` that refuses an ambiguous
   slug, API tokens issued/expired/revoked by `/me/tokens`, and four lookups by slug
   alone fixed — `add_member`, webhooks, orchestrator, train); P0-3b delivered (RLS
   **fail-closed** on `app.current_orgs`, set by the principal, `*` for the run token,
   the verified webhooks and the orchestrator; INSERT of a project judged on `org_id`; the API
   refuses a PostgreSQL superuser in staging/prod and the embedded PostgreSQL creates
   `choregos_app` without SUPERUSER; **four tests on a real PostgreSQL**, in CI through a
   service); P0-3c delivered (secret of the unsigned webhooks generated and injected by the chart, no
   more published default value; GitHub webhook refused without a secret in staging/prod;
   `rate_limit_per_minute` finally real — sliding window per address on `/auth/*` and
   `/webhooks/*`; `SECURITY.md` no longer promises more than what exists; runner guardrails:
   the nature of an ACP request without `kind` is DEDUCED from the title and the arguments — Claude Code's
   writes finally go through the scope —, the workspace root is
   removed from the paths, the access record counts the deduced natures).
   **Bench series `c` (evening, P0-2 images)**: the three code tickets `done` with real
   agents; the two HR ones pass `sourcing` (the `outputs_present` and
   `evidence_facts` guarantees accepted) then **fail in `qualification`**: the agent does not find
   the profiles, because the playbook reads `{{ spec }}` (a software document) and the stage
   outputs (`inputs: [profils]`) are not given to it — **contract defect**,
   next PR. The catalog was **listed** by the agents (10 `GET /tools`) but never
   **called** (0 `POST`): the tool is announced in the playbook, not required by a mechanism
   — a `tool_called` guarantee reading the ledger would be the mechanism.
   **P0-4a delivered**: the metrics exist (see S7-07). P0-4b remains: event bus
   on LISTEN/NOTIFY (SSE is mute from two replicas), `/readyz` that tests Temporal,
   request identifier and `project/work_item/run_id/stage` keys in the logs.
   **P0-4b delivered** (bus on `pg_notify` at commit + `LISTEN` relay per replica, `/readyz`
   tests Temporal, `X-Request-Id`, keys in the logs). **P0-5a delivered**: bootstrap of the
   first organization and its admins (`CHOREGOS_BOOTSTRAP_*`, chart `global.bootstrap`),
   `GET/POST /orgs`, `POST /projects/{id}/work-items` on the internal tracker (key minted by the
   platform, interpreter started), `choregos-admin tokens create` for the first token,
   CLI: `orgs`, `tokens`, `items create`, `projects create` without `--repo`, and its first
   tests. **P0-5b delivered**: the guarantee verdicts logged and displayed (#32); login
   page, logout, redirect on 401, session and **organization selector**
   (projects addressed `org:slug`); project wizard with optional repository, templates from
   the API, tracker choice; **editable** settings (connectors with their types, policy
   in YAML, model profiles); administration (members, API tokens, backends,
   executors); provisioning tracking; "new request" on an internal
   tracker's board; release transcript and abort; fixtures outside the production bundle;
   `next-intl` removed (the interface's English = P1-1b). Remaining: Playwright e2e against
   the real API (P1-3), a11y and bundle budget (P2).
   **P1-2 delivered** (#34): SBOM + provenance on the images, Trivy on the release image,
   blocking nightly scan, gitleaks in the gate, `tools/bump_version.py` (repository aligned on
   v0.3.0, the release refuses a diverging tag), wheels attached, coverage with `apps/api`
   and `adapters` (70 % threshold = 72 % measured). **P1-1a delivered**: `docs/` is the reference,
   in English (getting started, deployment, usage, concepts, development, security,
   contributing, `cli.md` generated and tested); `docs/fr/` archived the French guides (deleted in 0.21, ADR 0098); ADR
   and runbook indexes with English summaries. **`CLAUDE.md` → `AGENTS.md` was WRONG**: the same
   commit replaced the content of `AGENTS.md` with an import of itself (eleven bytes), and
   `CLAUDE.md` being a symbolic link to it, the repository's instructions were empty on
   both sides for two days — restored and guarded by `tests/docs/test_agent_instructions.py`,
   which also refuses a nonexistent `make` target. Remaining P1-1b/c: the English
   of the interface, the complete translation of the 14 ADRs and the 11 runbooks.
   **The product's first non-zero cost (2026-09-26, 18:00)**: on Diametral's dev tenant,
   a `platform/cheap` call answered through the tenant's gateway then the
   platform's — 16 tokens, and the virtual key `choregos-dev` carries **2.7 × 10⁻⁵ USD** of spend,
   under a ceiling of $20 over 30 days. The product's argument no. 1 had twelve ledger rows
   at zero since day one; it now has a figure.
   What prevented it was not what BLOCKERS said. The tenant's secret store's **ten** values
   were absent except one, and the Infisical operator wrote in their place the literal
   string `<no value>`: ten `1/1` pods, Argo CD `Synced`, `/readyz` at 200, and session
   cookies signed with a public constant. The nine values were set by a Job that
   **generates them in the cluster** — they transited through no log. The guard added the same
   day (refusal to start on `<no value>`) makes this failure mode impossible to repeat.
   What remains to be proven: an **agent** that consumes this key and a `kind=tool` row in the
   ledger. The tenant has no Anthropic access, so it will be on the `opencode` backend, never
   tried on a bench.

   **Community edition and enterprise edition (2026-09-26)**: [ADR 0024](../../adr/0024-two-editions.md)
   reopens D1 and enacts two editions. The core remains Apache-2.0 under the Varga Foundation; the enterprise
   edition will be proprietary, in a separate private repository, edited by Diametral. The
   community edition becomes **single-organization**: `POST /orgs` refuses the second with a 409 that
   names the decision, and the refusal is tied to the EDITION, not to a hard-coded ceiling — the enterprise
   edition will lift it without forking the core. The rights check comes before the
   edition check, so that a developer learns nothing of a limit that they have no right to
   meet. `GET /edition` answers. What goes out is thin and no *proven* capability is
   lost: what is withdrawn is the promise of an unfinished multi-tenancy (thirteen tables
   still outside RLS).

   **The extension seam (2026-09-26)**: there was NONE — `register()` was public
   but nothing ever imported a third-party module, which made the separate repository impossible.
   `load_extensions()` reads the `choregos.plugins` entry points; the API and the orchestrator
   call it at startup; a declared extension that does not load **stops the process**. The
   tests write a real `.dist-info` rather than stubbing the discovery.

   **Audit leak between organizations, found on 2026-09-26**: `audit_log` was the
   last mutation table **outside RLS**, and did not even have an organization column;
   `GET /audit` returned `select(AuditLog)` **without any `where`**. A `project_owner` of one
   organization read the audit of all the others. Fixed: `org_id` column on the 29 write
   sites (a **mandatory** argument, so `mypy --strict` guards the following ones), RLS policy
   with `WITH CHECK` distinct from `USING` (a connection writes a trace without an organization, it
   must not thereby be able to write one for another), and route filter on the
   organizations where `audit:read` is **really** held. Two tests on a real PostgreSQL, including
   one that fails if the filter is skipped. The same work uncovered a second defect, recorded
   in BLOCKERS: `is_platform_admin()` returns true for any `org_admin` of any
   organization.
   **Bench series `d` (images of `main` after P0)**: the three code tickets `done`; **the HR
   qualification succeeds** — the sourcing's profiles reach it (#28); 5 guarantee verdicts
   logged and displayed; 43 guardrail decisions **deduced** from the ACP title
   (all in scope); 0 result repairs; still **0 tool calls** for 10
   catalog listings → **P1-6 delivered**: `tool_called` guarantee that reads the ledger (the
   demo requires it on the sourcing), result contract with the objects' shape, `validate_result`
   tool so that the agent checks itself before finishing.
   **P1-3 (first slice)**: RBAC matrix **generated** from `ROLE_PERMISSIONS` (5 roles ×
   12 routes carrying a permission, the expectation is deduced, never written by hand); run
   tokens: expiry, audience, issuer, signature of another pair; migrations:
   `downgrade base` then `upgrade head`; HTTP contracts of LiteLLM (ceiling sent, alias
   already taken, spend, revocation, catalog) and of Slack on a simulated transport; the
   `live` tests run at night when the secrets exist. Remaining: Ecphoria, ArgoCD, Tekton,
   GitHub (scm, tracker, client), pgvector, rest, adf — and coverage at 80 %.
   **P1-4 delivered**: `pgvector` no longer imports the API (session and models **injected**, the
   factory wired by `choregos_api.adapter_wiring` at the start of the API and of
   the orchestrator); the `runner ⇄ tools-mcp` cycle is broken (the internal client lives in
   `choregos_tools_mcp.client`, the runner re-exports it); the CLI no longer depends on
   the orchestrator; `CHOREGOS_TEMPLATES_DIR` replaces `parents[5]` — and **the image did not copy
   `templates/`**: no project with a template could be provisioned from a pod,
   fixed and guarded by a test. Two findings of the stocktaking turned out wrong on
   reading: the "three GitHub clients" are one client and two adapters that
   use it; the "duplicated cross-backend computation" is a policy (choosing the
   reviewer) and a measurement (comparing it) — two functions, not a copy.
   **P1-5 delivered**: PodDisruptionBudget for the front end and **per queue** of
   the orchestrator (rendered only beyond one replica); `soft|hard|none` anti-affinity
   on the three components; **bounded** connection pool (`max_overflow`, `pool_timeout`,
   `pool_recycle`, exposed by the chart); chart guard that **refuses rendering** in
   staging/prod with an embedded dependency, `devSecrets` or `devLogin`; Temporal
   backup runbook (English); and per-domain egress **delivered** instead of promised: the
   provisioning writes **one Squid per project** in the runners namespace (allowlist =
   `allow_domains`, non-root, read-only, `CONNECT` to 443 only), the
   NetworkPolicy opens the Internet only to it, each agent pod receives `HTTPS_PROXY`. Proven
   on the local kind (`tests/cluster/test_egress_proxy.py`: 200 on `api.github.com`, 403
   elsewhere); what is NOT proven: a complete Tekton run behind this proxy (no
   Tekton on the bench). Along the way: the e2e test of the Jira/GitLab webhooks failed alone
   since P0-3 (empty secret → 401) — the e2e bench sets one.
   **P1-3 (second slice)**: HTTP contracts on simulated transport for everything that remained
   bare — GitHub App (JWT, targeted and cached installation token, expiry), GitHub
   client (secondary limits, 5xx, `retry-after`, GraphQL, pagination), GitHub SCM
   (branches, PR, draft → GraphQL, checks/reviews/files, merge queue or direct,
   50 annotations max), GitHub tracker (issues, scoped labels, rewritten tracking
   comment, Projects v2 board with option creation, webhook signature, events),
   REST client (Retry-After, bounded backoff, 204, pagination), Tekton (Kubernetes client,
   secret + PipelineRun, idempotence, states, cancellation, logs, CI, CloudEvents), Argo CD
   (health, aborted Rollout, windows, end-to-end GitOps promotion), local Docker,
   Ecphoria (tenant, circuit breaker after 3 failures, `metadata`, proposals, search),
   direct gateway, internal tracker, ADF, lexical vector. **Three defects found by these
   tests**: a Tekton pod's logs (`text/plain`) were parsed as JSON and `logs()`
   never returned anything; the **first promotion** to an environment always failed
   (the GitHub client raises on 404, `_write_manifest` expected `None`); the absent kustomization
   too. Measured coverage **78 %** (72 % before), threshold raised to 75.
   **P1-3 (third slice)**: the routers that nothing exercised, over HTTP with the fakes —
   tickets (internal creation, list, complete timeline, actions, decisions), runs
   (events, `after_seq`, accesses, diff in/out of scope, transcript), costs (by kind,
   stage, day, window, CSV with BOM and budget, organization by project), trains (state from
   the workflow, departure/freeze/unfreeze signalled and audited, approval/abort of a release),
   webhooks (Tekton deduplicated, Argo CD and Alertmanager under a shared secret, GitHub without a
   secret outside prod), templates (read from the repository, created, updated, **each shipped template
   passes the schema**), platform (backends, executors, keys, 403 outside admin),
   provisioning, internal API under the run token, `choregos-admin`, `RealTemporal.describe()`
   on a simulated client, event bus. **One defect found**: the shipped template
   `github-aca` declared `requires.azure_capabilities`, unknown to the schema — served from
   disk, it would have been refused by the API; the schema now carries it. Coverage **80.1 %**, threshold
   at **80**. **`pgvector` on a database, at last**: the adapter had never been exercised on
   a database, and on PostgreSQL it saw **nothing** — the factory gave it a bare
   session, and the fail-closed RLS of 2026-09-24 shows nothing to a session that does not name
   its organization ("unknown project" everywhere; SQLite, without RLS, let it through). The
   factory requires the organization (`org`, or `*` for the orchestrator) and refuses without; the API's
   memory router reads the **project's `memory` connector** instead of hard-coding
   Ecphoria (a `pgvector` project read Ecphoria here and pgvector in
   the orchestrator). Three tests: complete cycle on SQLite, refusal without an organization, and on
   PostgreSQL two organizations each seeing only its own memory — the bare session
   from before fails there, as expected.
   **P1-1c delivered**: the 14 ADRs and the 11 runbooks are **in English** in `docs/adr/` and
   `docs/runbooks/` (file names unchanged: they are identifiers, the alerts and
   the pages point to them); the French originals are archived in `docs/fr/adr/` and
   `docs/fr/runbooks/` (deleted in 0.21, ADR 0098). The indexes no longer say "French". P1-1b
   remains: the interface in English.
   **P3 (1–3) set down as ADRs**: ADR 0015 (signed AI authorship attestation, in-toto/DSSE,
   art. 50 evidence pack) and ADR 0016 (`agent-sandbox` executor: warm pool, suspension
   through a human door, snapshots — ADR 0013 revisited) are **proposed**, not
   implemented; `docs/positioning.md` honestly compares with ax, agent-sandbox, OpenHands,
   Tembo, the closed products and the tool brokers, and says what they do better.
   **P3 (4–10) set down as ADRs**: 0017 (MCP 2026-07-28 conformance: stateless transport,
   `tasks` for `get_ci_logs`/`ask_human`, elicitation instead of questions in prose,
   resource-bound OAuth for the catalog's MCP servers), 0018 (OTel GenAI from the
   runner and LiteLLM, one trace per run, the ledger remains the truth of cost), 0019
   (Workflow Streams as the transport of a run's log, `run_events` remains the archive —
   awaits the self-hosted server), 0020 (trajectory evals scored from the log by
   mechanisms, never by a judge), 0021 (SPIFFE behind the run token: revocation
   by deletion, mTLS to the gateway — platform-team option), 0022 (federable
   catalog: a registry entry becomes a PR, born closed), 0023 (visual builder:
   the read-only map is delivered, edits will be operations on the YAML). All
   **proposed**, none implemented; each states its conditions and what it refuses.
   **Rest of P2, deliberately deferred**: the splitting of `stage.py` (723 l.), `interpreter.py`
   (775 l.), `services.py` (678 l.) and `schemas.py` (780 l.) — the complexity thresholds
   are held (#51) and three open PRs (#39, #48, #50) touch `stage.py`: splitting
   now means three guaranteed conflicts for a gain in readability. To be done in a PR
   of its own after the merges, the interpreter under the replay histories (#48).
   **Deployment of merged `main` on the kind bench (2026-09-25, evening)**: images
   rebuilt, chart upgraded, six deployments restarted — `/readyz`, `/healthz`,
   OpenAPI, 39 `choregos_*` series, the web front end with its nonce CSP; no error in the log.
   The embedded LiteLLM awaits the `platform-llm-key` secret ("no key for now"), so
   `helm --wait` times out: it is the only missing pod. **Two defects found by looking at
   the namespace**: 64 `run-*` secrets (one token per run) survived their Job — the
   secret is now **attached to the Job** (`ownerReferences`, set after the Job's
   creation) and the garbage collector takes it away with `ttlSecondsAfterFinished`; and the Role rendered
   by `gitops.py` for the namespace of a **provisioned project** granted neither `patch` on
   the Jobs (the admission queue would have failed there silently) nor `update`/`patch` on the
   Secrets — the bench runs in the chart's namespace and never saw it. Both Roles
   have the same verbs, and a test now confronts the executor with the project's Role as
   it already did with the chart's Role.
   **The migration hook could not start on a real Argo CD (2026-09-26)**: the
   dev tenant had been blocked for **seven hours** on `choregos-migrations-1`, a Job with
   no pod, and the whole Sync waited behind it. Cause: the Job ran under
   `choregos-api`, an **ordinary** resource of the release, whereas a `pre-install` hook
   (which Argo CD translates into PreSync) starts before any ordinary resource — "error looking up
   service account choregos/choregos-api: serviceaccount not found". The chart already documented
   this trap for Helm and had worked around it by putting the kind bench in `post-install`
   (`values/local.yaml`, embedded database): the **default default** therefore remained, and only a real Argo CD
   deployment could show it. Fixed at the root: the Job has its **own**
   service account, rendered by the same hook with a lower weight (-10 against -5), with no
   mounted token (the migrations talk only to the database) and carrying the annotations of
   `global.serviceAccount` (a database on workload identity must be able to authenticate
   with it). Guard: `tests/charts/test_migration_hook.py`, which renders the chart for the
   four environments and requires that the hook's account be in the same hook and before the Job
   — nine cases, in Python, without a Helm plugin. **Verified on the kind bench** (2026-09-26): upgrade
   to chart **0.4.0**, Helm revision 13 "Upgrade complete", so the `post-upgrade` hook
   did create the account then played the Job; `alembic_version` = `b2d4f6a8c0e1` in the database, and the
   Job was cleaned up by its own policy (`hook-succeeded`). What remains, as the day before, is the
   sole embedded LiteLLM in `CreateContainerConfigError`: it awaits `platform-llm-key`, the
   provider key that we do not have.
   **v0.4.0 published (2026-09-26)**: ten signed multi-arch images and the chart on
   `oci://ghcr.io/vargafoundation/charts/choregos` — this is the chart that the Diametral tenant
   vendors. A job of the release failed, and on a real defect: `choregos-playbooks`
   declared `packages` (which embeds the whole tree) **and** a `force-include` on `roles/`,
   so hatchling refused to add the same path twice. Nothing saw it — `make ci`
   builds no wheel, and the workspace runs in editable mode where the templates
   are simply there. Fixed, and held by `tests/paquets/` which looks **inside** the wheel:
   the eleven role templates and the six evals are there, each path only once, and the
   list of `.md` files of the source folder is compared with that of the wheel rather than written by hand.
   **The dev tenant is upgraded, and delivered a third defect**: the migration hook
   passed (hook account created before the Job, migrations applied, Job cleaned up, sync
   `Synced`), all the pods came — except the API, in `CreateContainerConfigError` on
   "couldn't find key generic-webhook-secret". The chart **hard-coded** seven keys of the
   API secret, five of which serve only one feature: an unused feature
   therefore prevented the API from existing. Rule set and held by
   `tests/charts/test_optional_secret_keys.py` (four environments): `session-secret`
   and `run-token-private-key` remain **required** (starting without them would be worse — cookies
   signed with an empty key, tokens issued by an ephemeral pair); `run-token-public-key` (it is deduced
   from the private one), the two webhook secrets and the two GitHub App keys become
   `optional: true`, because the code already refuses when empty (`verify_shared_secret` returns False,
   the GitHub webhook refuses in staging/prod). A third test forbids a new key
   from arriving without having decided its side.
   **The same packaging defect in three packages**: `core` (DSL presets and templates) and
   `runner` (`versions.lock`) also had a `force-include` on a path already covered
   by `packages`. Release 0.4.1 fell on it again after 0.4.0 — because the first
   test built ONLY the package that had broken, and because "nine wheels" meant
   nothing without knowing how many to expect. `tests/paquets/` now builds the whole
   workspace (`uv build --all-packages`, the release's command), counts the
   wheels against the list of `pyproject` files, checks in EVERY wheel the data files
   read from disk, and exercises the legitimate `force-include`s — those of `contracts`, whose
   schemas live outside the module. Nineteen cases.
   **And the plugin that lied**: `make charts-test` checked only the *presence* of
   `helm-unittest`, not its pinned version. A 0.5.1 left on the workstation declared red a
   test that was green in CI (it treats an absent JSONPath path as an error). The target now compares
   the version, refuses to run if it differs, and says how to install it — a test
   whose verdict depends on the workstation is worth nothing. The CI was right: Helm 3.22 and the
   plugin 1.1.2 installed at every run.
   **Orchestrator split delivered**: `stage.py` (847 l.) becomes four modules —
   `plan` (the `StagePlan`), `stage` (the preparation: model, key, context, `StageInput`),
   `execution` (launch, wait, abort), `bilan` (spend, result, findings),
   `garde` (injection) — and `stage` re-exports everything, the interpreter and the tests read at the
   same place. The three activities that the workflow carried at the end of the file (`load_context`,
   `record_workflow_failure`, `signal_train`) and the reading of Temporal errors move into
   `activities/interpretation.py`; `interpreter.py` contains only the workflow. No
   logic changed: 107 orchestrator/replay/e2e tests green, the four histories replayed.
   **API split delivered**: `services.py` (678 l.) becomes the `services/` package —
   `definitions` (active workflow and policy, defaults of a new project), `projets`,
   `tickets` (projections of tickets, runs, requests, releases; internal key; storage of
   outputs), `couts`, `evenements` (`persist_event`), `memoire` (A/B) — and `schemas.py`
   (780 l.) the `schemas/` package in eight modules by domain (base, identity, projects,
   definitions, tickets, delivery, measures, platform). Both `__init__`s re-export
   everything: `choregos_api.services.X` and `choregos_api.schemas.X` remain the address of everything,
   no import changed elsewhere. No logic changed: 112 API tests, 7 on
   PostgreSQL, 115 orchestrator/CLI/e2e/replay and 476 package tests green.
   **P1-1b delivered**: the interface is **in English** — the 26 front-end files (pages,
   components, labels, `aria-label`, placeholders, error messages), `lang="en"`,
   `en-GB` formats (amounts, dates, durations). No i18n: `next-intl` had been removed on
   2026-09-24 ("no half-state") and English is the reference language; French
   remains in the code, the comments and the demo data (`mocks/data.ts`).
   Vitest tests, Playwright e2e and `tsc`/`eslint` aligned. S5-01 moves to ✅.
   **P2 front (a11y) delivered** (#47): `eslint-plugin-jsx-a11y` in strict mode (33 rules, zero
   faults); axe in Playwright on six screens in demo mode, blocking on serious/critical
   (one real violation found: a `<dl>` without `dt`/`dd`); journeys and axe played in CI in
   the web job, report as an artifact on failure. Not yet: keyboard on the workflow graph.
   **P2 front (CSP and bundle) delivered** (#52): **nonce** CSP for scripts (`'strict-dynamic'`,
   no more `unsafe-inline` — styles keep it, Next injects inline styles) set by
   `src/proxy.ts`, dynamic rendering forced so that the nonce changes on every response; e2e test
   that reads the header and checks that an inline script without a nonce does not run; bundle
   budget in CI (`pnpm bundle:check`: 1,600 kB in total, 600 kB per page; measured 1,025 /
   224 kB). Remaining P2 front: `design-system` published on npm/GHCR.
   **P2 front (keyboard on the graph) delivered**: the workflow graph's states can be traversed
   by keyboard — Tab in reading order (column by column, the DOM is sorted like the
   map), ← → follow the transitions (nominal first), ↑ ↓ change state, Home/End;
   the state under the cursor is described under the map (`aria-live`, transitions, actors, gates),
   each node carries an `aria-label`, visible focus ring. Along the way: a state reached
   only by a secondary edge (question, escalation) is placed after its origin instead of
   in the first column; the OpenAPI contract of edges finally declares `kind`/`label`/`wildcard`
   which the API emitted without saying so; the demo mode answers validation with a map, so
   the workflow screen is covered by axe. Seven vitest tests, one e2e journey.
   **P2 (Kyverno, RuntimeClass) delivered**: the `non-root` rule is **strict** — the security
   context of the pod (non-root, seccomp) and of the containers (no escalation, capabilities
   dropped) is required, no longer tolerated if missing; `tests/charts` renders the chart for the four
   environments and applies the rule to every pod (embedded LiteLLM fixed: it had
   no pod context; the image runs as uid 1000, verified at import, not yet in
   service on the bench for lack of a key); the agent pods (Job and PipelineRun) carry the
   seccomp profile at pod level; `runner.runtimeClass` (gVisor, Kata) applies to
   every agent pod when the policy does not already impose gVisor, which always wins.
   **Bench series `e`/`f` (2026-09-25, images of `main` after #37)**: series `e` died
   on a revoked OAuth token (redone, kind only); series `f` showed **two real defects**
   of the admission queue (ADR 0013). 1) Two HR runs "exceeded 20 min" **without
   a pod ever having run**: their Job was waiting for a slot, and the queue wait
   counted against the stage's budget. 2) The suspended Jobs of these dead runs stayed
   at the **head of the queue** — no one read their state any more, and `_admettre` admits only the
   oldest: five tickets frozen behind two zombies, unblocked by hand
   (`kubectl delete job`). Fixed: the queue wait has its own bound (`FILE_MAX_MINUTES`,
   six hours) and does not consume the budget; any run that `await_run` no longer awaits
   (budget exceeded, never admitted) **removes its Job** and marks itself `failed`; the Temporal
   bounds cover the queue. Three tests. What series `f` proves otherwise: the
   `tool_called` guarantee **refuses** when `verifier_adresse` is not in the ledger (6
   verdicts logged), and the code (`DEMO-2f`) passes implement → verify with the new token.
   What it still does not prove: a real tool call by an agent (the HR runs did not
   run), nor the cost (`direct` mode).
   **Bench series `g` (2026-09-25 09:12–09:58, orchestrator fixed from #48 loaded in kind)**:
   five tickets, eleven runs, **zero zombies** — the queue emptied by itself, each refused run
   ended, each ticket finished in `needs_human` (abandonment approval) and it
   Job remains in the namespace: series `f`'s fix holds on the bench.
   `tool_called` still refuses rightly (RH-1g/2g, `verifier_adresse` never in the ledger).
   But ten runs out of eleven died in `agent_silencieux`: **"OAuth access token has been
   revoked"** — the token copied into `agent-creds` was revoked by Claude Code's local
   rotation during the series (only `DEMO-1g` implement, started at 09:12, finished). `direct`
   mode with this token is **not reproducible** beyond a rotation: a real tool call
   and cost remain unproven until a provider key is in LiteLLM.
   **Replay finally real**: four bench histories archived in `tests/replay/histories/`
   (`DEMO-2d` code up to `done`, `RH-1d` sourcing → qualification, `DEMO-1e` dead on
   401, `RH-1f` never admitted) and **replayed** against the current interpreter — the test no longer
   skips. Temporal payloads are in base64: the gateway key (in `direct` mode, the OAuth
   token itself) and the run tokens were in them; they are redacted
   at archiving, gitleaks passes on the folder, and the internal API's URL is
   allowlisted there by pattern (`generic-api-key` false positive).
   **P2 (closed guardrails) delivered**: `sandbox.unknown_requests: allow|reject` in the policy (`contract-change`, schemas and types regenerated); in `reject` — the `regulated`
   preset — a permission request whose nature the runner does not recognize is
   **refused** with a message that names `report_finding` and `request_scope_change`; in
   `allow` (default) it passes and the log says "a net, not a wall". What is recognized
   (declared read, deduced write) does not change. Not yet: prompt-injection detection
   (ticket content marked untrusted, patterns) — OpenHands has it, we do not.
   **P2 (injection detection) delivered**: `choregos_core.injection` spots, lexically and
   in two languages, the coarse patterns (ignore instructions, role reassignment,
   prompt exfiltration, sending to a URL, downloaded script, prompt tags, encoded
   payload) in what the agent is about to read — ticket, documents, context pack memories —
   **before** the run; `sandbox.prompt_injection: warn` (default) logs a
   `security.injection_suspected` event on the ticket, `block` (`regulated` preset) stops the stage
   before any run with the reason (the ticket is marked dead, a human re-reads), `ignore` does not
   look. Ten positive patterns, five ordinary tickets with no false positive, three modes
   tested on the orchestrator. What this is not: an understanding — a net against
   the coarse cases, as at OpenHands.
   **P2 (complexity) delivered**: the rules `C901`, `PLR0912`, `PLR0913`, `PLR0915` are
   **active** (thresholds 15 / 20 / 8 / 80 — the stocktaking found them all ignored);
   seven functions exceeded: `_check_references` (validator) and `type_of` (TS
   generator) split up, `_execute` (provisioning) turned into a table of steps, the "silent
   agent" diagnosis moved out of `Runner.execute`, two wide signatures accepted with a reasoned
   `noqa`. **A defect found while splitting**: the validator's actor loop re-read
   the variable of the transitions loop — the escalation from a human actor to an
   unknown actor was never reported; fixed and tested. The splitting of the big modules
   (`stage.py`, `interpreter.py`, `services.py`) remains to be done: they pass the thresholds.

1. **What the single-node demonstration does not prove**: it runs with a fake SCM, so
   the guarantees that read a diff (`scope_respected`, `diff_size_max`, `no_secrets`) **refuse**
   instead of passing — this is intended since 2026-09-23, a blind guarantee was worth worse than
   nothing. A ticket cannot be replayed under the same identifier (Temporal refuses a finished
   id). And the HR path has no profile database: the agents block, rightly.
2. **What no real agent has yet gone through.** Since the 2026-09-23 bench, four things
   have been delivered and are verified **only by tests**: the tool catalog, the run
   admission queue, the project without a repository, and the business roles. The previous bench
   had found seventeen defects that no test saw; it must be replayed before announcing that
   these four hold.
3. **The real deployment**: the test stack on kind is deliberately small (a development
   Temporal, a simple Postgres). The HA values — three-node Temporal, three-instance CNPG,
   Argo Rollouts — remain to be tuned on a real environment.
4. **The remaining runbooks** must be played once in staging. The restore one was,
   and it was wrong: that is the argument for playing the others.
5. **Chaos beyond the node**: network cut, full disk. The loss of a node is played
   (`tests/cluster/test_node_loss.py`); what remains is the spot eviction notice, which the
   platform suffers instead of listening to.
6. **Ecphoria**: E-01 to E-14 are delivered and pushed to upstream `main`. What comes out of it and
   concerns us: the bench with the Choregos profile (20,000 facts, 200,000 events, 50 reads/s)
   measures search at ~250 ms p50 / ~540 ms p95 on a workstation, above the
   300 ms p95 target; `retrieval_scan_cap` at 512 brings p95 down to 24 ms with no measurable
   recall cost at this corpus size. To be decided at provisioning, not in production.

7. **The engine, and what still binds it to software** ([ADR 0012](../../adr/0012-the-engine-is-not-tied-to-software.md)):
   of the five attachments written on 2026-09-23, **three fell** the next day — optional
   repository, evidence named by the trade, open roles. What remains are `StageOutputs`, typed for
   development and verifiable only by presence, and the imbalance of the guarantees, most of
   which can read only a code diff. Neither blocks a trade; they
   are left written down rather than fixed by an abstraction that nobody needs.

8. **What the queue split did not prove, and what it proves since 2026-09-27.**
   `worker.py` had declared from day one which activity goes on which queue — "slow external
   calls are isolated so as not to block the decision loop" — and **nothing
   enforced it**: `workflow.execute_activity` without `task_queue` schedules on the
   *workflow*'s queue. Everything therefore ran on `orchestrator`; the three specialized worker
   deployments that the chart creates never received a task. No test could see it,
   the three Temporal suites mounting a single worker carrying all the activities: it answers
   whatever queue is requested. What made it visible was a Diametral network policy
   that did take the split seriously.

   What the fix proves: `apps/orchestrator/tests/test_repartition_des_files.py` reads in
   the Temporal history the queue on which each activity was scheduled — `prepare_stage` and
   `start_run` on `executor`, `mirror_state` on `tracker`, `load_context` on the workflow's
   queue. The history carries this information from the scheduling, before a worker has taken
   the task: the assertion therefore does not depend on what a worker listens to. The three suites now mount
   **one worker per queue**, and the workflow's queue carries there only the activities with no declared
   queue — otherwise a broken routing would run there anyway.

   What it does not prove: nothing measures that the isolation *serves* any purpose. Nobody has
   shown that a saturation of the `tracker` queue left the decision loop responsive; that is
   the reason for being invoked by the comment, and it remains an intention. In production,
   `orchestrator` moreover keeps all the activities — a net so as not to leave a task
   orphaned during a version upgrade — so a routing that regressed in production
   would degrade instead of breaking. It is the test that forbids it, not the deployment.

9. **The first real agent run, and the six obstacles it revealed** (night of 2026-09-27).
   None was visible in tests; each was found by lifting the previous one. In order:

   | Obstacle | What it gave | Where it is fixed |
   |---|---|---|
   | a ticket was born in its **terminal** state | workflow `COMPLETED` in 1.5 s, zero steps, green board | #105 (`initial` contract) |
   | activities never went to their declared queue | `start_run` on `orchestrator`, `ConnectTimeout` to the Kubernetes API | #106 |
   | the ticket recorded `"Application error"` | the real cause lived only in the log | #106 |
   | the platform's `job-runner` Role lacked 3 verbs | Job created, then `403` on `PATCH secrets` | infra#750 |
   | the agent pod received a **nonexistent** egress proxy | first call dead on "Name or service not known" | #110 + deploy#13 |
   | opencode **refused its configuration** | `initialize` timed out, stderr never read | #111 (the diagnosis) then the MCP schema fix |

   What this proves: the chain ticket → interpreter → `executor` queue → Kubernetes API → Job →
   virtual gateway key works end to end on a real tenant, under Kyverno in
   Enforce and `clusterResourceWhitelist: []`. The agent Job is created, its secret belongs to it,
   its key is issued then revoked.

   What this does **not** prove: no agent has yet produced a result, the ledger
   still has no `kind=tool` row, and the product's cost remains at 2.7 × 10⁻⁵ USD — a probe
   call, not a run. The two proofs of blocker 4 remain open.

   The lesson of method: **five of the six obstacles were found by making a failure readable,
   not by reading code.** The only one that a test could have caught — the queue split —
   was invisible because the three Temporal suites mounted a single worker carrying all
   the activities. A set that always answers proves nothing.

10. **The two proofs of blocker 4 are acquired** (2026-09-27, 19:05 UTC, dev tenant).

    What the database says, and it is the only thing that matters:

    | `kind` | provider | model / tool | rows | € |
    |---|---|---|---|---|
    | `model` | `openai` | `platform/cheap` | 6 | **0.045872** |
    | `tool` | `github` | `lire_un_depot` | 1 | 0.000000 |

    Plus the `choregos.tool.called` event — tool `lire_un_depot`, provider `github`,
    **code 200**: the platform called the third-party API on the agent's behalf, with its own
    key, and counted it. The run carries `status: succeeded`, `cost_usd: 0.028728`, 30,858 input
    tokens, 1,241 output tokens, and its result says "Implementation plan drafted from the
    repository metadata".

    The chain proven, end to end, on a tenant under Kyverno in Enforce and
    `clusterResourceWhitelist: []`: ticket → interpreter → `executor` queue → Kubernetes API →
    agent Job → opencode → gateway (real cost) → MCP tool server → internal API →
    GitHub (200) → cost ledger.

    The `tool` row is at €0.00 because this tool is free. It is the ROW that `tool_called`
    requires, not its amount; the non-zero amount is on the `model` side.

    **What this does not prove.** A single tool, free, on an FQDN already open: nothing is
    proven about a paid tool or a provider that must be allowed on the network. A single backend
    (`opencode`) and a single model (`platform/cheap`). No scope guarantee was
    tried — the project has no repository, so no diff. And the price is a **copy** of the
    platform gateway's rates, copied into the tenant's values: nothing
    resynchronizes it.

11. **Two of my own changes of this night were unnecessary, and it is written here.**
    Believing I had discovered that the `k8s_job` executor did not serve the catalog, I made
    the URL announcement conditional (#112) then added a sidecar to the Job (#113). But
    `runner/outils_locaux.py` already serves these tools when no one else does — its header describes
    exactly the defect I thought I had found. My condition therefore removed a **true** announcement,
    and the sidecar restores at great cost what the runner did alone. Fixed.

12. **Seam C3: half delivered, and the undelivered half is a reasoned refusal.**

    The plan asked for `IdentityProvider` and `TenancyPolicy` as `Protocol`s, so that the enterprise
    edition substitutes without forking `auth.py` or `deps.py`. Looking at the real decision
    points, there was only one that was substitutable.

    **Tenancy needs no protocol**: the only decision is "can a second organization
    exist?", and it already goes through `edition.is_enterprise()`. The enterprise edition
    calls `declare(ENTERPRISE)` from its extension (seam C1) and the guard opens — without
    touching `projects.py`. The seam therefore exists, it is called `edition.declare`. What
    the enterprise adds on top (organization suspension, per-organization ceilings, RLS on
    the thirteen remaining tables) are **new** routes and columns, not
    substitutions: a `TenancyPolicy` would have had only one implementation and one caller.

    **Identity needs it for a single point**: the translation of IdP groups → roles per
    organization. The core knows how to do a prefix (`choregos:<org>:<role>`); the enterprise wants one
    per organization, and groups coming from SAML or SCIM (§2.2). It is delivered by
    `edition.declare_group_mapper`, with a test that verifies that the declared mapper is
    **really called by the login** — otherwise it would be a decorative setting. The rest of
    enterprise identity (SAML, SCIM, session revocation, strong re-authentication) are new
    routes.

    Why this is written here rather than built: a protocol with one implementation is the kind
    of abstraction that this plan itself says to refuse (§8, and the same lesson as C2, where the gates
    were already extensible). The day the enterprise edition exists and wants something else, it will
    ask for it with a precise case — and that will be cheaper than guessing today.

13. **The COMPLETE traversal is proven on the dev tenant** (2026-09-27, 22:19 UTC, chart 0.8.3).

    §10 proved the two ledger rows. This one proves a ticket's entire journey:

    `inbox` → the agent calls the model **and the tool**, writes `outputs.rapport`, the `outputs_present`
    guarantee **accepts** → `en_cours` → a human approves through
    `POST /work-items/{id}/decisions` → **`fini`**.

    The agent's report, word for word (translated): "Default branch: main. Main language: Python.
    Open issues: 1." The three facts are **exact**, verified against
    `GET /repos/VargaFoundation/choregos` afterwards. The agent invented nothing: it called
    the tool, read the response, and reported. Run `succeeded`, `cost_usd 0.017137`.

    Ledger at the end of the night: `model/openai` **10 rows, €0.131805**, 243,303 input
    tokens and 9,949 output tokens; `tool/github` **5 rows**; **5** `choregos.tool.called`
    events.

    **Two tickets are `fini`, and they sum up the night.** The first was born `fini` — it is §10's
    `jsonb` default, a ticket closed in 1.5 seconds without having done anything. The second
    really went through. The same state, two opposite truths: that is why this repository requires a
    test that fails in the absence of the guarantee, and not a green screenshot.

    **What failed along the way, and it is a good failure**: at the first attempt with the tool,
    `outputs_present` REFUSED. The agent had put its text in `summary` instead of
    `outputs.rapport`; the ticket retried once then escalated to `needs_human`, exactly
    as `on_fail` declares. The guarantee did its job — and it was my instruction that
    named an intention where a small model follows a **form**. Corrected by giving the exact
    JSON, the stage passed.

    **What this still does not prove**: a paid tool, a provider that must be opened to the
    network, more than one backend, more than one model, and any scope guarantee (this project has no
    repository, so no diff). Seven tickets remain in `needs_human`: the debris of the night of
    debugging, kept on purpose — each carries the message that killed it.

14. **The EE image built FROM the CE image: the obvious gesture ran as `community`**
    (2026-09-28).

    #129 stopped before the image, Docker no longer answering. Tried on
    `choregos-api:0.8.3`: `RUN pip install --no-deps choregos_ee-*.whl` **succeeds**, the image
    starts, and `GET /edition` answers `community`. The venv, created by uv, has no `pip`;
    the one on the PATH is the system's, which put the wheel in `~/.local`. Only the startup
    guard catches it, and only if the operator wrote `global.edition: enterprise`.

    The common stage of `docker/api.Dockerfile` now declares `PIP_PYTHON` pointing to the venv's python:
    the obvious gesture becomes the right gesture for both images (API and orchestrator),
    without the private repository knowing the CE image's internal layout.

    **What this proves**: `tests/paquets/test_image_ee.py` reads the Dockerfile (in `make ci`,
    red without the line — verified); and, with Docker and `CHOREGOS_IMAGES_CE`, builds a real
    image FROM a CE image, then exercises `create_app()` and the loading of the orchestrator's
    activities with `CHOREGOS_EDITION=enterprise`. Green on both targets built
    from this commit; **red on `choregos-api:0.8.3`**, with the startup guard as its message.
    The CE image still starts as `community`.

    **What this does not prove**: the CI plays only the static guard — on `main` it builds
    multi-platform and pushes without loading, so the Docker test does not run there. The images
    published up to 0.8.3 included have the trap; `docs/development.md` gives the workaround
    (`pip --python /app/.venv/bin/python`). The EE↔CE compatibility matrix and the `choregos-ee`
    repository remain to be done.
15. **Routes seam: an extension can serve routes, and only ADD them**
    (2026-09-28).

    An extension knew how to register a connector, a guarantee, an edition, a group mapper —
    not serve a route: `create_app()` included only its own. `extensions.declare_router`
    fills this gap; the routes are included under `/api/v1` after those of the core, and guarded by
    the core's authentication dependencies like any other.

    **Found along the way**: since FastAPI 0.141, `app.routes` no longer shows included routes (an opaque
    `_IncludedRouter`). The first version of the overlap guard looked for them there and saw
    nothing; the witness test "without an extension, no route" was vacuously green. The guard now reads
    the core's routers themselves, and the witness reads the OpenAPI.

    **What this proves**: `test_extension_routes.py`, with an extension really installed (a
    `.dist-info` on disk) — route served, anonymous `401` and connected `200`, presence in
    the OpenAPI, overlap with `GET /api/v1/orgs` refused at startup. Overlap guard
    removed, then inclusion removed: red each time.
    **What this does not prove**: a nested router is refused, not supported; no
    real extension uses it yet (the enterprise edition, for EE-1).
16. **The migrations are in the wheel, and an extension can bring its schema** (2026-09-28).

    The `choregos-api` 0.8.3 wheel carried **no** migration (`apps/api/migrations`, outside the
    package): no one could migrate a database with the published core, and an enterprise edition
    tested against its wheels could not stand up a real PostgreSQL. They now live in
    `choregos_api/migrations`, and `python -m choregos_api.migrate` — the command of the chart's Job and
    of the cluster bench — finds them wherever it is launched, instead of requiring `/app/apps/api`.

    An extension declares its revisions in the `choregos.migrations` group; they form an Alembic
    branch (`depends_on` a core revision) played by `upgrade heads`. It creates ITS
    tables and does not touch those of the core.

    **What this proves**: `test_migrate.py` — the core migrated from any directory; the
    branch of a really installed extension played after the core, then downgraded alone; a
    declared but absent location refused. `test_distribuables.py` counts each revision in the
    wheel (red when they are excluded). The PostgreSQL fixture goes through the same configuration:
    `test_rls_postgres.py` therefore exercises in CI the command that is deployed.
    **What this does not prove**: the chart's Job has not run on a cluster with the new
    command (`tests/cluster` will do it nightly); no real extension brings a schema yet.
17. **Admission seam: an extension can refuse that a run starts** (2026-09-28).

    The core bounds spend per run and concurrency per executor; it knew nothing of a
    limit that depends on something else — suspended organization, monthly ceiling, freeze. An extension
    declares it (`choregos_core.admission.declare_admission`); `prepare_stage` plays it before
    issuing the key and creating the run, and after the replay path.

    **What this proves**: `test_admission.py`, extension really installed — the check receives
    organization, project, ticket, run and budget; a refusal stops the stage without retry
    (`admission_refused`, not replayable), with no key or run in the database, with a reason that names the
    check; an already prepared run is not refused on replay; a failure of the check bubbles up and is
    retried instead of passing for a refusal. In negative: call removed → red; call placed before
    the replay path → red.
    **What this does not prove**: no dedicated event on the ticket (it would take an `EventType`,
    hence a `contract-change` PR) — the reason goes through the activity's failure, like the guard
    against injection. No real extension uses it yet.

18. **RLS covers what attaches to a project, and no table escapes it without a reason any more**
    (2026-09-28, [ADR 0026](../../adr/0026-what-the-core-rls-covers.md)).

    `run_events`, `deployments` and `gateway_keys` carried an organization's data outside
    RLS — an agent's log, the production deployments, the spend ceilings. They go
    under forced RLS through their project. The instance's catalogs remain open, by decision.
    Identity (`organizations`, `users`, `memberships`, `api_tokens`) remains outside RLS **on purpose**:
    the principal is resolved before the scope is set, and `require_platform_admin`
    would count only the caller's organizations — the administrator of a single one would become
    administrator of the instance. That is the enterprise edition's work.

    **What this proves** (PostgreSQL, non-superuser role): a session bounded to `a` sees
    only `a`'s rows in the three tables, a session without a scope sees none;
    it cannot write an event on `b`'s run; and `test_chaque_table_est_sous_rls_ou_exemptee`
    refuses any table that is neither under forced RLS nor exempted with its reason. Migration removed: the
    three turn red. API and orchestrator suites green with PostgreSQL available.
    **What this does not prove**: the load — a function per row on `run_events`, whose
    reads already filter by run; no latency measurement on a large log.

19. **Identity seams: revoking a session, granting platform administration,
    requiring fresh authentication** (2026-09-29).

    A signed session was valid until `exp`, with no recourse; an extension can now refuse it on
    each request (`declare_session_validation`) — this is the path for server-side
    revocation and SCIM deprovisioning. It can also GRANT platform administration
    in addition to the core's rule, never withdraw it. The session carries `iat`
    (`Principal.authentifie_le`), and `auth/login?reauth=1` makes the IdP be asked again.

    **What this proves**: `test_identity_seams.py`, extension installed — `iat` set; session
    revoked → 401 with the reason at the next request; failing validation → the exception bubbles up;
    an extension grants `/platform/*` to whom the core refuses it, without withdrawing the right from the admin of
    both organizations; `reauth=1` → `prompt=login&max_age=0`, absent otherwise. Each of the three
    seams removed: red.
    **What this does not prove**: no real IdP honored `prompt=login` in front of us; the front end
    does not yet offer the re-authentication path.

20. **Human gesture control: an extension can refuse one project too many, or an approval
    without fresh authentication** (2026-09-29).

    `extensions.declare_gesture_check` on `project.create`, `workitem.decision` and
    `release.approve`, played after the core's rights and before the route acts. Response chosen by
    the nature of the refusal: 403, 409, or 401 that sends to `auth/login?reauth=1`.

    **What this proves**: `test_gesture_checks.py`, extension installed — a project too many is
    refused with 409 and is not created; a decision without fresh authentication is refused with 401
    and the request is not decided, then accepted with a recent session (`iat` a few
    seconds old); a release's approval goes through the same control and is not recorded.
    Each of the three calls removed: red.
    **Verified that there is no bypass**: the web route is the ONLY path that decides a
    request. The Slack buttons are links to it; the `/choregos approve` comments are
    parsed (`github_events.parse_command`) but the workflow translates them into nothing (`_absorb`) —
    an announced and inert feature, filed as a finding. The day it is wired, it
    will have to go through `check_gesture`.

21. **Ending a login: another protocol opens a session exactly like OIDC**
    (2026-09-29).

    The end of the OIDC callback (user, roles through the mapper in service, `auth.login` trace,
    cookie signed with `iat`, bounded redirect) becomes `routers.auth.terminer_la_connexion` /
    `ouvrir_la_session`, which the callback itself uses. A protocol brought by an extension —
    SAML, for the enterprise edition — therefore opens the same session, without a second hand-written path.

    **What this proves**: `test_identity_seams.py` — an extension route that calls
    `terminer_la_connexion` sets a session with `iat`, gives the role drawn from the group, traces
    `auth.login` with its channel, and bounds an external target (red when the bounding is removed); the
    existing OIDC tests pass on the refactored callback.
    **What this does not prove**: the core verifies NOTHING of the identity passed to it — that is up to
    the caller (assertion signature, audience, freshness), and it is written in the function.

22. **Two findings closed: a test that waited for an instant, a comment that lied**
    (2026-09-29, #135, #138).

    `test_hotfix_uses_express_lane` waited for the `collecting` state after approval; it now observes
    the EFFECT — production promoted from departure (outside hours), no canary before
    approval, the canary followed after. Without the approval signal, it turns red. The original failure
    could not be reproduced (12 isolated runs, 12 in parallel, two complete suites): this
    fix removes the race, it does not prove having seen it.

    The human-request comment invited answering with `/choregos approve`; these comments
    are parsed but never translated into a decision — the human answered, and the ticket stayed blocked
    without a sign. It now points to the interface, and S3-03 moves to 🟡.

23. **The webhooks routed NOTHING on PostgreSQL — and Tekton's was not authenticated**
    (2026-09-29).

    Found while inventorying the paths that read the database without a scope. The webhook routes
    (GitHub, Tekton, Argo CD, Alertmanager, Jira, GitLab) took a session WITHOUT a scope; the fail-closed RLS
    hid all the projects from them, and each event was thrown away as "no known
    project". The API tests run on SQLite, without RLS: no one saw it. On the dev tenant
    (PostgreSQL), no tracker, CI or CD event therefore ever reached a ticket —
    the §13 traversal went through the internal tracker and the internal API, not through a webhook.

    Fix: `deps.DbPlateforme`, a session of scope `*`, for the routes authenticated by
    signature or secret. While writing it, Tekton's webhook turned out to have NO verification at all:
    harmless as long as RLS hid everything from it, an anonymous write path as soon as it sees the
    projects. It now requires the shared secret, in a header or in the sink's URL (`?jeton=`,
    Tekton adding no header), and refuses outside development when it is not configured.

    **What this proves**: `test_rls_postgres.py::test_un_webhook_achemine_son_evenement_sous_rls`
    — on PostgreSQL, non-superuser role, a Tekton event reaches its ticket (0 before the
    fix); `test_bare_routers.py` — Tekton refuses without a secret or with a wrong one, accepts in a
    header and in the URL, refuses in production without a configured secret (red when the verification
    is removed).
    **What this does not prove**: only Tekton is tried on PostgreSQL; the other routes take
    the same session and verify their secret before reading, without a PostgreSQL test each. The tenant's
    CloudEvents sink must receive `?jeton=` at the upgrade.

24. **The whole API suite on PostgreSQL: two more defects, including a privilege escalation**
    (2026-09-29).

    After the webhooks (§23), the question was "how many others?". `CHOREGOS_TEST_SUITE_SUR_POSTGRES=1`
    makes the API's 171 tests run on PostgreSQL, schema migrated by `migrer`, non-superuser
    role; the CI now does it at every PR, after the SQLite pass.

    - **A project role became an organization role.** The principal was resolved before the
      scope was set; the join on `projects` (under RLS) saw no project, and a PROJECT
      membership arrived without a project — hence as a role over the whole organization. A
      developer invited on one project read the others. Identity is now resolved in platform
      scope, then the session is bounded.
    - **`POST /orgs` returned 500**: the new organization's audit trace was refused by
      `audit_log`'s RLS, the session being bounded to the caller's EXISTING organizations — the very
      gesture that the enterprise edition unlocks.

    Two security tests expected SQLite-specific responses (403, "ambiguous slug"); under
    RLS, PostgreSQL answers 404 and sees only the caller's project — it does not even reveal the other.
    They now admit both, and require in both cases never to return another
    organization's project.

    **What this proves**: `test_rls_postgres.py` — a project role remains a project role; a
    second organization is created (201); each red when its fix is removed. The entire API suite
    passes on PostgreSQL (171) and on SQLite.
    **What this does not prove**: the orchestrator suite still runs on SQLite only; its
    sessions are all of scope `*` (platform activities), which reduces the risk without
    excluding it.

25. **Identity under RLS: the 25 tables are covered or exempted with their reason** (2026-09-29,
    ADR 0026 updated).

    `organizations`, `memberships`, `users`, `api_tokens` go under forced RLS: an organization
    sees neither the name nor the members of another; a user is visible to the organizations of which
    they are a member, and always to themselves (`app.current_user`). What concerns the instance is read
    in an explicit and restored platform scope (`in_platform_scope`): the count of
    organizations that decides the platform administrator, the lookup of an invitee by
    email. Creating an organization goes into platform scope once the right is established.

    Condition lifted since ADR 0026: the principal resolves in platform scope (0.10.1), and
    the entire API suite runs under RLS in CI. Without this net, this change would have been a
    blind closing.

    **What this proves** (PostgreSQL, non-superuser role): a session bounded to `a` sees
    only `a`'s name, members, users and tokens, a session without a scope nothing;
    the admin of one organization out of two does not create an organization (red if the count is not done
    on the instance); inviting a member from elsewhere does not create a duplicate (red without the platform
    scope); a user without a membership reads their account and creates a token (red without
    `app.current_user`). Entire API suite: 176 green on PostgreSQL.
    **What this does not prove**: the organizations count in `/audit` has no test that fails
    in its absence — `audit_log`'s RLS already hides the platform rows; it stays as is,
    commented, and no unproven code was added for it. The enterprise edition must move
    SAML into platform scope before accepting 0.11 (its range stops at `<0.11`).

26. **Neighborhood: a namespace quota per organization, and a Temporal queue per organization
    ruled out by decision** (2026-09-29, [ADR 0027](../../adr/0027-noisy-neighbours-at-admission-and-quota.md)).

    The `ResourceQuota` of a project's namespaces was fixed (32 CPU, 96 Gi, 40 pods). An extension now
    sets it according to the organization (`choregos_core.quotas`), read by the provisioning step that
    writes into the GitOps repository. With admission (concurrent and monthly run ceilings, 0.9), an
    organization exceeds neither its runs, nor its budget, nor its quota. The Temporal queue and the
    PostgreSQL pool per organization are ruled out: what consumes is the runs, not the orchestration.

    **What this proves**: `test_quotas.py` — an installed extension sets the quota of ITS organization
    in the written manifests, another keeps the default, without an extension the historical default; a
    badly written quota is refused before Argo CD. Quota reading removed from the provisioning: red.
    **What this does not prove**: the quota takes effect at the next provisioning; nothing rewrites
    those of existing projects.

27. **A request's transaction is committed before the response is sent** (2026-10-04, #161).

    FastAPI by default closes `yield` dependencies AFTER sending the response; `get_db` committed
    its transaction there. Measured on a stack served by uvicorn (foundation trial, item 9): just after
    a development login of a new user, the next request answered 401 "stale
    session" six times out of ten, the user not being written yet. Worse: a `commit` that
    fails afterwards left the client with a success on a lost write. `Db` and `DbPlateforme`
    move to `Depends(..., scope="function")`: the session closes before sending.

    **What this proves**: `test_commit_before_the_answer.py` calls the ASGI application directly —
    `ASGITransport` waits for the application to finish and saw nothing — and requires that the `commit`
    precede `http.response.start`; red without the fix. Entire suite green, including the API
    suite on PostgreSQL.
    **What this does not prove**: the behavior behind a proxy that buffers responses (it
    would mask the race, not the lost write).

28. **The authentication time is the IdP's, and `reauth=1` requires it to be recent** (2026-10-04,
    #153).

    `Principal.authentifie_le` read the session's `iat`: the time when Choregos had opened it.
    After a silent SSO reconnection — the IdP still has a session and hands back without asking
    anything — this time is new while the user has not authenticated for a
    long time. Every "recent authentication" gate was therefore satisfied by a round trip
    to `/auth/login`, including the enterprise edition's freshness check. The session now carries
    the ID token's `auth_time` (read without verifying its signature: it comes from the `token` endpoint
    over TLS, OIDC Core §3.1.3.7); after `?reauth=1`, an `auth_time` that is absent or earlier than the handshake
    is refused (401); `authentifie_le` reads `auth_time`, and `iat` only as a fallback.

    **What this proves**: `test_fresh_authentication.py` — an SSO reconnection keeps the IdP's
    time; a `reauth` that the IdP did not honor, or without `auth_time`, is refused; a
    fresh re-authentication opens a session that says so; without `auth_time`, the old
    behavior. Guards removed: three reds.
    **What this does not prove**: a SAML session (enterprise edition) still carries `iat` only;
    the `AuthnInstant` would have to be passed there.

29. **An extension's tools reach the agent; the core's migrations describe only its
    tables** (2026-10-04, foundation trial).

    An extension could serve routes (`declare_router`) but no tool reached
    the agent: the catalog could be read only from a deployment file. The
    `declare_tool_provider(name, list_tools, call_tool)` seam makes them go through the catalog's
    path: announced by `GET /internal/runs/{id}/tools` to the `choregos-tools` MCP server,
    called by `POST /internal/runs/{id}/tools/{nom}` under the run's token, under the same ceiling,
    with a row in the cost ledger (`greffon:<name>`) and the `tool.called` event. A name already
    taken is refused with 409, never masked. And `db.models.is_core_object`, Alembic's `include_object`
    filter: an extension registers its models in the core's `Base`; without this filter,
    `test_migrations.py` turned red as soon as an extension had been imported earlier in the test
    session, and an autogeneration would have brought its tables into a core migration.

    **What this proves**: `test_extension_tools.py` (announcement, counted call, 401 without a token, 409 on
    collision, red without the guard); without the filter, `test_migrations.py` turns red after an
    extension's tests.
    **What this does not prove**: the price of an extension tool (€0 in the ledger, to be decided).

30. **The SCM adapter writes files on a branch** (2026-10-04, foundation trial).

    So that the PLATFORM opens a PR with its changes, token kept — the
    `gitops.pull_request` effect of the future foundation. `ScmAdapter` knew how to create a branch and open a PR, not
    write a file. `commit_files(repo, branch, files, message)`: GitHub through the Contents API (one
    commit per modified file), an identical file is not rewritten; the fake does likewise, and
    its `open_pr` reuses a PR open on the same branch, like GitHub.

    **What this proves**: adapter tests — creation, update with the `sha`, identical
    file skipped, read failure without write; the fake's idempotence.
    **What this does not prove**: a single commit for several files (Git Data API), nor a
    trial against a real repository.

31. **`global.extraEnv`: a setting that the chart does not know yet, without forking the chart**
    (2026-10-04).

    To enable, for example, an extension shipped inactive on a single environment. The variable arrives
    through `choregos.commonEnv` at the API, each worker and the migration Job — an extension enabled in
    the API but not in the migrations would start without its tables. A variable already set by the
    chart is refused at rendering: Kubernetes would keep the last of the two without saying anything.

    **What this proves**: `tests/charts/test_extra_variables.py` — the variable arrives at
    each process that reads the settings, migration Job included; nothing without a value; a name already
    set (`CHOREGOS_DATABASE_URL`) is refused, red without the guard.
    **What this does not prove**: the guard knows only the `commonEnv` variables; a variable
    set by a single template (the API's OIDC) can still be doubled.

32. **The development stack starts on a new workstation: RustFS replaces MinIO** (2026-10-04, #152).

    The MinIO server image is no longer published, neither on Docker Hub nor on quay.io: `dev/compose.yaml`
    started only on a workstation that had it cached. RustFS (`rustfs/rustfs:1.0.1`, Apache-2.0)
    replaces it, same credentials and same ports; it also issues STS credentials bounded by a
    session policy (foundation trial, item 7: Lakekeeper, PyIceberg and DuckDB pass with
    it).

    **What this proves**: `tests/dev/test_development_stack.py` — each image of the stack carries
    an explicit tag, and none is the MinIO server; both red on the old stack.
    **What this does not prove**: RustFS's behavior under load and over time; the data of an
    existing MinIO volume is not carried over (new `s3data` volume).

33. **The foundation's phase 0 trial is delivered, inactive by default** (2026-10-04, #151).

    The trial proves, on Choregos's core, the thin slice that carries the future foundation (Varga Foundation's
    specification): a YAML ontology validated and compiled, findings
    synchronized from an NDJSON v1 observations report, read by an agent by means of the MCP tools
    generated with its run token, a `gitops.pull_request` action proposed by the agent, approved by
    a re-authenticated human and opened by the platform, and a proof by collector rerun.
    Each item has its report in `trials/*/RESULTS.md`. The `choregos-ontology` package carries the
    extension, declared by its entry points and **shipped inactive**: it activates only with
    `CHOREGOS_ESSAI_ONTOLOGIE=1` (`global.extraEnv`).

    **What this proves**: `packages/ontology/tests` (109 tests, two of them on PostgreSQL), and
    items 1 to 6 replayed against an integrated stack served by uvicorn (`trials/local-start`).
    Inactive, the extension serves no route, announces no tool, and has no table (tested).
    **What this does not prove**: a model that chooses these tools, the decision in the console, a
    PR on a real repository, resumption after failure (the action engine runs in the request, not in
    Temporal). That is the object of the tests on the dev tenant, and of the D0 decision report.

34. **The dev tenant's console answered 404 since its installation: the Ingresses are annotated**
    (2026-10-05).

    The dev tenant's ingress controller (NGINX Inc.) refused the console's Ingress: the API's
    had taken the host, and this controller does not admit two Ingresses on the same host ("All hosts
    are taken by other resources"). Every console page returned nginx's 404; only the API
    (`/api`) answered. Its remedy is the mergeable Ingress (one master, minions), which requires
    annotating both: `choregos-api.ingress.annotations` and `choregos-web.ingress.annotations`.

    **What this proves**: `tests/charts/test_ingress_annotations.py` — both Ingresses carry the
    requested annotations, none without a value; red without the change.
    **What this does not prove**: the master Ingress is set by the deployment (`choregos-deploy`),
    not by this chart; the console served on the tenant is observed after the upgrade.

35. **The console says which edition it runs on: `GET /edition` served under `/api/v1`**
    (2026-10-05).

    The contract places `/edition` under `/api/v1`; the API served it only at the root, which the Ingress
    sends to the console. The console therefore could not know whether it ran in the community
    or enterprise edition, and `/admin` showed nothing of the enterprise edition. The route is
    now served under the prefix (the root remains, outside the schema, for the probes); the top
    bar carries an edition badge, and `/admin` a card that lists what the edition allows itself — or,
    in community, what the enterprise edition would add, without a fake screen.

    **What this proves**: `test_l_edition_se_demande_sous_le_prefixe_du_contrat` (404 without the
    change); three Vitest tests of the card and the badge; the Playwright journey sees the badge.
    **What this does not prove**: no screen of the enterprise edition's functions — that is the
    S17 stream (administration sections declared by manifest).

36. **A project's groups can be changed only by the organization administrator**
    (2026-10-05).

    ADR 0014 holds access to the catalog's tools by two locks: the deployment says who has the
    right (`groups` on the tool), the project says what it uses (`config.tools`). But
    `config.groups` lived in the project's configuration, which its owner modifies: they
    could add themselves to another trade's group and obtain its tools. Changing the groups
    now requires the `tools:grant` permission, which only the organization administrator carries;
    changing `tools` with equal groups remains with the project team.

    **What this proves**: `test_tool_groups.py` — a project owner who adds themselves to a
    group gets 403 (200 without the change); they keep control of their tools; the administrator
    changes the groups. The RBAC matrix stays green.
    **What this does not prove**: the connectors' per-operation policy (S19 stream), which
    will replace these two lists.
