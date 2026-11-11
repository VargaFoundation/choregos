# 0149 — A tool may live at an outpost

- **Status**: proposed, 2026-11-11
- **Concerns**: a connector type `mcp-outpost` (`packages/adapters/src/choregos_adapters/specs.py`,
  `builtins.py`, `registry.py`); `Route`, `route_of` and `connector_client`
  (`apps/api/src/choregos_api/services/mcp_gateway.py`); the broker (`services/broker.py`); the
  `connector.call` effect (`effects.py`), its activity and its workflow
  (`apps/orchestrator/src/choregos_orchestrator/activities/actions.py`, `workflows/action.py`); discovery
  and the operation rule (`routers/org_connectors.py`); `ActionStatus` (a new `in_doubt`, a contract
  change); `GET /orgs/{org}/agents/{slug}/access`; the `network_decisions` journal; the outpost's chart.
  When accepted, amends [ADR 0034](0034-connectors-by-capability.md) (a connector may be reached through an
  outpost; the broker's guarantees are unchanged),
  [ADR 0105](0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md) §1 and §5 (an outpost is a transport
  of the broker's outbound segment; `mcp-outpost` is exempt from the gateway's mode `required`) and
  [ADR 0127](0127-an-agent-is-a-closed-set.md) §6 (the access endpoint shows the outpost route and its
  offline state); applies [ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md); leaves
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md) unchanged. Builds on
  [ADR 0008](0008-idempotence-and-run-id.md), [ADR 0035](0035-governed-actions-in-the-core.md),
  [ADR 0052](0052-netops-connectors.md), [ADR 0066](0066-the-organisation-egress-allowlist.md),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md),
  [ADR 0085](0085-the-approved-row-is-the-outbox-of-a-governed-action.md),
  [ADR 0107](0107-an-agent-acts-under-an-identity.md),
  [ADR 0128](0128-a-tool-grant-may-bind-its-arguments.md), [ADR 0147](0147-an-outpost-only-dials-out.md)
  and [ADR 0148](0148-the-outpost-door-is-a-third-door-with-its-own-contract.md); plan
  `docs/plan/PLAN-2026-11-11-outposts.md` (stream S65)

## Context

An outpost (ADR 0147) polls a door (ADR 0148). This record says what it is handed, and what the platform
promises about it. Read on `main` at `9f646fda4`:

- **Every call to an organisation connector crosses one seam.** `route_of` decides how a connector is
  reached and `connector_client` returns the client it is called with (`services/mcp_gateway.py`). The
  broker's read, its proposal and its write (`services/broker.py`: `_call`, `propose_call`,
  `write_through_action`), the governed-write effect (`effects.py`: `_within_the_network_bounds`,
  `_call_the_connector`) and discovery (`routers/org_connectors.py`: `discover_operations`) all go
  through it. `Route` holds a host and whether that host is the organisation's gateway.
- **What governs a call is in the API, around that seam, never at the server.** The agent's selection,
  the operation's policy and groups, its argument schema and the grants that bind its arguments
  (`check_arguments`, ADR 0128), the approval (`propose_call`) and the governed write are decided before
  the client is built. The bound on the result (`bounded_result`), the injection guard, the ledger line
  `mcp:<connector>` and the digests of the `tool.called` event are applied when the answer comes back
  (`routers/internal.py`, `callRunTool`).
- **The callers only need two methods.** They call `call_tool` or `list_tools` when the client has them
  (`broker.py`, `effects.py`, `org_connectors.py`). The boundary test lets one class outside the client's
  own package extend the MCP client, the platform-run plugin servers' (`RUNTIME_MCP_CLIENTS`).
- **`mcp-gateway` is the precedent of a connector with no URL and no secret** (`specs.py`: `server` and
  `timeout_s`, no secret field, no egress host of its own; ADR 0105 §6). It cannot be built from its
  configuration alone: the platform supplies the rest.
- **A write is always a governed action** (ADR 0034 §7). The broker proposes it under a deterministic id
  (`call_id`: the same run, tool and arguments are the same action). The `ActionWorkflow` plays its
  `connector.call` effect in the workers; `execute_effect` records the effect's key
  (`<action id>:<position>`) as `started` and commits before each attempt, and answers a `done` key from
  the recorded result. An effect is written to be idempotent because it is retried: the workflow's
  `EFFECT` policy retries a transient failure, up to six attempts, and any final failure compensates what
  was done and closes the action `failed`.
- **The platform already fills an idempotency key.** An operation may list `action_key` among its
  `platform_arguments` (ADR 0052, ADR 0096 §9): the effect drops whatever the agent wrote under that name
  and fills a digest of the action's id, the operation and the canonical arguments (`effects.py`,
  `_platform_arguments`), the same at every retry of one action and different for another call. Only a
  connector type or a plugin server's descriptor can declare it: `declared_operation` answers `None` for a
  discovered MCP tool (`services/connectors.py`).
- **A discovered tool is born closed**, and it is a write unless its server declares it read-only
  (`discover_operations`; ADR 0034 §5).

## Decision

1. **A connector type `mcp-outpost`.** Kind `mcp`, declared with `{outpost, endpoint, timeout_s}`: the
   outpost's name, the alias of an endpoint in that outpost's policy file, and a timeout. No URL and no
   secret field, like `mcp-gateway`: the address and the credential are the outpost's (ADR 0147 §7).
   Because the kind stays `mcp`, discovery, drift detection, the per-operation policy and governed writes
   are unchanged; discovery returns the tools the outpost's policy lets through, each born closed.
   Declaring one needs an outpost of the same organisation that is enabled and announces `tools`, and an
   outpost that still has connectors is not deleted (409, as `gateway_in_use` is for the gateway).
2. **The `service` identity only.** The call reaches the outpost's server under the credential the outpost
   holds for that endpoint: what ADR 0107 calls the *assigned* identity. The type supports no other. An
   assistant's needs are bound to connectors and reach them through the same broker, so a need that must
   be called as the person cannot be bound to an `mcp-outpost` connector: it is refused when it is bound,
   not at the first call. Carrying a person's token to an outpost is left for later, with a security
   review of its own: such a token must not rest in a row of the platform nor cross a process that is not
   its audience, so it is sealed directly to the claiming instance's key, or it does not travel.
3. **`Route` gains "through an outpost", and the client keeps its interface.** For an `mcp-outpost`
   connector `route_of` returns a route that names the outpost and the alias and has no host;
   `connector_client` returns a client with the two methods the callers use, backed by the rendezvous (4)
   instead of an HTTP connection. It does not extend the MCP client: the boundary test keeps that to the
   platform-run plugin servers. The broker, the governed-write effect and discovery therefore do not
   change, and every guarantee holds as for a direct connector: selection, policy, groups, argument schema,
   argument-bound grants and approval are decided **before** anything leaves; the result is bounded and
   screened, the ledger line and the digests are written, when it comes back. The outpost sees a call
   after the platform has decided it. It decides nothing the platform decides; it may refuse what its own
   policy refuses (ADR 0147 §6). Who the call is made for, which the gateway route signs into an assertion
   (`identity_of_run`, `identity_of_action`), travels as the asserted names the outpost may log.
4. **A rendezvous, not a second durable queue.** A call through an outpost is a row with a short life
   (`outpost_calls`): written by the caller, claimed by one instance at its next poll, answered, then
   emptied (ADR 0148 §11). A read is a synchronous call of under a minute: when no instance claims it in
   time it fails fast, with a typed refusal that names the outpost. A write already has its durable
   outbox, the approved `actions` row: ADR 0085 made that row the outbox and rejected a separate outbox
   table beside it, because the two could disagree. The rendezvous adds no durability and promises none.
   What survives a crash is the action, not the row.
5. **Delivery semantics.**
   - **A read is delivered at least once.** A read that timed out may be asked again, and the outpost's
     server may execute it twice.
   - **A write is delivered at most once.** One claim. A claimed write is never leased again: not after a
     timeout, not when its instance disappears, not when Temporal retries its effect. A write nobody
     claimed reached no server, and only that one is retried, by the effect's existing policy.
   - **A lost lease on a write ends `in_doubt`.** When a claimed write gets no answer within its lease,
     the platform does not know whether the outpost's server executed it. The effect and its action end
     in a terminal state `in_doubt`: shown to a person with what was sent (the outpost, the alias, the
     instance, the operation, the digest of the arguments, when it was claimed) and with what the action
     had already done. It is never replayed automatically, and nothing the action had done is compensated
     automatically either: both would act on a state nobody knows. No route replays an `in_doubt` action.
     An agent that repeats the call finds the same action (`call_id`) and is told it is in doubt.
   - **The one exception is an operation that declares an idempotency key.** When its rule lists
     `action_key` as a platform argument, the platform fills that key as it does today, and the write may
     be delivered again with the same key: the server, not the platform, makes the second delivery
     harmless.
   - **A deterministic call id under a `UNIQUE` constraint.** A write's call id is derived from its
     effect's key, which `execute_effect` records before each attempt. The table refuses a second row for
     it, so a retry of the effect meets the first row (answered: the stored outcome; claimed and
     unanswered: in doubt) and creates no second delivery.
   - **The daemon keeps a persisted journal of call ids.** It records a write's id on disk before it
     executes it, and the answer after. An id it has seen is answered from the journal, never executed
     again; a daemon restarted in the middle of a write finds the id without an answer and reports
     exactly that.
6. **Refused when the write is opened** (ADR 0101). A write operation of an `mcp-outpost` connector
   cannot be set to `allowed` or `approval` unless its rule says one of two things: that the operation
   takes the platform's idempotency key, or that an in-doubt outcome is accepted for it, explicitly.
   Without either, the change is a 422 that names both. A discovered tool has no declaration, so the
   organisation's operation rule (`PATCH /orgs/{org}/connectors/{name}/operations/{operation}`) is where
   this is said, by the administrator who opens the write. A guarantee the platform cannot give, exactly
   once on a server it cannot see, is not implied by silence.
7. **Typed refusals**, in the manner of `GatewayRefusal`: a class per code, never a crash, never a 500.

   | Refusal | When | Executed at the outpost? |
   |:--|:--|:--|
   | `OutpostPaused` | the outpost is paused or revoked, or outposts are switched off | no: nothing is written |
   | `OutpostOffline` | no active instance claimed the call in time | no |
   | `OutpostRefused(code)` | the outpost's own policy refused: unknown alias, tool outside its patterns, write denied, a ceiling | no |
   | `OutpostTimeout` | a claimed read got no answer in time | unknown; a read may be asked again |
   | `OutpostInDoubt` | a claimed write got no answer in time | unknown; never asked again (5) |

   Each caller turns a refusal into its own: a tool error that names the outpost for a read; a refusal
   that leaves no action behind where the route is checked before the action exists (`propose_call`,
   `write_through_action`); a 409 at declaration and at discovery; a final refusal without a Temporal retry
   in the workers, except `OutpostInDoubt`, which is neither a refusal nor a retry. A call that reached no
   server is not booked in the ledger, as a call the network policy stopped is not.
8. **The gateway's mode `required` does not apply** (amends ADR 0105 §5). An `mcp-outpost` connector is
   not a direct `mcp` connector and is exempt, as a plugin's server is (ADR 0105 §9). `required` exists so
   that the gateway is the one place that holds upstream credentials and decides the upstream network;
   here Choregos holds no upstream credential and opens no upstream connection: the outpost does both.
   Today `route_of` and `check_declaration` refuse the type `mcp` alone, so the exemption needs a test
   that keeps it, not a branch.
9. **Plugin servers, and internal trackers and forges, at an outpost.** The outpost's operator deploys
   the server's image. The outpost's chart can render such servers beside the daemon, by a choice made in
   the outpost's own values. The control plane creates no workload at an outpost and sends it no secret:
   it calls an alias. ADR 0096 is unchanged: it governs the servers the platform runs, and a server at an
   outpost is not one of them. It is the operator's, declared as an `mcp-outpost` connector, its tools
   discovered and born closed like any MCP server's. An internal tracker, forge or deployment tool is
   reached the same way, as the tools of a server at the outpost; there is no generic HTTP relay
   (ADR 0147).
10. **The access endpoint says where a tool lives** (amends ADR 0127 §6). `GET
    /orgs/{org}/agents/{slug}/access` shows, for a connector tool reached through an outpost, the
    outpost's name and whether it is offline (no active instance seen within the poll window): a person
    reads, before a run, that a tool its agent names will not answer. The fields are additive and come
    from the function that computes the run's own listing (`services/agent_reach.py`).
11. **The network bounds.** A call through an outpost is not an egress of the organisation (ADR 0066) nor
    of a run (ADR 0070): the platform opens no connection for it, so the allowlist, the ceiling and the
    per-host rate have no host to look at. `enforce_connector_call` already checks nothing for a connector
    without an address, and the type declares no egress host, as `mcp-gateway` does not. It is not
    unbounded for that: the outpost's ceilings and the door's quotas bound it (ADR 0147 §6, ADR 0148 §10),
    and each call is written to `network_decisions` under the outpost and the alias where a host would be
    (`outpost:<name>/<alias>`), so the journal shows that the tool was reached, and where.
12. **Runs at an outpost are not decided here.** A whole run executing at an outpost is justified only
    when a build or its tests need dependencies only the outpost has, and on a named need. Before a record
    could decide it, all of this would have to be true:
    - **the run's API is fenced by the lease.** A run's token is minted when an instance claims the run,
      never when it is queued; it carries the outpost and the epoch of the lease; the run's API refuses a
      call whose epoch is no longer the lease's. This stands on a run that ended staying ended (S65-01,
      ADR 0143);
    - **run callbacks have an entrance of their own**, apart from the console's hostname (S65-02,
      ADR 0144);
    - **the runner and the agent are in separate containers** (a sibling record of this stream, in
      preparation): a credential handed to a sandbox at an outpost is out of the agent's reach by
      construction;
    - **no layer-7 gateway at the outpost.** The sandbox speaks TLS end to end to the run's entrance and
      to the model gateway's, through a CONNECT proxy of the daemon pinned to those two hostnames: one
      firewall rule, and no protocol terminated at the outpost;
    - **the wall is the outpost's, and it is verified**: a namespace of its own, deny by default, and a
      daemon that refuses the `runs` capability when it cannot verify that its network policy is enforced
      (ADR 0101);
    - **`executed_at` is a recorded fact**, `platform` or `outpost:<id>`, not an assurance level. A gate
      that wants measured evidence already has `measured_only`
      (`packages/core/src/choregos_core/gates/__init__.py`), and what the platform can compute from the
      pushed branch (the diff, the scope, `no_secrets`) it keeps computing itself.

    Until a record decides it, `runs` is a capability no outpost announces.

## Alternatives rejected

- **A durable queue of calls with retries.** For reads it buys nothing a synchronous failure does not;
  for writes it is the second outbox ADR 0085 refused, and its retries are the double write.
- **At-least-once writes, deduplicated by the daemon alone.** A journal lost with its disk is a second
  write. The only deduplication that survives the outpost is a key the server itself honours.
- **Replaying an in-doubt write after a delay.** Waiting does not tell whether it was executed.
- **A flag on the direct `mcp` connector** (`via: outpost`) instead of a type. The direct connector holds
  a URL and a secret; this one holds neither, and a type is what the checks can name (ADR 0105 took the
  same line for the gateway).
- **Endpoints managed from the control plane** (the URL typed in the console and pushed down). It gives
  the control plane the map ADR 0147 §7 keeps from it.
- **The platform's policy pushed down and checked at the outpost.** The outpost would check names the
  control plane asserts (ADR 0147 §6).

## Consequences

- Contract changes: the connector type and the connector's shape (S65-10, S65-13), `ActionStatus`
  `in_doubt` and the operation rule's two new statements (S65-14), the access answer's fields (S65-17).
- `workflows/action.py` is concerned, not only activities: its retry policy and its failure branch are
  what a claimed write must not go through. The change is a patched branch, and the replay suite has to
  stay green with the histories recorded before it.
- A plugin's declared floor (ADR 0096 §5: `human_approval` never lowered, an undeclared tool refused)
  does not follow its server to an outpost: there the tools are discovered, and the administrator sets
  each operation's policy.
- An offline outpost fails reads fast and leaves approved writes to their retries, then to a failure
  that says why. A flaky link between claim and answer turns writes into `in_doubt`: that is the price of
  never writing twice, and the count of in-doubt actions is the measure to watch.
- A call gains the latency of one wake-up of the poll (ADR 0148 §7).
- This record proves nothing: it is the decision. Stories S65-13 and S65-14 carry it.
