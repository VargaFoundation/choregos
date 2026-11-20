# 0197 — The chart deploys every solution the platform requires, in one install

- **Status**: accepted, 2026-11-20 (S80-18)
- **Concerns**: `charts/choregos` (two new subcharts, `values/all-in-one.yaml`), `docs/deployment.md`
- **Builds on**: ADR 0184 (single node), ADR 0195 (the core ships the generic deployment)

## Context

`docs/deployment.md` lists what a cluster must bring: an OIDC provider, an S3-compatible object store, the
agent-sandbox operator, External Secrets Operator, and optionally Kyverno. The chart embedded PostgreSQL,
Temporal, LiteLLM and Ecphoria (ADR 0184, 0195), but not the identity provider or the object store, and it
generated the platform's own Secrets only under `global.devSecrets`, a bench value that `staging` and `prod`
refuse. A cluster with nothing but Kubernetes could therefore not get a working platform from one `helm install`.

## Decision

1. **One profile, individual switches.** `values/all-in-one.yaml` is the entry point. A Helm condition reads
   values, not templates, so no single flag can embed things; the profile sets one switch per piece
   (`global.oidc.embedded`, `global.objectStore.embedded`, plus the existing four) and `global.allInOne.enabled`
   marks the mode. Every piece stays individually overridable: a customer sets its `embedded` to `false` and names its
   own issuer, endpoint or database. Nothing is embedded by default, and a render without the mode is unchanged
   (the API pod's configuration checksum leaves the new keys out while they are off, so no existing install rolls).
2. **Object store: RustFS**, the S3 server `dev/compose.yaml` already runs (MinIO's server image is no longer
   published). One replica, one volume, a hook Job that makes the bucket with `rclone mkdir` (idempotent).
3. **OIDC: Keycloak**, whose realm layout the platform's documentation and development stack already use: a
   confidential console client with the redirect URI the API registers, a public CLI client with the device grant,
   a group mapper emitting `choregos:<org>:<role>`, the five role groups of the bootstrap organisation, and one
   administrator. Secrets are `${ENV}` substitutions of the realm import, never written into the ConfigMap. The realm
   is imported once; that limit is documented, not worked around.
4. **Secrets: drawn by the chart, re-read on upgrade** (`lookup`), as the gateway, memory and PostgreSQL Secrets
   already are. `global.allInOne.enabled` extends the API Secret's generation, until now `devSecrets`-only; the
   OIDC and object-store Secrets have their own `generateSecret` switches so a site that provides them is not
   overwritten. External Secrets is refused on in the mode.
5. **The agent-sandbox operator is not vendored.** Its CRDs are cluster-scoped and installing them is outside what a
   namespaced release may do; its manifest is not in this repository (`infra/operators/agent-sandbox` is a pinned remote
   reference). The profile falls back to `k8s_job`, as ADR 0184 does, and `NOTES.txt` states it. Operator install stays
   a documented, separate step.
6. **Confinement.** The mode takes the single-node class's conditions (backup, NetworkPolicies, egress proxy, egress
   gate), because it embeds the same one-node-shaped data stores. The object store and Keycloak are walled by the same
   NetworkPolicy machinery; runners reach neither.
7. **Images are pinned by tag and digest** (`image.tag` and `image.digest` in each subchart), resolved from the
   registries on 2026-11-20.

## Consequences

- A bare cluster with a default StorageClass and a CNI that enforces NetworkPolicies installs with one command.
- The mode is not production-grade: single replicas, no HA, the backup on the cluster's own volume by default.
- Only the render is proven. Starting Keycloak with the realm, RustFS with the keys, and the API's sign-in through
  the embedded realm are not proven without a cluster.
- Ratchets: no crossing import, router-private import, organisation lookup or API path changed.
