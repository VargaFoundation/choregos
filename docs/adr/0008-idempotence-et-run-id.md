# ADR-0008 — Deterministic identifiers and idempotence everywhere

- **Status**: accepted
- **Concerns**: S1, S2, S6

## Context

Temporal replays activities, webhooks arrive twice, a runner can die after doing its work
but before answering. Without discipline we create two workflows for one ticket, two runs
for one stage, and pay twice.

## Decision

Everything that can be replayed carries a **deterministic** identifier:

| Object | Identifier | Consequence |
| :-- | :-- | :-- |
| a ticket's workflow | `wi-<project>-<key>` | one ticket = one workflow, whatever happens |
| train | `train-<project>-<env>` | a single train per environment |
| a stage run | `<work_item>-<transition>-<attempt>` | replaying prepares the same run |
| webhook delivery | `(source, delivery_id)` | a redelivery triggers nothing |
| ACP journal | `(run_id, seq)` | a resent batch is not written twice |
| spend | `spend_collected` flag | cost is read once at the gateway |

Activities that write first check what already exists, and return the existing result
rather than creating a new one.

## Consequences

- A non-regression test goes with every path: "replaying doubles neither run nor cost".
- Idempotence wins over concision: an activity does a `SELECT` before its `INSERT`.

## Alternatives discarded

- **Deduplication after the fact**: finds the duplicates after paying for them.
