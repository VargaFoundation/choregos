# 0059 — A work item filed in Choregos is born in its tracker

- **Status**: accepted, 2026-10-15
- **Concerns**: `POST /projects/{id}/work-items` (`createWorkItem`, `packages/contracts`), the MCP tool
  `create_work_item`, `TrackerAdapter.create_item`; builds on [ADR 0030](0030-an-mcp-door-for-external-clients.md)
  and [ADR 0031](0031-several-workflows-per-project.md)

## Context

A person could file a work item from the console or MCP only when the project's tracker was
internal; with GitHub, Jira or GitLab the API answered 409 "create the request there" and the board
hid its button. Filing in the tracker by hand loses what the platform adds: the workflow choice, the
creation inputs (`metadata.inputs`) and the budget given at launch, none of which fit in an issue
form. Yet the adapters already create issues (`create_item`), and the orchestrator already
files a promoted finding that way, then records the work item under the issue's key.

## Decision

1. **One path, any tracker.** `creer_un_ticket` (REST and MCP alike) asks `tracker_du_projet` for the
   tracker's adapter. Internal or absent tracker: the platform mints the key, as before. External:
   it calls `create_item`, then records the work item under the returned key (its mirror, exactly as
   if the webhook had brought it), and starts the interpreter.
2. **The issue carries `source:choregos` and never `agent-ready`.** The webhook (`issues.opened`) and
   the reconciliation only start an item labelled `agent-ready`; if the issue had it, the webhook
   could create a second work item for the same key before this transaction commits. Without it both
   ignore the issue until the work item exists; later events for the key reach its interpreter as usual.
3. **Validate first.** Workflow, fields and budget are checked (`verifier_la_naissance`) before the
   tracker is called: a 422 leaves no issue behind.
4. **Body footer** `<!-- choregos:origin -->Filed from Choregos by <email>`. The fields stay in the
   work item, the source of truth for the workflow; they are not mirrored into the issue.
5. **Failure modes.** The tracker refuses or is unreachable (`AdapterError`, `SecretIntrouvable`,
   `httpx.HTTPError`): 502 `problem+json` naming the tracker, nothing created. The database fails after the
   issue was created: an orphan issue without `agent-ready` remains, a plain issue a person can
   label; it is not cleaned up.
6. **Permissions unchanged**: `ITEM_CONTROL`. The platform writes with its own credentials (the
   GitHub App needs *Issues: read and write*), and the footer names the person.

## Rejected

- Keeping the 409 and linking to the tracker's "new issue" page: loses inputs, workflow and budget.
- A Choregos-only item with an internal key on a GitHub project: two sources of truth, and the
  interpreter would call `set_state` on a key the tracker does not know.
- Creating the issue with `agent-ready` and letting the webhook create the item: asynchronous, and
  loses fields, budget and workflow.

## Consequences

`createWorkItem` gains 422 and 502 and loses 409. The `TrackerAdapter` docstring now says `create_item`
also files a person's request. The console shows its button on every project (S39-16 PR B).
