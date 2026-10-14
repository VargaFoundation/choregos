# ADR-0003 — Temporal for durable orchestration

- **Status**: accepted
- **Concerns**: S1, S9, S7

## Context

A ticket lives for hours or days: an agent works, a human approves, CI runs, a train
departs. It has to survive restarts, node failures and deployments of the platform itself —
without losing state or paying twice for the same run.

## Decision

Temporal, self-hosted (Helm + CloudNativePG), with the option of moving to Temporal Cloud
without changing a line of code. One workflow per ticket (`WorkflowInterpreter`), one per
environment (`ReleaseTrain`), one per project (`FindingsTriage`, `MemoryIngestion`), one
per provisioning.

Three disciplines make this safe:

- **decision logic is pure**: `choregos_core.WorkflowEngine` does no I/O, which makes it
  testable without Temporal and replayable without surprises;
- **activities are idempotent** by `(run_id | work_item_id, stage)`;
- **`workflow.patched()` is mandatory** for any change of logic, and CI replays archived
  histories (`tests/replay`).

`numHistoryShards: 512` is fixed from day one: it cannot be changed afterwards.

## Consequences

- A worker failure loses nothing and bills nothing twice.
- The operator sees the real state (workflow queries) rather than a reflection in a database.
- We accept one more component to operate, with its backups and upgrades.

## Alternatives discarded

- **A queue plus a home-made state store**: rewriting badly what Temporal does well.
- **Airflow / Argo Workflows**: built for processing DAGs, not for long processes that wait
  for humans.
