# 0101 — Governance the platform does not enforce is refused when written, not warned

- **Status**: accepted, 2026-11-01
- **Concerns**: `choregos_core.dsl.validator` (`_check_unenforced_review`), `choregos_core.policy`
  (`unenforced_governance`), `PUT /projects/{id}/policy`, project creation, the `advanced` template;
  amends [ADR 0044](0044-people-are-declared-in-the-workflow.md)

## Context

[ADR 0044](0044-people-are-declared-in-the-workflow.md) kept `approvals.*` and
`review.require_human_for_risk` valid but logged a warning when a policy set them. A warning nobody
reads still leaves a document that promises a guard Choregos does not apply. The same hole exists in
workflows: `review.humans` has no reader, and `review.agents` was read only by the validator, which
counted it as a use of the actor and so hid `actor.unused`. Nothing schedules a review agent: only a
transition's `by` actor runs. The shipped `advanced` template promised a maintainer review it never
asked for.

## Decision

A key the platform does not enforce is refused when it is written.

| Field | Refused | Not enforced because | Fix |
|---|---|---|---|
| `approvals.spec\|merge\|prod\|scope_change` (not `never`) | policy PUT, project creation | no one is asked | a human actor on a transition, or `train.approval` |
| `attempts.default_max` | policy PUT, project creation | a transition's attempts are its `max_attempts`, lowered by `attempts.by_role` | remove it |
| `review.require_human_for_risk` (non-empty) | policy PUT, project creation | no one is asked | a human actor on a transition |
| `review.humans` (not `required: never`) | workflow validation | no reader | a human actor and a transition it answers |
| `review.agents` entry other than `by` | workflow validation | never runs | give it its own transition |

The refusal is a `422` that names each key and the fix. `review.agents` no longer counts as a use of an
actor, so an actor named only there also gets `actor.unused`.

Tolerant paths stay tolerant. A stored workflow whose JSON lacks `initial` is re-read from its YAML
with `strict=False`: it passed the rules of its day and must keep loading when a rule is added.
`ensure_defaults` refuses only when called with `refuse_unenforced=True` (project creation); the lazy
calls from read paths log as before, so a GET never answers `422`. A plugin template (ADR 0093) takes
the same project-creation path and is refused the same way, with its `plugin:` reference in the error.
`parse_policy` and the contract schema still accept the keys, and `policy_warnings` is unchanged.

`advanced` drops its `review.humans` and becomes version 3, so readiness shows a template update, not
an unexplained drift.

## Rejected

- **Enforcing the keys in the interpreter.** It would add a second place where humans are asked,
  next to the workflow's human actors that ADR 0044 made the only one.
- **Refusing in `parse_policy` or the schema.** Stored policies and workflows would stop loading.

## Consequences

Stored documents that still carry these keys are not migrated: they keep loading, and editing them
requires removing the keys. Refusing such a template at plugin install is not done here: the plugin
loader validates templates against the JSON schema only.
