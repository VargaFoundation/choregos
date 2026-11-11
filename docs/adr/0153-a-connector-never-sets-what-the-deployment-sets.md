# 0153 — A connector never sets what the deployment sets

- **Status**: proposed, 2026-11-11; S65-03 implements it
- **Concerns**: the configuration of a `runtime` and of a `ci` connector
  (`choregos_adapters.registry`: `DEPLOYMENT_FIELDS`, `refuse_deployment_fields`, `validate_config`,
  `AdapterSet.from_connectors`), the factories of the cluster-backed executors
  (`choregos_adapters.builtins`), their declared types (`choregos_adapters.specs`), `KubernetesClient`
  (`choregos_adapters.executor.tekton`), `PUT /projects/{id}/connectors/{kind}` and the organisation's
  connector routes; builds on [ADR 0034](0034-connectors-by-capability.md),
  [ADR 0058](0058-connector-secrets-are-write-only-values.md),
  [ADR 0072](0072-agent-sandbox-is-the-runtime-of-a-real-deployment.md) and
  [ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md)

## Context

A connector's configuration is written by whoever holds `connector:write` on a project. For most
connectors that is the right owner: the project's tracker, its repository, its notification channel.

A `runtime` connector is different. Its executors run a project's stages as pods of a cluster the
platform operates, and three of their settings named objects of that cluster: the Secrets injected
whole into a run's pod (`env_from_secrets`), the pod's pull secrets (`image_pull_secrets`) and the
account it runs as (`service_account`). A fourth, `kubernetes`, named the API server itself, with
its address, its token and whether its certificate is checked; it was a seam for the code that
builds an executor in a test, and no type declared it. All four were read from the connector's
configuration, and a configuration is not checked against its type's schema.

Where each project has a runner namespace of its own, a name in that namespace is the project's.
The `agent_sandbox` executor — the runtime of a real deployment (ADR 0072) — runs every project's
stages in the release namespace, and `k8s_job` may. There, a name a project writes reaches objects
that are not the project's, with the rights of the orchestrator's account, which reads every Secret
of the namespace it launches runs in. And `KubernetesClient`, given an address and no token, sent
the pod's mounted service-account token to that address.

The deployment already has a place for each of them: `runner.envFromSecrets`,
`global.imagePullSecrets`, the runner's account that the chart creates.

## Decision

1. **Which cluster the platform talks to, and what a run's pod is given from it, is the deployment's
   to set.** `kubernetes`, `env_from_secrets`, `image_pull_secrets` and `service_account` are not part
   of a `runtime` connector's configuration; `kubernetes` is not part of a `ci` connector's. The
   names are in one place, `DEPLOYMENT_FIELDS`.
2. **Refused when declared.** `validate_config` raises on a configuration that sets one of them, so
   the project's and the organisation's connector routes answer 422 and store nothing. The message
   names every field at once and says whose they are.
3. **Refused again when built.** `AdapterSet.from_connectors` refuses a stored configuration that sets
   one of them, before any adapter is built: a row written before this decision does not keep its
   effect, and its project's stages stop with the same message instead of running without what the
   row asked for ([ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md): what is not
   enforced is refused, not ignored).
4. **A blank value sets nothing.** `None`, an empty string, list or object is let through at both
   points: a form that saved every field of a type must not stop a project.
5. **The factories no longer read them from a configuration.** The pod's Secrets and pull secrets
   come from the deployment's lists (`CHOREGOS_RUNNER_ENV_SECRETS`,
   `CHOREGOS_RUNNER_IMAGE_PULL_SECRETS`), the account is the runner's. The types no longer declare
   the fields, so a form no longer offers them. `kubernetes` stays readable by `build`, for the code
   that builds an executor against a cluster of its own (the conformance suite, the cluster tests):
   that code is not a declaration.
6. **The mounted service-account token goes to its own cluster only.** `KubernetesClient` uses it
   when its address is the cluster's own API server — the service's names, or the address the kubelet
   gives every pod — and for no other. Elsewhere it uses the token it was given, or none.
7. **A project keeps what is a project's**: `max_active`, the CPU and memory ceilings, the Pipeline a
   Tekton executor starts, and where a project's CI pipelines run.

## Alternatives rejected

- **Letting an instance administrator set them on a connector.** A stored row does not say who wrote
  it: the rule could be checked at declaration and never again. A setting that is the deployment's
  belongs with the deployment's settings, where a chart review sees it.
- **A deployment-side list of the names a project may use.** It is `runner.envFromSecrets` under
  another name: per-project content is already had by giving each runner namespace a Secret of the
  same name.
- **Ignoring the fields silently.** A project that named a Secret would run without it and fail
  later, somewhere else, for a reason nobody would find in the connector.
- **Checking every configuration against its type's schema, and nothing else.** It would not have
  closed this: three of the four fields were declared. It is worth doing on its own.

## Consequences

- A deployment whose projects set one of these on a `runtime` connector must move it to the chart
  before upgrading; `docs/deployment.md` gives the query that lists such rows.
- Per-project Secrets in a shared namespace are no longer possible through a connector. Runner
  namespaces per project (`runner.namespacePattern`) keep per-project content.
- A Tekton executor always runs its stages as the runner's account.
- Connector configurations are still not checked against their type's schema in general: an
  undeclared field of another name is stored, and read by nothing.
