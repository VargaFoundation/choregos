# ADR-0009 — The cluster changes only through Git

- **Status**: accepted
- **Concerns**: S7, S8, S9

## Context

The platform creates namespaces, quotas, network policies and applications for every
provisioned project. Giving it the rights to do so directly means giving `cluster-admin` to
a service that executes agents' code.

## Decision

No Choregos component applies a manifest. Provisioning **writes to Git**
(`choregos-infra/projects/<slug>/`); an Argo CD `ApplicationSet` synchronises. Promoting a
release is a **PR** on the project's GitOps repository, never a `kubectl set image`.

The API only has read access to `PipelineRun`s; the orchestrator only has rights in the
`proj-*-runners` namespaces, through `Role`s generated project by project.

## Consequences

- Every cluster change is auditable, reviewable and reversible (`prune`).
- Deleting a project = deleting a directory.
- Provisioning is slower than a direct call: that is the price of traceability.

## Alternatives discarded

- **Direct Kubernetes API from the orchestrator**: fast, but gives the platform rights that
  no review can frame any more.
