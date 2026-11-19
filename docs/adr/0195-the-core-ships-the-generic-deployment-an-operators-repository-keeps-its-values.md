# 0195 — The core ships the generic deployment; an operator's repository keeps its values

- **Status**: accepted, 2026-11-19 (decided by the product owner on 2026-11-19; implemented by S80-12 and S80-14 to S80-17)
- **Concerns**: `charts/choregos` and its subcharts (`temporal`, `litellm`, `ecphoria`, a new `websearch`),
  `docs/deployment.md`, and the first operator's deployment repository (DiametralGroup/choregos-deploy,
  a French-language repository outside this one)

## Context

The first operator's deployment repository grew a second platform beside the chart. It vendors the
chart (`chart/choregos`, copied from each release) and, next to it, about 3,000 lines of raw manifests
(`socle/`, `socle-prod/`) that deploy what the chart does not: a Temporal with TLS (a `start-dev` pod behind
an nginx relay in dev, `temporal/auto-setup` on PostgreSQL in prod, a cert-manager chain of
certificates), a LiteLLM whose model list carries prices, cache prices and tiers, a copy of the
guardrail and span modules the chart already ships, an Ecphoria, a SearXNG and a Firecrawl, and the relay
that gives SearXNG the organisation's outbound proxy (ADR 0091). Each is a Deployment, a Service and a
ConfigMap that any operator of Tranibor needs and would write again. Three costs follow:

- **Drift.** The copies are not the chart's: the dev LiteLLM loads the guardrail (ADR 0084), the prod one
  does not; a checksum script and a guard script in the deployment repository exist only to keep the
  copies honest; a fix to the chart's LiteLLM (a non-root context, a pinned digest) reaches no one who
  copied it.
- **Two shapes for one thing.** `values.yaml` knows `choregos-temporal` and `choregos-ecphoria`, which are
  only good for a bench, so every serious installation sets them aside and writes its own.
- **A reviewer reads manifests, not intent.** The deployment repository's own review is about hostnames
  and secrets; it should not also be about how to run Temporal over TLS.

## Decision

1. **The boundary.** This repository carries every component an operator needs to run Tranibor, as
   subcharts, templates and values with tests in `tests/charts/`, and the generic documentation
   (`docs/deployment.md`). An operator's deployment repository carries: its values files; the custom
   resources of its platform (a PostgreSQL operator's resource, a secret operator's `InfisicalSecret` or
   `ExternalSecret`, its ingress controller's resources); the content only it has (its plugins' catalogue,
   its theme, its playbooks, its tool catalogue); its CI for its own enterprise images; and its
   operating documentation.
2. **What moves here, and in which order** (one story, one PR each):
   - S80-14: **Temporal** gains two modes besides the bench's `start-dev`: `tls` (a cert-manager issuer
     chain, or an existing secret; for `start-dev`, which cannot serve TLS, an nginx relay in the pod) and
     `mode: server` (`temporal/auto-setup` on a PostgreSQL the chart embeds or the operator provides, TLS on
     the frontend and between services). Both keep an existing volume (`persistence.existingClaim`) and an
     existing Service name (`service.name`).
   - S80-15: **LiteLLM** gains what the operator's copy had: per-model `modelInfo` (cache prices, tiers),
     the database URL options of a PostgreSQL that requires TLS, the pod's security context, a digest-pinned
     image, a configuration checksum on the pod (the chart computes it; the operator's script goes away).
   - S80-16: **Ecphoria** and the other subcharts accept an existing volume and a Service name, so an
     installation that already holds data adopts the chart's component without moving it.
   - S80-17: a new optional **`websearch`** subchart: SearXNG, Firecrawl (search only) and its Redis, and
     the outbound-proxy relay of ADR 0091.
   - The operator's PostgreSQL stays its own: the chart already takes an external server
     (`global.database`), and a PostgreSQL operator's resource is the operator's platform.
3. **No data is recreated when an installation adopts a component.** A migration to the chart's component
   keeps the volume (`existingClaim`), the Service name and the database; the operator's resources for the
   volume stay in its repository, annotated so a prune never deletes them, until the data is moved on purpose.
   Temporal in particular holds live workflows: its database is never recreated by a deployment change.
4. **The chart is still vendored, but not by hand.** An operator's AppProject may admit only its own
   repository (here, a control of the platform's infrastructure repository), and the chart's registry is
   private. So the deployment repository keeps a copy of a published version, reviewed in a PR. The copy is
   made by a script from the release, and a CI step compares it to the published archive's digest; referencing
   the chart by version from an OCI registry becomes possible the day that AppProject admits one (nothing in
   this repository changes for it).
5. **What the operator's repository keeps** is stated in its own plan; this ADR only fixes what leaves it.

## Consequences

- The next release (0.27.16) carries S80-14 to S80-16; S80-17 follows. Each switch of an operator's
  environment (dev first, then prod, which an operator syncs by hand) waits for its release.
- `global.temporal.tls` and the chart's Temporal agree for the first time: ADR 0183 stated that no
  TLS-enabled Temporal existed on the bench; with S80-14 one does, and the chart test renders it.
- A deployment that turns the chart's LiteLLM on gets the secrets guardrail (ADR 0084) it did not have
  when it ran its own copy: a behaviour change an operator decides, noted in the upgrade notes.
- Nothing here changes a default: every new value is off or equals what the chart rendered before.
