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
| exfiltrating data, or polluting the internet | egress is closed and bounded in three scopes that only narrow one another — organisation ceiling, project policy, agent version — with a deny list that always wins and IP literals refused (ADR 0070). **Enforced by layer**: a **per-run egress proxy** (S40-01c) is the wall for host, port and connection rate; the tool broker and the platform's own fetchers (MCP connectors, knowledge crawls, git skill sources) are a wall for what the platform requests: the ceiling is read at declaration and again at every call, a refusal is a 403 journaled with layer `broker`, calls to one host are throttled organisation-wide and carry an identifying User-Agent (S40-01d); the built-in `web_fetch` tool is the one place where method, robots.txt, User-Agent and politeness hold for what an agent reads (S40-01f); the runner's guardrails and the backend's settings are a net (S40-01b). Every decision is journaled per run (`network_decisions`), and `network` is part of an agent version's digest only when set. The per-project Squid of the GitOps provisioning (allowlist = `allow_domains`, `CONNECT` to 443 only) stays for per-project namespaces: host-level, project-level | `choregos_core/network.py`, `services/network.py`, `services/platform_network.py`, `services/web_fetch.py`, `tests/test_platform_network.py`, `tests/test_web_fetch_tool.py`, `gitops.py` (`render_egress_proxy`), `tests/cluster/test_egress_proxy.py`, `tests/cluster/test_sandbox_egress.py` |
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
- **The network policy is a wall only where the layer is.** The egress proxy sees a TLS tunnel, not
  what is inside it: **method, path, headers and User-Agent are invisible to it**, and one tunnel can
  carry many HTTP/2 requests, so its rate limit is a *connection* rate, not a request rate. robots.txt
  and method rules can hold only where the platform makes the request (the broker, the crawlers, the
  governed `web_fetch` tool). Claude Code's own `WebSearch` runs **at the model provider**, outside
  every Choregos network layer: the only lever is to deny the tool (`builtin_web_tools`), and that is
  a setting in the pod, not a wall. The runner's guardrails are a **net**: the agent shares the pod,
  and a `curl` from Bash is not a URL permission request. On the Diametral tenant the infra's FQDN
  policy lets runner pods reach eight hosts directly and its baseline policy lets every pod of the
  namespace reach every other one (PostgreSQL and Temporal included): until infra changes that, the
  Choregos proxy is bypassable for those hosts.
- **`choregos-egress` limits.** Its rate limits count per replica and per connection; it stops a
  name that resolves to a private address and connects to the address it resolved (one resolution), but
  a wall also needs the chart's NetworkPolicies (or the platform's) to stop runner pods going out
  another way. The per-project Squid of per-project namespaces stays host-level and project-level.
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

## Agents written in code

A code agent ([ADR 0063](adr/0063-agents-written-in-code.md)) runs in the run's pod, in the sandbox,
with the run's token and the run's own gateway key, and no secret an ordinary agent would not have.
Its image is **named by the operator and pinned by digest** (a workflow or an agent version names an
image, never a reference; a tag-only reference stops the platform at start-up). The SDK sends every
tool call and every write through the runner's permission path; that path is the reason the SDK, not
the user's code, makes the call. It is a convention the SDK keeps, not a wall: Python code in the pod
can open a socket or run a command like any program, and is bounded by the sandbox, not by the SDK.
Cosign verification of agent images is not built yet.

## What a run remembers

A run reads only the memories its agent version binds **and** that reach its project
([ADR 0075](adr/0075-an-agent-reads-several-memories.md)): a global memory with reach `projects` is
invisible to the runs of the others, a project memory to every other project, and an unresolvable
binding is skipped, not read from elsewhere. A namespace that is not the project's shared or own form is
never read as the shared one (`split_namespace` refuses it). What an agent writes to a **global**
memory is a proposal: nothing it says becomes a fact the whole organisation reads until an
administrator accepts it. Publishing a version that binds a global memory needs to see it; a memory a
caller cannot see answers 404, and "unknown memory" at publication, so its existence is not revealed.
Facts carry their organisation and sit under forced row-level security.

## Decisions that need a fresh sign-in

Some gestures are too consequential to be made from a browser left open: the session must have been
authenticated recently (`step_up_minutes`; the API answers `401 step_up_required` and the console
signs the person in again). They are made by a person in a session, never by an API token or an MCP
client, which propose and do not decide:

- deciding a governed action (approving one; [ADR 0035](adr/0035-governed-actions-in-the-core.md));
- **releasing an agent revision to production, or to a canary share, and loosening the release
  policy of an agent** ([ADR 0071](adr/0071-an-agent-version-reaches-production-through-channels.md);
  default 10 minutes; a personal agent is exempt; an edition can add its own rule through the
  `agent.release` gesture check). A release also runs the eval suite again and keeps a history of
  every move.

## Injection through context

Context packs and tickets contain text written by third parties. Two rules:

1. it is **marked untrusted** in the prompt ("data, not instructions");
2. it grants **no power**: even if an agent follows an injected instruction, the mechanisms
   above apply unchanged. A gate that cannot read what it must check refuses.

Drafting an agent from a description (S39-05) sends the description and the **names and one-line
descriptions** of the organisation's skills, knowledge bases and connector operations to the
gateway's model, never documents, files or secrets. The description is marked as data; the answer is
filtered to names that exist, and a draft is never saved or run.

## Remote agents: who they act as

A remote agent (A2A, [ADR 0062](adr/0062-remote-agents-over-a2a.md)) runs outside the platform, with the
rights of **the person who authorised its credential** — Atlassian's Rovo has no service account. Treat
that authorisation as granting that person's access to the agent, use a dedicated least-privileged
account, and read `authorised as` on the credential. The refresh token is a sealed value of the secret
store (ADR 0058): never returned, rewritten at every refresh, deleted on revocation. A run receives only
a short-lived access token, in its Secret (`CHOREGOS_RUN_REMOTE_TOKEN`), never in a payload or a journal.
The agent card is pinned at publication and read again by the pod: a card that changed stops the step
before anything is sent. The runner pod may reach only the hosts the project's policy allows. The
project's memory is not sent to a remote agent.

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
- The organisation's model catalogue (ADR 0060) limits which models an agent may name; it is a
  governance control in front of the gateway, not a replacement for the key's own model list. With an
  OpenAI-compatible gateway the `run_key` is shared by runs: scope it at the gateway (a consumer with
  a token rate limit), since Choregos cannot cap it.
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
- GitHub App installations are tenant boundaries ([ADR 0073](adr/0073-github-app-installations-are-discovered-not-typed.md)):
  one App serves the deployment, so a token for `owner/repo` is minted only through an installation
  **claimed** by the organisation of the connector or run. With several organisations the claim needs
  GitHub user authorization (the person must manage the installation, checked with the App's OAuth
  client); without it, typing another company's repository name yields no token, for a connector or
  for a runner's clone. The callback's `installation_id` is never trusted alone, and the install
  `state` is HMAC-signed, short-lived and bound to the signed-in person.
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
