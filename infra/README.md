# choregos-infra — the cluster in Git

This directory is the cluster's **single source of truth**: nobody, neither human nor API,
applies a manifest by hand. It is meant to live in a separate repository
(`VargaFoundation/choregos-infra`); it is delivered here so that the deployment is
reproducible from day one.

```
bootstrap/   operators and tooling (sync-wave 0–2): cert-manager, ESO, Envoy Gateway,
             CNPG, Tekton, Argo Rollouts, Kyverno, monitoring, gVisor
platform/    the `charts/choregos` umbrella, per environment (sync-wave 3)
projects/    written by ProjectProvisioning; one directory per project (sync-wave 4)
policies/    Kyverno: images signed by digest, non-root, labels, quotas, RuntimeClass
```

## Install

```bash
kubectl apply -f bootstrap/app-of-apps.yaml     # once, by a human
argocd app sync bootstrap                        # then Argo CD does the rest
```

## Sync order

| Wave | Content | Why |
| --: | :-- | :-- |
| 0 | cert-manager, External Secrets, Kyverno | nothing starts without secrets or certificates |
| 1 | CNPG, Envoy Gateway, Tekton, Argo Rollouts, gVisor | the operators before their objects |
| 1 | agent-sandbox, pinned in `operators/agent-sandbox/` (ADR 0016) | an operator before its objects; only where `runner.executor: agent_sandbox` |
| 2 | monitoring (kube-prometheus-stack, Loki, Tempo, OTel) | to watch the rest start |
| 3 | `platform/` — the Choregos umbrella | the platform itself |
| 4 | `projects/` — one ApplicationSet per directory | the provisioned projects |

## Secrets

No secret is in Git. A single `ClusterSecretStore` is created by hand at bootstrap;
everything else goes through `ExternalSecret`s. Rotations: GitHub App key 90 d, LiteLLM
`master_key` 90 d, API JWT keys 30 d (two keys), webhook secrets 180 d.
