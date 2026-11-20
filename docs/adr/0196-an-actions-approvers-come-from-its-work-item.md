# 0196 — An action's approvers come from its work item

- **Status**: accepted, 2026-11-20 (decided by the project lead at merge; story S84-01)
- **Concerns**: `packages/contracts` (`ActionApprover.group`, `ActionApprovalSpec.excluded`, the workflow
  schema, `openapi.yaml`), `apps/api/src/tranibor_api/services/actions.py`, the DSL validator

## Context

A governed action's approval names a rank and, since ADR 0052, an identity-provider group the decider
must also belong to. Both are written in the workflow, so one workflow has one set of approvers. A
workflow that grants access to many tools — each with its own owners — can only name a rank that covers
them all: every grant then waits for the same people, usually the project's owners, who become the
bottleneck the workflow was meant to remove.

The separation of duties of ADR 0035 excludes the proposer, or the owner of the proposing agent. A
transition's action is proposed by the platform (`system`), so it excludes nobody: the person an access
grants, or who asked for it, may approve it if their rank allows. For an access grant that is exactly
the person who must not decide.

## Decision

1. **An approver group may read one field.** In a workflow's action, `approvers[].group` may hold one
   `{{ fields.<name> }}` beside literal text and the `{project}` placeholder: `owners-{{ fields.tool }}`.
   It is rendered when the action is proposed, with the context of the title and the params, and the
   action stores the rendered group. The rank is still asked: the group is a second condition, as in
   ADR 0052.
2. **An action names who may not decide.** `approval.excluded` lists up to ten identities — an e-mail
   address, or exactly one `{{ fields.<name> }}` — rendered the same way. An excluded person may not
   decide or settle the action, whatever their rank: an organisation administrator is no exception.
   An exclusion that renders empty excludes nobody.
3. **Fail closed.** A group whose field is absent or empty refuses the proposal: the transition follows
   its `on_fail`. A rule nobody could satisfy, or one that `owners-` alone could, is never presented.
4. **Declared fields only.** The validator refuses any other template in an approval (a filter, two
   fields, a field beside text in an exclusion) and a field `metadata.inputs` does not declare.
5. **Actions proposed elsewhere** (the API, the broker) keep their literal rules; `excluded` is accepted
   there as addresses.

## Consequences

- One workflow routes each grant to the owners of what it grants, and keeps the person it concerns and
  the person who asked out of the decision.
- A field is data the work item carries, sometimes written by an agent from text someone outside the
  platform wrote. Keeping a literal prefix limits what it can choose to the groups the organisation
  named that way; the rank is still asked; the excluded people are rendered from the same work item.
  The workflow's author decides which fields are trusted enough to name approvers.
- The stored approval is the rendered one: the console and the audit show the group and the people
  that held when it was proposed. A field changed later does not move a pending action.

## Alternatives discarded

- **The organisation's per-operation `approver_group`** (ADR 0052): one group per operation, so one per
  `add_to_group`, whatever the group added to.
- **A free Jinja expression**: a filter or a condition in an approval rule is code no reviewer reads at
  decision time; one declared field is enough.
- **Excluding the work item's reporter automatically**: the reporter of a trigger's work item is the
  platform; the workflow knows which field holds the requester.
