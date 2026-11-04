# Losing a node (spot eviction, hardware failure)

## How to know it is this

A node goes `NotReady` and does not come back. The pods it carried stay displayed `Running`
for several minutes — not a display bug: **Kubernetes does not evict them right away**, and
until it does, the work restarts nowhere.

```bash
kubectl get nodes
kubectl get pods -A -o wide --field-selector spec.nodeName=<node>
```

## The three instants that matter

Measured on a cluster (`tests/cluster/test_node_loss.py`), node stopped abruptly:

| | Kubernetes defaults | With `unreachableTolerationSeconds: 20` |
| :-- | --: | --: |
| Node lost → `NotReady` | 52–57 s | same (that is the `node-monitor-grace-period`) |
| Node lost → worker restarted elsewhere | **354–359 s** | **74 s** |

The five minutes in between are a pod's default toleration of
`node.kubernetes.io/unreachable`. It is meant for workloads whose replacement is expensive
to start — a Choregos worker is not one of them: it is interchangeable and Temporal resumes
its work where it stopped.

The chart therefore sets `choregos-orchestrator.unreachableTolerationSeconds: 20`. To get
Kubernetes' standard behaviour back: `0`. Since 2026-09-25 the replicas of the API, the
front and each worker queue also repel each other across nodes (`global.antiAffinity`), so
one lost node does not take a whole component down.

## During the outage

1. **Check the API is not affected.** It has `topologySpreadConstraints`: if everything
   fell, the problem is wider than one node.
   ```bash
   kubectl -n choregos-system get pods -l app.kubernetes.io/component=api -o wide
   ```
2. **Force nothing during the first 20 seconds.** A node that comes back (network restart,
   kubelet relaunched) picks its pods up again, and a `delete --force` would have created
   duplicates for nothing.
3. **After the eviction**, check that the runs the node carried resumed:
   ```bash
   kubectl -n choregos-system logs -l choregos/queue=executor --tail=50 | grep -i resume
   ```
   Temporal replays the activity; `start` being idempotent, the job already launched is not
   launched a second time — which is what `test_start_rejoue_ne_double_pas_l_execution`
   checks for ACA, and its Kubernetes counterpart for `k8s_job`.

## If a run stays stuck

A run whose pod vanished without Temporal noticing unblocks by relaunching the activity:

```bash
# Identify the run
kubectl -n choregos-system exec deploy/choregos-api -- choregos runs list --state running
# Relaunch it (idempotent — no double execution, no double billing)
kubectl -n choregos-system exec deploy/choregos-api -- choregos runs resume <run-id>
```

## What this does not cover

- **The spot eviction notice.** Azure and AWS warn ~30 seconds before reclaiming a machine.
  The platform does not listen to it: it suffers the loss as a hard failure. Listening would
  allow draining the node before it leaves — an improvement, not a fix.
- **Losing several nodes at once.** With workers on two nodes and both lost, there is
  nowhere to reschedule: that is a capacity problem, not a toleration one.
- **A node that comes back after the eviction.** Its old pods are deleted by the kubelet at
  restart; nothing to do by hand.
