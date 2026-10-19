# 0072 — The runtime of a real deployment is agent-sandbox

- **Status**: accepted, 2026-10-16 — stream S40 (S40-03a)
- **Concerns**: the chart (`runner.*`), the executor registry, `GET /platform/executors`, the project `runtime` connector
- **Supersedes in part**: [0016](0016-agent-sandbox-executor.md) (the "opt-in" wording), [0013](0013-agent-task-density.md) ("no warm pool")

## Context

ADR 0016 made `agent_sandbox` an opt-in executor and left `tekton` the default. Two facts
overtook it. Tekton runs on none of the clusters this platform is deployed on, and the
project settings page said "Platform default: tekton" next to a deployment whose workers ran
`k8s_job`. Three separate causes produced that word:

1. the API never received `CHOREGOS_EXECUTOR_KIND` (only the workers did), so
   `default_executor_kind()` fell back to `tekton`;
2. the chart and the code defaulted to `tekton` (`runner.executor`, `OrchestratorSettings.executor_kind`);
3. `GET /platform/executors` without database rows listed `tekton` as enabled, whatever the
   deployment ran, so a project could pick it and die at its first run.

The product owner's rule: in a real deployment the agent runtime is the sig `agent-sandbox`
(warm, credential-free pool; suspension across a human question), not Tekton.

## Decision

1. **`agent_sandbox` is the default executor** of the chart (`global.runner.executor`) and of
   the code fallbacks. `tekton`, `k8s_job`, `aca` and `local_docker` stay available, each
   **chosen by name**: `values/local.yaml` writes `k8s_job`, `compose.yaml` writes `local_docker`,
   Tekton exists only where `global.runner.executor: tekton` is written.
2. **One setting for every pod.** `global.runner.executor` feeds `CHOREGOS_EXECUTOR_KIND` through
   `choregos.commonEnv`, so the API and the workers answer the same. The orchestrator's own
   `runner.executor` may only repeat it; a different value fails the render.
3. **The runner namespace follows the executor.** `runner.namespacePattern` defaults to empty:
   the release namespace for `agent_sandbox` (the SandboxTemplate lives there), `proj-{slug}-runners`
   otherwise. An explicit value wins.
4. **The deployment states what projects may pick.** `global.runner.allowedExecutors` (default
   empty = only the executor) becomes `CHOREGOS_EXECUTORS_ALLOWED`. `GET /platform/executors`
   without database rows is computed from the two variables, and
   `PUT /projects/{id}/connectors/runtime` answers 422 for a type outside the list, naming the
   allowed ones. A server that declares neither variable (a bare development server) refuses nothing.
5. **The operator is checked at render.** With `agent_sandbox` and no
   `extensions.agents.x-k8s.io/v1beta1/SandboxTemplate` among the cluster's API versions, the
   chart fails and names the operator and `k8s_job` as the way out. Argo CD and `helm install`
   pass the versions; offline renderings pass `--api-versions` or set
   `runner.skipOperatorCheck: true`.
6. **Tekton-only objects render only for Tekton**: the API's ClusterRole on `tekton.dev` and the
   `choregos-agent-stage-task` ConfigMap.
7. **Quota sizing** (from the volume lifetime fix, S35-02): the namespace holds at most
   `storage × (maxActive + warmPool.size + runs finished within ttlSecondsAfterFinished)` of volumes.
   Size the tenant's PVC quota with that formula.

## Rejected

- *Keep Tekton as default and only give the API the variable*: does not answer the request, and Tekton is absent from the clusters.
- *Default `k8s_job`*: works everywhere, but gives up the warm credential-free sandbox and suspension across a human question.
- *Refuse the chart without the operator unconditionally*: breaks offline `helm template` in CI. Hence the capability check and its escape hatch.
- *A template and a pool per project*: out of scope, the limit of ADR 0053 stays.

## Consequences

- **Upgrading from 0.17 or earlier**, which defaulted to Tekton: set `global.runner.executor: tekton`
  (and keep the orchestrator's `runner.executor` out of your values, or equal to it) to keep Tekton.
  A deployment that set only `choregos-orchestrator.runner.executor: k8s_job` now fails the render
  with a message naming `global.runner.executor`.
- A cluster without the operator fails the render rather than the first run.
- Steps that name their own image stay refused by `agent_sandbox` (ADR 0053); a deployment that
  needs them lists `k8s_job` in `allowedExecutors`.
