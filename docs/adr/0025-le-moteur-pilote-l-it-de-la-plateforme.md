# 0025 — The engine drives platform IT

- **Status**: accepted, 2026-09-26 — phase 1 not implemented; this record fixes the shape and, above all, the order
- **Concerns**: a new project kind, the runner's command guardrail, the executor, the workflow template and playbook seams

## Context

Diametral's platform already watches itself, and watches itself well. `infra/ops/healthcheck`
runs a weekly Tekton pipeline with nineteen collectors — OS packages, systemd, disk, clock, MAAS,
Ceph, ArgoCD drift, k3s, k3s certificates, GitOps, SSO, observability, the LLM gateway, apps,
ingress, client IP, load-balancer exposure, MSS clamping, NetBird relays. The last report counted
**343 observations and 102 findings**. `triage.py` groups findings into a handful of actions —
"eight hosts need a reboot" is one piece of work, not eight — buckets them `prio` / `a-faire` /
`confort` through the platform LLM gateway, and falls back to a mechanical grouping that *says so*
when the gateway is unreachable. `publish_issues.py` then writes idempotent GitHub issues keyed by
the **nature** of the problem, and closes an issue when its key stops being produced.

That is a better watch than most platforms have. Its own header states the gap:

> This report **states and proves; it does not prioritise** — ranking is the next step.

So the missing piece is not observation, and building another one would be waste. The missing
piece is **turning a finding into a change that is planned, approved, applied and proven** — which
is the whole of what Choregos does. A healthcheck finding is a work item: it arrives as a request,
it is instructed in steps, and there is a mechanical way to know it is fixed.

Two facts make the fit tighter than it first appears. The findings live in a **git repository**, so
every diff-reading gate applies unchanged — scope, size, secrets. And the way to prove a fix is to
**re-run the collector that produced the finding** and require its key to be absent: evidence that
is measured, not narrated, which is the point of [ADR 0010](0010-les-gates-sont-des-mecanismes.md).

## Decision

**A Choregos project named `plateforme` takes the healthcheck's issues as work items and carries
them to a proven fix. The agent applies the change, behind a human gate — and the three classes of
change arrive in a fixed order, because that order is the safety.**

### The wiring

| Piece | Choice |
| :-- | :-- |
| Tracker | `github-issues` on the infra repository, filtered on the healthcheck label — the issues already exist, keyed and idempotent |
| SCM | the same repository, so `scope_respected`, `diff_size_max` and `no_secrets` have real matter |
| Workflow | a template `maintenance-plateforme`, brought by the deployment through `CHOREGOS_WORKFLOW_TEMPLATES_DIR` |
| Roles | `analyse_infra`, `plan_infra`, `applique_infra`, `verifie_infra`, brought through `CHOREGOS_PLAYBOOKS_DIR` |
| Closing evidence | the collector is re-run; `evidence_facts` requires the finding's key to be **absent**. No new gate: [ADR 0012](0012-le-moteur-n-est-pas-lie-au-logiciel.md) already opened named business facts |
| Closing the issue | nothing closes it by hand. The next healthcheck run stops producing the key, and `publish_issues.py` closes it with a comment saying what stopped being observed |

### Three classes, three levels of authority, in this order

**Phase 1 — GitOps, no new capability.** Changes under `platform/*` and `gitops/*`: chart versions,
image tags, values, CRDs. The agent opens a **pull request on infra**. The gate is the CI that
already exists — the AppProject linters AP1–AP10, the ApplicationSet linters AS1–AS12, the
`generate_projects.py --write` check that refuses a registry drifting from `projects/<t>.yaml`. A
human merges. Argo CD applies. *Choregos adds no power it does not already have*, which is exactly
why this phase comes first: it proves the state machine, the plan, the human gate and the
mechanical evidence on real tickets, at the cost of a revert if it is wrong.

**Phase 2 — the cluster, without touching a node.** Helm values that change a workload, a CRD
upgrade, a rollout restart. Same pull-request path, plus the release train for anything that
reaches prod: prod stays reachable only `via: release_train`, which the DSL validator already
enforces (`prod.requires_train`).

**Phase 3 — the nodes.** `apt`, kernels, reboots, k3s, Ceph. This needs **SSH to the machines,
which nothing in the platform has today**, and it is where a mistake costs a cluster rather than a
revert. It is therefore last, and it carries its own conditions, all of them refusals:

- a dedicated executor with the Ansible inventory, running as a Job in a platform namespace — never
  the tenant's runner;
- an **allowlist of playbooks**, not a blocklist of commands. `guardrails.py::check_command` is a
  blocklist today; for this domain that is backwards, and turning it into an allowlist is the
  prerequisite of the phase, not a detail of it;
- **one node at a time**, with a fact between each: `nodes_ready`, `ceph_quorum`, `pdb_respected`.
  `evidence_facts` refuses on a missing or false fact, so the run stops where it stands;
- a **refusal to start** when the relevant collector (`20-k3s`, `14-ceph`, `22-k3s-certs`) is not
  already clean. Upgrading on top of an existing fault is how a cluster is lost;
- a maintenance window, expressed as the train's sync window rather than as a prompt.

## Consequences

- **The riskiest capability arrives last**, and only after the harmless path has been exercised on
  real tickets. Nothing in phase 1 or 2 can reach a node.
- `check_command` must gain an allowlist mode. That is a real change to the runner, and it belongs
  to phase 3 — writing it earlier would give the agent nothing to use it on.
- **The healthcheck is not reimplemented, and must not be.** If a finding is missing, the fix is a
  collector, in infra. Choregos never observes the fleet; it acts on what infra observed. Two
  watches would disagree, and the disagreement would be discovered during an incident.
- Model calls go through the platform gateway with a virtual key per run, so **the cost of
  maintaining the platform becomes a number per ticket**. Nobody has that number today.
- The `plateforme` project is a tenant of Choregos like any other, with its own policy: tight
  budgets, `sandbox.deny_commands`, and — in phase 3 — the narrowest allowlist of the installation.
- A failure mode to accept: the healthcheck runs weekly. A fix applied on Tuesday is only *proven
  closed* at the next run. The workflow therefore proves it itself by re-running the one collector,
  and lets the weekly run be the second opinion.

## Alternatives discarded

- **Kargo.** Already assessed in `infra/docs/KARGO.md` and declined in favour of rendered manifests
  with per-environment ledgers. It promotes versions; it does not read a fleet finding, does not
  order a node-by-node upgrade, and does not prove a finding is gone.
- **Renovate or Dependabot alone.** They propose a bump. They do not know that eight hosts are one
  reboot, cannot refuse to start on a degraded Ceph, and produce no evidence of closure.
- **A Tekton pipeline per maintenance task.** That is what exists for the healthcheck, and it is
  right for *observing*. It has no state machine, no human gate carrying a reason, no cost ledger,
  and no evidence contract — the four things this decision is about.
- **Letting the agent touch nodes from the start.** Refused. The value of phases 1 and 2 is not
  that they are useful first; it is that they make phase 3 defensible.
- **A new `collector_clean` gate.** Tempting, and unnecessary: the runner writes the collector's
  verdict as a named fact and `evidence_facts` judges it. One mechanism fewer to maintain.
