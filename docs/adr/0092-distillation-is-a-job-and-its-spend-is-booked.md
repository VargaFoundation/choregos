# 0092 — A distillation is a job (stop, relaunch, monitor) and its spend is booked in the cost ledger

- **Status**: accepted, 2026-10-26
- **Concerns**: `routers/knowledge.py`, `knowledge/distill.py`, the `KnowledgeDistill` workflow, the
  `distillation` record of a knowledge base, `CostLedger`, `routers/costs.py`, the console component
  `knowledge-distill.tsx`; extends [ADR 0082](0082-distillation-in-passes.md); keeps
  [ADR 0008](0008-idempotence-and-run-id.md) (idempotent activities) and
  [ADR 0026](0026-what-the-core-rls-covers.md) (what the RLS covers)

## Context

ADR 0082 made a distillation a Temporal workflow that can spend real money (up to the platform
budget) for minutes. Three gaps remain, all reported by the product owner:

1. a running distillation cannot be stopped (the `cancelled` code exists, no route raises it), and a
   failed one can only be started again from scratch by hand;
2. what a run is doing and what it cost live only in the base's last `distillation` record, which the
   next run overwrites: nobody can compare the last two runs;
3. the spend never reaches the cost tracking: `CostLedger` has no line for it, so the organisation's
   cost view, its exports and its budgets do not see a distillation at all.

## Decision

1. **Stop** (`POST …/distill/stop`): the API signals the workflow to cancel; the run ends through
   `fail_distillation` with `status: stopped`, `code: cancelled`, `stopped_by`. The model call in
   flight finishes (it cannot be recalled) and is booked; no further call is made; nothing is
   published. When there is no Temporal server (fakes, tests) the in-process run checks a stop flag
   in the record before every call. A run that is not running answers 409.
2. **Relaunch** (`POST …/distill/relaunch`): starts the same distillation again from `failed`,
   `stopped` or a finished (`pending_review`…) record. It keeps the last run's `exclude` and
   `max_usd` unless a body replaces them (both are now kept in the record). No special resume logic:
   every call is stored by the digest of what it read (ADR 0082 §2), so a relaunch pays only for the
   parts that never finished. Same rights, same 409 when one is running.
3. **Monitor**: the record already carries phase, parts done/total, spend, `requested_by`,
   `started_at`, heartbeat. It gains `stopped_by`, `exclude`, `max_usd` and `history`: when a run
   starts, the previous record (minus its own history) is pushed on `history`, newest first, five
   runs kept. The console shows the current run, the previous outcome and the history, and polls
   while one runs. The history is a window, not an audit trail: the audit log keeps who started and
   stopped (`knowledge.distill`, `knowledge.distill.stop`).
4. **The spend is booked in `CostLedger`**, one line per model call that was actually made (a part
   call, a merge group): `kind = "distill"` (a distinct kind, so `GET …/costs?kind=distill` and
   `group_by=kind` separate it from agent models and tools), `agent_slug = "knowledge-distiller"`,
   the model, the tokens and the priced cost (an unpriced model books 0, as the budget counts it).
   The line is written **in the same transaction as the stored part**, so a replayed or retried
   activity that finds the part stored books nothing, and a relaunch that reuses parts books only
   what it spent. This is what makes the figure honest: reuse is free in the ledger as it is in
   money.
5. **Attribution**: to the base's `owner_project` when it names a project of the organisation (its
   spend then counts in that project's views, and in its daily budget like any other line);
   otherwise to the **organisation**. `CostLedger.project_id` becomes nullable and a nullable
   `org_id` is added: exactly one of the two is set. No hidden "system project" is created: it
   would appear in project lists, carry a policy, and invite code that treats it as real.
   - The organisation's cost report counts lines of its projects **and** its own (`org_id`);
     an organisation-level line shows under `—` when grouped by project. The project report never
     sees it.
   - Budgets are per project (`daily_project_usd`): an organisation-level line is in the
     organisation's totals but charged to no project budget. The platform budget of the run
     (`knowledge_distill_budget_usd`, the requester's cap) remains the only brake on such a spend.
   - The RLS policy of `cost_ledger` judges the organisation from the project, or from `org_id` when
     there is no project (`COALESCE`): a line of one organisation is invisible to another's session.
   - A base whose `owner_project` names a project that no longer exists falls back to the
     organisation.

## Consequences

- The cost views that group by project, agent or workflow handle a null project: the key is `—`.
  Consumers that join `projects` (per-agent reports, web-search budgets) never count these lines,
  which is right: they are about runs and projects.
- A migration changes a hot table (`cost_ledger`): one nullable column, one dropped NOT NULL, one
  replaced policy; no rewrite, no backfill.
- A stopped run that spent part of its budget keeps that spend in the ledger: stopping saves what has
  not been called yet, it does not refund.
- The in-flight call of a stopped run is the one unavoidable overshoot (at most one call, bounded by
  the part size).
