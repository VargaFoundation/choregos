# 0147 — An outpost only dials out

- **Status**: proposed, 2026-11-11
- **Concerns**: a new organisation object, the outpost, and its instances (new tables `outposts`,
  `outpost_instances` and `outpost_enrolments`, the management routes `/orgs/{org}/outposts/*`); a new
  principal kind `outpost` and a permission `outpost:manage` (`apps/api/src/choregos_api/rbac.py`); the
  daemon `choregos-outpost` (a new package, `packages/outpost`) and its local policy file; the setting
  `CHOREGOS_OUTPOSTS`. When accepted, amends [ADR 0024](0024-two-editions.md) decision 2 (outposts are part
  of the community edition) and applies
  [ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md) to the daemon (a rule it cannot
  enforce is a capability it refuses). Builds on [ADR 0010](0010-gates-are-mechanisms.md),
  [ADR 0030](0030-an-mcp-door-for-external-clients.md), [ADR 0062](0062-remote-agents-over-a2a.md),
  [ADR 0078](0078-governed-workflows-as-a2a-skills.md),
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md),
  [ADR 0105](0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md),
  [ADR 0127](0127-an-agent-is-a-closed-set.md) and
  [ADR 0131](0131-an-application-calls-under-a-machine-account.md). The door an outpost calls is
  [ADR 0148](0148-the-outpost-door-is-a-third-door-with-its-own-contract.md), the connector that uses one is
  [ADR 0149](0149-a-tool-may-live-at-an-outpost.md); plan `docs/plan/PLAN-2026-11-11-outposts.md`
  (stream S65)

## Context

Everything the platform reaches, it reaches by opening the connection itself:

- an organisation's connector is called by the API's pods, or by the workers for a governed write, with the
  connector's credential resolved there (`services/broker.py`, `effects.py`); an agent never holds a
  server's address or its key ([ADR 0034](0034-connectors-by-capability.md) §6);
- a plugin's server runs in the platform's own cluster and admits the API's pods only (ADR 0096 §1, §8);
- an organisation's MCP gateway is an `https` endpoint an administrator types, which the platform then
  calls from its own network (ADR 0105; `services/mcp_gateway.py`);
- a remote agent is called over A2A from the runner's pod, at an endpoint that must be reachable and
  allowed (ADR 0062 §4, §8).

The systems an organisation most wants an agent to act on are often the ones nothing outside may connect
to: a production network, an internal tracker or forge, a database behind a firewall whose rule is "no
inbound". Opening that network to the platform, or keeping at the platform a standing credential to it, is
what its owners refuse, and they are right.

The usual answer elsewhere is a small program installed inside, which connects out: the self-hosted
runners of forges, on-premises data gateways, the private-location agents of monitoring tools. Nothing of
the kind exists here. Searched on `main` at `9f646fda4`: no enrolment of anything that executes, no lease,
no heartbeat, no peer that only dials out. The one caller that waits for work is a hosted session's pod,
on `GET /internal/agent-sessions/{id}/turns/next` (`routers/agent_session_pods.py`), inside the platform's
cluster.

Two things have to be separated before deciding.

- **What must be inside is the agent's hands, not its head.** The tools (a cluster's API, SQL, internal
  APIs) have to run in the remote network; the model loop does not. Moving the whole agent there brings a
  model key, the source code, the prompts, the knowledge and a shell driven by a model into that network.
  Moving the tools brings only bounded operations, each already decided by the platform.
- **Outbound does not mean safe.** The direction of the connection solves the firewall. It says nothing
  about trust: a daemon that executes what the control plane sends is an execution channel into that
  network for whoever holds the control plane.

## Decision

1. **An outpost is a place a person enrols, and it only dials out.** It belongs to exactly one
   organisation; in this version its scope is the organisation's, never a project's or a person's. Every
   connection between an outpost and the platform is opened by the outpost, to one hostname, on port 443
   (the door of ADR 0148). The platform never connects to an outpost, stores no address of it and needs no
   route to it.
2. **An instance is one copy of the daemon `choregos-outpost`, with keys made on the spot.** At its first
   start an instance generates an Ed25519 signing key and an X25519 sealing key. The private halves never
   leave the machine; enrolment sends the public halves. An outpost may hold several instances
   (availability, a rolling upgrade): each is enrolled, approved and revoked on its own.
3. **A principal kind of its own: `outpost`.** A request signed by an instance resolves to a principal of
   kind `outpost`, bound to one outpost of one organisation. It has no membership and no role, so it holds
   no permission (`Principal.permissions` is empty without a role, `rbac.py`). It holds no session, no
   `chg_` token and no OAuth token, which is all the REST API, the MCP door and the A2A door accept
   (ADR 0030 §3, ADR 0078 §3): the outposts' door is the only place its signature is read. It is not a
   machine account (ADR 0131). A
   machine account *starts* work: it is a user row with a project membership and `a2a` tokens. An outpost
   *executes* what the platform has already decided, and can start nothing.
   *Rejected: a machine account with one more scope.* It is a member of a project (ADR 0131 §2); every
   later check would have to remember to exclude it.
4. **Enrolment needs a person twice.** Someone who holds `outpost:manage` (the organisation's
   administrator, whose role holds every permission) creates the outpost and asks for an enrolment token:
   bound to that outpost, single use, valid 15 minutes at most, stored as a digest. `choregos-outpost
   enrol` presents it with the instance's public keys and prints the fingerprint of its signing key. The
   instance is born **pending** and is handed nothing. A person then approves it in the console by
   comparing the fingerprint shown there with the one the daemon printed, in a session that carries an
   authentication time, the condition `decide` already puts on a decision (`services/actions.py`): a token
   cannot approve. Only then is the instance `active`. A stolen enrolment token therefore enrols an
   instance nobody approves.
   *Rejected: trust on first use.* The token travels by hand, through a chat or a ticket.
5. **Revocation is immediate and moves an epoch.** Revoking an instance, or pausing or revoking the
   outpost, holds from the next request: the door reads the row every time. Each outpost has an epoch that
   a revocation increments, and an answer is accepted only under the epoch its call was claimed in
   (ADR 0148): nothing in flight from a revoked instance is taken. A revoked key does not come back: that
   holds for as long as its outpost exists. Deleting a revoked outpost deletes its instances, and with
   them the memory of their keys; after a deletion the same key is a new enrolment like any other, with a
   token and a person's approval against its fingerprint.
6. **What an outpost protects against, and what it does not.** An outpost checks only what it can verify
   alone, from a policy file the control plane cannot write:
   - who signed the call: the door's keys the file pins (ADR 0148 §13). A call signed by no key of that
     list is not made, and nothing the door sends adds a key to it;
   - its endpoints: an alias maps to a URL and a credential that exist in that file only;
   - `writes: deny | allow` per endpoint, `deny` by default. Whether a tool writes is read by the outpost
     from its own server (the `readOnlyHint` the platform's discovery also reads,
     `choregos_adapters/mcp/client.py`), never from the call;
   - tool-name patterns per endpoint;
   - ceilings: concurrency, payload size, a timeout.

   Anything else it could be asked to check (the project, the agent, the version a call is "for") is a
   name the control plane asserts. A local rule on those names would stop nobody who holds the control
   plane, so the policy has no such rule; the names may be written to the local audit log, as assertions.
   ADR 0010 says a guarantee is a mechanism: checking a name nobody can verify is not one.

   ```yaml
   control_plane: https://outposts.example.com
   door_keys: ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw"]   # the only keys whose calls it makes
   endpoints:
     tracker:
       url: http://tracker-mcp.tools.svc:8080/mcp     # known to the outpost only
       auth: { header: Authorization, from_file: /secrets/tracker-token }
       tools: ["search_*", "get_*", "create_issue"]
       writes: allow                                  # deny by default
       timeout_s: 60
       max_payload_kb: 512
   concurrency: 8
   audit: { file: /var/log/choregos-outpost/audit.jsonl }
   ```
7. **The control plane stores an alias, never a URL or a credential of the outpost's side.** A connector
   names an outpost and an endpoint alias (ADR 0149). The address behind the alias and the credential that
   opens it live in the outpost's file only. A compromised control plane holds no map of the outpost's
   network and no secret of it: it can ask for what the policy allows, and no more.
8. **A local audit log.** The daemon appends one line per assignment received, executed or refused (the
   call's id, the alias, the tool, a digest of the arguments, the outcome, the asserted names) to a file of
   the outpost's. It is the operator's own record, independent of the platform's: the two can be compared.
9. **Upgrades are pulled and verified by the outpost, never commanded.** The door announces a current and
   a minimum version (ADR 0148). The outpost's operator chooses when to upgrade, pins the image by digest
   and verifies its signature. No verb of the door makes a daemon download or run anything. A daemon below
   the minimum is refused cleanly and says so; nobody upgrades it for its operator.
10. **What an outpost learns, and what it does not give.** With the `tools` capability an outpost receives,
    for each call, the alias, the tool's name and the arguments, after the platform has decided the call.
    It receives no prompt, no model key, no source code and no run token. The platform learns the alias
    names, the tools each alias serves, each instance's version and when it was last seen, and each call's
    result or the code of its refusal, which names a class of refusal and not the rule.
    **An outpost does not give data residency.** A tool's result travels to the control plane, is bounded
    and screened there, is handed to a model through the model gateway and is kept where the platform keeps
    what a tool returned (a governed write's recorded result, a conversation's history). A result that must
    not leave the outpost's network must not be a tool's answer. The operator's guide says so before
    anything else.
11. **Capabilities.** An instance announces what it can do, and an outpost is used only for what it
    announces and its organisation enabled. `tools` is this version. `runs`, a whole run executing at an
    outpost, is not decided here: the last point of ADR 0149 lists what would have to be true first. There
    is no `relay` capability (below).
12. **Off unless turned on.** `CHOREGOS_OUTPOSTS` is `off` by default (the chart's
    `global.outposts.enabled: false`). Off, the door is not mounted and creating an outpost is refused: it
    is also the global switch that stops every outpost at once.
13. **Part of the community edition** (amends ADR 0024, decision 2, which lists what that edition keeps).
    The contract, the door, the daemon and the `tools` capability are in it. A daemon that takes work
    inside someone else's network has to be readable by whoever runs it, and the isolation it relies on,
    row-level security, is already the community edition's (ADR 0024, decision 4).

## What is refused, and why

- **A generic HTTP relay into the outpost's network.** It is a tunnel: the platform would address hosts,
  not operations, and ADR 0105 §1 refuses a passthrough for that reason. An internal API is reached as a
  tool of a server the outpost's operator runs.
- **Plugin servers placed by the control plane.** Creating a workload at an outpost, or sending it a
  secret, gives the control plane code execution there under the label "tools". The operator deploys what
  runs at the outpost (ADR 0149).
- **A Temporal worker at the outpost.** Activities open the platform's database and mint run tokens
  (`apps/orchestrator/src/choregos_orchestrator/activities/execution.py`): the worker would hold both.
- **The home cluster as "outpost zero".** Routing the platform's own cluster through the outpost protocol
  would put one more single point of failure under every run, to exercise a protocol. Not before runs at an
  outpost have proven themselves, and not by default then.
- **Placement in the agent version.** An agent is a closed set of five components (ADR 0127) and a place
  is not one of them. Where a tool lives is a property of its connector; where a run would execute, of its
  project.
- **A local model gateway with a declared cost.** Cost is counted at the gateway and a budget is a
  reservation behind a hard cap ([ADR 0004](0004-cost-counted-at-the-gateway.md),
  [ADR 0047](0047-a-budget-is-a-reservation.md)); a declared cost has neither.
- **A personal device as an execution place of a shared project's workflow.** A run's rights are the
  run's, not its launcher's; on a machine whose owner is root they become that person's, and every
  guarantee that is a mechanism in a pod the platform controls (ADR 0010) becomes that person's promise.
  A personal agent does not run in a project's workflow either
  ([ADR 0061](0061-agent-ownership-scopes-and-forks.md)).
- **A phone as an execution place.** No container, background execution throttled, no isolation. A phone
  is a surface: it converses, approves and is notified.

## Alternatives rejected, and when each wins

| Alternative | Wins when | Loses when | Verdict |
|:--|:--|:--|:--|
| The organisation's own MCP gateway behind its own outbound tunnel, consumed as it is (ADR 0105) | the organisation already runs a gateway or a DMZ: no code at all | it has no gateway, or refuses third-party tunnels | **Offer it first.** An outpost is the same service for those who have none, shipped by the platform |
| A remote cluster reached over a VPN, the platform holding a credential to that cluster's API | both infrastructures have one owner and a private network already joins them | the other side is another party's production: it does not expose its cluster's API, and a standing credential to it would rest at the control plane | Right for one's own second cluster; not this need |
| A remote agent over A2A (`kind: remote`, ADR 0062) | the agent is a third party's and a black box is accepted | its endpoint must be reachable (§4, §8); its facts are declared, never measured (§5); its tools are not governed | No, for this need |
| A Temporal worker at the remote place | never here | activities open the database and hold the key that signs run tokens | **Refused** |

## Consequences

- New objects: three tables under row-level security, a principal kind, a permission, a package, a chart
  and a door. Stories S65-10 to S65-19 build them; this record proves nothing, it is the decision.
- An organisation that already exposes an MCP gateway through its own tunnel needs none of this: ADR 0105
  serves it as it is. The documentation says so before it describes an outpost.
- The outpost's operator becomes a party with duties: the policy file, one firewall rule (one hostname,
  port 443, outbound), the upgrades, the local log. They get a guide of their own (S65-18), and a
  shared-responsibility statement that lives outside this repository.
- Holding the control plane stays serious for an outpost that allows writes: what its policy allows can be
  asked for. The outpost bounds that (aliases, patterns, `writes: deny`, ceilings); it does not remove it,
  and the guide does not pretend otherwise.
- A revocation moves the whole outpost's epoch, so what its other instances had in flight is not taken
  either, and a write among it ends in doubt (ADR 0149 §5). Revoking answers a key that may be stolen. It
  is not how a healthy instance is retired: the outpost is paused first and its calls left to finish.
