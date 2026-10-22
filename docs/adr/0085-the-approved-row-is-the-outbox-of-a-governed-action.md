# 0085 — The approved row is the outbox of a governed action, and a workflow without one runs nothing

- **Status**: accepted, 2026-10-22 — implemented by S40-19
- **Concerns**: the API (`db/session.py`, `deps.py`, `services/actions.py`, `services/action_relay.py`,
  `main.py`), the ontology plugin (`service/actions.py`), the orchestrator (`activities/actions.py`);
  builds on [ADR 0008](0008-idempotence-and-run-id.md) (idempotence),
  [ADR 0035](0035-governed-actions-in-the-core.md) (governed actions) and
  [ADR 0052](0052-netops-connectors.md) (the `connector.call` status check)

## Context

S40-19 asked whether an audit event can be lost: a write recorded in one store and not in the other,
or a refusal that leaves no trace. The code was read for every place the platform writes a record of
what happened next to the thing that happened.

**What already holds.** These need no change; the decision records why.

- **The audit log** (`audit.py::record`) adds its row to the caller's session. The request session
  (`deps.py::get_db`) commits once at exit and rolls back on any exception, and it commits *before*
  the response is sent (`scope="function"`). An audited change and its audit row are one
  transaction: both are kept or neither. A refusal the platform audits (an MCP call refused,
  `mcp/transport.py`) is answered with a 200, so its transaction commits.
- **Events** (`services/evenements.py::persist_event`) write the `events` row in the same session, and
  their publication goes through `pg_notify`, which PostgreSQL delivers at commit and drops on
  rollback. The `events` table is already the outbox of the live stream; every replica relays it
  (`RelaisPostgres`).
- **The secret store** is a table of the same database (`SealedDbSecretStore`).

**What did not hold: governed actions between the database and Temporal.**

1. `services/actions.py::decider` set the action `approved`, audited the decision and emitted its
   event, then started `action-<id>` in Temporal — **inside** the request's transaction. If the
   commit then failed, or the process died before it, the workflow was running for an approval
   nobody kept.
2. `load_action`, the workflow's first activity, refused nothing: a missing row was retried, any
   other status was executed. A workflow started for an action still `pending_approval` (the
   failed commit above), `rejected`, or already finished ran its effects. Only the `connector.call`
   effect re-checked the status (ADR 0052); every other effect — a tracker write, a GitOps pull
   request, an ontology object — did not.
3. The broker (`services/courtier.py`) commits an action approved by the policy, then starts its
   workflow. If Temporal is down or the process is killed between the two, the action stays
   `approved` and nothing starts it: a replay of the same call finds the action and starts nothing.
   It stays stranded for ever.
4. The ontology plugin's auto-approved proposal started its workflow inside the transaction, like 1.

## Decision

1. **The `approved` row is the outbox.** A workflow is started only *after* the transaction that
   approved its action has committed. `db/session.py::after_commit` registers a callback on a
   session; an `after_commit` SQLAlchemy event releases it, an `after_rollback` drops it, and the
   request session (`get_db`, `get_db_plateforme`) and `session_scope` run what was released once the
   commit returned. `services/actions.py::start_once_committed` is the only way the decision and the
   ontology's auto-approval start an action. A callback that fails is logged, never raised: the
   approval is committed and the relay starts it.
2. **A relay finds what the start lost.** `services/action_relay.py::start_approved_actions` starts the
   workflow of every action still `approved` more than 30 seconds after its last update, at most 100
   per pass, every 30 seconds, in every API replica (`main.py` lifespan). The workflow id is
   deterministic (`action-<id>`) and started with `ALLOW_DUPLICATE_FAILED_ONLY`: a running workflow is
   not started twice, a completed one is never started again, and one that failed before it moved
   the row (its first activity) is started again — so replicas need no lock. An action that reached
   `running` is never relayed: its workflow is Temporal's to retry.
3. **A workflow without a committed approval runs nothing.** `load_action` executes only an action
   `approved` or already `running` (its own retry after it moved the row). Any other status is a
   non-retryable `ActionNotApproved`, raised before the first effect and outside the compensation
   block: the workflow fails, no effect runs, nothing is compensated, and the row is left exactly as
   it was — a pending action stays the approvers' to decide. The `connector.call` check of ADR 0052
   stays, as a second line.
4. **Nothing else changes.** Audit rows and events stay in the caller's transaction (above). The
   workflow's code and history are unchanged: only activities moved, so no replay history is
   recorded.

## Alternatives rejected

- **A separate outbox table** (`action_starts`) written with the approval and drained by a worker. It
  says the same thing as `status = 'approved'` twice, and the two could disagree. The action row
  already is the durable intent.
- **Start inside the transaction, and let `load_action` retry until the row is approved.** It keeps the
  workflow alive for an approval that may never commit, and it cannot tell "not yet committed" from
  "rolled back": it would wait, then fail, after running nothing — or worse, run the moment someone
  else approves through another path.
- **Two-phase commit with Temporal.** Temporal has no XA; the deterministic id already gives the
  idempotence a retry needs.
- **Relay from the orchestrator worker** (`start_reconciliation_loops`). The API owns the decision and
  already runs the event relay; an API replica sees an approval the moment it commits, a worker
  might not be deployed with the API (the local install runs the API alone with fakes).

## Consequences

- An approval is answered once its row is written; the workflow starts right after, in the same
  request, or within about a minute through the relay if Temporal was unreachable.
- An approval whose commit fails starts nothing (`test_action_outbox.py`); a workflow started by any
  other path for an action that is not approved runs no effect and leaves its row alone
  (`test_actions.py`, `test_netops_governed_writes.py`). The netops test now expects the workflow to
  fail instead of closing the pending action as `failed`: the stricter, safer outcome.
- A replica of an older version can still start a workflow before its commit during a rolling
  upgrade; `load_action` refuses it if the row is not yet approved, and the relay starts it again
  once the approval is committed (the failed run allows a new one).
- **Not decided or not covered here**:
  - on SQLite (development only), `events.py::diffuser` publishes to the in-process bus at once,
    before the commit; PostgreSQL's `pg_notify` is transactional;
  - the egress reporter (`egress/report.py`) holds cumulative counters in memory and flushes them
    periodically and at SIGTERM: a killed sidecar loses the counts since its last flush. They are
    measurements, not audit records; making them durable is its own story;
  - the relay's cadence and grace are constants, not settings;
  - no metric counts relayed starts yet; the relay logs `action.relay.started`.
