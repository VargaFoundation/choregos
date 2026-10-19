---
id: connector.runtime.tekton
title: Run agent stages as Tekton PipelineRuns
applies_to: {connector: runtime/tekton}
audience: operator
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Tekton Pipelines", url: "https://tekton.dev/docs/pipelines/"}
egress: []
fields:
  pipeline: "the Pipeline each run starts, choregos-agent by default"
  service_account: "the service account the PipelineRun runs as"
---

## Before you start

- A cluster with Tekton Pipelines, and a `Pipeline` named `choregos-agent` (or your own name) in the
  runner namespace, installed from the chart.
- The default executor when Tekton is present: `runner.executor: tekton`.

## At the cluster

Install Tekton, then the chart's Pipeline. Set `runner.executor: tekton` and `runner.namespacePattern`
in the orchestrator's values. Each agent stage becomes one `PipelineRun`; its Secret belongs to the run
and goes with it however it ends.

## Permissions

The orchestrator creates, reads and deletes `PipelineRun`s and `Secret`s in the runner namespaces. The
`service_account` is the identity of the stage's pod: give it nothing beyond what the runner needs.

## In Choregos

Mostly chart values. If a connector is declared, `pipeline` names the Pipeline to start and
`service_account` the account it runs as.

## Test it

Start a run and look at `kubectl get pipelineruns -n <runner namespace>`.

## When it fails

- A `PipelineRun` that never schedules: the Pipeline name does not exist in that namespace.
- `Forbidden`: the orchestrator's service account lacks `pipelineruns` rights.
- The run's pod is rejected at admission: see the namespace policies (security context, `LimitRange`).
