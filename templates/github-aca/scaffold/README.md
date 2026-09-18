# Scaffold du template `github-aca`

Ces fichiers sont déposés dans le dépôt applicatif par une PR, jamais écrits directement :
un humain les relit et les fusionne comme n'importe quel changement.

| Fichier | Rôle |
| :-- | :-- |
| `AGENTS.md.j2` | ce que l'agent doit savoir du dépôt : commandes, conventions, DoD |
| `.choregos/workflow.yaml.j2` | le workflow du projet, dérivé du template choisi |
| `.choregos/policy.yaml.j2` | budgets, approbations, périmètres |
| `CODEOWNERS.j2` | qui relit quoi |
| `.github/PULL_REQUEST_TEMPLATE.md` | le gabarit de PR |

Le template `github-aca` ne dépose pas de pipeline Tekton : les runs d'agents tournent sur
Azure Container Apps, déclenchés par l'orchestrateur, pas par un `PipelineRun` dans le dépôt.
