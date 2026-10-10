# 0041 — The release approval follows what the batch ships, and the train tells each work item

- **Status**: accepted, 2026-10-07
- **Concerns**: `choregos_core.dsl.trains`, `WorkflowInterpreter._run_train`, `ReleaseTrain`, the
  `finish_release` and `rollback` activities, the GitHub webhook; amends
  [ADR 0006](0006-trois-verrous-de-production.md)

## Context

The product review of 2026-10-07 tried to build one project with two delivery workflows: a small fix
that goes to production with no human, and a feature whose production release a release captain
approves. It is impossible on 0.16, because three pieces of the release train were declarative only:

1. **The workflow's approval was never read.** `train: {env: prod, approval: captain}` is written in
   the shipped templates, but the interpreter passed the train only the environment, and
   `ReleaseTrain` took its approval from the project policy alone. One project could not ship a fix
   without a captain and a feature with one.
2. **The train never told the work item it had shipped.** After boarding, the work item waited for a
   `cd.*` event that nothing sent: Argo CD events go to the train, not to work items. The tests
   injected the event by hand. In production, every work item that reached a train ended in
   `needs_human` when the 72-hour timeout ran out.
3. **Any `cd.*` event counted, whatever its environment**, and the GitHub webhook boarded **every**
   merged pull request onto the **production** train: a study that merges an ADR went to production,
   and a workflow that goes through staging first skipped it.

## Decision

1. **The approval of a departure is required when the policy requires it, or when one work item of
   the batch requires it.** A work item requires it when the train transition it takes names an
   approver (`train.approval`, a human actor whose group approves). The requirement travels with the
   boarding (`approval: {required, group}`). A work item boarded twice keeps the stronger
   requirement. The group is the policy's when it names one, otherwise the first work item's. The
   timeout stays the policy's.
2. **The train tells each work item of the batch.** When a release is verified (`finish_release`),
   each work item's interpreter receives `cd.rollout.completed`, and on a rollback
   `cd.rollout.aborted`, as an `inbound` signal carrying `{env, release_id}`. A work item that has
   stopped listening does not fail the train.
3. **A work item waiting for a train only counts events for that train's environment.** An event that
   names no environment still counts, so an event sent by hand keeps working.
4. **The GitHub webhook boards a merged work item onto the train that its pinned workflow takes right
   after the merge, if any**, with that train's approval. A study boards nothing. A careful delivery
   boards staging.
5. Histories recorded before this change replay as before: the interpreter's new path is behind
   `workflow.patched("train-par-environnement")`. The train needs no marker, because a boarding
   recorded before this change carries no `approval` and computes the same departure as before.

## Consequences

- `default-simple`, `full-auto` and `advanced` name a captain on their production train. Their
  production releases now wait for that approval even when the project policy does not require one.
  This is what the templates always said.
- A batch holding one feature that requires approval and several fixes that do not leaves under
  approval as a whole. Batches are not split by requirement: one deployment in flight per environment
  (ADR 0006) matters more than letting the fixes leave alone.
- The express lane still boards with no labels (`labels: []`), so `express_lane.approval` stays
  unread. `auto_sync` environments (no train) still never tell the work item. Both are filed as
  findings: the GitHub template avoids them by declaring a train for every environment it deploys to.
  *Update 2026-10-10 (S22-17, #279)*: a work item now keeps its labels (`documents.labels`) and
  boards with them, and an express departure takes `express_lane.approval` instead of `approval`,
  still under the requirement of the work items it carries (`patched("express-lane-approval")`).

## Alternatives discarded

- **Approving per work item, not per departure**: the captain would approve the same deployment
  several times, and one refusal could not hold back a batch that has already been promoted.
- **Recording `deployed[env]` on the work item and short-circuiting a later boarding**: when a work
  item is reworked after a release, it would skip its next train. The case it covers (the batch
  leaves between the webhook's boarding and the interpreter's) has a safe outcome without it: the
  work item boards the next batch.
