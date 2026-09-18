# Scaffolding du template `github-tekton-argo-k8s`

Ces fichiers sont rendus en Jinja2 puis proposés en **pull request** sur le dépôt du projet
lors du provisioning (étape `repo.scaffold_pr`). Rien n'est poussé directement sur la branche
par défaut : l'équipe relit et fusionne.

Variables disponibles : `project` (config du projet), `inputs` (réponses du wizard),
`workflow_yaml`, `policy_yaml`, `env`.
