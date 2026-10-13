# 0054 — A run keeps how its pod ended and the tail of its log, redacted

- **Status**: accepted, 2026-10-12
- **Concerns**: the orchestrator's execution activities, the `Executor` protocol, the `runs` table;
  builds on [ADR 0008](0008-idempotence-et-run-id.md) (idempotent activities)

## Context

A run leaves two traces: its ACP journal, batched by the runner to the API, and its result. Both
are written by the runner. A failure **before** the runner speaks ACP — an image that does not pull,
an agent CLI that crashes, an OOM kill — writes neither: the journal is empty, the result is a
generic "run failed", and the pod that knew why is deleted with its Job. `Executor.logs()` existed
on every executor and was called by nothing.

## Decision

1. **At the end of every run**, success or failure, the orchestrator archives a `RunLogArchive`
   in `runs.log_archive`: the executor's state, exit code, termination reason (`OOMKilled`,
   `Error`, `ErrImagePull`, `ImagePullBackOff`…), message, and the **last 200 lines** of the
   runner's log, each cut at 2,000 characters.
2. **Through the `Executor` protocol only** (`status`, `logs`). An executor that implements them —
   Tekton, Kubernetes Job, local Docker, ACA, the fake, a sandbox executor to come — is archived
   with no change in the orchestrator. `ExecStatus.reason` carries the runtime's own word for how
   the container ended; an executor that cannot tell leaves it empty.
3. **Before any cancel.** Deleting a Job deletes its pods and their logs: on a timeout, a queue
   timeout or a cancellation, the archive is taken first.
4. **Redacted.** Every line and the message pass through `choregos_core.redaction.redact`: known
   token shapes (those of the `no_secrets` gate), bearer and basic credentials, URL user-info, and
   the value of any `key=value` / `key: value` whose key names a secret. It prefers masking too
   much to too little; it complements a scanner, it is not one.
5. **Written once, never raises.** The first archive stays; a replayed activity finds it and does
   nothing (ADR 0008). Logs that cannot be read within 30 s, or at all, still give an archive whose
   `error` says why: a run must end even when its logs cannot be read.

## Consequences

- The run page can show why a pod died even when the journal is empty (S36-03 serves it).
- When the runner posts its result, the pod may still be running: the archive then holds the log
  up to that moment and the state `running`, not the final exit code.
- The archive lives in the database, not the object store: 200 short lines per run is small, and
  it follows the run's row-level security without a second access path.
- A secret printed in a shape the redactor does not know is archived as printed. The
  redactor is a floor, not a guarantee; the guarantee remains that no broad secret reaches a runner.

## Alternatives discarded

- **A new Temporal activity after `await_run`**: a workflow change, a patch id and replayed
  histories, for a step that belongs to "the run ended" and needs the same executor reference.
- **Ship pod logs to a log stack (Loki, Log Analytics)**: right for operations, but optional per
  deployment; the run page needs the reason without one.
- **Keep the whole log**: unbounded in size, and the tail is where a crash is.
