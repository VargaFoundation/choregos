# 0184 — A supported single-node installation: one k3s machine, a nightly backup, no high availability

- **Status**: accepted, 2026-11-17; S80-07b delivers the profile, the backup and restore, the chart
  tests and the proof script; S80-07c (2026-11-18) confines the agent pods; S80-07d (2026-11-18)
  runs the proof end to end once; it does not run nightly yet (see "What is not done"); amended by
  S80-07f (2026-11-18): the egress gate, every agent pod waits for its egress policy (below)
- **Concerns**: `charts/choregos/values/single-node.yaml`, `charts/choregos/templates/backup.yaml`,
  `charts/choregos/templates/guard.yaml` (`singleNode.enabled`), `charts/choregos/templates/networkpolicies.yaml`, `tools/single-node/`,
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
   - **Agents run as Jobs, confined by the chart's NetworkPolicies and the egress proxy**
     (S80-07c). On a bare k3s there is no agent-sandbox operator: this profile departs from
     ADR 0072 (the agent-sandbox runtime of a real deployment) and runs agent stages with the
     `k8s_job` executor, in the release namespace, beside the embedded dependencies. The chart's
     NetworkPolicies name each dependency by its namespace on a cluster and, embedded, by its pods
     (`app.kubernetes.io/name: choregos-postgresql`, ...). An agent pod (label
     `app.kubernetes.io/name: choregos-runner`, set by the executor, which the agent cannot change)
     reaches DNS, the API, the model gateway, the memory and the per-run egress proxy
     (`global.egress.enabled`, ADR 0070), and nothing else: not PostgreSQL, not Temporal, not the
     outside but through the proxy, which lets a run reach only the hosts its organisation opened.
     PostgreSQL and Temporal also admit connections only from the platform's pods that use them, by
     name: a second wall on the store itself. The platform's own pods go out freely
     (`networkPolicies.platformEgress`): on one machine no platform team opens the OIDC provider,
     the model provider, the Kubernetes API or the backup bucket for them. The guard refuses the
     profile with `networkPolicies.enabled` or `global.egress.enabled` off. k3s enforces
     NetworkPolicies with its embedded kube-router; a k3s started with `--disable-network-policy`
     enforces none, and the isolation is then gone: the proof checks it on the machine.
     What the walls do not cover: the model gateway and the memory are reachable by design (per-run
     keys; LiteLLM's administration routes on the same port need the master key, which no agent pod
     holds); the egress proxy never reaches a private address, so a git host or a tracker on the
     site's LAN is out of an agent's reach.
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
- The proof ran end to end once, on 2026-11-18 (S80-07d, k3d on a workstation, 925 s), after the
  defects it found were fixed (the memory's image, the migrations' temporary directory, the backup
  and restore connecting before the NetworkPolicy controller admitted them). Its isolation step held
  on a running k3s: from a pod with an agent's labels the API, the gateway, the memory and the proxy
  open, PostgreSQL and Temporal do not.
- k3s's NetworkPolicy controller (kube-router) applies a pod's rules only once it has seen the pod:
  a new pod is admitted to a store's ingress about 3 s after it starts (the backup and restore wait
  for PostgreSQL for this reason), and, measured in the same run, a new agent pod reaches the
  Internet directly for about 2 s before its egress rules apply. PostgreSQL and Temporal stay closed
  in that window (their own ingress policies admit their clients by name). Closing it needs either a
  policy engine that programs a pod before it runs, or an agent that cannot act in its first
  seconds. S80-07f does the second: see "Amendment: the egress gate".

## Amendment: the egress gate (S80-07f, 2026-11-18)

**The measurements** (issue #1854). kube-router, the NetworkPolicy engine k3s embeds, programs a pod
by IP after the fact: a pod is "actionable" only once `status.podIP` is set, which the kubelet
publishes after the containers have started; every event only asks for a full sync (one at a time,
`iptables-save`, rebuild, `iptables-restore`); a packet from a pod that has no chain yet falls through
to flannel's `-s 10.42.0.0/16 -j ACCEPT`. A race in the sync (target pods listed before pods to chain,
both from the live cache) can also give a pod a first chain whose egress is the default allow. A probe
looping on `nc -z -w1 1.1.1.1 443` from a container's first instruction, with a namespace-wide
default-deny egress created before the pods:

| Cluster | Starts | Direct egress |
| :-- | :-- | :-- |
| k3d, k3s v1.35.5 (kube-router + flannel) | 15 | open from +0.00 s in 15/15; windows 1.15, 1.29, 1.62, 3.79, 3.91, 3.96, 4.03, 4.08, 4.15, 4.48, 4.53, 4.61, 4.70, 4.85, 5.16 s (median 4.08 s); one full sync 2.2-2.9 s |
| Cilium 1.16.4, runc | 10 | refused from the first attempt, 10/10 |
| Cilium 1.16.4, gVisor | 10 | refused from the first attempt, 10/10 |

A pre-existing default-deny changes nothing on k3s: it only decides where a chain that does not exist
yet would lead. Cilium has no window because its CNI plugin builds the endpoint synchronously (labels
resolved, first regeneration awaited) before the kubelet starts a container. A failed
`iptables-restore` aborts kube-router's sync: the window then lasts until the next event, up to the
5-minute periodic sync. And a hosted agent (ADR 0080) is a native sidecar that starts BEFORE the
runner and runs its author's code from its first instant.

**The decision.** Every pod that runs agent code has, as its FIRST init container, a gate
(`choregos_core.egress_gate`, from the runner's image): it tries a direct TCP connection to a
destination the runner pods' policy forbids (`1.1.1.1:443` by default), and exits 0 only after three
attempts in a row did not connect; an attempt that connects starts the count again; still open after
30 s, it exits 115 (`EGRESS_OPEN`) and the pod fails before anything of the agent starts. It is placed
by the `k8s_job` executor (before a hosted agent's sidecar) and by the chart's `agent_sandbox` template
(before the ADR 0146 agent container), from one definition the chart test compares; the runner
journals what it measured (`egress.gate`). Setting: `choregos-orchestrator.runner.egressGate`
(`enabled`, `target`, `deadlineSeconds`), read by the executor workers as `CHOREGOS_RUNNER_EGRESS_GATE*`,
the deployment's and never a connector's.

**On the single-node profile** it is on and the guard refuses it off: kube-router is the profile's
engine. **On a multi-node cluster it is on by default too.** Under Cilium it costs three connection
timeouts per pod (about 3.5 s; nothing for a warm sandbox, which waits before it is claimed) and protects
against nothing Cilium does not already stop; but the chart cannot see which engine a cluster runs, and
the gate is what turns a cluster that enforces nothing (k3s with `--disable-network-policy`, a CNI
without NetworkPolicy support, a broken engine) into refused runs rather than unfenced agents. The Cilium
cluster the measurements above were taken on already refuses `1.1.1.1:443` to runner pods (its
namespace baseline admits only the namespace's pods, DNS and named addresses): there the default costs
seconds, not runs. A cluster where a runner pod can reach the outside directly fails every run with 115
after the upgrade, which is the finding, said in "Upgrading past 0.27.13: the egress gate". Turning it
off is for a bench without NetworkPolicies (`values/local.yaml`) or a cluster that has measured its
engine.

**What it does not cover.** It proves one destination is closed, not every one: under kube-router a
pod's chain is all-or-nothing, so one closed destination proves the chain is in place, and under
Cilium the endpoint is programmed before the first container; for an engine that programs rules
destination by destination it proves less. A target that is down, or a node with no route out (an
air-gapped site), reads as closed: the gate then passes having proved only that nothing leaves directly.
A policy that regresses after the gate passed (a later sync that fails, an IP reused) is not watched.
The Tekton executor's pods, ACA and local Docker are not gated. The structural fixes stay open: a CNI
that enforces from the first packet on the single node (k3s with Cilium, measured on a cluster only),
two upstream fixes to kube-router (one snapshot of the pods per sync; a deny default for a local pod IP
not yet programmed, kube-router#873), and the agent in a network namespace with only `lo` (ADR 0146's
direction).

**The proof.** `tools/single-node/proof.sh` starts, on k3s, a Job built by the `k8s_job` executor in the
executor worker with the deployment's settings, its runner container replaced by a loop that tries
`1.1.1.1:443` from its first instruction for 10 s: its first attempt must be refused and none may
connect. The same pod without the gate runs beside it as a control.

## Alternatives considered

- **Harden `docker compose` into the product.** The plan costs it at 25 to 40 person-days: a second
  installation to keep in step with the chart, its own upgrade and backup story. Refused: one chart.
- **A backup operator (CloudNativePG, Velero).** Right for a cluster, heavy for one machine: two
  operators to install and upgrade before the platform, and Temporal's SQLite file still outside
  both. Refused for this profile; still the answer for a multi-node installation.
