# 0199 — A fan-out's children are the platform's

- **Status**: proposed, 2026-11-20 (story S84-04)
- **Concerns**: `apps/orchestrator/src/tranibor_orchestrator/activities/children.py` (`spawn_children`),
  `apps/api/src/tranibor_api/services/tickets.py` (`create_work_item`), the tracker writes of
  `activities/tracker.py`

## Context

A fan-out (ADR 0057) opens one child work item per element of a list, keyed `<parent>.<rank>` so that a
retried activity or a replayed parent finds the children instead of opening them twice. The children were
created through `create_work_item`, which files a work item in the project's tracker first when that
tracker is external, and records the TRACKER's key. On a Jira or GitHub project the given key was
silently replaced: a replay looked for `<parent>.<rank>`, found nothing, and filed the children again. On
a project whose tracker cannot create items — a plugin's server over a mailbox or a chat — every child was
refused, so the fan-out could never pass. The documentation said "children need the internal tracker".

## Decision

1. **Born in the platform.** `create_work_item(..., key, in_platform=True)` creates the work item under
   the key its caller gives and files nothing in the project's tracker; `spawn_children` uses it. A child
   is the platform's whatever the project's tracker.
2. **Its tracker writes go nowhere** when the project's tracker keeps its items elsewhere
   (`owns_items`): the state mirror, the status comment and fields, a human request's comment, the
   closing comment and close (`tracker_for`). The tracker never heard of the child. With the internal
   tracker, nothing changes.
3. **The parent stays the tracker's item.** What the children concluded reaches it through the join
   (`documents.children`) and its own mirrors.

## Consequences

- A fan-out works on every project, and its replay guarantee holds on external trackers too.
- On a Jira or GitHub project, the children are no longer issues there: people follow them in the
  console. A workflow that wants an issue per element files it with an action (`work_item.file`, ADR 0197).
- Finding comments on a child's origin (`findings.py`) still go to the tracker; a later story.

## Alternatives discarded

- **Keep filing children in an external tracker and record both keys**: two keys per work item, for a
  copy nobody asked for, and still impossible on a tracker that cannot create items.
- **Refuse a fan-out on an external tracker's project**: the documented limit, which this removes.
