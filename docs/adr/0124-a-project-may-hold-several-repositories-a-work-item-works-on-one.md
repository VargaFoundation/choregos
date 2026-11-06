# 0124 — A project may hold several repositories; a work item works on one of them

- **Status**: proposed, 2026-11-06 (product owner: "several repositories will have to be attached to the agent")
  — stream S58
- **Concerns**: `ProjectConfig.repositories` and `RepoConfig.name` (`project.schema.json`), the reserved
  field `fields.repository` (`choregos_core.repositories`, `choregos_core.domain.REPOSITORY_FIELD`), every
  orchestrator activity that talks to git (`activities/stage.py`, `job.py`, `scm.py`, `gates.py`,
  `run_network.py`, `outcome.py`, `decision.py`), the run's network (`services/network.py`), the birth
  of a work item (`services/routing.py`), `PATCH /work-items/{id}`, the `WorkItem` DTO, the console's
  project settings and ticket page; amends [ADR 0110](0110-a-project-without-a-repository-gets-a-workflow-that-delivers-no-code.md)
  (a project held zero or one repository) and [ADR 0119](0119-an-agent-declares-the-workspace-it-needs.md)
  (the repository a run sees was the project's one); builds on
  [ADR 0118](0118-what-the-tracker-says-about-a-ticket-is-a-reserved-field.md) (a reserved field),
  [ADR 0073](0073-github-app-installations-are-discovered-not-typed.md) (installations claimed per owner),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (the run's network) and
  [ADR 0057](0057-fan-out-and-join.md) (one child per element)

## Context

A project holds at most one repository (`ProjectConfig.repo`), and every stage of every work item works
on it: the branch, the pull request, the checks, the review, the diff guarantees, the runner's clone,
the DoD commands, the run's git token and the repository host on its network all read `config.repo`.

Platform maintenance (the plan `infra/docs/D2C-VULNERABILITIES-PLAN.md`) fixes findings across the
repositories of one platform team — `infra`, `diamctl`, `fondation`, `choregos-deploy` — under one
project, one policy, one budget and one set of agents. Today that needs four projects (four policies,
four budgets, four connector sets, the agents installed four times), or a monorepo nobody has.

## Decision

1. **A project may hold several repositories.** `ProjectConfig.repositories` is a list of
   `NamedRepository` — the fields of `RepoConfig` plus a required `name` (a slug) — beside `repo`, which
   stays the **primary** repository. Additive: a project that does not write `repositories` is exactly
   what it was. `repo` gains an optional `name`; without it the primary is named after the last segment
   of its URL (`https://github.com/acme/infra.git` → `infra`). The model and the schema refuse:
   - `repositories` without `repo` — "the first one is primary" would make reordering a list move every
     work item that names none to another repository;
   - two repositories with the same name, the primary included (a derived name that collides is
     refused with the way out: give `repo.name`);
   - two names for the same URL: one work item, one branch, and two names would allow two.
2. **A work item works on one repository: its target.** The target is the reserved field
   `fields.repository`, a repository **name** of the project; absent, the primary. Like `fields.tracker`
   (ADR 0118) it is the platform's, not the workflow's: `metadata.inputs` does not validate it (a
   workflow whose inputs forbid other properties still accepts it), and a workflow may not declare an
   input named `repository` (`inputs.reserved`). It is written:
   - **at birth**, by whoever files the work item: the console, the API, the MCP and A2A doors, an HTTP
     trigger's `fields` (`repository: /repo/name`), a schedule's `fields`, a fan-out child's `fields`
     (`repository: "{{ item.repository }}"`). A name the project does not hold, or any name on a project
     without a repository, is refused there (422) — the caller can be answered;
   - **from a tracker label** `repository:<name>`, when the fields do not say it. The label is copied as
     written and checked at first use (point 4): a tracker delivery and the backup poll cannot be refused
     without losing the ticket or blocking the poll for every other one;
   - **by a step's output**: an agent or job transition that declares `repository` in its `outputs`, or a
     decision step with a question named `repository`, copies the value its successful step answered into
     `fields.repository` (checked at first use, as a label is);
   - **by a person**, `PATCH /work-items/{id}` with `fields.repository`, as long as it is not fixed
     (point 3): 422 for an unknown name, 409 once fixed.

   Not by a routing rule: a rule matches labels to choose a workflow, and the `repository:<name>` label
   is already read from those same labels; a rule that chose both would tie two independent choices.
3. **One work item, one repository, one branch.** The target is **fixed** at the first activity that
   touches git for the work item — a stage whose `StageInput` carries a repository, `ensure_branch`,
   `open_pull_request` — and recorded in `documents.repository_fixed` (projects that hold several
   repositories only; a single-repository project has nothing to choose and is unchanged). A work item
   with a pull request, or with a run that cloned a repository, is fixed too, on its current target (it
   ran before its project held a second repository). Once fixed, `fields.repository` may only name the
   fixed repository: a person is refused (409), and an output or a label that names another is refused
   at the next use.
4. **Everything that talks to git resolves the target** (`choregos_core.repositories.target`), never
   `config.repo`: `StageInput.repo` (url, base, clone depth) and the branch name (the target's
   `branch_prefix`) for agent and job stages, the DoD commands of `StageInput.project` (`test_command`,
   `lint_command`, `typecheck_command` of the target), `ensure_branch`, `open_pull_request`,
   `enqueue_merge`, `collect_run_artifacts` (the diff and the evidence), `evaluate_gates` (the diff of
   `scope_respected` and `diff_size_max`, the checks of `ci_green`, the review of `review_approved`, the
   branch of `already_fixed_proven`, the check-runs published) and `check_scope_violations`. A target
   that names no repository of the project, or another than the fixed one, is a refusal that names the
   repositories the project holds: `prepare_stage` raises `RepositoryRefused`, a configuration refusal the
   interpreter escalates like any other (a person fixes the field, then replays the stage); the system
   effects raise it non-retryable; the gates refuse for want of a diff.
5. **The run's credentials and network follow the target.**
   - The runner's git token (`POST /internal/runs/{id}/git-token`) is minted for `StageInput.repo` —
     the target — through the installation the organisation claimed on **its** owner, restricted to that
     one repository (ADR 0073, unchanged code, now proven for a secondary repository).
   - The orchestrator's SCM adapter is the project's connector; for a target whose owner differs from the
     connector's repository owner, it is rebuilt pinned to the installation the organisation claimed on
     the target's owner (`pin_for_runtime`), and refused on a multi-organisation instance without such a
     claim, as for the connector itself.
   - The repository host implied on the run's network (ADR 0070, 0119) is the target's — in the run's
     `Permissions` and in the egress proxy's list (`GET /internal/runs/{id}/network`, which reads the
     run's recorded `StageInput.repo`) — never the other repositories' hosts.
6. **The console** lists the repositories in the project's settings (add, remove, make primary) and shows
   a work item's target ("primary" when it names none), with a way to set it while it is not fixed.
   `WorkItem` gains `repository` (the resolved target name, `null` without a repository) and
   `repository_fixed`.

## Out of scope

- **One change across several repositories in one work item.** Use a fan-out (ADR 0057): one child per
  repository, each born with its `fields.repository`, joined by the parent.
- **Read-only mounts of the other repositories** in a run's workspace: possible later, as an agent's
  workspace option (ADR 0119); a run sees its target only.
- **Webhook routing of a secondary repository's events.** A GitHub pull request or check event routes to
  the project whose slug is the repository's name, as before; the gates of a secondary repository's pull
  request read it from the SCM when they poll, so `ci_green` and `review_approved` still conclude, only
  later than with a routed event.
- **What stays the primary's**: the project's readiness ("repository set"), the knowledge bases a template
  ships from the project's repository (ADR 0117), the eval cells' toy repositories, the default workflow a
  project without a template receives (ADR 0110: a primary means `default-simple`).
- GitLab: the connector's Maintainer token must reach every repository of the project; nothing pins a
  GitLab token per owner.

## Consequences

- A project with one repository changes in nothing: no `repositories`, no `fields.repository`, no
  `documents.repository_fixed`; every existing test runs unchanged.
- Changing the primary of a project, or removing a repository, does not move a fixed work item: it keeps
  its repository, or is refused at its next use when the project no longer holds it.
- A finding scanner that knows where a finding lives files one work item per finding with its
  `fields.repository`, or one parent whose fan-out opens one child per repository.

## Alternatives rejected

- **One project per repository**: four policies, budgets and agent installations for one team's work.
- **A list of repositories per stage, all cloned**: a run's write scope, push, pull request and diff
  guarantees are built around one branch of one repository; several would need each of them reworked
  for a need nobody has yet (out of scope above).
- **The target in a new `WorkItem` column**: `fields` already reaches every reader (stage input,
  internal ticket, jobs, the console), as ADR 0118 found for `fields.tracker`.
- **Refusing an unknown name everywhere at once**: a tracker delivery or a step's output cannot be
  answered; dropping it would run the work item on the primary, the wrong repository, in silence.
