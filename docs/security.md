# Security — what protects what

This page answers one question: **what stops an agent from doing damage?** The answer is
never "the prompt forbids it".

## The threat model, plainly

A coding agent runs unreviewed code, in a repository that matters, with a model that can be
wrong or manipulated (ticket text, repository content, poisoned memory). We assume **an
agent may attempt anything** — by mistake or by injection. And a platform that hosts several
organisations must assume **a member of one will try to read another**.

## What stops it

| Threat | Mechanism | Where |
| :-- | :-- | :-- |
| writing outside the allowed paths | ACP permission refused → diff verified after the run → revert → gate `scope_respected` | `guardrails.py`, `scope.py`, `gates/` |
| exfiltrating data | egress denied by default; the only way out is a **per-project Squid proxy** whose allowlist is the policy's `allow_domains`, non-root, read-only, `CONNECT` to 443 only; the agent's own guardrails refuse the same domains first | `gitops.py` (`render_egress_proxy`), `tests/cluster/test_egress_proxy.py`, `tests/cluster/test_sandbox_egress.py` |
| using credentials | there are none in the workspace; the run token only authenticates the agent **to us**, and never leaves for a third party (tool catalogue, ADR 0014) | `docker/runner.Dockerfile`, `catalogue.py` |
| pushing a secret | `no_secrets` on the diff, sensitive files refused, gitleaks in CI | `gates/`, `guardrails.py`, `.gitleaks.toml` |
| leaking a secret through a pod log | the tail of a run's pod log is archived only after `redact` (known token shapes, bearer/basic credentials, URL user-info, values of secret-named keys) — a floor, not a scanner (ADR 0054) | `choregos_core/redaction.py`, `executor/log_archive.py` |
| spending without limit | one virtual key per run with a hard cap, a budget per ticket, tool calls in the same ledger | ADR 0004, ADR 0014 |
| touching production | `prod.requires_train` in the DSL, release train, Argo windows, GitHub Environment | ADR 0006 |
| changing what an agent does | an agent has a scope — common, organisation, project or personal — and one server-side function decides who may publish, suspend or pin it, with the reason for every refusal: common and organisation agents are an `org_admin`'s, a project's agent its `project_owner`'s, a personal agent its owner's (and an `org_admin`'s); an agent you may not see is a 404; a personal agent or another project's agent can neither be pinned nor named in a workflow nor run in one (ADR 0061) | `services/agent_governance.py`, `routers/agents.py`, `services/agents.py`, `tests/test_agent_governance_matrix.py` |
| changing its own configuration | `.choregos/**` refused for writing (except `result.json`) | `guardrails.py` |
| escaping the container | non-root, no capabilities, seccomp — **required** by a strict Kyverno rule on every pod of a `choregos-*` or `proj-*` namespace (a pod with no security context is refused, not tolerated); no service-account token; `RuntimeClass gvisor` where the policy requires it, `runner.runtimeClass` (gVisor, Kata) for every agent pod where the deployment chooses it | `infra/policies/pod-security.yaml`, `tests/charts/test_securite_des_pods.py`, `k8s_job.py`, `tekton.py` |
| prompt injection through what the agent reads | lexical detection over the ticket, its documents and the memory pack **before** the run: `sandbox.prompt_injection: warn` journals a security event on the ticket, `block` (the `regulated` preset) stops the stage before any run with the reason — a net against the crude cases, not an understanding; the walls remain the ones below | `choregos_core/injection.py`, `activities/stage.py` |
| lying about evidence | the runner **runs** the tests and overwrites declared evidence; gates read the diff, not the prose | `dod.py`, `result.py`, `gates/` |
| reading another organisation | PostgreSQL row-level security, **fail-closed**: every session declares the organisations it may see, an undeclared session sees nothing | `db/session.py`, `deps.py`, migrations `b2d4f6a8c0e1` and `c3e5a7f9b1d4` |
| reading another organisation's audit trail | `audit_log` carries an organisation and is under the same policy; `GET /audit` additionally restricts to the organisations where the caller really holds `audit:read` — being a member is not enough. Platform-level rows (`org_id IS NULL`: logins, API tokens) are returned only when that right covers every organisation of the instance | `routers/admin.py`, `audit.py`, migration `c3e5a7f9b1d4` |
| a plugin running code in the console | a plugin's screens are **data** — forms, tables, actions, secrets shown once — rendered by the console's own blocks; the CSP stays `script-src 'self'`; a section naming a path no route serves stops start-up | `greffons.py`, `routers/ui.py`, `main.py`, ADR 0032 |
| an organisation left without an administrator | the last `org_admin` of an organisation cannot be removed (409) | `routers/admin.py` |
| logging in without an identity | OIDC with discovery, PKCE and a signed single-use `state`; development login off by default and refused in staging/prod | `routers/auth.py`, `config.py` |

## What is *not* a wall, and is said so

- **The runner's guardrails are a net by default, a wall on request.** Claude Code's ACP
  does not send `kind`, so the nature of a request is *inferred* from the title and
  arguments, and the access card counts how many decisions were inferred. A request whose
  nature cannot be classified at all is allowed and journaled by default; with
  `sandbox.unknown_requests: reject` (the `regulated` preset) it is **refused**, with a
  message that names the legitimate tools (`report_finding`, `request_scope_change`). The
  hard mechanisms remain the sandbox and the diff check after the run.
- **Row-level security needs a non-superuser role.** A PostgreSQL superuser ignores every
  policy. The API refuses to start as one in `staging` and `prod`; the embedded PostgreSQL
  creates `choregos_app` at initdb for that reason.
- **Development login opens the API** to anyone who can reach it. It exists for benches
  without an identity provider (`values/local.yaml`), nowhere else.

## Who curates a knowledge base

A knowledge base is what agents read as reference: whoever feeds it steers them, so curating is
a permission, not a convenience ([ADR 0051](adr/0051-knowledge-bases.md)). An organisation
administrator (`agent:manage`) curates every base and alone decides its **reach** (`scope`,
`projects`, `groups`). A base created from a project (`POST /projects/{id}/knowledge`) is owned by
it (`owner_project`): its owners (`project:write` on that project) upload, sync, distil, rename and
archive it, but cannot widen it to the organisation, to other projects or to groups, so a project
never decides what *other* projects' runs read. An administrator who widens it makes it the
organisation's: the owner stops curating. Another project's owners, and developers, read at most.

## Injection through context

Context packs and tickets contain text written by third parties. Two rules:

1. it is **marked untrusted** in the prompt ("data, not instructions");
2. it grants **no power**: even if an agent follows an injected instruction, the mechanisms
   above apply unchanged. A gate that cannot read what it must check refuses.

Drafting an agent from a description (S39-05) sends the description and the **names and one-line
descriptions** of the organisation's skills, knowledge bases and connector operations to the
gateway's model, never documents, files or secrets. The description is marked as data; the answer is
filtered to names that exist, and a draft is never saved or run.

## Secrets

- Never in Git, never in an image, never in a workspace.
- A connector's `env:NAME` reference is an allowlist, not a free read of the process environment: the
  API and the orchestrator also hold the platform's own secrets (session key, gateway master key,
  database password), so a name must match `CHOREGOS_SECRET_ENV_PATTERN` (default
  `^CONNECTOR_[A-Z0-9_]+$`) and `CHOREGOS_*`, `DATABASE_*`, `PG*`, `AWS_*`, `KUBERNETES_*`,
  `TEMPORAL_*` are refused whatever the pattern. It is checked when the reference is written and
  again when it is resolved. Only the organisation's administrator writes a reference; a project
  owner cannot. Before this rule, a role allowed to write a connector could point a secret field at
  a platform variable and have it sent to a host of its choosing.
- External Secrets Operator ↔ the provider's vault; one hand-made `ClusterSecretStore`.
- Git token minted **per run**, repository scope, 1 h TTL, injected in memory.
- Run token: ES256 JWT, `aud=internal`, `sub=run_id`, TTL `max_minutes + 15`, renewed while
  the run waits for a slot. Minted by `start_run` for the run's Secret only: never stored in
  `runs.stage_input`, never carried by a Temporal payload (#519).
- Gateway key: one per run, capped. `prepare_stage` reserves its cap (a `gateway_keys` row named
  `reserved:<run_id>`); `start_run` mints it and the executor puts it in the run's Secret next to
  the run token (`k8s_job`, `tekton`: Secret key `gateway-key`; `agent_sandbox`: sealed in the
  assignment, and given afresh on `resume`; `aca`: a job secret; `local_docker`: the container's
  environment). The runner reads it from `CHOREGOS_RUN_GATEWAY_KEY`. It is never stored in
  `runs.stage_input` and never carried by a Temporal payload (#519, #523).
- The key is revoked however the run ends: `collect_spend` at the end of a stage; `cancel_run`;
  and `record_workflow_failure` when the interpreter dies — cancelled, or `await_run` gave up —
  which books the spend of the ticket's runs left uncollected first. A revocation that fails is
  logged and the key dies at its TTL (`max_minutes + 30`). Migration `e7a9c1b3d5f7` scrubbed the
  rows written before #519.
- The run's Secret (`k8s_job`, `tekton`) is owned by what runs the run, so the garbage collector
  takes it with that object: the Job, collected by `ttlSecondsAfterFinished` (#499); the
  PipelineRun, collected when it is deleted — Tekton has no TTL of its own, so a namespace without
  a pruner keeps PipelineRuns and their Secrets (#501). A queued run is a suspended Job, which
  never finishes, so its TTL never starts and Kubernetes stops `activeDeadlineSeconds` while it is
  suspended: `await_run` deletes it at `queue_timeout_minutes`, and `record_workflow_failure`
  deletes the Job of every queued run of an interpreter that dies (#501). A Temporal termination
  runs no code: a queued run of a terminated interpreter keeps its Job and Secret.
- Compatibility, for one release: a runner without `CHOREGOS_RUN_GATEWAY_KEY` (started by an
  orchestrator older than #523) uses the key its input carries, as before; `start_run` hands to
  the Secret, and removes from the row, a key an older `prepare_stage` stored there. A runner
  image older than #523 behind a newer orchestrator gets no key: the orchestrator and its
  `runner_image` ship together.
- Connector secrets typed in the console ([ADR 0058](adr/0058-connector-secrets-are-write-only-values.md)):
  sealed in `org_secrets` (forced RLS) with an AES-256-GCM envelope — a random data key per value,
  wrapped under a key-encryption key that the **deployment** holds (`CHOREGOS_SECRETS_KEKS`, never the
  database), the organisation, name and version bound as authenticated data. No plaintext, no hash.
  An organisation administrator can set, replace and clear a secret but **cannot read it back**: no
  response, audit entry or `last_error` carries the value, and the platform reads it only to build an
  adapter (values it resolved are masked in logs and error texts). A `store:` reference is derived by
  the platform and refused when written by hand, so a connector cannot point at another's secret.
  A database dump or backup alone reveals nothing; losing the KEK loses the values.
- Webhook secrets: injected by the chart from the vault (or generated on a bench); an
  unconfigured GitHub webhook is refused in `staging`/`prod`.
- Rotations: [`runbooks/rotation-secrets.md`](runbooks/rotation-secrets.md).

## Supply chain

Images signed with cosign (keyless, GitHub OIDC), **SBOM and SLSA provenance attached**
(BuildKit attestations) on `main` and on releases, Trivy on the published image before it is
signed, digest references enforced by Kyverno, Tekton Chains signing `PipelineRun`s.
Renovate proposes bumps; every agent bump goes through conformance. A nightly CRITICAL in
the dependency scan fails the night and opens an issue.

## Default skills and MCP servers

The platform ships two catalogues ([ADR 0065](adr/0065-catalogues-of-mcp-servers-and-skills.md)):
well-known MCP servers and default skills. They are files in the image, generated from a reviewed seed
and recorded sources; **an entry enables nothing**. Installing one goes through the gate that already
exists: an MCP entry becomes an ordinary organisation connector (administrator only, secret by
reference, tools born closed); a skill is assessed by the supply-chain gate again at install, a
high-risk one waits for a second administrator, and a bundled one is checked against its sha256
provenance. A skill enters the catalogue only from a listed publisher, under an open licence, at a pinned
commit; Anthropic's proprietary document skills and anything unlicensed are never listed or bundled.
A changed upstream reaches an organisation only when a catalogue release is merged **and** an
administrator updates the skill: two people, as ADR 0050 asks against bait-and-switch.

## Reporting a vulnerability

[`SECURITY.md`](../SECURITY.md) at the root — including the list of what we do **not**
guarantee yet. Do not open a public issue for a vulnerability.
