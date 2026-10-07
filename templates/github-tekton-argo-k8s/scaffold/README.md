# Scaffolding of the `github-tekton-argo-k8s` template

These files are rendered with Jinja2, then proposed as a **pull request** on the project's
repository during provisioning (step `repo.scaffold_pr`). Nothing is pushed directly to the
default branch: the team reviews and merges.

Available variables: `project` (the project's configuration), `inputs` (the wizard's answers),
`workflow_yaml`, `policy_yaml`, `env`.
