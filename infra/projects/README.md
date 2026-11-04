# projects/ — written by the platform, applied by Argo CD

Each subdirectory is a provisioned project: namespaces, quotas, NetworkPolicies, the runner's
RBAC, Tekton EventListener and Argo Applications. The content is **generated** by the
`gitops.write_project_manifests` activity (see `apps/orchestrator/gitops.py`), dropped by a PR,
then synchronised by the neighbouring `ApplicationSet`.

Deleting a project = deleting its directory: `prune: true` cleans the cluster.
