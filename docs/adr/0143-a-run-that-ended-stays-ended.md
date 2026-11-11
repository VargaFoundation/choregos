# 0143 — A run that ended stays ended

- **Status**: proposed, 2026-11-11; S65-01 implements it
- **Concerns**: how the wait for a run ends (`await_run`, `cancel_run`, `record_workflow_failure`), the
  run's API (`/internal/runs/{id}/…`), the egress proxy's source, the runner's first calls; builds on
  [ADR 0008](0008-idempotence-and-run-id.md) (replayable activities),
  [ADR 0016](0016-agent-sandbox-executor.md) (a suspended sandbox),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (a run's network) and
  [ADR 0115](0115-the-agent-never-holds-the-run-token.md) (the egress token)

## Context

A run ends twice. It ends for the platform when the orchestrator stops waiting for it: its budget is
spent, the queue never admitted it, the executor no longer knows it, a person cancelled it, its
interpreter died. It ends for its process when the pod is gone. The orchestrator asks the executor to
remove the pod and goes on, and the two moments are not the same: the cancel can fail, a pod can be
slow to die, a queued pod can start late, a partition can heal. The process that is left holds the run
token, which cannot be revoked before it expires ([ADR 0021](0021-spiffe-behind-the-run-token.md)
lists it as debt, and proposes an identity that stops being issued; it is optional and not
implemented).

Until this decision:

- the orchestrator stopped waiting without saying so on the run's row. The path that abandons a run
  asked the executor to cancel first, then set a status, both under one `suppress(Exception)`: a
  cancel that raised left the run `running`. No path wrote a result;
- the run's API asked one question — is there a result? — and four routes only asked it;
- so a process that outlived its run could post its result. The run came back as `succeeded`, and its
  work item took the pull request, the size, the risk, the scope and the outputs of that result, while
  the next attempt was already running. The same process could call the run's tools, take a git
  token, read its input and start working.

## Decision

1. **"Closed" has one definition**, in `choregos_core.run_closure`, read by the API and the
   orchestrator alike: a run is closed when it holds a result, or when its status is a terminal one
   (`succeeded`, `failed`, `cancelled`, `timed_out`). A `queued` or `running` run without a result is
   open. A run has no `suspended` status: a run that waits for a person holds the result that asked
   the question, so it is closed while its sandbox sleeps, and `resume_run` is what opens it again
   (result cleared, status `running`).
2. **When the orchestrator stops waiting, it closes the row first**: a terminal status, the end date
   and the result the workflow is told, written together and committed before the executor is asked
   to cancel anything. Every way the wait ends does it:

   | The wait ends because | Status | `result.reason` |
   | :-- | :-- | :-- |
   | the stage budget is spent | `failed` | `timeout` |
   | the queue never admitted the run | `failed` | `queue_timeout` |
   | the executor no longer knows the run | `failed` | `lost` |
   | the pod ended and left no result | `failed` | `invalid_result`, or the executor's own state (`failed`, `cancelled`) |
   | the pod ended, its result is read from its journal | from that result | that result's |
   | the run is cancelled (`cancel_run`) | `cancelled` | `cancelled` |
   | its interpreter died while it waited for a slot | `failed` | `cancelled` |
   | the executor refused to start it | `failed` | the refusal's code |

   These reasons are the ones those paths already used; the contract does not change.
3. **Closing is replayable and never overwrites** ([ADR 0008](0008-idempotence-and-run-id.md)).
   The first result stays, the runner's or an earlier close's; a run that already ended keeps how and
   when it ended. When the runner's result got there first, the workflow is told that result: what an
   activity returns is what the row holds. The row is read under the lock an update takes
   (`FOR NO KEY UPDATE`), by the close and by `POST …/result`, so that a close and a result made at
   the same moment cannot both win.
4. **A cancel that fails is logged**, with the run it leaves behind (`run.cancel_failed`), and the
   activity does not raise for it. The run is closed already: what the cancel leaves is a process to
   remove, not an open run.
5. **The run's API refuses a closed run with one problem**: HTTP 409, `code: run_closed`, raised by
   one dependency (`OpenRunAuth`, and `OpenNetworkAuth` for the route that also takes the egress
   token) that every route of `/internal/runs/{id}` takes unless it is named in point 6. It comes
   after authentication: a caller without the run's token learns nothing of the run's state. A test
   reads the route table, so a route added later is guarded or named.
6. **Three routes still answer.** `POST …/result` gives its idempotent answer (`already_recorded`)
   and changes neither the run nor its work item. `POST …/events` and `POST …/network/decisions` keep
   accepting: they are append-only journals, idempotent by construction, and what a process did after
   its run ended is exactly what an operator wants to read.
7. **A closed run has no network.** `GET …/network` is refused; the egress proxy treats that refusal
   as it treats credentials it does not know (407), and remembers it for a few seconds instead of
   asking again at every request.
8. **The runner ends cleanly.** Its first calls are its egress token, then its input: refused with
   409 on either, it exits with 0, as it already did for a posted result.
9. **Nothing is added to the schema.** No column, no migration, no claim in the token.

## Alternatives rejected

- **A token epoch**: a counter in the token, checked against the run's row, bumped when the run is
  closed. It needs a column, a migration, and a new token at every resumption, to say what the row's
  status and result already say.
- **A deny-list of run tokens.** One more table read on every call of the run's API, and a queued
  run has had several tokens.
- **Answering a late result with an error.** A replayed post is the common, legitimate case, and the
  runner cannot tell the two apart; one idempotent answer serves both.
- **Closing the row after the cancel, or only when it succeeds.** That was the defect.
- **Refusing the journals too.** It would hide the one thing a late process leaves behind.
- **Closing the running run of an interpreter that died.** A restarted interpreter awaits that run
  again ([ADR 0008](0008-idempotence-and-run-id.md)); it is not abandoned, so it stays open.

## Consequences

- A closed run's token opens three routes, none of which changes the run or its work item.
- A row closed before this decision — a terminal status and no result — is closed too.
- What was minted before the close lives until its own expiry: the run's git token (one hour) and
  the answer the proxy cached (`cache_seconds`, one minute by default); a tunnel already open stays
  open until it closes. Closing the row does not reach them.
- A Temporal termination runs no code: the run of a terminated interpreter is not closed, as its Job
  and Secret were already not removed.
- A runner image older than this decision, refused its egress token, ends with an error instead of
  0. Nothing waits for that run.
- The 409 is not yet listed in the OpenAPI contract for the routes that did not answer one before;
  adding it is a `contract-change`.
