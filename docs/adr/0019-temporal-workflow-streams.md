# 0019 — Temporal Workflow Streams for the live journal

- **Status**: proposed, 2026-09-25 — not implemented; depends on the self-hosted server version
- **Concerns**: `apps/api/src/choregos_api/events.py`, the SSE routes, the runner's journal client

## Context

The live views (a run's journal, provisioning steps, the board) are fed by an `EventBus`
that P0-4 made real: `NOTIFY` inside the writing transaction on PostgreSQL, one `LISTEN`
connection per API replica, SSE to the browser, replay from `run_events` with `after_id`.
It works, and it was measured across replicas. It is also a second path: the runner writes
a journal row through the internal API, the API notifies, the replica fans out. The
orchestrator, which knows the stage's lifecycle, is not in that path at all.

Temporal announced **Workflow Streams** at Replay 2026: an ordered, durable stream attached
to a workflow execution, written by activities and read by clients with a cursor, surviving
worker restarts and replay. That is, feature for feature, what `run_events` plus the bus do
for a run — held by the system that already owns the run's lifecycle.

## Decision

1. **Wait for the self-hosted server.** Streams ship on Temporal Cloud first. This ADR takes
   effect when the version pinned in `charts/temporal` supports them; until then nothing
   changes, and the ADR exists so that the bus is not extended in the meantime.
2. **The stream is a transport, `run_events` stays the record.** Journal rows keep being
   written through the internal API (the evidence pack and the gates read them, ADR 0015 and
   0010). The stage activity attaches the same rows to the workflow's stream; the API's SSE
   route reads the stream with a cursor instead of the bus for the run's journal.
3. **Provisioning and board events stay on the bus.** They belong to no single workflow
   execution's lifetime the way a run's journal does; the bus is the right tool there.
4. **Replay must stay silent.** The replay suite (`tests/replay/histories`) rejects any
   history whose stream writes are not idempotent on `(run_id, sequence)`.

## Conditions

- One more thing the operator's Temporal must be new enough for; the chart's `garde.yaml`
  refuses the stream route on an older server rather than serving an empty log.
- A stream's retention is the workflow's retention. Long-lived evidence needs the table.

## Consequences

- The run's live log is ordered by the system that orders the run, with no gap between
  "the stage ended" and "the last line arrived".
- One fewer moving part in the API for the hottest view, and none added elsewhere.

## Alternatives discarded

- **Replacing the bus entirely**: the bus also carries events no workflow owns. Removing it
  would move provisioning and board updates into a workflow they do not belong to.
- **Reading the journal by polling the internal API from the browser**: it was the state
  before P0-4 and it hid the bus being mute; the fix was measured, not reverted.
