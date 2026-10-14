# 0027 — Noisy neighbours are handled at admission and by quota, not by a Temporal queue per organisation

- **Status**: accepted, 2026-09-29
- **Concerns**: `choregos_core.admission`, `choregos_core.quotas`, `gitops.render_project_manifests`, the Temporal task queues

## Context

The enterprise plan listed three controls against one organisation starving the others: a Temporal
task queue per organisation (there are four shared queues), a partition of the PostgreSQL pool, and a
`ResourceQuota` derived from the organisation instead of fixed at 32 CPU / 96 Gi.

What actually consumes resources is a **run**: an agent pod, its model calls, its minutes. Workers only
orchestrate: they schedule short activities and wait. A busy organisation fills the executor with pods
and the gateway with tokens long before it fills a Temporal queue.

## Decision

1. **Runs are bounded per organisation at admission** (`choregos_core.admission`, since 0.9): a
   monthly euro ceiling and a concurrent-runs ceiling, refused before any gateway key is minted. The
   enterprise edition sets them per organisation.
2. **The namespace quota is set per organisation** (`choregos_core.quotas`, since 0.12): a plugin
   returns the `ResourceQuota` of a project's namespaces from its organisation; without one, the
   historical default applies. It is written to the GitOps repository and takes effect at the next
   provisioning of the project.
3. **No Temporal task queue per organisation, no PostgreSQL pool partition.** A queue per organisation
   needs a worker per organisation — N deployments to scale for work that is not the bottleneck. A pool
   partition means nothing without per-organisation processes. Both would add operational weight to
   protect the part of the system that is not contended.

## Consequences

- An organisation cannot exceed its concurrent runs, its monthly budget, or its namespace quota; the
  first two are enforced before anything is spent.
- If orchestration itself ever becomes contended — measured, not assumed — the answer is Temporal's
  task-queue priority and fairness keys on the existing queues, not new queues. This record is the
  place to reopen.
