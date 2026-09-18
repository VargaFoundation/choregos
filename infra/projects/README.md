# projects/ — écrit par la plateforme, appliqué par Argo CD

Chaque sous-répertoire est un projet provisionné : namespaces, quotas, NetworkPolicies,
RBAC du runner, EventListener Tekton et Applications Argo. Le contenu est **généré** par
l'activité `gitops.write_project_manifests` (voir `apps/orchestrator/gitops.py`), déposé
par une PR, puis synchronisé par l'`ApplicationSet` voisin.

Supprimer un projet = supprimer son répertoire : `prune: true` nettoie le cluster.
