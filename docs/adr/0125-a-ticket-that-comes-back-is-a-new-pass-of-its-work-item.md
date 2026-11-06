# 0125 — A ticket that comes back is a new pass of its work item

- **Status**: proposed, 2026-11-06 — C1 of the D2C plan (`infra/docs/D2C-VULNERABILITES-PLAN.md` §4)
- **Concerns**: `work_items.recurrence` and `recurred_at`, `services/recurrence.py`, the webhook dispatch, the
  HTTP triggers (`reopened`, a new `ReceivedEvent.outcome`), the tracker catch-up (`reconcile_tracker`), the
  interpreter's start payload and `load_context`; builds on [ADR 0008](0008-idempotence-and-run-id.md),
  [ADR 0113](0113-a-workflow-starts-from-a-trigger.md) and [ADR 0121](0121-a-decision-is-a-step-recorded-and-measured.md) §6

## Context

The platform's healthcheck reopens a ticket when a finding it had closed comes back (label `recidive`, H2 of the
D2C plan): "one key, one issue, one work item". The work item had ended — `resolved`, `declined`,
`still_observed` — and nothing restarted it:

- a trigger delivery for its key answered `already_open`, the work item finished or not;
- the tracker catch-up skipped every candidate whose work item was terminal;
- a reopened issue (GitHub `reopened`, a Jira move back to work) only marked the work item's decisions
  `reopened` in the ledger; re-applying `agent-ready` asked Temporal for a run of an id whose last run had
  completed, which `ALLOW_DUPLICATE_FAILED_ONLY` refused in silence.

## Decision

1. **A comeback is a new pass of the same work item**, not a new work item. The ticket's key stays unique per
   project and keeps naming one work item and one interpreter id (`wi-<project>-<key>`, S1-10): every webhook,
   signal, train and console page keeps finding it, and its page tells the whole story. A new work item linked
   to the old one (`recurrence_of`) would need two work items under one tracker key, and every lookup by key —
   the webhooks, the interpreter id, the train — to choose between them.
2. **A comeback is** an ended work item (`closed_at` set) whose ticket is reopened — GitHub's `reopened`,
   GitLab's `reopen`, a Jira move into a status no terminal state maps to, out of one a terminal state maps to
   or one no state of the workflow works in, and never into a status of Jira's Done category — or whose start
   label is applied again, or whose key a trigger delivery names. The catch-up sees one when the tracker dates a
   return to work after the end (GitHub's last `reopened` event, Jira's `statuscategorychangedate` while the
   issue is not Done) by more than a clock margin, and the ticket is not in a terminal state's status.
3. **The pass is claimed in one conditional update**: the work item still ended and still at the recurrence the
   door read becomes recurrence + 1, its state the workflow's start state, `recurred_at` now; `closed_at`, the
   failure, the pause, the pull request and the pass's own documents (`deployed_release`, the last human
   feedback and answer) are cleared. The update writes a `workitem.state_changed` event (`reason: reopened`,
   the pass number, the door, the state and pull request it ended with) and marks the ending pass's decisions
   `reopened`. A door whose update matched nothing — a replayed delivery, a second door, a second catch-up pass —
   starts nothing more (ADR 0008).
4. **The pass is the next Temporal run of the same id**, `ALLOW_DUPLICATE`, started at the start state
   (`resume_from`) with its number (`recurrence`). A run still finishing is past its close-out, since the work
   item is ended: it is terminated (`TERMINATE_EXISTING`) rather than left to swallow the new pass.
5. **A pass runs as a first one would**: its run numbers go on past the earlier passes' (a run id names a run, and
   handing an old one back would read its old result), its attempts budget, ticket spend and last outputs count
   from `recurred_at` (`load_context`: `attempts_before_pass`, marker `fresh-attempts-each-recurrence`). The
   work item's totals, runs, events and decisions keep every pass.
6. **A trigger answers `reopened`** for the delivery that started the pass, and `already_open` while it runs;
   the webhook records the event that started it as `reopened` and does not also deliver it to the new pass.

## Consequences

- `ReceivedEvent.outcome` and `TriggerAck.outcome` gain `reopened` (additive); the console labels it.
- The catch-up reads an ended work item's ticket again while it is a candidate (one tracker read per pass for
  an ended work item whose issue stays open with its start label).
- Not seen: a GitLab or plugin tracker's comeback by the catch-up (the webhook sees GitLab's `reopen`; their
  `fetch_item` dates nothing); a person who moves an ended Jira ticket between two working statuses (not a
  comeback); a branch of an earlier pass left on the repository is checked out again by the runner.
