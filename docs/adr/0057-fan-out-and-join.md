# 0057 — Fan-out and join: a transition opens child work items and waits for them

- **Status**: accepted, 2026-10-14
- **Concerns**: the workflow contract (`packages/contracts`, `workflow.schema.json`), the DSL
  validator, the orchestrator's interpreter and its `close_out`; builds on
  [ADR 0008](0008-idempotence-et-run-id.md) (idempotence) and
  [ADR 0056](0056-a-deterministic-step.md) (a deterministic step)

## Context

A work item was a single line of work. A campaign that reviews ten products, an onboarding that
opens one request per account, a release that checks every service: each needs one unit of work per
element of a list, each followed on its own (its own owner, its own decision, its own audit), and a
parent that resumes only once they have all concluded. Nothing did this. `FindingsTriage` opens work
items but does not wait for them; a template that wanted a join had to poll from an agent, which
costs a model and is not reproducible.

## Decision

1. **A `spawn:` block on a SYSTEM transition**: `{workflow, for_each, title, fields?, max_children?,
   succeeded?, min_succeeded?}`. `for_each` is a path, `fields.<name>` or `outputs.<name>[.<key>…]`,
   to a list the parent holds; `title` and `fields` are rendered in sandboxed Jinja with `item`,
   `rank`, `fields`, `outputs` and `parent`, and a string that is one `{{ … }}` keeps its type. The
   transition does nothing else (`spawn.not_alone`) and declares `on_fail` (`spawn.on_fail_missing`).
2. **One child work item per element, in the workflow named**, through the same constructor as any
   internal work item (`creer_un_ticket`): its fields are validated by the child workflow's
   `metadata.inputs`. `max_children` (default 20, at most 100) bounds the list; a longer list, an
   absent path, a child refused, are a refusal that follows `on_fail` — retrying would be refused
   the same way.
3. **Idempotent by key**: the child's key is the parent's with its rank (`RESEARCH-12.1`). A retried
   activity, a parent replayed after a crash, a migration, find the children already there and open
   none twice; their interpreters are started with a deterministic identifier, so a second start is
   refused by Temporal.
4. **The join waits for every child to be SETTLED** — closed, in a terminal state or stopped. A child
   keeps its parent in `documents.parent`; `close_out` signals `child_settled` to the parent after
   its commit. The parent also re-reads its children every 30 minutes, so a lost signal delays the
   join, never blocks it.
5. **The join writes `documents.children` on the parent** — total, count per state, each child's key,
   title, final state and fields — so a later step reads what the children concluded. A child ending
   in a state listed in `succeeded` counts as a success; fewer than `min_succeeded` fails the
   `children_settled` gate.
6. **Behind the `fan-out-and-join` patch marker**: no history before it holds a `spawn:`, and the
   marker is only read when a transition holds one.

## Consequences

- A parent waits as long as its slowest child: a child with no SLA can hold a parent forever. The
  child workflow's human actors carry the SLA; the parent says how many children are still open in
  its status.
- Children are work items like any other: they appear in the inbox, have their own audit and their
  own budget. The parent's budget does not cover them.
- The local mode does not run a fan-out: it stops at the transition and says so, rather than let it
  pass without a child.
- Only an internal tracker can hold children: a project whose work items come from an external
  tracker is refused at the first child.
