# Scaffold of the `github-aca` template

These files are added to the application repository through a pull request, never written
directly: a human reviews and merges them like any other change.

| File | Role |
| :-- | :-- |
| `AGENTS.md.j2` | what the agent must know about the repository: commands, conventions, definition of done |
| `.choregos/workflow.yaml.j2` | the project's workflow, derived from the chosen template |
| `.choregos/policy.yaml.j2` | budgets, approvals, scopes |
| `CODEOWNERS.j2` | who reviews what |
| `.github/PULL_REQUEST_TEMPLATE.md` | the pull request template |

The `github-aca` template adds no Tekton pipeline: agent runs execute on Azure Container Apps,
started by the orchestrator, not by a `PipelineRun` in the repository.
