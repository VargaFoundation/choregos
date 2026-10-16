---
id: connector.runtime.k8s_job
title: Run agent stages as Kubernetes Jobs
applies_to: {connector: runtime/k8s_job}
audience: operator
version: 1
reviewed: 2026-10-16
egress: []
---

## Before you start

- A Kubernetes cluster and a namespace for agent pods. This is the executor with the fewest moving parts
  and the one that supports queueing; use it when Tekton is not there.
- This is a chart setting, not a form: `runner.executor: k8s_job` in the orchestrator's values.

## At the cluster

Set the values of `choregos-orchestrator.runner` (see the deployment guide, "Running agent stages"):
`executor`, `namespacePattern` (for example `proj-{slug}-runners`), `maxActive`, `limits`, and
`envFromSecrets` for credentials mounted by reference into each agent pod.

## Permissions

The orchestrator needs rights to create and read `Job`s and `Secret`s in the runner namespaces
(`global.rbac.jobRunner: true`, or the platform grants the same verbs). Under a namespace `LimitRange`,
set `runner.limits` below the per-container ceiling, or the pod is rejected and the Job waits without
saying why.

## In Choregos

Nothing to type: the executor is chosen by the chart. `maxActive` caps concurrent runs per namespace;
beyond it a Job is created suspended (no pod, no image pull, no quota) and admitted in arrival order.

## Test it

Start a run on a test project and watch its pod: `kubectl get jobs -n <runner namespace>`.

## When it fails

- A Job that never starts: the namespace quota or a `LimitRange` refuses the pod. Lower `runner.limits`.
- `Forbidden` creating the Job: the orchestrator's service account lacks the rights above.
- A pod that cannot pull its image: the image or registry credentials; the run reports the container's
  reason and the runner's exit code.
