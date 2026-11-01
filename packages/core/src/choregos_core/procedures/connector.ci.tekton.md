---
id: connector.ci.tekton
title: Read pipeline status from Tekton
applies_to: {connector: ci/tekton}
audience: operator
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Tekton Pipelines events (CloudEvents)", url: "https://tekton.dev/docs/pipelines/events/"}
egress: []
values: ["webhook_url:tekton"]
fields:
  namespace: "the namespace whose PipelineRuns carry the project's builds"
  pipeline: "the Pipeline's name (choregos-ci by default)"
---

## Before you start

- A cluster where Tekton Pipelines is installed, and a namespace where the project's `PipelineRun`s run.
- The platform reads the cluster with the API's own in-cluster service account: it must be allowed to
  `get` and `list` `pipelineruns.tekton.dev` in that namespace. If you only use GitHub Actions, the
  project needs no `ci` connector: checks are read through the `scm` connector.
- The platform's shared webhook secret (`CHOREGOS_GENERIC_WEBHOOK_SECRET`).

## At the cluster

1. Label each `PipelineRun` the platform should follow with `choregos/sha=<commit>`; the connector
   finds a commit's runs through that label. Runs that carry `choregos/project`, `choregos/work-item`
   and `choregos/run-id` are also mapped back to their work item.
2. Point Tekton's CloudEvents sink at the platform:
   `{{webhook_url:tekton}}?token=<the shared webhook secret>`. Tekton's sink is configured by URL and
   cannot add a header, which is why the secret travels in the query string. `?jeton=` is still
   accepted, so a sink URL set up earlier keeps working.

## Permissions

- A `Role` granting `get`, `list` on `pipelineruns` in the namespace, bound to the API's service account.
- The shared webhook secret is the only credential of the sink. Treat the URL as a secret.

## In Choregos

1. Project settings, Connectors, **CI**, type **Tekton Pipelines**.
2. `namespace`: where the runs live. `pipeline`: the Pipeline's name, `choregos-ci` by default.

## Test it

Press **Test**: the connector asks the cluster for the Tekton API and answers `ok`. Then start a build
and watch its state change on the work item.

## When it fails

- `Forbidden` from the cluster: the API's service account lacks the `Role` above.
- Status stays `unknown`: no `PipelineRun` carries the `choregos/sha` label of that commit.
- Tekton events refused with `401`: the secret in the sink URL differs from the platform's.
- Events never arrive: the sink URL is unreachable from the cluster, or Tekton's `config-defaults` ConfigMap does
  not name it in `default-cloud-events-sink`.
