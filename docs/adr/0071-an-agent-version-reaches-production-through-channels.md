# 0071 — An agent version reaches production through channels

- **Status**: accepted, 2026-10-17
- **Concerns**: the `agents` table (`stable_version`, `candidate_version`, `candidate_percent`,
  `candidate_since`, `release_policy`), the new `agent_releases` table, `projects.agent_channel`,
  `project_agents.channel`, the agent resolver (`services/agents.resoudre_l_agent`), the release
  endpoints (`releaseAgentVersion` and five others), the governance action `RELEASE`
  (`services/agent_governance.py`), the plugin gesture `agent.release` (`greffons.GESTES`); amends
  [ADR 0033](0033-agent-registry-and-skill-library.md) (§2-3: "else the latest version" becomes "else
  production") and [ADR 0061](0061-agent-ownership-scopes-and-forks.md) (one more action in the
  permission matrix)

## Context

Request 2 of the product owner (S40, 2026-10-16): *"as for software development, users must master the
publication of an agent and its lifecycle: eval, testing, prod."*

ADR 0033 gave an agent immutable, numbered versions. Publishing one is the only gesture, and it is
also the moment every project that does not pin starts running it (`resoudre_l_agent` took the
latest version). What was missing:

- a revision that is saved but not live, a **testing** stage, and therefore anything measured on real
  work before everyone gets it;
- gates beyond the deterministic eval suite (ADR 0020, level 1): real runs, a success rate, a soak
  time, a person with a recent sign-in for the step that affects every project;
- a **rollback** that does not mean publishing a copy of the old version as `vN+1` or pinning every
  project by hand;
- a **history** saying "v5 went to production at 14:02, by X, with these checks".

Reading the code on the way found three readers that took the *latest* version where they meant "the
one that runs" (the agent's daily budget alert, the tool patterns an external agent gets at the MCP
door, the metrics page), and an `effective()` that *assigned* a project's override instead of taking
the minimum — harmless while a pin was validated against one fixed version, wrong as soon as a pin can
follow a channel whose revision changes.

## Decision

1. **Vocabulary.** A **revision** is an `agent_versions` row: immutable, numbered, unchanged. An agent
   has two **channels**, each a pointer to a revision: **production** (`stable_version`) and
   **testing** (`candidate_version`). "Publish" keeps meaning "save the next revision"; **"release"**
   means "point a channel at a revision". "Promote" is not reused: ADR 0061 uses it for a change of
   scope.

2. **Two pointers on the agent, not a state on each version.** The stages the request names map to:

   | Stage | Mechanism | Gate to enter |
   | :-- | :-- | :-- |
   | eval | every revision is evaluated by its suite (level 1, exists); S40-02d adds a live evaluation | saving a revision: render check and suite (unchanged) |
   | testing | served to the projects that follow testing and to a canary share of the other projects' work items | the suite passes (re-checked at release) and the revision is newer than production |
   | production | the default for every project that pins no fixed revision | the policy's gates, a human session with a fresh sign-in, the plugin gesture `agent.release` |

3. **Resolution of a run**, first match wins: (1) `agent: slug@N` in the workflow (`explicit`); (2) the
   project pins a fixed revision (`pin`); (3) the project pins the agent on a channel (`pin_channel`;
   `candidate` with no candidate falls back to production); (4) the project follows testing
   (`projects.agent_channel = candidate`) and the agent has a candidate (`project_channel`); (5) the
   agent has a candidate with a share above 0 and the work item falls in it (`canary`); (6) production
   (`stable`), or the latest revision for an agent that has no production pointer (`latest`: defensive,
   an agent written by a path that sets none). Overrides of a pin still only tighten; `effective()`
   takes the minimum.

4. **The canary share is deterministic per work item**:
   `int(sha256(f"{agent.id}:{work_item_id}")[:8], 16) % 100 < percent`. Every stage and every retry of
   one work item gets the same revision, and a canary is reproducible from the two ids. Resolution is
   done in an activity (`prepare_stage`), not in workflow code: no Temporal history is concerned.

5. **What saving a revision does is the agent's policy** (`release_policy.on_publish`): `stable` (the
   default, **today's behaviour**: it goes to production), `candidate` (it goes to testing) or `none`
   (nothing moves). Every existing agent is migrated with `stable_version = max(version)`, one
   `baseline` row in `agent_releases` and the default policy, so nothing changes for anyone who does
   nothing. `on_publish: stable` combined with `require_candidate` or a candidate threshold is refused
   (422): a revision published straight to production is never a candidate. The console proposes
   `on_publish: candidate` with `require_candidate` for new organisation and project agents; the API
   default stays `stable` so that templates, the catalogue, scripts and existing clients keep working.

6. **Gates.** To testing: the eval suite passes again at release, and the revision is newer than
   production. To production (`release_policy.stable`): the suite passes again; if `require_candidate`
   or any threshold is set, the revision is the current candidate with at least `min_candidate_runs`
   finished runs since it became the candidate, a success rate of at least `min_success_rate`, after
   at least `min_soak_hours`. A failing check is a `409` listing each one (`release_check_failed`); the
   readiness endpoint returns the same checks without acting.

7. **A person decides production.** Releasing to production, or to a canary share above 0, needs a
   **human session with a sign-in younger than `step_up_minutes`** (default 10, as for governed
   actions, ADR 0035; `401 step_up_required`, which the console already follows) and the plugin gesture
   `agent.release`, where an edition may add its own rule. An API token may save revisions, send one
   to testing *without* a share, and withdraw the candidate. Personal agents, which run for nobody but
   their owner, are exempt from the step-up and the gesture; an agent whose policy says
   `on_publish: stable` (the organisation chose "publish is release") may be released by a token.
   Loosening a policy (publish straight to production, a lower threshold, a longer sign-in age) is
   itself a gesture that needs a fresh sign-in and is audited.

8. **Rollback and withdrawal.** Releasing to production a revision older than the current one *that
   was in production before* (a `done` stable row in the history, or at most the migration's baseline:
   under the old rule every version was "latest" once) is a **rollback**: it skips the suite, candidate
   and soak gates — it restores an approved state and speed matters in an incident — but keeps the
   rights, the human session, the fresh sign-in and the gesture check. Any other move of production is
   a promotion with every gate. The pointer moves with a compare-and-set from the revision the checks
   saw (`409` when another release was made meanwhile). **Withdrawing** the candidate empties testing;
   it needs no fresh sign-in.

9. **History and audit.** `agent_releases` records every move — channel, kind (`publish`, `promote`,
   `rollback`, `withdraw`, `baseline`), revision, previous revision, share, status, requester, reason,
   and the checks as they were evaluated. It is the timeline of the lifecycle tab and the rollback
   oracle, under forced row-level security like the registry's tables. Every move is also audited
   (`agent.release`, `agent.release_policy`, `project.agent_channel`).

10. **Who may release.** A new governance action `RELEASE`, on the row of publish, edit and life of the
    ADR 0061 matrix: an `org_admin` for common and organisation agents, the project's `project_owner`
    for a project agent, the owner for a personal agent; `AgentPermissions.release` carries it to the
    console. A project owner switches *their project* to follow testing, or pins an agent on a channel,
    with the existing pin right (`project:write`).

11. **Readers of "the revision that runs".** The daily-budget alert reads the run's own revision
    (`runs.agent_version`); the MCP door gives an external agent the tool patterns of its production
    revision; the metrics page shows production's budget and a breakdown by revision; the organisation's
    own distiller and drafter agents run their production revision.

12. **Approvals (S40-02b).** `release_policy.stable.approvals` (0-3, default 0) makes a promotion wait
    for other people. The request passes the checks and the requester's step-up, then is stored as
    `pending_approval` (`approvals_required`, `decisions`) and production does not move; at most one
    request waits per agent. `POST …/releases/{id}/decision` takes `approve`, `reject` or `cancel`.
    Approving needs the right to release the agent, a **human session with a fresh sign-in**, and — with
    `separation_of_duties` (default true) — being neither the requester nor the revision's author
    (`created_by`); nobody approves twice; the plugin gesture check sees each approval
    (`decision: approve`). When `approvals` different people have approved, the request re-runs the
    checks and moves production with a compare-and-set from the revision it saw: production moved
    meanwhile makes it `superseded` (`409`; the status is committed before the refusal is raised).
    Rejecting needs a reason; the requester or an org_admin cancels. A rollback never waits (speed in an
    incident, and it restores an approved state). `on_publish: stable` with approvals saves the
    revision and opens the same request, and a newer revision supersedes an older request; retiring the
    agent cancels it. Lowering `approvals` or switching separation off is a loosening. Audited:
    `agent.release.request`, `agent.release.decide`, then `agent.release` when applied. The rules are
    those of a governed action's decision (ADR 0035); its table is not reused (decision above). The
    approvers' inbox is `GET /orgs/{org}/agent-releases?status=pending_approval`, limited to the agents
    the caller may see; nobody is notified yet.

## Alternatives rejected

- **A state per revision** (`status = draft | testing | prod`): mutable state on immutable rows, and
  "which one is production" becomes a query that can return two rows. Two pointers are atomic, cheap to
  read at run time, and make a rollback a pointer move.
- **Environments as Choregos instances, or as a project attribute only.** A project is not an
  environment of the agent; a second instance does not share the registry. The project-level switch
  "follow testing" gives the *testing project* idea without inventing environments; moving agents
  between instances is "agents as code", out of scope.
- **Shadow runs** (run the candidate beside production on the same work and discard its output): a
  Choregos run writes branches, pull requests, tracker comments and governed actions; there is no
  effect-free run mode, it doubles the cost and needs workflow code. The canary share on real work
  plus the live evaluation on samples cover the need. A possible later step.
- **A random canary per run**: a work item could flip revisions between its stages or on a retry.
- **Automatic promotion when thresholds are met, automatic rollback on failure**: a person decides
  (ADR 0040 §4, ADR 0061 §5: never silently). `on_publish: candidate` is allowed because it is the
  owner's stated policy and testing is bounded.
- **Reusing governed actions (ADR 0035) for approvals**: an action is project-scoped and executed by
  Temporal effects; a release is an organisation-level pointer move inside one transaction. We reuse
  their rules (rank, human session, step-up, separation of duties, compare-and-set), not their table.
- **Making `staged` the API default**: breaks every client, template and test that publishes and then
  expects runs to use the new revision; the console proposes it instead.
- **A `release_manager` role**: roles are a contract enum (ADR 0061 rejected `maintainer` for the same
  reason).

## Consequences

- "Publish" no longer means "in production" for an agent whose policy says otherwise; the console says
  where a saved revision went.
- Runs record the revision they used, so a canary is reproducible from `(agent, work item)` and the
  metrics can be split by revision.
- The migration adds columns with defaults and one table; existing behaviour is unchanged until a
  policy is changed. Downgrading removes channel pins, which have no fixed revision to fall back to.
- An edition can add an organisation-wide rule on production releases through the `agent.release`
  gesture, as it does for `release.approve`.
- Approvers are not notified: they find the request in the inbox or on the agent's page.
- Not decided here, planned next in the same stream: the live evaluation of a revision (S40-02d), which
  extends this file.
