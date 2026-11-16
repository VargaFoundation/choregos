# 0171 — What is merged is what was judged

- **Status**: proposed, 2026-11-11; S65-05 implements it
- **Concerns**: `activities/gates.py` (`evaluate_gates`), `activities/scm.py` (`enqueue_merge`), the
  `ScmAdapter` protocol (`head_of`, `enqueue_merge`, `capabilities`), the GitHub and GitLab SCM adapters,
  `FakeScm`; builds on [ADR 0008](0008-idempotence-and-run-id.md),
  [ADR 0101](0101-governance-the-platform-does-not-enforce-is-refused.md) and
  [ADR 0124](0124-a-project-may-hold-several-repositories-a-work-item-works-on-one.md)

## Context

A gate does not take the agent's word, it checks. What it checked was a branch, designated by its name
at the moment of the evaluation: the diff against the default branch, the run's own scope and the pull
request were three separate readings, and nothing recorded which commit a verdict was about. The merge
named no commit either. A branch whose head moved after its gates had ruled was therefore merged as it
then stood.

A work item has one branch, shared by all its steps, and a work item can wait a long time between a
verdict and its merge. The forge runs its own required checks again on a new head; the platform's
guarantees do not run by themselves.

## Decision

1. **An evaluation judges one commit.** `evaluate_gates` reads the head of the work item's branch once
   (`ScmAdapter.head_of`), and every reading of the branch in that evaluation is a reading of that
   commit: the diff against the default branch and the run's scope from its start commit are both
   compared at its SHA, never at the branch's name. A head that cannot be read is not judged: the
   diff guarantees refuse (`diff unavailable`), as for a diff that cannot be read. A branch that does
   not exist stays what it was, the empty diff of a run that measured no change.
2. **A pull request's checks count when they are the judged commit's.** `ci_green`, `review_approved`,
   `scans_ok` and `provenance_signed` read the pull request only when its head is that commit. When it
   is elsewhere they stay pending, and are read at the next evaluation.
3. **A verdict is recorded with its commit.** Each `gate.outcome` of the run's journal carries
   `head_sha`. The check-runs `choregos/scope` and `choregos/evidence` are published on the judged
   commit, never on one a run declared; without a judged commit none is published.
4. **The judged commit is kept on the work item**: `documents.judged_commit` holds its `sha`, the
   `repository`, the `branch` and the `state` the work item was in. The orchestrator alone writes it,
   as it does `documents.repository_fixed` (ADR 0124). Judging the same commit again records the same
   thing.
5. **The merge names it.** `enqueue_merge` gives the adapter the commit the pull request's head must be
   at: `sha` on GitHub's pull request merge and `expectedHeadOid` on `enablePullRequestAutoMerge`, `sha`
   on GitLab's merge request merge. An adapter first reads the pull request's head and refuses, without
   asking the forge, when it is elsewhere. No merge goes out without a commit.
6. **The commit named is the one judged in the state the work item merges from**: the verdicts of its
   merge transition, which are what let the merge go. A merge transition that carries no gate has no
   verdict to hold; the merge then reads the pull request's head once and names that.
7. **A refused merge is typed, and is not retried.** A head elsewhere is a `HeadMovedError` in the
   adapters and a non-retryable `HeadMoved` from the activity: nothing is merged. The work item's
   workflow stops on it as on any activity that fails for good — the reason is written on the work
   item (`failure`) and published (`workitem.workflow_failed`) — and, started again
   (`mark_agent_ready`), it re-enters its merge transition, whose gates judge the new head. A pull
   request already merged at the named commit is left as it is: a replayed merge is not a second one
   (ADR 0008).
8. **An adapter says that it can.** `ScmAdapter.capabilities` gains `merge_at_commit`, declared by the
   three shipped adapters (`github`, `gitlab`, `fake`). The orchestrator never asks an adapter without
   it to merge: a non-retryable `MergeUnpinned`, because a merge the platform cannot hold to a commit is
   refused, not made blind (ADR 0101).

## Alternatives rejected

- **Carrying the commit through the workflow**, from the evaluation's result to the merge's input. It
  changes workflow code and what recorded histories hold, for two activities that already read the
  same work item one after the other.
- **Keeping it in the run's journal only.** A run appends to its own journal, so the journal is a
  record for readers, not a source for the merge; and a merge transition entered by an interpreter that
  was started again has no run to file it under.
- **A column on the work item.** `documents` already holds what the platform fixes about a work item's
  repository; a migration buys nothing more.
- **Refusing the merge whenever the head is not the commit an earlier step's diff guarantees judged.**
  It also refuses what the platform accepts today — a later step that writes under other gates, a
  person's fix on the pull request, the default branch merged into it — and leaves no way to judge
  again where the merge transition carries no diff guarantee.
- **Running an earlier step's diff guarantees again at the merge transition.** Gates a transition does
  not declare would decide it: a change to what the DSL means, to be decided on its own.
- **A direct merge only, never auto-merge.** A repository that requires a merge queue could no longer
  be merged.

## Consequences

- The commit a merge transition's gates ruled on is the commit that is merged, or nothing is. Reading
  a run page tells on which commit each guarantee was given.
- **A guarantee holds on the merged commit when the merge transition carries it.** One judged at an
  earlier step holds on the commit it judged; the head may have moved before the merge transition is
  entered, and its own gates are then the ones that judge the new head. A workflow that wants a diff
  guarantee held on what is merged declares it on its merge transition, as the `study` workflow does
  with `adr_number_free`. The other shipped workflows carry their diff guarantees on the steps that
  write; adding them to their merge transitions is left to a later story.
- **GitHub's auto-merge is pinned when it is enabled, not when it merges.** `expectedHeadOid` refuses a
  head that has already moved; once enabled, auto-merge follows the pull request's head, and GitHub
  only disables it for a push from an account without write access. It applies when the forge still
  waits for something as the platform merges; a pull request already mergeable is merged directly, at
  its `sha`. GitLab cancels an auto-merge when commits are added.
- A merge refused on a moved head stops the work item's workflow rather than escalating the work item:
  escalating needs a marker in the interpreter and a replayed history, which is another change.
- Every evaluation of a project with a repository costs one more call to the forge.
- An SCM adapter kept outside this tree has to implement `head_of`, take the commit in
  `enqueue_merge` and declare `merge_at_commit`; until then its diff guarantees refuse and the platform
  does not merge through it.
