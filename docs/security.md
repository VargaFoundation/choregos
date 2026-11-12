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
| a skill that carries an attack (injection, exfiltration, a vulnerable dependency) | the supply-chain gate's pattern scan ([ADR 0050](adr/0050-skill-supply-chain-gate.md)), then SkillSpector offline on every version a person publishes ([ADR 0095](adr/0095-skillspector-behind-the-skill-gate-offline.md)): AST, taint, YARA, MCP tool poisoning, OSV advisories from a local mirror, no call leaving the pod; a HIGH finding or `DO_NOT_INSTALL` holds the version for a second administrator, a failed scan holds it too | `services/skill_gate.py`, `services/skill_scanner.py`, `scanners/`, `apps/api/tests/test_skill_gate.py`, `apps/api/tests/test_skill_scanner.py` |
| exfiltrating data, or polluting the internet | egress is closed and bounded in three scopes that only narrow one another — organisation ceiling, project policy, agent version — with a deny list that always wins and IP literals refused (ADR 0070). **Enforced by layer**: a **per-run egress proxy** (S40-01c) is the wall for host, port and connection rate; the tool broker and the platform's own fetchers (MCP connectors, knowledge crawls, git skill sources) are a wall for what the platform requests: the ceiling is read at declaration and again at every call, a refusal is a 403 journaled with layer `broker`, calls to one host are throttled organisation-wide and carry an identifying User-Agent (S40-01d); the built-in `web_fetch` tool is the one place where method, robots.txt, User-Agent and politeness hold for what an agent reads (S40-01f); the runner's guardrails and the backend's settings are a net (S40-01b). Every decision is journaled per run (`network_decisions`), and `network` is part of an agent version's digest only when set. The per-project Squid of the GitOps provisioning (allowlist = `allow_domains`, `CONNECT` to 443 only) stays for per-project namespaces: host-level, project-level | `choregos_core/network.py`, `services/network.py`, `services/platform_network.py`, `services/web_fetch.py`, `apps/api/tests/test_platform_network.py`, `apps/api/tests/test_web_fetch_tool.py`, `gitops.py` (`render_egress_proxy`), `tests/cluster/test_egress_proxy.py`, `tests/cluster/test_sandbox_egress.py` |
| deciding from a tracker comment | a `/choregos approve\|reject\|answer\|scope\|pause\|resume\|stop` comment is answered on the tracker with a console link, audited as `tracker.command.redirected`, and never signalled to the workflow: a tracker login is not a Choregos principal, and decisions need the console and its fresh sign-in ([ADR 0102](adr/0102-a-tracker-comment-carries-no-decision.md)) | `routers/webhooks.py` (`_answer_tracker_command`), `apps/api/tests/test_tracker_commands.py` |
| using credentials | there are none in the workspace; the agent never holds the run token: the proxy variables it inherits carry an egress token that opens its run's network routes and no other ([ADR 0115](adr/0115-the-agent-never-holds-the-run-token.md)), and the proxy's reports also need the proxy's own key, which no run's pod receives, so the agent cannot rewrite its run's network counters ([ADR 0145](adr/0145-the-proxy-reports-with-a-key-the-agent-does-not-hold.md)); and on every executor the agent, the project's checks (which run the code it wrote) and the runner's git start from the runner's environment without any `CHOREGOS_RUN_*` variable but the run id (`agent_base_environment`, S57-02); a job (ADR 0056) receives the run token on purpose, by name, and it never leaves for a third party (tool catalogue, ADR 0014) | `docker/runner.Dockerfile`, `catalogue.py`, `apps/api/tests/test_egress_token.py`, `tests/charts/test_egress_proxy_chart.py`, `packages/runner/tests/test_egress_env.py`, `packages/runner/tests/test_agent_holds_no_run_token.py` |
| pushing a secret | `no_secrets` on the diff, sensitive files refused, gitleaks in CI | `gates/`, `guardrails.py`, `.gitleaks.toml` |
| leaking a secret through a pod log | the tail of a run's pod log is archived only after `redact` (known token shapes, bearer/basic credentials, URL user-info, values of secret-named keys) — a floor, not a scanner (ADR 0054) | `choregos_core/redaction.py`, `executor/log_archive.py` |
| a run writing to a third party without a governed action | a deployment catalogue tool that may write (an http POST, or a remote MCP tool) is neither listed nor called unless the deployment marks it `read_only: true`; a direct call answers 409 `catalogue_write_refused`; writes go through organisation MCP connectors, whose operations are governed (ADR 0034 §7) | `choregos_core/catalogue.py` (`may_write`), `catalogue.py`, `apps/api/tests/test_catalogue_write_is_refused.py` |
| spending without limit | one virtual key per run with a hard cap, a budget per ticket, tool calls in the same ledger | ADR 0004, ADR 0014 |
| a connector reaching a project that did not ask for it | an organisation connector is `all_projects` or `selected_projects`; a selected one reaches only the projects an owner or an administrator enabled, enforced in the broker, in the project policy behind governed actions and in the project's operations list (ADR 0074) | `services/catalogue_rights.py`, `apps/api/tests/test_connector_enablement.py` |
| touching production | `prod.requires_train` in the DSL, release train, Argo windows, GitHub Environment | ADR 0006 |
| changing what an agent does | an agent has a scope — common, organisation, project or personal — and one server-side function decides who may publish, suspend or pin it, with the reason for every refusal: common and organisation agents are an `org_admin`'s, a project's agent its `project_owner`'s, a personal agent its owner's (and an `org_admin`'s); an agent you may not see is a 404; a personal agent or another project's agent can neither be pinned nor named in a workflow nor run in one (ADR 0061) | `services/agent_governance.py`, `routers/agents/`, `services/agents.py`, `apps/api/tests/test_agent_governance_matrix.py` |
| changing its own configuration | `.choregos/**` refused for writing (except `result.json`) | `guardrails.py` |
| the platform calling a host nobody opened | an **organisation egress allowlist** (`org_egress_rules`), decided in the console by the organisation's administrator and enforced by the platform's own outbound clients on every request, redirects included, **failing closed** when a call has no policy in context; a deployment **ceiling** per pod family refuses, before sending, a host the cluster's network does not open; every governed "add" opens its host first (ADR 0066) | `choregos_core/egress.py`, `choregos_adapters/http/egress.py`, `services/egress.py`, `routers/egress.py` |
| escaping the container | non-root, no capabilities, seccomp — **required** by a strict Kyverno rule on every pod of the `choregos` namespace, a `choregos-*` or a `proj-*` one (a pod with no security context is refused, not tolerated; a container's own `seccompProfile` or `appArmorProfile`, an init or ephemeral container's, or the pod's AppArmor profile, set to `Unconfined`, is refused too); no service-account token; `RuntimeClass gvisor` where the policy requires it, `runner.runtimeClass` (gVisor, Kata) for every agent pod where the deployment chooses it | `infra/policies/pod-security.yaml`, `tests/charts/test_pod_security.py`, `k8s_job.py`, `tekton.py` |
| the agent writing outside its workspace, or reaching the network around the egress proxy | the runner launches the agent through a Landlock self-restriction ([ADR 0094](adr/0094-the-kernel-confines-the-agent-at-launch.md)): writes beneath the workspace, `$TMPDIR` and `/dev` only; TCP connections to the proxy's and the direct platform endpoints' ports only, when a proxy is set; no `ptrace` of the runner. Ports, not hosts; UDP not filtered; nothing under gVisor (`best_effort` journals it, `enforce` refuses the run) | `choregos_runner/confine.py`, `packages/runner/tests/test_confinement.py` |
| prompt injection through what the agent reads | lexical detection over the ticket, its documents and the memory pack **before** the run: `sandbox.prompt_injection: warn` journals a security event on the ticket, `block` (the `regulated` preset) stops the stage before any run with the reason — a net against the crude cases, not an understanding; the walls remain the ones below | `choregos_core/injection.py`, `activities/stage.py` |
| a secret the agent read leaving for the model provider | with `sandbox.llm_security_analyzer` (the `team` and `regulated` presets) the run's and the hosted session's gateway key are minted marked, and LiteLLM's `pre_call` guardrail masks known token shapes, long bearer/basic credentials and URL user-info in the request **before** the provider call, and its `post_call` guardrail masks them in the model's answer (text, tool arguments and reasoning, streamed or not, on `/chat/completions`, `/v1/messages` and `/v1/responses`; a signature or encrypted reasoning left as is) before the agent or the record sees it, and withholds an answer it fails on; never in the agent's process, never turned on or off by the request. Only the LiteLLM gateway enforces it: with `direct` or `openai-compatible` the flag refuses the stage and the session; a filter, not a scanner (ADR 0084) | `choregos_core/llm_guardrail.py`, `charts/choregos/charts/litellm`, `activities/run_key.py` |
| a tool server flooding or steering the agent through its answer | every broker result is checked against the `outputSchema` its server declared (422 without the payload), its secrets masked with `redact`, and cut with a marker to its operation's cap (`CHOREGOS_BROKER_RESULT_MAX_BYTES` unless an organisation administrator set one), never above `CHOREGOS_BROKER_RESULT_CEILING_BYTES`, its data counted once (a text block that serializes its `structuredContent` is not counted again, #1374); the injection guard then reads what is left (#755, S40-17). A catalogue or plugin tool's answer is masked and cut the same way, to the cap its catalogue entry declares or the default, without a schema (#759), then read by the same injection guard (S40-18); `web_fetch`'s page and `web_search`'s results are masked and cut the same way, at the default (#1383), and the API names the bound it applied (`result_max_bytes`), up to which the run's tool server shows the model the result. The guard reads an MCP answer's `structuredContent` as well as its text, and a hosted session's tool answers as a run's (S40-18b); a session's model reads a `structuredContent` its text does not serialize, masked, capped and guarded with it (S40-18c) | `services/broker.py` (former name `services/courtier.py`), `apps/api/tests/test_broker_result_bounds.py`, `apps/api/tests/test_catalogue_plugin_result_bounds.py`, `apps/api/tests/test_operation_result_cap.py`, `apps/api/tests/test_catalogue_plugin_injection_guard.py`, `apps/api/tests/test_guard_structured_content.py`, `apps/api/tests/test_result_bound_both_doors.py` |
| an MCP gateway widening what an agent may do, or an agent reaching it | the organisation's gateway is consumed, never run by Choregos: it holds the upstream credential, Choregos holds a sealed gateway credential and signs a 60-second ES256 assertion per call; the internal-hosts rule refuses an endpoint inside the cluster; its host is denied to runs and refused for the runners family; a connector's approvals, result bounds and injection guard are unchanged; mode `required` refuses direct connectors before any action exists (ADR 0105) | `services/mcp_gateway.py`, `services/mcp_gateway_identity.py`, `tests/licences/test_mcp_gateway_boundary.py` |
| lying about evidence | the runner **runs** the tests and overwrites declared evidence; gates read the diff, not the prose | `dod.py`, `result.py`, `gates/` |
| reading another organisation | PostgreSQL row-level security, **fail-closed**: every session declares the organisations it may see, an undeclared session sees nothing | `db/session.py`, `deps.py`, migrations `b2d4f6a8c0e1` and `c3e5a7f9b1d4` |
| reading another organisation's audit trail | `audit_log` carries an organisation and is under the same policy; `GET /audit` additionally restricts to the organisations where the caller really holds `audit:read` — being a member is not enough. Platform-level rows (`org_id IS NULL`: logins, API tokens) are returned only when that right covers every organisation of the instance | `routers/admin.py`, `audit.py`, migration `c3e5a7f9b1d4` |
| a governed action running without a kept approval, or an approval never run | its workflow starts only after the approval commits, a relay in every API replica starts an action left `approved` (every `CHOREGOS_ACTION_RELAY_INTERVAL_S`, 30 s by default; two API processes relaying it still run it once, S40-23j), and the workflow's first activity runs nothing for an action that is not `approved` (a failed commit, a hand-made start, a finished action); an audit row and an event commit with the change they record, or not at all ([ADR 0085](adr/0085-the-approved-row-is-the-outbox-of-a-governed-action.md)) | `db/session.py`, `services/action_relay.py`, `activities/actions.py` |
| an extension running code in the console | an extension's screens are **data** — forms, tables, actions, secrets shown once — rendered by the console's own blocks; the CSP stays `script-src 'self'`; a section naming a path no route serves stops start-up | `extensions.py`, `routers/ui.py`, `main.py`, ADR 0032 |
| an organisation left without an administrator | the last `org_admin` of an organisation cannot be removed (409) | `routers/admin.py` |
| keeping a role the identity provider withdrew | at each IdP sign-in, the organisation roles the IdP granted (`memberships.source = idp`) and no longer maps are removed and audited (`member.revoke`); the last `org_admin` is kept, with a warning. A tenant's sign-in revokes in its own organisation only. Roles granted by people stay, and are never converted. Rows that existed before this release are manual, so roles withdrawn at the IdP before the upgrade are not revoked. Bearer tokens and open sessions keep their roles until that sign-in (finding filed) (ADR 0100) | `routers/auth.py`, `db/models.py`, `apps/api/tests/test_idp_membership_revocation.py` |
| logging in without an identity | OIDC with mandatory discovery (issuer checked, 503 otherwise, never cached), PKCE, a `nonce` and a signed single-use `state`; the ID token's `iss`, `aud`, `nonce` and `sub` (equal to userinfo's) are checked; the console identity is bound to the provider's `sub`, and an e-mail only links an unbound account the provider does not call unverified (ADR 0100); development login off by default and refused in staging/prod | `routers/auth.py`, `config.py` |
| a page on another site acting with your console session | Origin guard on cookie-authenticated mutations: the `Origin` must be the console's or a listed CORS origin (compared lower-cased, without `:443`/`:80`), `null` is refused, an empty `Origin` counts as absent, only an `Authorization: Bearer` header exempts the request (any other scheme still falls back to the cookie), a cross-site or same-site fetch without `Origin` is refused; `SameSite=Lax` cookies; explicit CORS methods and headers, never `*` | `origin_guard.py`, `apps/api/tests/test_request_edge.py` |
| a token of the run's API presented from outside the cluster | the run's API (`/api/v1/internal/*`: what a runner, a hosted session's pod, the per-run egress proxy and the outbound relay call, each with its own token) is guarded by the name a request arrives under ([ADR 0144](adr/0144-the-run-s-api-answers-only-where-runs-are.md)). With `CHOREGOS_INTERNAL_API_GUARD=enforce`, a request under a declared public hostname of the API (`Host`, or any `X-Forwarded-Host` entry, which the console's relay writes) gets the 404 of an unknown route before any token is read, unless the hostname is a run door (`CHOREGOS_RUN_DOOR_HOSTS`), which the chart routes to `/api/v1/internal` and nothing else; the in-cluster Service is served as before. `warn`, **the default**, refuses nothing and logs `internal_api.would_refuse`; `enforce` is never applied to the address runs are sent to | `internal_api_guard.py`, `apps/api/tests/test_internal_api_guard.py`, `charts/choregos/charts/api/templates/httproute.yaml`, `tests/charts/test_run_door.py` |
| an outside agent driving a workflow, or deciding in a person's place | the A2A door (ADR 0078) serves only the workflows that opted in (`a2a:`), to a token with the `a2a` scope (a `*` token is refused, and an `a2a` token is refused by the REST API); the caller acts with its person's rights, a resent message is the same work item (id derived from organisation, client and `messageId`), a waiting decision is answered `input-required` with the console's `decision_url` and a reply to a task is refused; every call is audited (`a2a.call`), rate-limited per token, counted in the daily write budget shared with the MCP door, and a message is capped (20 parts, 100,000 characters; file parts within the project's `a2a.max_file_bytes`, 10 MiB by default, each and together, in a media type the skill lists, never fetched by `uri`, stored as input artefacts before the work item exists and audited as `workitem.input_file`; the body the door reads is bounded by the organisation's largest project limit) | `a2a/door.py`, `mcp/appelant.py`, `apps/api/tests/test_a2a_inbound.py` |
| an application acting under a person's name, or outliving its purpose | a machine account (ADR 0131) belongs to one project and is a `kind: machine` principal, which every gate asking for a person refuses; its tokens open the A2A door only, are bound to the project and expire within 365 days; it has no group, so no approval rule can name it, and the door takes no decision; the OAuth path refuses it; removing it ends its tokens at once | what the application does with a token it holds: a stolen token acts within the project's opted-in workflows until it is revoked or expires |
| a process that outlives its run (a cancel that failed, a pod slow to die, a queued pod that starts late) writing as that run | when the orchestrator stops waiting it closes the run's row first — a terminal status, the end date and the result the workflow is told, committed before the executor is asked to cancel, whatever the cancel then does; a closed run's API answers 409 `run_closed` on every route but its two append-only journals (`events`, `network/decisions`) and `POST …/result`, whose idempotent answer changes neither the run nor its work item; a closed run has no network, and the runner refused at its first call exits with 0 ([ADR 0143](adr/0143-a-run-that-ended-stays-ended.md)) | `choregos_core/run_closure.py`, `activities/execution.py`, `deps.py` (`OpenRunAuth`), `apps/api/tests/test_run_closed.py`, `apps/api/tests/test_run_closed_postgres.py`, `apps/orchestrator/tests/test_run_closed_when_the_wait_ends.py` |

## What is *not* a wall, and is said so

- **The origin guard is a browser's wall only.** A request without `Origin` and `Sec-Fetch-Site` (a CLI) passes, and bearer tokens are not cookie-bound.
- **The guard of the run's API is a wall only once it enforces, and only for the names the deployment
  declared.** Its default is `warn`: until an operator sets `enforce`, `/api/v1/internal/*` still
  answers on the public hostnames to whoever presents a valid token. It reads names: a caller inside
  the cluster uses the Service's name and is not stopped by it (the tokens and the network policies
  are), a hostname routed to the API that nobody declared stays served
  (`choregos-api.publicHostnames`), and a proxy that rewrites `Host` without writing
  `X-Forwarded-Host` hides the public name. A run door is as public as its route: restrict it at the
  ingress ([deployment](deployment.md#the-runs-api-and-the-run-door)).
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
  governed `web_fetch` tool). The governed `web_search` (ADR 0083) sends the query from the API, under
  the ceiling, refuses a query redaction would change, and records only its fingerprint; but what a
  backend keeps of a query is **declared, not verified**: the shipped backends declare nothing, and an
  organisation's `data_handling` override is believed on its `terms_ref`.
  Claude Code's own `WebSearch` runs **at the model provider**, outside
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
- **Closing a run does not take back what was minted before.** A run token cannot be revoked, so
  the platform closes the run instead ([ADR 0143](adr/0143-a-run-that-ended-stays-ended.md)): the
  token then opens the run's journals and nothing else. But the run's git token lives its hour
  (`contents: write` on the run's repository), the egress proxy serves the network answer it cached
  for up to `cache_seconds` (one minute by default), and a tunnel already open stays open until it
  closes. The running run of an interpreter that died is left open on purpose — a restarted
  interpreter awaits it again — and a Temporal termination runs no code, so it closes nothing.
- **Row-level security needs a non-superuser role.** A PostgreSQL superuser ignores every
  policy. The API refuses to start as one in `staging` and `prod`; the embedded PostgreSQL
  creates `choregos_app` at initdb for that reason.
- **Development login opens the API** to anyone who can reach it. It exists for benches
  without an identity provider (`values/local.yaml`), nowhere else.

## Egress: three layers, and what is not guarded

The platform makes outbound calls for a tenant (the MCP broker and discovery, the git skill fetch,
the knowledge sources, a remote agent's card). Three layers decide whether one may leave
([ADR 0066](adr/0066-the-organisation-egress-allowlist.md)), after the organisation's **network
bounds** — the deny list and allowed set of its policy ceiling's `network` section, with the
instance's deny list — which are read first, in every mode, by the same guarded transport and by the
declaration and call checks: a deny always wins, over an opened host too, and a host the bounds
refuse cannot be opened (ADR 0070, amendment of 2026-10-19: one model, the list opens, the ceiling
bounds):

1. **The organisation's list** (`/admin/egress`): hosts, ports and the pod family that calls them,
   decided by the organisation's administrator (`tools:grant`), requested by anyone, audited.
2. **The deployment ceiling** (`global.egress.ceiling`): what the operator says the cluster's
   network lets through, per family. A host the organisation allowed but the ceiling excludes is
   refused before it is sent, with a message naming the platform change.
3. **The cluster's own network policy**, which the application cannot see and **cannot change**: it
   holds no credential to the platform's repository and must not hold one. The console shows the
   change to make (a snippet from the operator's template, an optional prefilled issue); the
   platform team applies it.

`CHOREGOS_EGRESS_MODE` is `enforce` (the default), `warn` (log `egress.would_refuse` and pass; the
default under `CHOREGOS_FAKES=1`) or `off`. In `enforce`, a guarded call with no policy in context
is refused: a forgotten call site fails loudly. **Guarded**: the MCP client, the web, Confluence and
Graph knowledge sources, the git skill fetch; a `git` source checks its host up front and forbids
redirects, because a subprocess cannot be wrapped. **Not guarded**: the deployment's own
dependencies (gateway, Temporal, memory, object store, OIDC issuer, the platform's GitHub App) and,
until phase 2, the REST connectors (Jira, GitLab, Slack…).

**Runners.** A rule whose pod family is `runners` joins every run of the organisation as an implied
host of its effective network (ADR 0070, amended): the runner's own guard reads it from the stage
input, the per-run egress proxy from `GET /internal/runs/{id}/network`, the per-project proxy the
provisioning renders from the project's union; every deny list (instance, organisation ceiling,
project) still wins over it. An administrator's "allow this host for agent pods" therefore works in
every project without editing each policy. A wildcard rule (`*.x.test`) becomes the domain `x.test`, which the
guard and the proxy read as the name and its subdomains. The proxy is rendered when a project is
provisioned: a host opened later reaches the guard at the next run and the proxy at the next
provisioning render. A project whose own list is empty gets exactly what its organisation opened.

## Who curates a knowledge base

A knowledge base is what agents read as reference: whoever feeds it steers them, so curating is
a permission, not a convenience ([ADR 0051](adr/0051-knowledge-bases.md)). An organisation
administrator (`agent:manage`) curates every base and decides its **reach** (`scope`,
`projects`, `groups`), as do the owners of the base's owning project (ADR 0081, amended). A base created from a project (`POST /projects/{id}/knowledge`) is owned by
it (`owner_project`): its owners (`project:write` on that project) upload, sync, distil, rename and
archive it, and may widen it to the organisation, to other projects or to groups (it is their
project's base, as a GitHub repository admin sets its visibility; every change is journalled). An
administrator who widens it makes it the organisation's: the owner stops curating. Another project's owners, and developers, read at most.

Since S42-11 ([ADR 0081](adr/0081-knowledge-base-rights.md)) people can also hold a **role on one base**
(`viewer`, `editor`, `owner`) by a grant to a user, a group or a project, and a base can be `restricted`:
people then read it only through a grant, and a run is served it only on behalf of the person who
started its work item, when that person reads it ([ADR 0097](adr/0097-a-run-reads-a-restricted-base-on-behalf-of-its-initiator.md)):
an agent no longer reads for someone what that person may not read. What the run writes after reading
it stays with the base's readers: its work item, runs and artefacts are read only by people who read the base
([ADR 0114](adr/0114-what-a-run-writes-after-reading-a-restricted-base-stays-restricted.md)). Only an administrator or the owners of its project widen
or restrict a base. A grant never crosses organisations. A source read with a service account (Confluence)
mirrors everything that account sees, so restricting the base and granting roles is the confidentiality
boundary, not the source's own permissions.

## Agents written in code

A code agent ([ADR 0063](adr/0063-agents-written-in-code.md)) runs in the run's pod, in the sandbox,
with the run's token and the run's own gateway key, and no secret an ordinary agent would not have.
Its image is **named by the operator and pinned by digest** (a workflow or an agent version names an
image, never a reference; a tag-only reference stops the platform at start-up). The SDK sends every
tool call and every write through the runner's permission path; that path is the reason the SDK, not
the user's code, makes the call. It is a convention the SDK keeps, not a wall: Python code in the pod
can open a socket or run a command like any program, and is bounded by the sandbox, not by the SDK.
Cosign verification of agent images is not built yet.

## The builder of hosted zips

The rootless `buildkitd` the chart deploys for hosted zips (S41-13, ADR 0080 §9) is the one pod of the
platform with a weaker profile than the strict rule: it runs the `RUN` steps of a Dockerfile the
platform writes over files a user uploaded, and rootless BuildKit makes a user namespace (through
the setuid `newuidmap`, so privilege escalation stays allowed and SETUID/SETGID stay in the bounding
set) and mounts inside it, which the RuntimeDefault seccomp profile and AppArmor refuse. The
container is therefore `Unconfined` for both, and a Kyverno `PolicyException` (`choregos-buildkitd`)
lifts the `non-root` rule of `choregos-pod-security` for that Deployment's pods only. That rule
refuses `Unconfined` on any container, init container (native sidecars included), ephemeral
container or pod AppArmor profile; the exception is the one place it is lifted.

What contains it instead: uid 1000, every other capability dropped, no service account token, a
read-only root filesystem, no platform credential (the push credential of the build repository
only), and a NetworkPolicy that admits the orchestrator alone and lets it out to the Internet,
never to a private range. A build is a user's code running in that pod: the pod, not a per-step
sandbox, is the boundary (`--oci-worker-no-process-sandbox`). A cluster that cannot grant this
profile names a builder of its own (`global.images.buildkitAddress`). A node that restricts
unprivileged user namespaces (Ubuntu 24.04's `kernel.apparmor_restrict_unprivileged_userns=1`)
refuses the builder until that setting is lifted.

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

## A customer's own identity provider

When an edition lets each organisation sign in with its own provider
([ADR 0108](adr/0108-each-organisation-signs-in-with-its-own-identity-provider.md)), that provider is run
by the customer, not by the operator, so the core does not trust it as it trusts the instance's:

- it signs in only addresses whose domain the edition's resolver gives to the same organisation; a
  domain no tenant owns is signed in by the instance's provider only;
- the roles it grants land on its own organisation, whatever its groups name;
- its subjects are stored as `tenant:<org id>:<sub>`, never mixed with another provider's;
- a discovery document that cannot be read fails the sign-in instead of being guessed, and an address it
  marks unverified is refused.

An edition proving another protocol (SAML) finishes its sign-in with `terminer_la_connexion(org=…)` to
get the same confinement. The resolver must map a domain only to an organisation that proved it owns it.

## Decisions that need a fresh sign-in

Some gestures are too consequential to be made from a browser left open: the session must have been
authenticated recently (`step_up_minutes`; the API answers `401 step_up_required` and the console
signs the person in again). They are made by a person in a session, never by an API token, an MCP
client or an A2A caller, which propose and do not decide:

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

## Who started a run, and what it read

A work item records who started it, `<kind>:<subject>`, written once by the door it came through and
never by an agent ([ADR 0077](adr/0077-delegated-identity-between-steps.md) §2): `person:<email>`
(console, REST, API token), `mcp:<email>`, `a2a:<client>`, `schedule:<name>`, `work_item:<key>` (a
fan-out child), `tracker:<kind>`, `finding:<id>`, `eval`, `demo`, `trial:<email>`, or `system`. At stage
start the orchestrator copies it onto the run and records `inputs_from`: the runs that produced the
latest revision of each artefact the step reads (declared inputs, and the spec or plan its prompt
embeds); a document a person edited since is not credited to the run that wrote it. `GET
/runs/{id}/access` shows the result as `chain`: the initiator, then each run with its agent and
version, earliest first, walked within the work item. A run recorded before S40-22 has no `chain`.

A step reads an earlier step's artefact only through its declared inputs: `GET
/internal/runs/{id}/inputs/{name}` answers 404 for any other name, and leaves no read receipt. Not yet
partitioned: `GET /internal/runs/{id}/ticket` still gives every step the spec and the plan (#890). A
step still runs with the platform's identity; acting on behalf of the initiator (token exchange,
ADR 0077 §3) is not implemented (#756).

## Tracker writes: who they act as

Two kinds of writes reach a tracker, under two different accounts ([ADR
0107](adr/0107-an-agent-acts-under-an-identity.md) point 4):

- **The bookkeeping** — the state, the status comment, the Cost, Size, Risk and Run fields, the
  questions to a person, the close-out — is written by the platform, through the project's tracker
  connector and the vendor's REST API, under that connector's account: the Jira connector's `email`,
  the Choregos GitHub App, the owner of the GitLab token, a plugin's server. The tracker connector's
  card says "Choregos writes as …"; each state change and question it wrote is recorded with `via:
  {type, account}` and shown on the work item's timeline.
- **An agent's own actions** go through the MCP tools its version names. The broker calls them with
  each organisation connector's credential, which never reaches the agent's pod; a run lists them,
  with the account each calls as (a `basic` username, else "the connector's key"), as `tool_connectors`.

`account` is read from the connector's stored configuration and is never a secret: an email, a
username, a plugin's server; `null` when the configuration names none. Not yet: acting as the person
who started the work (on-behalf-of, ADR 0107 stories 2 and 3, #756); every write above is the
connector's account.

## A tool call under the person's identity (ADR 0135)

By default a connector is called with its own credential (`service`). A connector may instead be called
as the person who talks to the assistant, so the source decides what that person reads
([ADR 0135](adr/0135-a-tool-call-may-carry-the-person-s-identity.md), `services/identity_exchange.py`).

**Declaring it.** In the connector's configuration:

```yaml
identity_modes: [service, user_exchange]   # absent: [service]
token_exchange: {audience: iris-mcp}       # required for user_exchange
```

The agent version's `mcp_servers` entry picks the mode (`identity: user_exchange`; absent: `service`).
Only conversations use it today; runs, realtime and pod sessions keep `service`.

**What happens.** At each call the platform exchanges the person's token at the identity provider
(RFC 8693: `subject_token` = the person's access token, `audience` = the connector's, `requested_actor`
= `assistant:<slug>`) and calls the source with the exchanged token as bearer. The token serves one call;
it is never stored, logged, journaled or handed to a run or a pod. The subject token is the bearer the MCP
door validated for that person, or a refresh token the console's sign-in sealed per person
(`user/<id>/oidc_refresh`, ADR 0058), forgotten at sign-out.

**No fallback.** Without a valid subject token, a refused exchange, or a connector that does not declare
the mode, the call fails with the typed tool error `identity_unavailable` and nothing reaches the source.
The `TOOL_CALLED` event carries `as: person:<subject>` (or `assigned:<connector>`) and `identity_mode`.

**What the identity provider must allow.** The exchange client (`CHOREGOS_TOKEN_EXCHANGE_CLIENT_ID` and
`_SECRET`, `_ENDPOINT`; by default the console's client and token endpoint) needs the realm's standard
token exchange enabled, permission to exchange towards each source's audience, and refresh tokens at sign-in (the realm's normal ones). Keycloak's standard exchange has no actor token and ignores
`requested_actor`: `act` is in the issued token only if a protocol mapper adds it. Sources must accept
tokens of their audience (`iris-mcp`, `insights360-mcp`, ...). Through an MCP gateway the mode is refused.

The dedicated security review of this exchange (ADR 0135) was done on 2026-11-11: see
[Review of the person's identity](#review-of-the-persons-identity-s63-07-s63-08-s63-14-2026-11-11). Set
`CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` (a JSON list) to the audiences of your sources: an audience outside it is
never asked of the provider, whatever a connector's configuration says. A JWT issued for another audience than
the one asked is refused.

### Connected accounts: `user_consent` (ADR 0135 points 1, 3, 4, 6)

For a source that has its own OAuth, the person consents once and the platform keeps their refresh token
(`services/connected_accounts.py`). The connector declares the mode and the source's settings:

```yaml
identity_modes: [service, user_consent]
user_consent:
  authorization_endpoint: https://source.example/oauth/authorize
  token_endpoint: https://source.example/oauth/token
  revocation_endpoint: https://source.example/oauth/revoke   # optional
  client_id: choregos
  client_secret_ref: store:connector/source/oauth_client_secret  # optional: a public client has none
  scopes: [read, offline_access]
```

The three endpoints must be plain `https` addresses. `client_secret_ref` can only be the connector's own
`store:connector/<name>/oauth_client_secret` (the catalogue install writes it) or, for a platform
administrator, an `env:` reference. The platform calls the token and revocation endpoints under the
organisation's egress allowlist (the install gate opens their hosts). The account remembers what the person
consented to (authorization server, client, source address): if a connector's editor changes one, the account
is not used until the person connects again.

The mode is picked by the agent version's `identity: user_consent`, or, in an assistant, by the BINDING of
the need (`assistant_tool_bindings.identity`). The routes, all about the caller's own accounts:
`GET /me/connected-accounts`; `POST /me/connected-accounts/{connector}/connect?org=` returns the source's
authorization URL (authorization code with PKCE `S256`; the state is signed, the verifier travels in a signed
`HttpOnly` cookie bound to the state by a nonce); `GET /me/connected-accounts/callback` exchanges the code,
seals the refresh token (`user/<id>/consent/<connector>`, ADR 0058) and redirects to the console, returning
no token; `DELETE /me/connected-accounts/{connector}?org=` deletes the sealed secret and the row at once, then
asks the source to revoke it (RFC 7009, best effort, only at the endpoint the person consented to). The table `connected_accounts` is under forced RLS on
the organisation and a restrictive owner-only policy: nobody else reads a person's account.

At each call the platform draws an access token from the refresh token (re-sealing a rotated one), calls the
source with it as bearer, and keeps nothing. A plugin's server receives it in `X-Choregos-User-Token` next to
the platform's own bearer. With no account (or one the source stopped honouring) the call fails with the
typed tool error `account_not_connected` carrying the console's connect URL
(`<public url>/me/connected-accounts/{connector}/connect?org=<org>`); the agent tells the person and goes on
without that source. There is no fallback to the connector's own identity. The MCP catalogue's `needs_oauth`
refusal no longer applies to an entry installed with `user_consent` (`POST .../mcp-catalogue/{id}/install`
with a `user_consent` body; the client secret is sealed).

### The standing delegation (ADR 0137 points 3-4)

A task of an assistant with `delegation: read_only` may read on a person's behalf while they are away (a
scheduled run, a poll). That is the one place the platform keeps a long-lived credential of a person, so it
is narrow and visible:

- **The agreement is the exact list.** `POST /me/delegations {org, assistant, task, agree_to}` is a 409 that
  shows the task's needs and their operations until `agree_to` equals that list. Read-only only: a task that
  lists a write need cannot be delegated (422), and a write attempted under a delegation is refused with the
  typed reason `write_under_delegation` before any source is called. A task has no conversation to confirm a
  write in, so there is no proposal to make (the proposals of S63-10 belong to a conversation).
- **Short and capped.** 90 days by default, capped by the installation's `policy_overrides.delegation.max_days`.
  There is no automatic renewal. A reminder is sent ONCE, `policy_overrides.delegation.remind_days` (7 by
  default) before the end, through the installation's `email` notifier (`remind_expiring`, a pass of the task
  loop); without a notifier the console shows the expiry.
- **The credential.** For a `user_exchange` need the platform asks the identity provider for an OFFLINE token
  (`offline_access`, RFC 8693 `requested_token_type` refresh token) from the person's own token, and seals it
  at `user/<id>/delegation/<delegation id>` (ADR 0058). It is issued to the exchange client only, never returned
  by a route, and used as the refresh token of each delegated read, which then exchanges for the source's
  audience exactly as a conversation's call does. A `user_consent` need uses the person's connected account.
  A `service` need is read as before.
- **Refused at every read and every run** (`assistant_delegations.check`, nothing cached): revoked, expired,
  the person left the audience (their groups, from their user row) or the organisation, or the digest of the
  task's needs in the installed manifest changed (a new agreement is then needed). A refused delegation is
  marked `suspended` with the reason; one refused only for the audience resumes when the person returns.
- **Revocation is immediate.** `DELETE /me/delegations/{id}` revokes the offline token at the provider (RFC 7009,
  `CHOREGOS_TOKEN_REVOCATION_ENDPOINT`; without one only the sealed copy is deleted and the token lives until the
  provider's own lifetime) and deletes the sealed secret. Erasing a person from an assistant does the same.
- **The steps of the run.** The workflow's own agent steps read under it too (`task_reads`): a read of a
  connector bound to a need is judged by the same `guard_read` at each call and made as the person; a write by such
  a step is refused (`write_under_delegation`). A connector the installation binds to no need keeps the run's
  identity.
- **Journaled.** Every delegated read writes a line of the reads journal with the delegation's id (and, for a
  step of the run, the work item's key); never content.
- **Bound to what was agreed.** The delegation records the connector and the identity each need was bound to; a
  re-binding suspends it (a new agreement is needed), and a read of an operation outside the agreed list is
  refused.
- **Only the owner reads it.** `delegations` is under forced RLS on the organisation plus a restrictive
  owner-only policy.

Security review done 2026-11-11 (see the review below); what remains is the deployment's. This feature stores a
credential that can act as a person with nobody present. Before enabling it in a production organisation, check: that the provider's offline sessions are
bounded (idle and maximum lifetime) and revocable at the endpoint configured here; that the exchange client is the
only client the offline token is bound to and its audiences are limited to the connectors that need them; the
sealed-store keyring and its rotation; and the `max_days` the organisation sets. The tests prove the platform's
side against a fake provider, not a real provider's offline-session behaviour.

## Review of the person's identity (S63-07, S63-08, S63-14), 2026-11-11

[ADR 0135](adr/0135-a-tool-call-may-carry-the-person-s-identity.md) and
[ADR 0137](adr/0137-a-standing-read-only-delegation.md) require a dedicated security review before production.
This is that review of the code in the repository. It is not an audit of a running deployment, and it does not
change the status of either ADR: acceptance also waits for the items under "Outside the repository".

**Scope.** `user_exchange` (RFC 8693: `services/identity_exchange.py`, `choregos_adapters/token_exchange.py`, the
person's sealed sign-in refresh token in `routers/auth.py`, the MCP door's bearer used as subject token in
`mcp/caller.py`); `user_consent` connected accounts (`services/connected_accounts.py`,
`routers/connected_accounts.py`: authorization code with PKCE, state, callback, sealing, refresh, revocation, the
headers a plugin server receives); the standing delegation (`services/assistant_delegations.py`: the offline
token, suspension, revocation, what a delegated read may touch); the tables `connected_accounts` and
`delegations`; what the broker, the events, the reads journal and the error messages say about a token.

**Method.** Adversarial reading of each path against a list of questions, then a test that fails without the fix
for every finding. The questions: could a token minted for another audience or for another person be accepted or
sent; can a subject token be replayed or confused with another; is the OAuth callback bound to the session and to
the person (CSRF); can a URL a person is sent to, or the platform calls, be chosen by someone who should not
choose it (open redirect, SSRF, egress); where is the PKCE verifier kept; can a token reach a log, an event, a run
payload or an error text; which key seals what; do the row-level policies hold; can a revocation lose a race;
can a delegation reach more than the person agreed to (a write, a need or an operation outside the agreement, a
changed manifest, a re-bound need); can one organisation reach another's rows or secrets. The attacker models
are a member of one organisation, an organisation's connector administrator (a role that writes the free-form
`config` of a connector) and a source or provider that misbehaves.

### Findings

| # | Severity | Finding | Fix | Test |
| :-- | :-- | :-- | :-- | :-- |
| 1 | High | `user_consent.client_secret_ref` travels in the connector's `config`, outside the `secret_refs` checks. A connector administrator could name any sealed secret of the organisation (`store:user/<id>/oidc_refresh`, another person's consent or delegation token, another connector's key) or an `env:` variable, and set `token_endpoint` to a host they run: the platform sent that value there as `client_secret` at the next consent. | A `store:` reference is only the connector's own derived one (`store:connector/<name>/oauth_client_secret`), refused at write and again at use; an `env:` one follows the rule of every secret reference (a platform administrator's). | `test_the_client_secret_reference_cannot_read_a_sealed_secret_that_is_not_the_connectors_own` |
| 2 | High | The calls to the source's authorization server (code exchange, refresh, revocation) went through a plain HTTP client: outside the organisation's egress allowlist and bounds, so a connector administrator could make the API call any host it can reach (SSRF; the reply is not shown, but the call and its form are sent). | The client is the guarded transport; the callback and the revocation run in the organisation's egress scope (the refresh already ran in the broker's); the install gate now also opens the token and revocation hosts. | `test_the_callback_runs_under_the_organisations_egress_allowlist` |
| 3 | Medium | The authorization endpoint was returned to the console as `authorization_url` with no scheme check: `javascript:`, `data:`, `file:` or `https://user:pw@host` could be configured, and the console navigates to it. Plain `http` was accepted for the token endpoint. | `https` only (`http` only under `CHOREGOS_FAKES`), a host, no user-info, no fragment, for the three endpoints. | `test_an_authorization_endpoint_that_is_not_a_plain_https_address_is_never_handed_to_the_browser` |
| 4 | Medium | An account was bound to the connector's name, not to what the person consented to. An editor who moved the token endpoint, the client or the connector's URL made the platform send the person's refresh token (refresh, or the revocation at disconnect) and access token there. | `connected_accounts.provider_digest` (migration `a5e5c0f0e0d5`): digest of the authorization server, client and address at consent. When it differs the account is not used and nothing is sent: the person connects again. A disconnect revokes only at the endpoint they consented to. Existing accounts have no digest and connect again once. | `test_an_editor_who_moves_the_token_endpoint_receives_no_refresh_token`, `test_disconnecting_does_not_send_the_refresh_token_to_a_revocation_endpoint_that_was_moved` |
| 5 | Medium | Revocation races. (a) Two calls refreshing at once against a source that rotates: the loser's `invalid_grant` marked the account `reconnect` and deleted the winner's NEW sealed token. (b) A refresh in flight when the person disconnected sealed the rotated token again, for an account that no longer exists, and so outlived its revocation. | (a) On `invalid_grant` the sealed value is read again: a different one is retried once, and only an unchanged one ends the account. (b) The account row is read again under `FOR UPDATE` before a rotated token is sealed; none, no seal. | `test_a_rotation_by_a_concurrent_call_does_not_cost_the_person_their_account`, `test_a_token_rotated_while_the_person_disconnects_is_not_sealed_again` |
| 6 | Medium | The same race for the identity provider's refresh tokens: a renewal in flight when the person signs out, or when a delegation is revoked, sealed what the provider had just issued. For a delegation that is an orphan OFFLINE token, alive at the provider, that no row points to and no revocation reaches. | The sealed secret is checked again before the rotated token is sealed; if it is gone the token is revoked at the provider and the call is an unavailable identity. | `test_a_token_rotated_while_the_person_signs_out_is_killed_not_sealed_again` |
| 7 | Medium | Token confusion: the token the provider issued was handed to the connector without looking at its `aud`. A provider or proxy that answers with a token for another audience would send it to the wrong source. | When the token is a JWT its `aud` must contain the audience asked for, or the call is `identity_unavailable`. (An opaque token cannot be checked here: see residual risks.) | `test_a_token_issued_for_another_audience_is_never_handed_to_the_source` |
| 8 | Medium | The audience of an exchange is written in the connector's `config`: whoever edits the connector chooses what the person's token is exchanged for, bounded only by the provider's permissions. | `CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` (a JSON list): when set, an audience outside it is never asked of the provider. Empty keeps the old behaviour, so a production deployment must set it. | `test_the_deployments_audience_list_bounds_what_a_connectors_editor_can_ask_for` |
| 9 | Medium | A delegation recorded the needs agreed to, not what they were bound to. An administrator who re-bound a need to the connector's own identity, or to another connector, made the standing token read there (as the service account, or at a source the person never saw). | Each agreed need records its connector and the binding's identity; `check` suspends the delegation when either differs, and a new agreement is needed. | `test_a_need_bound_otherwise_than_when_the_person_agreed_suspends_the_delegation` |
| 10 | Low | A delegated read was judged by need, not by operation: an operation the manifest later names that the person's agreement did not list would have been read. | `guard_read` refuses an operation outside the agreed list (`outside_agreement`). | `test_a_delegated_read_of_an_operation_the_person_did_not_agree_to_is_refused` |
| 11 | Low | The exchanged token, the drawn access and refresh tokens and the offline token were not registered with `redact`: an opaque token quoted back in a source's error text would not be masked (a JWT is, by shape). | Each is remembered (`remember_secret`) when drawn. | `test_an_exchanged_opaque_token_is_masked_wherever_an_error_would_echo_it` |

All eleven are fixed in this change. Nothing found was left open.

### What was reviewed and held

- **Subject-token origin.** The door's bearer is accepted only after the issuer, signature, expiry and mandatory
  audience of the door are checked (`mcp/oauth.py`), is kept in a context variable read for the same person only,
  and is never stored. The person is found by `sub` (at the same issuer) or by a verified e-mail; a machine
  account is refused. A bearer cannot serve another person, and a token minted for another audience is refused at
  the door. The sign-in's refresh token is sealed per person and per organisation, and read only to draw an access token.
- **CSRF on the callback.** The state is signed (`purpose` separated, expiry checked) and carries the person, the
  organisation, the connector and a nonce; the PKCE verifier is in a signed `HttpOnly`, `SameSite=Lax` cookie
  scoped to the route and bound to the state by the same nonce, and never goes to the source. A callback is refused
  when the state was started by another person, when the cookie is absent or another browser's, when the state or
  the cookie is forged or expired, or when the organisation is not the caller's. The cookie is deleted on success.
- **Open redirect.** The callback redirects to the console's own URL, never to a parameter; the connect URL
  carries the organisation slug URL-encoded. The only URL chosen from outside is the authorization endpoint, now
  constrained (finding 3).
- **Leakage.** Tokens are not columns, not in `TOOL_CALLED` (which carries `as` and the mode), not in the reads
  journal (subject and operation only), not in run payloads, not in the 422 bodies (`identity_unavailable`,
  `account_not_connected` carry a message and a connect URL), and the provider's errors are reduced to their
  `error` code. `ExchangedToken` hides its tokens from `repr`. No route returns a token.
- **Sealing key.** Everything uses the sealed store of ADR 0058 (AES-256-GCM envelopes, the organisation and the
  name in the associated data, KEKs from the deployment). Names under `user/<id>/...` are written by the
  platform only; no route lets an administrator list, read or replace an arbitrary name (finding 1 closed the one path that
  could read one).
- **Row-level security.** `connected_accounts` and `delegations` are under forced RLS on the organisation plus a
  RESTRICTIVE owner-only policy; the tests read another person's rows as an organisation administrator and get none (PostgreSQL).
- **Delegation scope.** Read-only is enforced three times (the grant refuses a write need, the manifest check at
  install, `guard_read` before any call); expiry is capped by the installation; `check` is evaluated from the rows
  and the clock at every read and run; erasure revokes at the provider then deletes.
- **Cross-organisation.** A connector, an account, a delegation and a sealed secret are all read under the
  organisation of the caller or of the conversation; no path takes an organisation from a request parameter without
  checking the caller's membership.
- **Redirects with credentials.** The MCP and plugin clients do not follow redirects, so `X-Choregos-User-Token`
  and the bearer never follow one to another host.

### Residual risks (not fixed, and why)

- **A connector administrator names the source.** For `user_exchange` they choose both the connector's URL and its
  audience: pointing a legitimate audience at their own server gives them the person's exchanged tokens for that
  audience for as long as the call goes out. Bounded by the egress allowlist (an administrator opens the host), the
  audience list of finding 8 and the provider's exchange permissions, and audited as a connector change, but not
  prevented. A `user_consent` account no longer has this problem (finding 4).
- **Opaque tokens.** The `aud` check of finding 7 reads JWTs only; an opaque exchanged token is trusted as the
  provider's answer.
- **One sealed sign-in token per person.** A sign-out on one device removes the token every device uses (the next
  call is `identity_unavailable` until the next sign-in); a person removed from an organisation keeps a stale
  sealed token in its store until the organisation is erased. Neither widens anything: a conversation needs an
  authenticated request, and a delegation has its own token.
- **Race window.** Findings 5 and 6 close the races with a re-read and a row lock, not a transaction spanning the
  provider's answer: a sign-out committed in the instant between the re-read and the seal would still be lost.
  The provider's own refresh-token lifetime bounds it.
- **State is not single-use.** A state can be replayed for 10 minutes, but only with the cookie of the browser it
  started in, and the authorization code is single-use at the source. The state names the person's and the
  organisation's internal ids to the source's authorization server.
- **The source's `id_token` subject is not verified.** It is shown as the account's subject and journaled; it
  decides nothing.
- **The provider's endpoints are the deployment's.** Calls to the identity provider (token, discovery,
  revocation) are made by the API's own client, outside the organisations' egress allowlists, as every other
  platform call to the deployment's identity provider.
- **Delegation limits already documented.** No reminder before expiry; a scheduled workflow's own agents keep
  their identity (only polled reads and `read_operation` take the delegation); without `CHOREGOS_TOKEN_REVOCATION_ENDPOINT`
  an offline token is only deleted here and lives to the provider's own lifetime.

### Outside the repository

These cannot be established from the code and gate production:

- **Keycloak (or the deployment's provider).** Standard token exchange enabled for the platform's client, and
  permitted towards each source's audience only; offline sessions bounded (idle and maximum lifetime) and revocable at the
  endpoint configured in `CHOREGOS_TOKEN_REVOCATION_ENDPOINT`; refresh tokens bound to the client that holds
  them (if the exchange client is not the console's, refreshing the sealed sign-in token fails); a protocol
  mapper for `act` (Keycloak ignores `requested_actor`); the audience of the door's tokens;
  `CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` set to the list of sources.
- **A real source's behaviour:** that it accepts exchanged tokens, restricts on `act`, rotates and revokes refresh
  tokens as RFC 6749 and 7009 say (Notion, Google, Microsoft).
- **The sealed-store keyring** and its rotation, the `max_days` an organisation sets, and the works council and
  data protection impact assessment ADR 0137 waits for.
- **The deployment's network policy** under the API pod, which the egress allowlist assumes.

## MCP gateway

An organisation may put its MCP servers behind a gateway it runs
([ADR 0105](adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md)); how to use it is in
[Integrations](integrations.md#your-organisations-mcp-gateway-adr-0105). This is what it changes for
security.

**Trust boundaries.** There are two policy points, and neither replaces the other: Choregos (the
broker, approvals, per-tool permissions, result bounds, egress) and the gateway (its own access rules
and the upstream's). A call is allowed only if both allow it. Choregos never routes, aggregates or
re-serves third-party servers. A plugin's server (ADR 0096, `services/plugin_servers.py`) is not an
upstream: it is never reached through the gateway and `required` mode does not refuse it; the boundary
test whitelists exactly those two modules and `choregos_adapters.plugin_servers`
(`tests/licences/test_mcp_gateway_boundary.py`).

**Credentials.** The upstream credential is held by the gateway only, and never enters Choregos. The
gateway credential (bearer, header, OAuth client secret) is sealed in the organisation's secret store
and never shown again; a `jwt_assertion` sign-in has none (`services/mcp_gateway.py`).

**The assertion** (`services/mcp_gateway_identity.py`). Each call carries a JWT signed ES256 with the
organisation's own key (never the run-token key), with a `kid` and a unique `jti`; `exp - iat` is 60
seconds. Its claims: `org`, `project`, `run`, `work_item`, `agent`, `initiator`, `action`,
`approved_by`, `connector`, `server`, `tool`, `purpose` (and `org_id`, `sub`, `aud`). **Personal data**:
`initiator` and `approved_by` are people's emails; they leave the platform for the gateway. The issuer
is `{public_url}/api/v1/orgs/{org}/mcp-gateway`, stored on the first key so that an organisation rename
does not change it; the keys are at `/jwks.json` there, public, `Cache-Control: public, max-age=300`,
with the same 404 for an unknown organisation and for one without a key. **Rotation** signs with a new
key and keeps the previous one published, older ones are dropped with their private key. A gateway
that caches the JWKS may refuse new assertions until it refetches, up to the cache time.

**What the gateway sees.** The arguments and the results of every call routed to it, and the identity
above. Choose a gateway you would trust with that data. The token cache of an OAuth sign-in is per
process (API, workers): nothing is shared.

**Threats and mitigations.**

- *A tenant aiming the platform at internals.* The endpoint and the token URL are refused when they
  name an IP address, a name without a domain, or a private name, unless an operator lists the host in
  `CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS`; plain http is only for a listed host (`services/mcp_gateway.py`).
- *A compromised gateway.* Its results are still capped, checked, masked and read by the injection
  guard, and a write still needs its approval, decided in Choregos (`services/broker.py`). It cannot
  widen what an agent may call.
- *Replay of an assertion.* `exp` is 60 seconds and `jti` is unique: a gateway that remembers `jti`
  refuses a second use.
- *An agent reaching the gateway.* A run has no egress to it: the API refuses a rule that opens the
  gateway to `runners` and denies its host to every run, even under a wildcard
  (`services/egress.py`, `services/network.py`); the chart opens it to the API and the workers only
  ([Deployment](deployment.md#an-mcp-gateway-adr-0105)). The gateway's own egress rules (origin
  `mcp-gateway`) are revoked when it moves or is deleted; a rule merged with another origin keeps its
  other families.

## The outposts' door

An outpost is a place an organisation's administrator enrols, which only dials out
([ADR 0147](adr/0147-an-outpost-only-dials-out.md)). The door it dials, `/outposts/v1`, is a third
door of the API beside `/mcp` and `/a2a`
([ADR 0148](adr/0148-the-outpost-door-is-a-third-door-with-its-own-contract.md)); its contract is
`packages/contracts/outpost-door.yaml`. It is not mounted unless `CHOREGOS_OUTPOSTS` is on
([Deployment](deployment.md#outposts-adrs-0147-0149)). This is what it changes for security. Nothing
routes a call to an outpost yet: the connector that does is a later change, so today the door enrols,
authenticates and waits.

**Who is at the door.** No session, no API token and no OAuth token opens it, and the signature that
opens it opens no other door: the REST API, the MCP door and the A2A door answer 403 to whatever
presents `Outpost-Signature`. Every request is signed with the Ed25519 key the instance enrolled; the
signature binds the method, the URL under the door's public origin, the SHA-256 of the body as sent, an
`iat` within 60 seconds, a nonce the door issued and a sequence the instance raises. A request is
answered for the first check that fails, in the contract's order (`apps/api/src/choregos_api/outposts/auth.py`).

- *A replay.* The last sequence the door took is on the instance's row, on 64 bits: a request whose
  sequence is not above it is refused at whichever replica it arrives. The API has no shared store, by
  decision, and a row is what the replicas share. The nonce is an authenticated timestamp the door
  checks alone and keeps for 300 seconds; it proves freshness, the sequence refuses the replay.
- *A proxy on the path.* An organisation's outbound proxy often terminates TLS. It reads ids, sizes and
  timing; it does not read a call or its answer, which are sealed end to end; it cannot hand the
  outpost a call of its own, because every call is signed by the door with a key the outpost's own
  policy file pins; and it cannot swap a key at enrolment, because the enrolment is signed with the key
  it enrols. It can stop, starve or mislead a daemon about versions and time, as it can by dropping
  packets.
- *A stolen key.* An instance holds one poll at a time. A second poll while one waits is refused
  (409 `poll_conflict`) and raises an alert: the audit event `outpost.poll_conflict` of the
  organisation, once a minute at most for an instance, which `/metrics` counts as
  `choregos_outpost_poll_conflicts_total`. Revoking the instance holds from its next request and moves
  the outpost's epoch: nothing it had in flight is taken.
- *A hostile outpost.* A body above its verb's cap is refused before it is parsed, on the bytes
  received; an instance's requests are rate-limited (429 with `retry_after`); a lease lasts 300 seconds
  at most, whatever the daemon asks for; an answer is taken only from the instance that claimed the
  call, under the epoch of the claim, before the deadline, once; a refusal is a code from a closed
  list. The door does not open an answer: it stores the envelope for whoever placed the call, who
  treats one that does not open as no answer.
- *Another organisation's outpost.* The door resolves a signature to one instance of one outpost of one
  organisation and never works under the all-organisations scope. The outposts' tables are under forced
  row-level security that also requires the session to name the outpost: whatever is not this outpost's
  answers 404, never 403 (`apps/api/tests/test_outpost_rendezvous_postgres.py`).

**What is at rest.** A call's row holds references and digests. Its arguments wait for their claim
sealed under the deployment's key wrapper (`CHOREGOS_SECRETS_KEKS`, ADR 0103), with the private half of
a key made for that one call's answer; both are emptied when the call completes, and a sealed answer
nobody came to read is emptied after 15 minutes. Nothing of the outpost's side is stored: the platform
has no address and no credential of it.

**The door's own secret** is its signing key (`CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY`). Whoever holds it
can sign calls that the outposts which pin it will make, within what each outpost's policy allows. The
key that authenticates the nonces is derived from it, so replicas agree with no further secret; after a
rotation a daemon's nonce is stale once, and the refusal that says so carries a fresh one.

**Why this door does not make Choregos a gateway** (ADR 0148 §12): it serves assignments and never MCP,
it has no route that names a connector or a server, and it imports no broker module. Each of the three
is a test (`tests/licences/test_mcp_gateway_boundary.py`).

**What it does not give.** Data residency: a tool's result travels to the control plane like any
other. Protection against whoever holds the control plane and its signing key: an outpost bounds what
can be asked of it, it does not remove it. A limit on requests that carry no valid signature: each
costs the door one lookup, and bounding them by source is the entrance's (the door has a hostname of
its own for that).

## Secrets

- Never in Git, never in an image, never in a workspace.
- **Whose secret decides where it lives** (S39-06e). The platform's own secrets (session key, run-token
  keys, gateway master key, database password, the KEK itself) come from the deployment's vault through
  External Secrets. A connector's key belongs to the organisation that configures the connector (a
  market-data key, a Jira token, an MCP server's token): it is **typed in the console and sealed**
  (below), never put in the deployment's vault nor in the pods' environment, where every organisation's
  connector could name it and only an operator could rotate it. `env:` stays for a key the operator
  owns and manages, or a vault a plugin reads.
- A connector's `env:NAME` reference is an allowlist, not a free read of the process environment: the
  API and the orchestrator also hold the platform's own secrets (session key, gateway master key,
  database password), so a name must match `CHOREGOS_SECRET_ENV_PATTERN` (default
  `^CONNECTOR_[A-Z0-9_]+$`) and `CHOREGOS_*`, `DATABASE_*`, `PG*`, `AWS_*`, `KUBERNETES_*`,
  `TEMPORAL_*` are refused whatever the pattern. It is checked when the reference is written and
  again when it is resolved. Only a **platform** administrator writes a reference
  ([ADR 0088](adr/0088-a-deployment-reference-is-the-operator-s.md)): the pods that resolve it serve
  every organisation, so with two organisations one's administrator could otherwise name the
  other's `CONNECTOR_*` key and send it to a host of its choosing (#983). An organisation types its
  keys; a connector that holds a deployment reference is reconfigured by a platform administrator
  only, or the key would follow a new URL. On a single-organisation
  install its administrator is the platform administrator, so nothing changes there. Before S39-06b,
  a role allowed to write a connector could point a secret field at a platform variable.
- External Secrets Operator ↔ the provider's vault; one hand-made `ClusterSecretStore`.
- Git token minted **per run**, repository scope, 1 h TTL, injected in memory. It travels in
  `-c http.extraHeader`, command by command, never in `.git/config` nor on disk (S22-07).
- **The runner's own git runs no repository-controlled code** (#1345). The agent can write
  `.git/config`, `.git/hooks/*` and `.git/info/attributes` from its shell — those are the workspace,
  a write root of the confinement (ADR 0094) — and the runner's `git` (`commit`, `push`, the
  `diff`/`status`/`log`/`checkout` of the scope check) runs as the runner, outside that confinement;
  worse, the push carries the git token in `GIT_CONFIG_PARAMETERS`, which git exports to every child
  it spawns. So the runner forces, on each of its own git commands (`Workspace.git`, `_git_env`,
  never the agent's `agent_git_env`), command-line `-c` overrides that beat `.git/config` and any
  `include`/`includeIf`: `core.hooksPath=/dev/null` (plus `--no-verify` on `commit` and `push`) so no
  hook runs; `core.fsmonitor=false`, `diff.external=`, `core.pager=cat` and the per-driver
  `filter.<n>.{clean,smudge,process}` / `diff.<n>.{textconv,command}` (read from the effective config
  and each set to empty) so no filter, textconv or external diff/fsmonitor program runs;
  `credential.helper=` so no helper reads the token at push time; `GIT_CONFIG_NOSYSTEM` +
  `GIT_CONFIG_GLOBAL=/dev/null` so no system or user config (nor its includes) is read. The ssh
  transport is never used (https or a local path), so `core.sshCommand=false` is only defence in
  depth. A `pre-push` hook the agent wrote read the token before this (`packages/runner/src/
  choregos_runner/workspace.py`, `packages/runner/tests/test_runner_git_hooks.py`).
- Run token: ES256 JWT, `aud=internal`, `sub=run_id`, TTL `max_minutes + 15`, renewed while
  the run waits for a slot. Minted by `start_run` for the run's Secret only: never stored in
  `runs.stage_input`, never carried by a Temporal payload (#519). The runner reads it, and the
  remote agent's token beside it, into its settings; no process it starts inherits either — the
  agent, the project's checks, git — except a job, which is given the run token by name (S57-02).
  It cannot be revoked before it expires; what it opens ends with the run. Once the run is closed
  (it holds a result, or its status is `succeeded`, `failed`, `cancelled` or `timed_out`), every
  route of `/internal/runs/{id}` answers 409 `run_closed`, except `events` and `network/decisions`,
  which still append, and `result`, which answers `already_recorded` and writes nothing (ADR 0143).
- The organisation's model catalogue (ADR 0060) limits which models an agent may name; it is a
  governance control in front of the gateway, not a replacement for the key's own model list. With an
  OpenAI-compatible gateway the `run_key` is shared by runs: scope it at the gateway (a consumer with
  a token rate limit), since Choregos cannot cap it.
- Gateway key: one per run, capped. `prepare_stage` reserves its cap (a `gateway_keys` row named
  `reserved:<run_id>`); `start_run` mints it and the executor puts it in the run's Secret next to
  the run token (`k8s_job`, `tekton`: Secret key `gateway-key`; `agent_sandbox`: sealed in the
  assignment, and given afresh on `resume`; `aca`: a job secret; `local_docker`: the container's
  environment, handed to `docker run` through its own environment so the value never shows in the
  host's process list, #439). The runner reads it from `CHOREGOS_RUN_GATEWAY_KEY` and gives it to the
  agent under its backend's own variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`…), never under that
  name (S57-02). It is never stored in `runs.stage_input` and never carried by a Temporal payload
  (#519, #523).
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
  The key-encryption keys sit behind a `KeyWrapper` seam ([ADR 0103](adr/0103-secret-keks-sit-behind-a-key-wrapper.md)):
  the default `EnvKeyWrapper` is the keyring above, and the bytes it writes are unchanged. An
  extension can supply a KMS or Vault-transit wrapper (`set_secret_store`, or a `SealedDbSecretStore`
  built with a wrapper); core ships no KMS client. The wrapper's `kek_id` is stored in `org_secrets`
  and is at most 32 characters; `rewrap` moves the rows whose `kek_id` differs from the wrapper's
  active id.
- GitHub App installations are tenant boundaries ([ADR 0073](adr/0073-github-app-installations-are-discovered-not-typed.md)):
  one App serves the deployment, so a token for `owner/repo` is minted only through an installation
  **claimed** by the organisation of the connector or run. With several organisations the claim needs
  GitHub user authorization (the person must manage the installation, checked with the App's OAuth
  client); without it, typing another company's repository name yields no token, for a connector or
  for a runner's clone. The callback's `installation_id` is never trusted alone, and the install
  `state` is HMAC-signed, short-lived and bound to the signed-in person.
- Webhook secrets: injected by the chart from the vault (or generated on a bench); an
  unconfigured GitHub webhook is refused in `staging`/`prod`.
- Rotations: [`runbooks/secret-rotation.md`](runbooks/secret-rotation.md).

## Supply chain

Images signed with cosign (keyless, GitHub OIDC), **SBOM and SLSA provenance attached**
(BuildKit attestations) on `main` and on releases, Trivy on the published image before it is
signed, digest references enforced by Kyverno, Tekton Chains signing `PipelineRun`s. A hosted agent's
image is verified by cosign at publication, on its pinned digest, by the operator's key or keyless
identity, and, with `global.images.hostedCosignPolicy`, again by Kyverno at admission, init containers
included ([`hosted-agents.md`](hosted-agents.md#signatures-by-cosign-s41-14)).
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
Who sees, installs and enables is decided by one server function (ADR 0074): every member sees a catalogue, only the organisation's administrator installs, and a project owner or the administrator enables an installed connector for a project.
A changed upstream reaches an organisation only when a catalogue release is merged **and** an
administrator updates the skill: two people, as ADR 0050 asks against bait-and-switch.

## Reporting a vulnerability

[`SECURITY.md`](../SECURITY.md) at the root — including the list of what we do **not**
guarantee yet. Do not open a public issue for a vulnerability.
