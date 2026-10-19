# 0070 — Network access is bounded by the organisation, narrowed by the project and the agent, and enforced per run by an egress proxy

- **Status**: accepted, 2026-10-16
- **Concerns**: the contract (`policy.schema.json`, `stage-input.schema.json`, `event.schema.json`,
  `openapi.yaml`), `choregos_core/network.py` and `policy_ceiling.py`, the API (`services/network.py`,
  `routers/internal.py`, `routers/workflows.py`, `routers/admin.py`, `network_decisions`), then the
  orchestrator and the runner (S40-01b) and an egress proxy with its chart (S40-01c); builds on
  [ADR 0005](0005-adaptateurs-et-fakes.md) (fakes), [ADR 0034](0034-connectors-by-capability.md) (the
  tool broker) and [ADR 0044](0044-people-are-declared-in-the-workflow.md) (a setting nothing enforces
  is said so)

## Context

"Agents must have network access policies to control what they do. Every outbound call must be
controlled: we do not want our agents to pollute the internet or get banned. Likewise we need global
access policies." (S40, request 1.)

What existed:

- **One allowlist, per project, host-level**: `sandbox.network.allow_domains`, copied into the run's
  `permissions.allow_domains`. `deny_by_default` is read by nothing. No per-agent notion.
- **Advisory guardrails** in the runner: an empty list meant "allowed (the sandbox filters)", a
  `WebSearch` request has no URL and fell through, and a `curl` from Bash is not a URL request at all.
- **A per-project Squid proxy**, rendered only by the GitOps provisioning (one namespace per project).
  The `agent_sandbox` executor runs in the release namespace, where no proxy is ever rendered.
- **Chart NetworkPolicies** that give runner pods the same egress as the API (PostgreSQL, Temporal).
- **An organisation ceiling** (`PolicyCeiling`, ADR in S27-05) that caps budgets and scope, with no
  network section.
- **The tool broker** (ADR 0034) and the platform's own fetchers (knowledge crawl, git skill import)
  with no organisation-wide policy on destinations and no politeness.

On the Diametral dev tenant the real wall is infra's FQDN policy (eight hosts, identical for every
project and agent): the console's `allow_domains` has no effect there.

## Decision

**One model, three scopes, intersection only.**

| Scope | Where it is written | What it can do |
| :-- | :-- | :-- |
| Instance | `CHOREGOS_NETWORK_INSTANCE_DENY` (chart `egress.denyDomains`), and on Diametral the infra FQDN policies | the outermost bound; never edited from the console |
| Organisation ("global") | `PolicyCeiling.network` in `organizations.settings.policy_ceiling` | the widest allowed set (`allow_domains`, `null` = no cap), a deny list that always wins, rate caps, built-in web tools allowed or not, a contact for the User-Agent |
| Project | `Policy.sandbox.network` | its allowlist (within the organisation's), its own deny list, rates under the caps, built-in web tools |
| Agent | `AgentSpec.network`, each published version | `domains: null` = the project's list; `[]` = no internet; a list = only these hosts, each of which the project and the organisation must allow **or the run is refused at admission**; a rate under the project's; built-in web tools off |
| Run | computed (`effective_network`) | agent ∩ project ∩ organisation, plus the *implied* repository host; deny = instance ∪ organisation ∪ project |

**Host patterns** (one implementation, `choregos_core/network.py`; the API, orchestrator, runner and
proxy import it, nothing else matches hosts): `example.com` is that host and its subdomains (the
existing guardrail and Squid semantics); `*.example.com` is the subdomains only; `*` is any public
host *name*. No scheme, port, path or user-info. **IP literals are refused** in patterns and match
nothing, so a deny list keyed on names cannot be bypassed with an address. A deny always wins.
`pattern_within(inner, outer)` decides "may only narrow".

**Egress is always closed.** An empty list means no internet, for a run whose `permissions.egress` is
`listed` (set by the orchestrator, S40-01b). `deny_by_default: false` was accepted and ignored; it now
raises a `policy.network_deny_by_default_not_enforced` warning telling the author to write
`allow_domains: ['*']` to open every public host.

**Where each rule is enforced, and how strongly:**

| Layer | What it sees | Enforces | Strength |
| :-- | :-- | :-- | :-- |
| L3/L4: Kubernetes NetworkPolicy (chart) or Cilium (Diametral infra) | pod → IP:port | runner pods reach only the API, gateway, memory, telemetry and the egress proxy | **Wall** where the CNI enforces it and the runner has no other policy. On Diametral the infra owns it and today lets runners reach eight hosts directly |
| Egress proxy `choregos-egress` (per release namespace, S40-01c) | the run's identity (`Proxy-Authorization` = run id + run token), `CONNECT host:port` or the plain-HTTP request line | effective allow and deny per run, ports 443/80, IP literals and private addresses refused (resolved and pinned), connection rate per host, per run and per organisation host, concurrent tunnels, decisions journaled | **Wall for host, port and connection rate**, when L3/L4 forces traffic through it. It cannot see method, path, headers or User-Agent inside TLS, and one tunnel can carry many HTTP/2 requests: its rate is a *connection* rate |
| Tool broker and platform-side fetchers (S40-01d) | the whole request | organisation allow/deny on the destination at declaration and at call, rate per destination host organisation-wide, identifying User-Agent, robots.txt and crawl-delay for crawls | **Wall** (the API makes the call) |
| Governed `web_fetch` tool (S40-01f) | the whole request | GET/HEAD only, the run's allowlist, robots.txt, User-Agent with contact, politeness, size cap, redirects re-checked | **Wall** for what goes through it: the only place robots.txt and method rules can hold for agent browsing |
| Runner guardrails and backend settings (S40-01b) | ACP permission requests | early refusal with a useful message; built-in `WebFetch`/`WebSearch` denied in the backend's settings when the effective policy says so | **Advisory**: the agent shares the pod, and a `curl` from Bash is not a URL request |
| Model provider server-side tools (`WebSearch`) | nothing | only by denying the tool in the backend settings | **Advisory**: it runs at the provider, outside every Choregos network layer |

**Per-run identity at the proxy.** The proxy holds no secret and no database access. The runner
rewrites its `HTTP(S)_PROXY` to carry the run id and run token as proxy credentials; the proxy
forwards the token to `GET /internal/runs/{id}/network` (existing `RunAuth`), caches the answer for
60 s and posts aggregated decisions to `POST /internal/runs/{id}/network/decisions`. A run can only
learn and spend its own policy; a revoked agent or a changed ceiling applies within 60 s. The token
is already readable by any process of the pod: no new exposure.

**Audit.** `network_decisions` (org-scoped, forced row-level security): one row per
(run, layer, host, port, decision, reason), counters **cumulative** (the reporter sends running
totals; a replayed batch replaces them and never adds), bytes up and down, first and last seen, the
agent's slug. The first `denied` of a host for a run also emits `choregos.network.denied` on the work
item's timeline. Ceiling edits are already audited (`policy.ceiling`). An administrator reads
`GET /orgs/{org}/network-activity`.

**The ceiling clamps, it does not only refuse.** `widenings()` makes `PUT /projects/{id}/policy` answer
422 for an allowed host outside the ceiling, a host the ceiling denies, a rate above a cap (no rate
under a cap counts as unlimited) and built-in web tools `allow` under a `deny` ceiling. A policy stored
before the ceiling is clamped when it runs (`clamp()`), and the ceiling's deny list is merged into the
project's, silently (it is not an excess: the effective view shows it).

**Digests do not move.** `AgentSpec.network` defaults to all `None`, and `empreinte()` drops it when
default, like `knowledge` and `memory`: versions published before this ADR keep their digest.

## Alternatives rejected

- *Per-agent Cilium/NetworkPolicy by pod label*: warm sandboxes are created before their run, so a
  policy cannot be per run or per agent; it would need `pods/patch` for the executor and one object per
  agent; Diametral forbids tenants to create NetworkPolicies. Not portable.
- *Keep Squid and render a union per project*: no run identity, no rates, no audit, no per-agent wall.
  Kept only for the per-project-namespace provisioning path (project-level, host-level).
- *Squid `external_acl_type` helper calling the API*: a C++ proxy and a helper protocol in a Python
  codebase is harder to test with fakes than a small asyncio proxy; rates and audit would still need
  custom code.
- *TLS interception to see methods and paths*: needs a CA trusted by every tool in the runner image,
  breaks pinning and makes the proxy a holder of all cleartext. Refused: methods, paths and robots are
  enforced where the platform makes the request.
- *A separate "network policy" object with its own CRUD*: the ceiling, the policy and the agent spec
  already have publication, versioning, audit and clamps.

## Consequences

- Presets gain `files.pythonhosted.org` and `githubusercontent.com` (without them `pip install` and
  GitHub raw, LFS and release downloads fail behind any strict proxy); `team` and `regulated` switch
  built-in web tools off, `regulated` caps a run at 60 new connections per host per minute.
- This PR changes **no behaviour in a pod**: the contract, the core, the API and the journal exist;
  S40-01b makes the orchestrator fill `Permissions` and the runner hold it, S40-01c ships the proxy.
- The decisions table keys on the port as well as on (run, layer, host, decision, reason): a host
  reached on 443 and on 80 are two rows, and the cumulative totals of one never overwrite the other.
- A trial run (a draft that is no registry version) and a job step have no `AgentVersion`: the API
  reads the network the orchestrator wrote into the run's permissions for them.
- On Diametral the infra keeps the outer wall; removing the runner's direct FQDNs there is what turns
  the Choregos proxy from a net into a wall (a gesture for the platform team, listed in the S40-01
  pull requests).
- The enterprise edition needs nothing: each tenant organisation has its own ceiling, set by its
  `org_admin`; the instance-wide bound is the deployment's. A follow-up could let a platform
  administrator lock a tenant's network ceiling.

## As built (S40-01c)

- The proxy is `apps/api/src/choregos_api/egress/` (`python -m choregos_api.egress`, an asyncio server on
  the API image, no new dependency). Sources and sinks are protocols with fakes
  (`FakeRunNetworkSource`, `FakeDecisionSink`); the resolver and the "is this address public" predicate
  are injectable and the default predicate refuses loopback, private, link-local, reserved, multicast and
  IPv4-mapped forms of those. It never adds `Via` or `X-Forwarded-For`, strips `Proxy-Authorization`
  before forwarding plain HTTP, and refuses with one plain-text line naming the host, the reason and where
  to change it.
- Rates are token buckets (per run and host, per run, per organisation and host), counted per replica.
  A refusal is `429` with `Retry-After`; a run holds at most 64 tunnels.
- The chart renders it from `global.egress` (not a top-level `egress` key: the API and the orchestrator
  read the same values) in the release namespace, and refuses `global.egress.enabled` with an empty
  `runner.egressProxy`. With `networkPolicies.enabled` the runner pods get their own policy and the
  platform policy no longer selects them.

## Amendment 2026-10-19 — one network model with ADR 0066 (integration of main 0.18.8)

S39-22 shipped, in parallel, an organisation **egress allowlist**
([ADR 0066](0066-the-organisation-egress-allowlist.md), released in 0.18.x): `org_egress_rules`
decided by the administrator in `/admin/egress`, a request and approval flow, the guarded transport
of every platform client, a deployment ceiling per pod family, `allow_egress` and the 409
`egress_required` on every "add", and rules for `runners` that join every run. Its paths and its
contract stay as released. The two decisions are reconciled into **one model with two halves**,
never two parallel mechanisms:

| Half | Where | Says | Decided by |
| :-- | :-- | :-- | :-- |
| **What the organisation opens** (grants) | `org_egress_rules` (ADR 0066) | the hosts, ports and pod families (`api`, `workers`, `runners`) the platform may call for it; absent means refused (in `enforce`) | its administrator (`tools:grant`), on request of anyone |
| **What nothing may cross** (bounds) | `PolicyCeiling.network` (this ADR) | a deny list that always wins, an allowed set (`null` = no cap), rates, built-in web tools, the User-Agent contact | its administrator |

The instance adds its deny list (`global.egress.denyDomains`) and its ceiling per family
(`global.egress.ceiling`); both are one `global.egress` block of the chart.

1. **One decision for a call the platform makes.** `EgressPolicy` (the object the guarded transport
   reads on every request, redirects included) carries the organisation's bounds
   (`NetworkBounds`, built from the ceiling's `network` and the instance deny list). A host the
   bounds refuse is refused **first, in every egress mode** (`enforce`, `warn`, `off`): the mode is
   the list's, ADR 0070 has no warn mode. Inside the bounds, the list and the deployment ceiling
   decide as ADR 0066 says. `choregos_core.network.platform_allowed` is the one function both the
   transport and the API's declaration and call checks (`services/platform_network.py`) use; an
   internal host (an IP literal, a single-label name) is not capped, only a deny naming it refuses
   it, as S40-01d decided.
2. **Order of the refusals at an "add".** The bounds answer first with a **422** (no opening can fix
   it: change the ceiling's network access), then the list with the **409 `egress_required`** (an
   opening fixes it), then the side effect. This holds for MCP connectors (organisation and
   catalogue installs, through `declare_org_connector`), project connectors, knowledge sources, git
   skill imports and git-delivered skill catalogue entries. At call time the broker and governed
   actions read the bounds again (403 `network_policy`, journaled with layer `broker`, throttled per
   host), and the call itself runs under the egress scope (ADR 0066).
3. **An opening never contradicts the bounds.** Creating a rule, approving a request and
   `allow_egress` on an add refuse (422) a host the bounds refuse, a `*.` host included when a deny
   covers it: a deny always wins, so the rule would open nothing.
4. **Runners.** The hosts the organisation opened for `runners` (S39-22d) join each run's effective
   network as **implied hosts**, beside the repository's host: in the orchestrator (the run's
   `Permissions`, `activities/run_network.py`), in the API (`GET /internal/runs/{id}/network`, read by
   the egress proxy, and the console's project network). Every deny list (instance, organisation
   ceiling, project) wins over them, and an agent that names one of them is admitted. A remote
   agent's admission (ADR 0062) reads the same deny lists. The per-project Squid of the GitOps
   provisioning keeps receiving them as before (`org_allow_domains`).
5. **One implementation each.** `org_runner_hosts` lives in the API (`services/egress.py`) and the
   orchestrator re-exports it; host patterns of the bounds are this ADR's grammar
   (`example.com` covers its subdomains), rules of the list keep ADR 0066's (exact, or `*.` for the
   subdomains only) and are converted (`runner_domain`) where they join a run.

Not changed: the contracts of both ADRs (no new field; the 422 is the existing validation problem),
`/admin/egress` (the grants) and the Administration page's *Network access* card (the bounds), which
remain two views of the same policy.
