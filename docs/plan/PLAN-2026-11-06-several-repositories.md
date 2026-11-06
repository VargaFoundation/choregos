# Plan — 2026-11-06: a project may hold several repositories (stream S58)

Source: the product owner, 2026-11-06, for platform maintenance (`infra/docs/D2C-VULNERABILITIES-PLAN.md`):
the findings of one platform team live in `infra`, `diamctl`, `fondation` and `choregos-deploy`, and "several
repositories will have to be attached to the agent".

Checked against `main` at `b55f7f0c`. Stream number: **S58**. ADR: **0124**
([the decision](../adr/0124-a-project-may-hold-several-repositories-a-work-item-works-on-one.md)).

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

## 0. What the audit found

| What reads the repository | Where | Today |
|:--|:--|:--|
| The run's clone, branch and push | `activities/stage.py` `_repo_ref`, `activities/job.py` → `StageInput.repo`; the runner reads only `StageInput.repo` | `config.repo` — one repository |
| The DoD commands | `StageInput.project.test_command` and its neighbours | `config.repo`'s |
| Branch, pull request, merge, diff for the console | `activities/scm.py` (`ensure_branch`, `open_pull_request`, `enqueue_merge`, `collect_run_artifacts`) | `config.repo` and `config.branch_for` |
| The guarantees | `activities/gates.py` (`scope_respected`, `diff_size_max`, `ci_green`, `review_approved`, `already_fixed_proven`, `adr_number_free`, check-runs), `check_scope_violations` | `config.repo` |
| The runner's git token | `POST /internal/runs/{id}/git-token` | **exists per run**: minted for `StageInput.repo.url` through the installation claimed on its owner |
| The orchestrator's SCM token | the project's `scm` connector, pinned to the installation of its `repo`'s owner | one owner: another owner's repository is refused by `GitHubAppAuth.token_for` |
| The repository host on the run's network | `activities/run_network.py`, `services/network.py` (`project_repo_host`) | `config.repo`'s host |
| A field the platform owns | `fields.tracker` (ADR 0118): refused from callers, left out of `metadata.inputs`, `inputs.reserved` | **exists**: the pattern to follow |
| Where a work item is born | `services/routing.new_work_item`, the single constructor | **exists**: one place to validate |
| A step's output into the work item | `record_run_outcome` stores declared outputs in `documents`; nothing writes `fields` | **missing** for `repository` |

## 1. Stories

Four pull requests, stacked in this order (each branch on the previous one; the integrator merges in order).

### S58-00 · docs(adr): ADR 0124, this plan, the backlog stories

ADR 0124 `proposed`; the stories appended to `08-backlog.yaml`.

### S58-01 · contract(schemas): a project's repositories, and the reserved field

- `RepoConfig.name` (optional), `NamedRepository` (`name` required), `ProjectConfig.repositories`;
  refused: `repositories` without `repo`, two equal names (the primary's derived name included), two names
  for one URL. `ProjectConfig.primary_name`, `repository_names()`, `repository_named()`, and `branch_for`
  taking the repository whose prefix it uses. `project.schema.json` in step (compared by a test).
- `choregos_core.domain.REPOSITORY_FIELD`; `choregos_core.repositories`: the target of a work item
  (fixed, else `fields.repository`, else the primary) and the refusal that names the project's repositories.
- `inputs.reserved` refuses a workflow input named `repository`.
- OpenAPI: `WorkItem.repository`, `WorkItem.repository_fixed`; `make contracts`.

### S58-02 · feat(orchestrator,api): everything that talks to git works on the target

- Every row of the audit table resolves the target; the target is fixed at first use
  (`documents.repository_fixed`) on projects that hold several repositories.
- `prepare_stage` raises `RepositoryRefused` (a configuration refusal the interpreter escalates: one
  `workflows/` line, with a recorded history); the SCM effects raise it non-retryable; the gates refuse.
- The SCM adapter rebuilt for a target of another owner, pinned to that owner's claimed installation.
- The run's network and the proxy's list imply the target's host only.
- An agent or job output `repository` the transition declares, and a decision question named
  `repository`, are copied into `fields.repository`.

### S58-03 · feat(api,web): settings and the ticket

- `new_work_item` refuses an unknown `fields.repository` (422) and copies a `repository:<name>` label;
  `validate_fields` leaves the field out; `PATCH /work-items/{id}` refuses an unknown name (422) and a
  change once fixed (409); the `WorkItem` DTO says the target and whether it is fixed.
- Console: a *Repositories* card in the project's general settings (the primary's name, the other
  repositories with their URL, branch and checks, add, remove, make primary); the ticket's target in
  *About this ticket*, with a selector while it is not fixed.
- `docs/usage.md`: a project with several repositories, and the reserved field.

## 2. What the platform-maintenance plugin does with it

The project declares the four repositories (`repo` = `infra`, `repositories` = `diamctl`, `fondation`,
`choregos-deploy`). A work item gets its target either at birth — the scanner's HTTP trigger maps
`fields: {repository: /finding/repository}`, or a parent's fan-out opens one child per finding with
`fields: {repository: "{{ item.repository }}"}` — or from a triage step: a job (or a decision step) whose
transition declares `outputs: [repository]` answers the repository's name before the first stage that
clones. A work item that names none works on `infra`.
