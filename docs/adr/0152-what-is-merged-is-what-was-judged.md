# 0152 — What is merged is what was judged

- **Status**: proposed, 2026-11-11; S65-05 implements decisions 1 to 8, S65-07 decisions 9 to 13
- **Concerns**: `activities/gates.py` (`evaluate_gates`), `activities/scm.py` (`enqueue_merge`), the
  `ScmAdapter` protocol (`head_of`, `enqueue_merge`, `capabilities`), the GitHub and GitLab SCM adapters,
  `FakeScm`; the merge transition of every shipped workflow that merges (`choregos_core/dsl/templates/`,
  `templates/*/workflows/`), the `over` parameter of `scope_respected`, the validator's
  `workflow.no_diff_gate_at_merge`, `workflow.run_scope_on_system_step` and `gate.scope_over_unknown`;
  builds on
  [ADR 0008](0008-idempotence-and-run-id.md), [ADR 0031](0031-several-workflows-per-project.md),
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
9. **The shipped workflows hold their diff guarantees to the merge.** Decision 6 leaves a commit that
   landed after an earlier step's gates, and before the merge transition is entered, to the merge
   transition's gates alone. Every workflow the repository ships that merges — eleven of the core's,
   the three of `github-software-delivery` that merge and `governed-delivery` — therefore declares on
   its merge transition each guarantee an earlier step asks of the work item's whole diff, with the
   same parameters: `no_secrets`, `diff_size_max`, `docs_updated`, `markdown_sections` (and
   `adr_number_free`, which the two decision-record workflows already carried there). These read the
   diff between the default branch and the judged commit and nothing of a run, so at a merge
   transition — where the platform acts, and the run the interpreter passes is the last step's, or none
   once it was started again or carried its history over — the verdict is the same, and it is about
   the commit the merge names. Templates change; the DSL and the interpreter do not.
10. **`no_secrets` is on every shipped merge transition**, the two decision-record workflows
    included, though they asked it of no step. A merge is where the platform writes to the default
    branch; `no_secrets` takes no parameter and assumes nothing about the trade, so it is the one
    guarantee every shipped merge can hold, and no other is added that a workflow did not ask for.
11. **`scope_respected`, as the steps declare it, is a guarantee about a run, and no merge declares
    it that way.** It judges what that run wrote since the commit where it began (S22-11), against
    the paths that run may write (`_run_diff`, `Run.allowed_paths`). At a merge transition the run is
    the last step's. A file that landed before that step began is not among its writes; a refusal
    sends the work item back to a step whose run begins later, and the next verdict no longer reads
    the file; without a run, the same branch is judged on its whole diff. A verdict that depends on
    which step ran last, and on whether the interpreter still knows it, is not a guarantee on what is
    merged, and declaring it there would say that it is. The core names these guarantees
    (`RUN_SCOPED_GATES`). Decision 13 gives the scope the reading a merge can hold.
12. **A workflow written by an organisation holds what it declares.** The validator warns about a
    transition that merges and carries no guarantee read on the whole diff
    (`workflow.no_diff_gate_at_merge`; one judged on a run does not count). It is a warning: a workflow
    may rely on the forge's required checks alone, and says so by leaving it.
13. **`scope_respected` has a second reading, over the work item.** With `over: work_item`, its only
    value, the guarantee judges the work item's whole diff at the judged commit — the diff
    `no_secrets` reads at that evaluation — and nothing of a run: no start commit, no run's paths,
    whether the evaluation was handed a run or not. So the verdict of a transition the platform
    takes no longer changes with what the interpreter remembers. Without `over` the guarantee is
    what it was. Read over the work item, it refuses:
    - any changed file under a path the policy forbids (`scope.deny_paths`), always;
    - any changed file outside `paths`, when the declaration gives them.

    It does **not** read the work item's own allowed paths. They are not a fact the platform owns:
    a step's result sets them (`outputs.allowed_paths`) and a granted scope change extends them. They
    can therefore be narrower than what earlier steps legitimately wrote — in `governed-delivery` the
    architect's decision record is written, and approved by people, before the specification sets
    them — and they are no more certain than the step that last set them. Read at the merge they
    would refuse real work, or hold less than they seem to. Without `paths`, only the forbidden paths
    are held, and the verdict says so. A diff that cannot be read refuses, as for every diff
    guarantee.

    The fifteen shipped workflows declare it on their merge transition. A workflow whose steps are
    all held to the same `paths` declares them there: the two decision-record workflows merge nothing
    outside `docs/adr/**`. `advanced` asked a scope of the transition that opens its pull request:
    the platform takes it, so it reads the whole diff there, and what the release notes step wrote
    is now judged on that step. No workflow takes another version: nothing was published between
    decision 9 and this one.

    The validator follows the declaration, not the name: such a declaration counts as a guarantee
    held on the diff (decision 12); an `over` that is not `work_item` is an error
    (`gate.scope_over_unknown`); and a `scope_respected` judged on a run, on a transition a `system`
    actor takes, is warned about (`workflow.run_scope_on_system_step`) — the run is the last step's or
    none, so the verdict is about neither the transition nor the work item.

    `check_scope_violations`, the activity that compares the whole diff with the work item's allowed
    paths and the forbidden ones, has no caller in any workflow and none in the replayed histories.
    Its forbidden-path half is what the guarantee now does; its allowed-path half is the reading this
    decision declines. It is kept because an activity is registered under a name a running history
    could carry: removing one is its own change.

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
- **Reading the work item's allowed paths at the merge**, where the declaration gives no `paths`. It
  is what a reader expects of "the scope", and it is what the run's reading falls back to. But the
  platform does not own them (decision 13): a merge held to them refuses a governed delivery for the
  record its architects approved. An allow-list for a merge has to be the platform's first.
- **A new guarantee for the whole-diff scope**, under another name. Two names for one promise, and
  every existing workflow would have had to learn the second; a parameter keeps one guarantee and
  makes the declaration say which change it reads.
- **Making every `scope_respected` on a `system` transition read the whole diff.** It changes what a
  workflow already published means without a word in its document; the warning asks its author
  instead.
- **A direct merge only, never auto-merge.** A repository that requires a merge queue could no longer
  be merged.

## Consequences

- The commit a merge transition's gates ruled on is the commit that is merged, or nothing is. Reading
  a run page tells on which commit each guarantee was given.
- **A guarantee holds on the merged commit when the merge transition carries it.** One judged at an
  earlier step holds on the commit it judged; the head may have moved before the merge transition is
  entered, and its own gates are then the ones that judge the new head. The shipped workflows declare
  theirs there (decision 9); a workflow an organisation wrote holds them only if it does the same, and
  the validator says when it declares none.
- **The scope is held to the merge as far as the platform owns it.** What a shipped workflow merges
  holds no file under a path the policy forbids, whichever step wrote it and whenever it landed, and
  a decision record merges nothing but the record. A file that lands between two steps is read at the
  merge even though every later run starts from a branch that already holds it.
- **What remains is the work item's allowed paths.** They are held on what each run wrote, as before,
  and no further: a file that lands between two steps or before the merge, outside them and outside
  the forbidden paths, is merged if no other guarantee refuses it. Holding them to the merge needs
  them to be the platform's — who may set them, and whether a later step may narrow or widen them —
  which is a decision of its own. Until then a repository that must keep other paths closed forbids
  them in its policy, or relies on its forge (protected paths, code owners, required reviews).
- **A forbidden path is forbidden to the work item, whoever writes.** A person's commit on the pull
  request under such a path is refused by the platform's merge like any other; the person merges it
  by hand, or elsewhere.
- **A decision record that carries anything else stops at the merge.** A file an earlier step pushed
  outside `docs/adr/**` — no step's scope read it, the framing and research steps declare none — used
  to be merged with the record. It now sends the study back to the architect, who cannot take it out
  under its own scope, and then to a person.
- The `choregos/scope` check-run of a merge transition marks the commit it read, with the files it
  refused.
- **A size limit now counts everything that is merged.** `diff_size_max` on the merge transition reads
  the work item's whole diff with the bound the implementation step was given: what later steps add
  (documentation, a review's changes, a CI fix) counts against it, where it used to be read once, after
  the implementation. A work item that outgrows the bound stops before the merge and goes to a person.
- **`no_secrets` reads what it read before**: the lines the cumulated diff adds, in the patches the
  forge returns. It complements the repository's own secret scanning and does not replace it.
- **A refusal follows the transition's `on_fail`**, the path a red CI already takes: back to
  `in_progress`, `fixing_ci` or `decided` a bounded number of times, then to a person
  (`needs_human`); `documentation` goes to a person at once. No shipped workflow dead-ends there. The
  merge transition waits for its pending checks before it rules, as it did for `adr_number_free`: a
  refusal on the diff is acted on once the pull request's checks have answered or timed out, and
  nothing is merged meanwhile.
- **A workflow document is versioned, and a project keeps its copy.** Each changed workflow takes its
  next `metadata.version` (ADR 0031). A new project receives it. An existing project keeps the version
  it stores until someone publishes the shipped file as its next version: the readiness items
  `workflow-template` and `workflow-catalogue` (ADR 0111) say when the shipped one differs, and
  nothing is applied on its own. A work item in progress finishes on the version it was born in,
  unless it is migrated.
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
