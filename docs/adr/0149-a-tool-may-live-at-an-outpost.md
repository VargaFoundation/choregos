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
   Declaring one needs an outpost of the same organisation that is enabled for `tools` and is handed
   calls: an outpost the organisation does not have is a 422, one that is paused or revoked a 409 (7),
   and an edit is judged as a declaration. An instance that announces `tools` need not be there yet: an
   administrator may declare before the outpost's operator has one running, and discovery then says
   that the outpost is offline. An outpost that still has connectors is not deleted (409, as
   `gateway_in_use` is for the gateway).
2. **The `service` identity only.** The call reaches the outpost's server under the credential the outpost
   holds for that endpoint: what ADR 0107 calls the *assigned* identity. The type supports no other. An
   assistant's needs are bound to connectors and reach them through the same broker, so a need that must
   be called as the person cannot be bound to an `mcp-outpost` connector: it is refused when it is bound,
   not at the first call. A connector declares the identities it carries in its configuration
   (`identity_modes`, ADR 0135), and this type's declared schema is closed and has no such key; the one
   function that answers the question (`services/connector_identity.py`) says `service` alone for the
   type, whatever a row holds. Carrying a person's token to an outpost is left for later, with a security
   review of its own: such a token must not rest in a row of the platform nor cross a process that is not
   its audience, so it is sealed directly to the claiming instance's key, or it does not travel.
3. **`Route` gains "through an outpost", and the client keeps its interface.** For an `mcp-outpost`
   connector `route_of` returns a route that names the outpost and the alias and has no host;
   `connector_client` returns a client with the two methods the callers use, backed by the rendezvous (4)
   instead of an HTTP connection. It does not extend the MCP client: the boundary test keeps that to the
   platform-run plugin servers. The broker, the governed-write effect and discovery therefore do not
   change, beyond turning one more family of refusals into their own where they already turn the
   gateway's (7), and every guarantee holds as for a direct connector: selection, policy, groups, argument schema,
   argument-bound grants and approval are decided **before** anything leaves; the result is bounded and
   screened, the ledger line and the digests are written, when it comes back. The outpost sees a call
   after the platform has decided it. It decides nothing the platform decides; it may refuse what its own
   policy refuses (ADR 0147 §6). Who the call is made for, which the gateway route signs into an assertion
   (`identity_of_run`, `identity_of_action`), travels as the asserted names the outpost may log.
4. **A rendezvous, not a second durable queue.** A call through an outpost is a row with a short life
   (`outpost_calls`): written by the caller, claimed by one instance at its next poll, answered, then
   emptied (ADR 0148 §11). A read is a synchronous call of under a minute: when no instance claims it in
   time it fails fast, with a typed refusal that names the outpost; and when no instance that serves the
   alias polled within the poll window, it fails at once and nothing is written. The caller places the
   row in its own transaction and commits it, whoever called: the row must be visible before an instance
   can claim it. It then waits with no transaction and no pooled connection. A write already has its durable
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
     in a state `in_doubt` where the platform stops: shown to a person with what was sent (the outpost,
     the alias, the instance, the operation, the digest of the arguments, when it was claimed) and with
     what the action had already done. It is never replayed automatically, and nothing the action had
     done is compensated automatically either: both would act on a state nobody knows. No route replays
     an `in_doubt` action. An agent that repeats the call finds the same action (`call_id`) and is told
     it is in doubt. Only a person moves it on, by settling it (below).
   - **What ends in doubt, exactly** (S65-14). The effect's journal row takes the status `in_doubt`
     with what was sent, before anything is said to the workflow, and it is not attempted again: a
     task Temporal delivers twice is answered from the row. The workflow closes the action `in_doubt`
     on a patched branch that compensates nothing; the activities hold the same line by themselves (a
     compensation asked for an action that has an effect in doubt undoes nothing, and such an action
     is never closed `failed`), so a workflow task answered by code that predates the status does
     not compensate either. Once a delivery of an effect is in doubt, nothing that follows makes the
     effect a failure: a refusal or an outage met by a later attempt still ends in doubt. A retry
     that comes after the rendezvous emptied an answer nobody recorded (ADR 0148 §11) finds a
     delivered write whose answer is gone: it is in doubt too, never a second call. Until the
     effect's result is recorded the answer waits on its row; it is emptied in the transaction that
     records the result. Two things are not a doubt and must not become one. A worker that stops
     while its write waits does not give the call up, as it does for a read: the instance still
     answers, and the effect played again by another worker meets the same call. And an effect
     played again that is refused before it leaves (its outpost paused, its operation closed since)
     is a refusal only when no instance claimed the delivery of the attempt before it; when one
     did, and what became of it was never recorded, the effect ends in doubt instead of failing,
     since failing would compensate what the action had done around a write that may have been made.
   - **A work item waits for its action in doubt.** The action of a workflow's transition that ends
     in doubt does not settle the transition: its `action_succeeded` guarantee waits, as it waits
     for an approval nobody gave. Read as a failure it would follow `on_fail`, whose retry proposes
     the action again under a new attempt, which is a second delivery of the write. A person moves
     the work item on.
   - **A person settles an action in doubt** (S65-26). What the platform cannot know, a person can:
     they check the system the write was for and record on the action what they found,
     `POST /projects/{id}/actions/{action_id}/settlement` with an `outcome` and a `note` (and, with
     `made`, the `result` they read there).
     - *Who.* Whoever may approve the action, under the same four guards as the approval (ADR 0035,
       ADR 0052): a human session with a recent authentication under the action's `step_up_minutes`,
       the rank of one of its approvers and the group the rule names, and the separation of duties
       (whoever proposed the action, or owns the agent that did, does not settle it). The settlement
       is a decision about the action: it decides whether the rest of an approved action runs, or
       what it did is undone, so it is guarded as the decision that let it run in the first place.
       An API token, an MCP client and therefore the CLI never settle.
     - *What is recorded.* On the effect's journal row (`action_effects.settlement`): the outcome, the
       person, the time, the note, the stated result, the age of their authentication, and the
       doubt's evidence (what was sent and never answered), moved there from the row's result so that
       the result becomes what the effect returned. The action reads it back (`Action.settlement`),
       the audit has `action.settle`, the events `choregos.action.settled`.
     - *`made`.* The effect is `done` with the stated result (none: empty), which the effects after it
       cite as they would cite an answer. The action is `approved` again and its `ActionWorkflow`
       runs once more under the same id: the journal answers the effects already done (an evidence
       one of them waited for is not asked again), the effects after the write are played, and a
       later failure compensates backwards as for any action, the settled effect included, since a
       person established that it was made. The action ends `succeeded` or `failed`.
     - *`not_made`.* The effect is `not_made`: `execute_effect` meets it as a final refusal and never
       attempts it, so the workflow takes its failure branch. What the action had done before is
       compensated backwards, and it ends `failed`. This is the rule a doubt suspended, not a new
       one: nothing was compensated because undoing what came before would act on a state nobody
       knew; once a person states the write was not made, the state is known and is the state of
       any effect that failed. The write is not delivered again: a person who wants it made proposes
       the action again, under a new decision.
     - *The work item.* It waited for the action as for an approval; the run that follows the
       settlement closes the action as any run does and tells the work item that waits for it
       (`action_settled`), which then follows its success or its `on_fail`.
     - *Twice.* The action's row and its effects are read locked and moved by a compare-and-set
       from `in_doubt`. A settlement with the outcome already recorded answers the action as it is
       and records nothing; the other outcome is a 409 `action_already_settled`; an action that is
       not in doubt and was never settled, a 409 `action_not_in_doubt`.
     - *No change under `workflows/`.* The workflow is not resumed: a new run of `action-<id>` is
       started after the commit, as an approval starts one, and the relay starts it if that start is
       lost (ADR 0085). For that run to start at all, the action's first run having completed, the
       start is a restart (`restart_action`, `WorkflowIDReusePolicy.ALLOW_DUPLICATE`, joining a run
       still going), the one start that reuses an action's id; the relay restarts rather than starts
       an approved action that was once closed in doubt. Every other start keeps refusing a finished
       id, so a replica's late start of a finished action still starts nothing; a late duplicate of
       a restart starts a run that does nothing, since `load_action` runs nothing for an action that
       is not approved.
   - **The one exception is an operation that declares an idempotency key.** When its rule lists
     `action_key` as a platform argument, the platform fills that key as it does today, and the write may
     be delivered again with the same key: the server, not the platform, makes the second delivery
     harmless. The doubt is then one the effect's existing policy retries: each attempt that follows a
     doubt places one more delivery, a call of its own at the rendezvous (a claimed write is never
     handed over twice under one id), and a doubt that outlives the policy's attempts is still a
     doubt, never a failure.
   - **A deterministic call id under a `UNIQUE` constraint.** A write's call id is derived from its
     effect's key, which `execute_effect` records before each attempt. The table refuses a second row for
     it, so a retry of the effect meets the first row (answered: the stored outcome; claimed and
     unanswered: in doubt) and creates no second delivery. A compensation is played under its effect's
     key and is a call of its own (`<key>:compensation`): through the connector its effect went
     through, it never meets its effect's row.
   - **The daemon keeps a persisted journal of call ids.** It records a write's id on disk before it
     executes it, and the answer after. An id it has seen is answered from the journal, never executed
     again; a daemon restarted in the middle of a write finds the id without an answer and reports
     exactly that. The journal holds the id of every call, a read's too, at least until the call's
     deadline, and the first thing the outpost decided for it (to make it or to refuse it): that is what
     makes a call replayed on the path harmless (ADR 0148 §13).
6. **Refused when the write is opened** (ADR 0101). A write operation of an `mcp-outpost` connector
   cannot be set to `allowed` or `approval` unless its rule says one of two things: that the operation
   takes the platform's idempotency key, or that an in-doubt outcome is accepted for it, explicitly.
   Without either, the change is a 422 that names both. A discovered tool has no declaration, so the
   organisation's operation rule (`PATCH /orgs/{org}/connectors/{name}/operations/{operation}`) is where
   this is said, by the administrator who opens the write. A guarantee the platform cannot give, exactly
   once on a server it cannot see, is not implied by silence.

   The two statements are fields of the rule (S65-14). `platform_arguments` is the list a connector
   type and a plugin server's descriptor already declare, said here by the organisation for a
   discovered tool: listing `action_key` is the first statement, and it is refused for a tool whose
   input schema has no such argument, for a read, and for an operation whose type declares its own.
   `in_doubt_accepted` is the second, and is refused for anything but a write of an `mcp-outpost`
   connector: nothing else ends in doubt. The rule is judged as a change would leave it, so
   withdrawing the last statement of an open write is refused as opening it with neither is; a tool
   whose schema drifts is closed again (ADR 0034) and loses both; and the effect reads the rule
   again before a write leaves, as it reads the policy. The platform does not verify that a server
   honours the key its rule says it takes.
7. **Typed refusals**, in the manner of `GatewayRefusal`: a class per code, never a crash, never a 500.

   | Refusal | When | Executed at the outpost? |
   |:--|:--|:--|
   | `OutpostPaused` | the outpost is paused or revoked, or outposts are switched off | no: nothing is written |
   | `OutpostNotAdmitted(reason)` | a quota of the door is reached (ADR 0148 §10), or the call is too large to cross it | no: nothing is written |
   | `OutpostOffline` | no instance that serves the alias polled within the poll window, or none claimed the call in time | no |
   | `OutpostRefused(code)` | the outpost's own policy refused: unknown alias, tool outside its patterns, write denied, a ceiling; or the door could not carry the call to its claimer (`payload_too_large`) | no |
   | `OutpostTimeout` | a claimed read got no answer in time | unknown; a read may be asked again |
   | `OutpostInDoubt` | a claimed write got no answer in time | unknown; never asked again (5) |

   Each caller turns a refusal into its own: a tool error that names the outpost for a read; a refusal
   that leaves no action behind where the route is checked before the action exists (`propose_call`,
   `write_through_action`); a 409 at declaration and at discovery; a final refusal without a Temporal retry
   in the workers, with two exceptions that are a write's (5): a write nobody claimed (`OutpostOffline`,
   a quota of the door) is retried by the effect's policy, and `OutpostInDoubt` is neither a refusal nor
   a retry. A call that reached no server is not booked in the ledger, as a call the network policy
   stopped is not. The agent whose write ended in doubt is told so with the action's id, as a claim
   that got no answer (504, `action_in_doubt`), and told the same each time it repeats the call.

   For a read, the tool error carries the refusal's code, the outpost and the alias, under a status
   that says its class: 403 paused, 503 offline, 422 refused (with the outpost's own code), 429 a
   quota, 413 a call too large, 504 a claim that got no answer. The first five reached no server and
   carry the type every booking of a tool call already skips (`network_policy`; `rate_limited` for a
   quota), so no caller of the broker had to learn a new one; the last may have been executed, carries
   none and is booked. At declaration and at discovery a quota is a 429 that says when to come back,
   not a 409: it is not a state of the outpost. A deployment that has outposts switched on and no key
   to seal a call's arguments under while they wait hands nothing to an outpost: that is
   `OutpostPaused`, said with its reason.
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
    per-host rate have no host to look at. `enforce_connector_call` checks nothing for a connector
    without an address, and this one has none: its `endpoint` is an alias. The platform reads a
    connector's address from a list of configuration keys that `endpoint` is one of, so the alias is
    told apart there (`connector_host` answers none for a configuration that names an outpost);
    otherwise it would be judged by the organisation's deny list, rate-limited with any direct
    connector whose host bears the same name, and journaled as a place the platform reached. The type
    declares no egress host, as `mcp-gateway` does not. It is not
    unbounded for that: the outpost's ceilings and the door's quotas bound it (ADR 0147 §6, ADR 0148 §10),
    and each call is written to `network_decisions` under the outpost and the alias where a host would be
    (`outpost:<name>/<alias>`), so the journal shows that the tool was reached, and where: `allowed`
    when it was answered, `denied` when the outpost or the door refused it, `rate_limited` at a quota,
    `unreachable` when no instance claimed it or the claim got no answer. The line is the call's
    project's; a discovery belongs to none and is in the audit log.
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
- Decisions 5 and 6 arrived after the route (S65-14 after S65-13). In between, a write through an
  outpost was not opened at all. The refusal of decision 6 replaced that one; the rendezvous still
  places no write without the key of the governed effect that plays it.
- `workflows/action.py` is concerned, not only activities: its retry policy and its failure branch are
  what a claimed write must not go through. The change is a patched branch (`action-in-doubt`), and
  the replay suite stays green with the histories recorded before it; `action-in-doubt-S65-14` covers
  the branch. No activity result is final for that workflow, so the outcome could not be carried
  without it: a failure is final only through the compensation it must not run.
- `ActionStatus` gains a status where the platform stops, and which only a person's settlement leaves
  (S65-26): a contract change of its own (`settleAction`, `ActionSettlement`, `SettlementOutcome`,
  the effect status `not_made` and the event `choregos.action.settled`), one column
  (`action_effects.settlement`), and a restart that reuses an action's workflow id after a completed
  run. A
  settlement `made` that is false runs the rest of the action on a write that was not made; one
  `not_made` that is false undoes what came before a write that was: the platform records who said
  it, and checks neither.
- A plugin's declared floor (ADR 0096 §5: `human_approval` never lowered, an undeclared tool refused)
  does not follow its server to an outpost: there the tools are discovered, and the administrator sets
  each operation's policy.
- An offline outpost fails reads fast and leaves approved writes to their retries, then to a failure
  that says why. A flaky link between claim and answer turns writes into `in_doubt`: that is the price of
  never writing twice, and the count of in-doubt actions is the measure to watch.
- A call gains the latency of one wake-up of the poll (ADR 0148 §7).
- This record proves nothing: it is the decision. Stories S65-13 and S65-14 carry it.
