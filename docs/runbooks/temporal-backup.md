# Temporal: what to back up, and what a loss costs

## What lives in Temporal

Every ticket in flight is **one Temporal workflow** (`WorkflowInterpreter`), and so is every
provisioning and every release train. Temporal holds their *histories*: which stage ran,
what it returned, where the workflow is waiting. The platform database holds the
*business state* (tickets, runs, evidence, ledger, events) and is backed up separately
(`restauration-postgres.md`).

Losing Temporal therefore loses **the position of every ticket in flight**, not the
tickets themselves. A ticket whose workflow is gone stays in its current state forever,
with no event and no failure — the API reads the workflow status on every work item
(`workflow_status`) and shows `NOT_FOUND`, but nothing restarts it by itself.

## Embedded Temporal (`global.temporal.embedded: true`)

The embedded Temporal is `temporal server start-dev`: one process, **SQLite** on a
PersistentVolume (`/data/temporal.db`), one replica, `Recreate` strategy. It exists for a
bench. The chart refuses it in `staging` and `prod` (`templates/garde.yaml`).

If you still need a copy of it — to move a bench, or to keep a history for a replay test:

```bash
NS=choregos
POD=$(kubectl -n $NS get pod -l app.kubernetes.io/name=choregos-temporal -o jsonpath='{.items[0].metadata.name}')
# 1. Quiesce: no worker, no new workflow. Workers resume where they stopped.
kubectl -n $NS scale deploy -l app.kubernetes.io/component=orchestrator --replicas=0
# 2. Copy the file. SQLite in WAL mode: take the -wal and -shm files with it, or the copy is
#    a point in the past.
kubectl -n $NS exec $POD -- sh -c 'cd /data && tar cf - temporal.db temporal.db-wal temporal.db-shm 2>/dev/null' > temporal-$(date -u +%Y%m%dT%H%M%SZ).tar
# 3. Bring the workers back.
kubectl -n $NS scale deploy -l app.kubernetes.io/component=orchestrator --replicas=1
```

Restore: scale the Temporal deployment to zero, untar into the volume (a throwaway pod
mounting the same PVC), scale back up. The workflow ids are deterministic
(`wi-<project>-<key>`), so the API finds them again without any table to fix.

## External Temporal (production)

Temporal's state is its **persistence database** — PostgreSQL or Cassandra, provisioned by
whoever runs Temporal. Back that database up with the same tool and the same RPO as the
platform database; the `restauration-postgres.md` procedure applies to a CloudNativePG
cluster hosting `temporal` and `temporal_visibility`.

Two rules that are not obvious:

- **`numHistoryShards` never changes** after the first start. A restore into a cluster
  configured with another value is a corrupt cluster (`montee-temporal.md`).
- **Restore Temporal and the platform database to the same instant.** A Temporal older than
  the platform database replays stages whose runs already finished — idempotent by run id
  (ADR 0008), so no double spend, but the tickets go back to an earlier state. A Temporal
  newer than the database points at runs the database no longer knows.

## After any restore: reconcile what is in flight

```bash
temporal workflow list --query 'ExecutionStatus="Running"' --address <frontend>:7233
```

For every work item that is not terminal, compare with what the API reports:

```bash
choregos items list <project>        # state and workflow_status per ticket
```

- `workflow_status: NOT_FOUND` on a non-terminal ticket: the workflow was lost. Restart it
  from the ticket (`POST /work-items/{id}/start` restarts a ticket whose workflow is
  absent; a running one is left alone).
- `FAILED`, `TERMINATED`, `TIMED_OUT`: the failure is on the ticket (`failure`) and in the
  events (`workflow.failed`); the board shows it. Decide, per ticket, to restart or close.

## Check it is fixed

- `/readyz` on the API answers `ok` (it checks Temporal).
- `choregos_workflows_failed_total` stops growing; the `ChoregosWorkflowFailed` alert clears.
- A new ticket starts and reaches its first agent state.
