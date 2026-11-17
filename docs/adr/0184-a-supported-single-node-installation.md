# 0184 — A supported single-node installation: one k3s machine, a nightly backup, no high availability

- **Status**: accepted, 2026-11-17; S80-07b delivers the profile, the backup and restore, the chart
  tests and the proof script; the proof does not run nightly yet (see "What is not done")
- **Concerns**: `charts/choregos/values/single-node.yaml`, `charts/choregos/templates/backup.yaml`,
  `charts/choregos/templates/guard.yaml` (`singleNode.enabled`), `tools/single-node/`,
  `docs/runbooks/single-node-restore.md`; builds on [ADR 0072](0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md)
  (which it departs from on one point, below) and on the embedded dependencies of the chart

## Context

The platform's only supported installation was a Kubernetes cluster with its dependencies run
outside the chart: a PostgreSQL operator, a Temporal cluster, a shared gateway. The chart could
embed all four dependencies, but only for a bench: `templates/guard.yaml` refused them in `staging`
and `prod`, because an embedded PostgreSQL had no backup and an embedded Temporal is one process on
SQLite. A small organisation that wants the platform on one machine had nothing it could be
supported on; the plan of 2026-11-16 (workstream P4) asks for exactly that, the cheapest way:
the existing chart on one k3s node, with a backup.

## Decision

1. **One profile, the existing chart.** `values/single-node.yaml` installs the chart on one k3s
   machine: every component at one replica, no autoscaler, no disruption budget, no anti-affinity,
   volumes on k3s's `local-path`, Traefik ingresses. No second chart, no compose file hardened into
   a product.
2. **What is embedded, and what the site brings.** Embedded and backed up by the chart: PostgreSQL
   (with pgvector), Temporal (`start-dev`, one process on SQLite), the LiteLLM gateway, the Ecphoria
   memory. Brought by the site: the OIDC provider, the object store (only workflows that produce
   files need one), the model provider's key, the application secrets (`apiSecretRef`, whose
   `secrets-keks` seals the connector secrets) and an off-machine place for the backups.
3. **The machine.** 8 vCPU, 32 GiB of memory, 250 GB of SSD. The platform requests 2 CPU and 6 GiB;
   four agent runs at once (`runner.maxActive: 4`) take at most 2 CPU and 4 GiB each.
   `tests/charts/test_single_node_profile.py` holds the sum.
4. **The guard admits it, with its backup only.** `singleNode.enabled: true` lets the embedded
   dependencies render in `staging` and `prod`, and fails the render without `backup.enabled: true`.
   The development secrets and sign-in and the demonstration fakes stay refused in every profile.
5. **The backup is the chart's.** A CronJob (`<release>-backup`, every night at 02:00 UTC by
   default) writes one directory per run: `pg_dump -Fc --create` of every database of the embedded
   PostgreSQL, the roles without their passwords, an online SQLite copy of Temporal, the memory's
   files, optionally the object store, a `MANIFEST` and a `COMPLETE` marker; it ships the directory to
   an S3 bucket (`backup.target.kind: s3`, the profile's default) or a volume, and keeps the `keep`
   (7) newest complete backups. A suspended CronJob (`<release>-restore`) is the restore's template;
   `tools/single-node/restore.sh` stops the writers, runs it, and starts them again.
6. **What the customer signs for.**
   - **No high availability.** The machine is a single point of failure: a lost disk, kernel or
     power supply stops the platform until it is restored. A k3s upgrade or a reboot is a downtime.
   - **A recovery point of 24 hours (RPO).** What happened since the last nightly backup is lost on
     a restore: work items created, decisions taken, runs. The schedule can be tightened; the RPO is
     then the interval.
   - **A recovery time of the order of an hour (RTO)**: a new machine, k3s, the chart, the restore.
     It is the proof's own sequence; nothing measured it on a customer's machine yet.
   - **The site keeps its secrets.** The backup holds no application secret, on purpose (a backup
     that carries the key that seals its own secrets protects nothing): without the same
     `secrets-keks` and run-token key, a restore opens no sealed connector secret.
   - **Agents run as Jobs, without the per-project egress proxy and without the chart's network
     policies.** On a bare k3s there is no agent-sandbox operator: this profile departs from
     ADR 0072 (the agent-sandbox runtime of a real deployment) and runs agent stages with the
     `k8s_job` executor, in the release namespace. The chart's NetworkPolicies open the dependencies in
     their own namespaces (`choregos-data`, `choregos-temporal`, ...) and would cut the embedded ones,
     so they are off. An agent pod can therefore reach the embedded PostgreSQL on the network; its
     credentials are not in the pod. A site that runs agents on untrusted inputs is told so before it signs.
7. **The proof.** `tools/single-node/proof.sh` plays, from a blank node: k3s, the chart, one work
   item to `approved` (its agent simulated, the rest of the path real), the backup, the node
   destroyed, a new node, the chart, the restore, and the work item read back `approved`. It runs in
   k3s-in-Docker (k3d) on a workstation or CI, and on a throwaway VM with `PROOF_CLUSTER=k3s`.

## Consequences

- A single-node installation is supported, with its limits written here and in the deployment
  guide; nothing about the multi-node installation changes (`singleNode` and `backup` are off by
  default and the guard is as strict as before without them).
- The backup pod mounts the volumes of Temporal and the memory beside their owners. A ReadWriteOnce
  volume allows it on one node and nowhere else: the backup is for this profile, not a general
  backup of the chart's embedded dependencies on a cluster.
- The memory's DuckDB files are copied as files, consistent between two checkpoints of Ecphoria, not
  at an instant: a backup taken during a write may restore the memory a few writes behind. The
  database and Temporal copies are consistent.

## What is not done

- The proof does not run nightly yet: no CI job carries it (it needs a runner with Docker and about
  twenty minutes). Until one does, the guarantee is only as fresh as the last manual run, recorded
  in STATUS.
- No embedded object store and no embedded OIDC provider: a site without either brings one.
- The network isolation of agent pods on this profile (NetworkPolicies for the embedded layout, the
  egress proxy) is a follow-up.

## Alternatives considered

- **Harden `docker compose` into the product.** The plan costs it at 25 to 40 person-days: a second
  installation to keep in step with the chart, its own upgrade and backup story. Refused: one chart.
- **A backup operator (CloudNativePG, Velero).** Right for a cluster, heavy for one machine: two
  operators to install and upgrade before the platform, and Temporal's SQLite file still outside
  both. Refused for this profile; still the answer for a multi-node installation.
