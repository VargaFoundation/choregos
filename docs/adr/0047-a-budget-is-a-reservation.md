# 0047 — A budget is a reservation

- **Status**: accepted, 2026-10-12
- **Concerns**: the runner, the interpreter, `prepare_stage`, the cost ledger, the policy contract;
  builds on [ADR 0004](0004-cost-counted-at-the-gateway.md) (one virtual key per run) and
  [ADR 0014](0014-a-platform-held-tool-catalogue.md) (tool calls in the same ledger)

## Context

Every run has had a virtual key with a hard cap since ADR 0004, and the policy declares budgets per
ticket, per stage, in turns and in minutes. Yet an agent could burn far more than any of them
(plan 2026-10-12, items 1 and 6):

1. the runner never said `reason="budget"`: a gateway refusal ended as `agent_silencieux` or
   `invalid_result`, two reasons the workflow **retries** — with a fresh key at the full stage
   budget;
2. each key was minted at the full stage budget, whatever the ticket had left; the ticket budget
   was checked *after* the stage;
3. the ticket's spend lived in workflow memory: a restarted interpreter started again at zero;
4. "Replay the stage" reset the attempts, and with them the retry budget;
5. tool-call costs were not in the ticket counter; `max_turns` was only written in the prompt;
   `attempts.default_max` was read by no one; nothing capped a project's day, an organisation's
   month, or the number of stages a ticket may run.

Worst case: stages × attempts × stage budget, with each human click starting over.

## Decision

1. **A budget is a reservation, not a report.** A run's key is minted at what is *left*:
   `min(stage budget, agent run budget, ticket remaining, project day remaining, organisation month
   remaining)`. What is left is the cap minus what the ledger says was spent minus the keys still
   open. Below a floor, the stage does not start.
2. **A refusal is a stop, never a retry.** The runner recognises the gateway's budget refusal (its
   error, its error output, or the text the agent relays) and ends the run with
   `reason="budget"`. The interpreter sends such a stage to `on_budget_exceeded`, else
   `on_question`, else the retry policy's `escalate_to` — never back to the stage.
3. **The ledger is the counter.** A ticket's spend is read from the cost ledger (models and tools),
   not from workflow memory; it survives a restart, a `continue_as_new` and a replay.
4. **More money is a decision.** Resuming a parked ticket keeps its spend; a person who wants it to
   go further grants a stated top-up, which the audit log records.
5. **Ceilings nest.** The organisation sets a ceiling; a project policy may only tighten it; a
   budget given at launch may only tighten the project's.
6. **Guardrails are enforced, not prompted.** The runner cuts a run at `max_turns` and on a loop;
   the interpreter refuses a stage beyond `max_stages_per_ticket`; an agent has a monthly cap with
   a hard stop and a kill switch.

## Consequences

- An over-budget ticket lands in `needs_human` with the reason `budget`, after one run, and the
  overspend is bounded by one key — the reservation — instead of stages × attempts × stage budget.
- Workflow changes are guarded by `workflow.patched` markers; histories recorded before them
  replay their old decisions.
- A refusal the gateway words differently from LiteLLM would not be recognised: the signatures
  live in `choregos_runner.budget` and a new gateway adds its own there.

## Addendum: the monthly cap and the reservation (S27-09)

Decision 6 gives an agent a monthly cap (`AgentBudget.monthly_usd`). It is a cap like the others
and enters the reservation like them: the run's key is minted at the minimum of what is left under
each cap, and the monthly cap contributes `agent_month = monthly_usd - spend of the calendar month`.
A run starting with USD 0.30 left on the month therefore gets a key of USD 0.30, and the gateway
refuses beyond it (`reason="budget"`, to a person, never a retry).

- Alerts: `month` alerts at 50, 80 and 100 %, once per (agent, month, threshold). Slack is told
  once per alert (`notified_at`); a notifier failure leaves the alert unsent, and the next
  collection retries it.
- At 100 % the agent is suspended (audit action `agent.budget_suspend`) and a new run is refused
  at admission with a non-retryable `admission_refused`.
- Limit, stated plainly: like `agent_day`, `agent_month` subtracts what has been spent, not what
  the agent's other open keys may still spend. Two concurrent runs of one agent can each be
  minted the whole remainder, so the month can be overshot by the keys in flight at the
  moment. The 100 % suspension bounds the overshoot to those runs; it does not remove it.
  Closing it means summing the agent's open reservations, as the ticket cap does for tickets.

## Addendum: serialized per scope, every scope subtracts its open keys (S27-11)

The limit stated above is closed.

- `prepare_stage` locks the scopes it reserves from (the work item, the project when it has a day
  budget, the organisation when its ceiling has `monthly_org_usd`, the agent when it has a day or
  month cap) until its transaction ends, in a fixed order. PostgreSQL: `pg_advisory_xact_lock`;
  SQLite (dev, tests): an in-process lock released when the transaction ends. A second stage
  therefore reads the first one's `reserved:<run_id>` row instead of the same remainder, and is
  refused below the floor like any other.
- The organisation's month is a new cap, `budgets.monthly_org_usd` of the policy ceiling: what all
  its projects may spend in a calendar month. Its remainder is the cap, minus the ledger of the
  organisation's projects since the first of the month, minus the open keys of those projects.
- `agent_day` and `agent_month` subtract the open keys of the agent's runs too.
- Cost: the lock is held until `prepare_stage` commits, which includes building the context pack.
  Stages of one organisation contend only when a scope they share has a cap.
