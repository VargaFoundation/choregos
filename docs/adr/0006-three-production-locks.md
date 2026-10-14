# ADR-0006 — Three independent locks protect production

- **Status**: accepted
- **Concerns**: S9, S3, S7

## Context

Agents produce PRs at a pace no human review can follow. The risk is not that an agent
writes bad code — CI catches that — but that ten changes reach production at once, with
nobody able to tell which one broke what.

## Decision

Three locks, independent, that do not fall together:

1. **The merge queue** (GitHub): `main` stays integrated and green, one PR at a time.
2. **The release train** (Temporal, a singleton per project × environment): one deployment
   in flight, batches, a cadence, windows, a soak, an approval, a canary, a rollback, a
   freeze.
3. **Declarative guardrails**: Argo CD *sync windows*, a `GitHub Environment production`
   with required approvers, an Atlantis lock for Terraform.

The third lock exists for one precise reason: **if Choregos goes down, nobody deploys
outside the rules**. The platform is not the only rampart.

## Consequences

- A rollback freezes the train (`freeze_on_rollback`): we do not retry by reflex.
- A freeze requires a reason — a train frozen without one is a silent incident.
- A departure requested by a human overrides the window and the cron (it is a traced act),
  but **never** a freeze.

## Alternatives discarded

- **Continuous deployment on merge**: unmanageable at the pace of a fleet of agents.
- **Manual approval of every PR**: brings back the human bottleneck we are trying to avoid.
