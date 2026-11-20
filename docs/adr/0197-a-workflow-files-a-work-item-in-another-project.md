# 0197 — A workflow files a work item in another project

- **Status**: proposed, 2026-11-20 (story S84-02)
- **Concerns**: `apps/api/src/tranibor_api/effects.py` (a third core effect), `ProjectConfig.accepts_items_from`
  (`packages/contracts`), `tests/conformance/templates/test_templates.py`

## Context

A workflow that sorts requests — a desk, a support queue — often ends with "this belongs to that team":
a bug report goes to the backlog of the solution it concerns. Each solution's project already has its
tracker (Jira, GitHub, GitLab, a plugin's), and the platform already files a person's request into it
(`create_work_item`, ADR 0059). A workflow had no way to do the same: its actions call an organisation
connector's operation, and the operations that create an issue differ by tracker (and by MCP server),
so a workflow written for one tracker is useless to the next organisation.

## Decision

1. **A third core effect, `work_item.file`**: `{project, title, body, labels}` files a work item in
   another project of the same organisation through `create_work_item`, so the target project's tracker
   receives it exactly as it receives a person's request. It answers `{project, key, url, work_item_id}`.
2. **The target opens the door.** The target project lists the source projects it accepts in its
   config's `accepts_items_from`; any other source, the same project, an unknown project: refused, nothing
   filed. A workflow does not write into a project that did not agree to it.
3. **A person decides.** Its policy is `approval`, like an extension effect: the action waits for a
   person, whoever wrote the workflow.
4. **Filed, not started.** The work item is born in the target project's routing, labelled
   `from:<source>`, its body naming the work item that filed it; the target's people or start rules
   start it. The source's workflow does not decide what another project runs.
5. **Idempotent under the effect's key** (ADR 0008): the work item's id is derived from it, so an effect
   played again finds what it filed. An external tracker's issue is created before that row, as for a
   person's request: an activity that dies between the two files the issue again when replayed.

## Consequences

- A workflow files "into the solution's project" without knowing its tracker: the same template serves
  an organisation on Jira and one on GitHub.
- Template conformance admits `work_item.file` beside `connector.call` and `check`.
- The window of point 5 is the one a person's request has; closing it would need each tracker to find
  an issue by a key the platform sets, which not all do.

## Alternatives discarded

- **A `create_issue` operation per tracker connector**: the operations and their arguments differ by
  tracker and by MCP server; the workflow would name one.
- **A spawn into another project**: a fan-out's children belong to the parent's project and its join
  waits for them; a filed bug has its own life.
- **Starting the filed work item**: the source would run a workflow in a project it does not own.
