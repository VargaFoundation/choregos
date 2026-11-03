# 0119 — An agent declares the workspace it needs

- **Status**: accepted, 2026-11-02 — implemented by S51-03. Amends
  [ADR 0080](0080-hosted-prompt-and-voice-agents.md) (`workspace` was a hosted runtime's only) and
  [ADR 0110](0110-a-project-without-a-repository-gets-a-workflow-that-delivers-no-code.md) (whether
  a run sees a repository was the project's alone)
- **Concerns**: `AgentSpec.workspace` (`choregos_api.schemas.agents`, `openapi.yaml`),
  `choregos_core.workspace`, `choregos_orchestrator.activities.stage.prepare_stage` (`repo`,
  `allowed_paths`, `permissions.write_paths`), `activities.run_network` and
  `choregos_api.services.network` (the repository host), `activities.gates`
  (`empty_diff_of_unchanged_run`), `POST /internal/runs/{id}/scope-change`, the console's agent
  definition and builder

## Context

The repository is the project's (`config.repo`), and every stage of every agent cloned it: the
orchestrator put a `repo` in each `StageInput`, the run's write scope was the work item's
(`**` when it had none), and the repository's host joined the run's network as an implied host
(ADR 0070). Only a remote agent (ADR 0062) and a hosted one published with `workspace: none`
(ADR 0080) went without. On the production tenant an analysis agent that only reads a ticket ran
with the whole repository on disk, `permissions.write_paths: ["**"]` and
`allow_domains: ["github.com"]` — more than it needs, for no gain, and a wider blast radius if its
prompt is turned.

## Decision

1. **A version says what it needs**: `AgentSpec.workspace` is `none`, `read`, `write` or absent.
   Absent means `write` — every published version keeps today's behaviour — and is left out of the
   stored spec, so no version's digest moves. The repository stays the project's: the agent never
   names one.
2. **`none`**: the `StageInput` carries no `repo`. The runner prepares an empty directory with a
   local git and pushes nothing (as for a project without a repository, ADR 0012). The repository's
   host is no longer implied on the run's network, in the run's `Permissions` (orchestrator) as in the
   egress proxy's list (`GET /internal/runs/{id}/network`, which reads `repo: null` from the run's
   recorded `StageInput`). An agent that still names that host in `network.domains` must find it in
   the project's allowlist, or it is refused before any key is minted (`admit_network`).
3. **`read`**: the repository is cloned, and the write scope is `.choregos/outputs/**` — a pattern
   that matches no repository file, never `[]`, because an empty scope means "no scope declared:
   write allowed" to the runner's guards and scope check. A write elsewhere is refused live and
   reverted after; nothing is committed, so nothing is pushed. The run records that scope, and
   `POST /internal/runs/{id}/scope-change` denies any widening of it: neither the policy's automatic
   grant nor a human turns a read into a write.
4. **`write`**: today's run.
5. **One answer per question**: a remote (`a2a`) agent gets no workspace and refuses the field; a
   hosted agent says it in `runtime.workspace`, and a top-level `workspace` that differs is refused.
   What a run gets is `AgentSpec.effective_workspace()`: `none` for `a2a`, the runtime's for
   `hosted`, `workspace or "write"` otherwise.
6. **Diff guarantees on a run that never cloned**: when the SCM says the work branch does not exist,
   a run whose `StageInput` had no `repo` is judged on an empty diff, whatever its runner measured in
   its empty directory (#536 required a measured empty change, which notes left there would break).
   A branch that exists, pushed by an earlier stage, is read as before.

## Consequences

- A remote agent and a hosted `workspace: none` agent also lose the implied repository host: they
  never cloned it either. A hosted `read` agent gets the read-only scope; its container never wrote
  back under `read` anyway.
- The console shows the workspace on an agent's definition ("none — never sees the repository",
  "read", "write (default)"), says that the repository comes from the project, edits it in the
  builder and shows it in a version diff.
- A project without a repository is unchanged: no `repo` whatever the agent declares.
- Not covered: the hosted sessions of an agent (S41-11) keep their own rule (`workspace: none`); a
  job step (ADR 0056) declares no agent and clones as before.

## Alternatives rejected

- **The workspace on the workflow's actor** rather than on the agent: what an agent needs is part of
  what the version is, measured by its evals and reviewed in its diff; an actor naming it would let a
  workflow give a reading agent write access its authors never reviewed.
- **An empty `write_paths` for `read`**: it means "write allowed" to the runner, the opposite.
- **A runner-side `read-only` flag**: a contract change of `StageInput` (ADR 0001) for what a scope
  that matches nothing already says.
