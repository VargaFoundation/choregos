# 0153 — A human step may be addressed to a person

- **Status**: proposed, 2026-11-12; S66-02 implements it
- **Concerns**: `packages/contracts` (`HumanActor`, `workflow.schema.json`, `openapi.yaml`),
  `packages/core` (validator, process view), `apps/api` (`services/human_assignee.py`,
  `routers/workitems.py`, `human_requests.assignee`), `apps/web` (the work item's awaited decision)
- **Amends**: [0044](0044-people-are-declared-in-the-workflow.md) (who answers a human step)

## Context

A workflow's human actor names only a group: `{type: human, group: release-captains}`. Who may answer
is enforced where a person decides (ADR 0044, ADR 0052): a member of the group, or an organisation
administrator overriding, audited. Two needs are not met:

- **a step for one person**: a manager's approval, an owner's sign-off. The workaround is a group of
  one in the identity provider, which nobody maintains and which outlives the person's role;
- **a waiting step whose addressee is away**: today the only way out is the administrator's override,
  which answers in the administrator's name instead of handing the decision to the right person.

## Decision

1. **`HumanActor` gains `user`** — an e-mail address, matched without regard to case — and `group`
   becomes optional. At least one is required (the model's validator and the schema's `if`/`then`):
   an actor naming nobody is refused.
2. **Semantics.** `group` alone: unchanged. `user` alone: only that person answers. Both: the person
   is asked first; once the request's SLA has passed (its `due_at`: `sla_hours`, 24 h by default) a
   member of the group may answer too, and the person still may. The rank (`item:decide` on the
   project) stays necessary in every case.
3. **An organisation administrator may always answer**, audited as today
   (`workitem.decision.group_override`), the payload naming the `group` and now the `user` it
   bypassed, and `reassigned` when the request had been reassigned.
4. **An organisation administrator may reassign a waiting request**:
   `POST /work-items/{id}/human-requests/{request_id}/assignee` with `{user?, group?, reason}`. The
   person must be a member of the organisation; `{project}` in the group names the work item's
   project. The new assignee is stored on the request row (`human_requests.assignee`, nullable JSON)
   and replaces the workflow's actor **for that request only**; the workflow does not change. Audited
   as `workitem.request.reassign` with `before`, `after` and `reason`. A decided request is a 409.
5. **The request shows who it waits for**: `HumanRequest.assignee` (`user`, `group`, `reassigned`),
   read from the override when there is one, else from the workflow's actor at the request's
   transition. The console shows "Assigned to …" on the awaited decision, and "Reassign" to an
   organisation administrator.
6. **Validation in a project warns** (`actor.user_unknown`) when `user` is not a member of the
   organisation, as `actor.group_unknown` does for a group nobody carries.
7. **A train's approver names a group** (`actor.train_approver_group`): a release train's departure is
   approved by a group, and a `user` alone would have been silently ignored there.
8. **Backward compatibility.** An absent `user` is left out of the stored spec (`exclude_none`), so a
   group-only workflow keeps its digest and every work item pinned to it still finds it.

## Consequences

- A step can be addressed to a person without an identity-provider group of one.
- A step whose addressee is away is handed on, with a reason in the audit log, instead of being
  answered by the administrator.
- The decision check reads the request's override before the workflow; the orchestrator does not
  change, so nothing replays differently.

## Limits

- The person is identified by e-mail, as the platform knows its users; renaming an address leaves the
  workflow naming the old one (the validation warning says so).
- Notifications still go to the project's channels; nobody is notified personally when a step is
  addressed or reassigned to them.
- The project's readiness page does not list a person who is not a member; the workflow validation
  does.
- A reassignment is not undone by a gesture of its own: a second reassignment replaces it.
