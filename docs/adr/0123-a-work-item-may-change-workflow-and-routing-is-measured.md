# 0123 — A work item may change workflow, and the routing that chose it is measured

- **Status**: accepted, 2026-11-05 (product owner, asked where a wrong routing would be seen: "add re-routing")
- **Concerns**: `decision-record.schema.json` (`run_id` optional, a `router` source), the work item's actions
  (`reroute`), the interpreter's human wait, the decision ledger (`decision_records`), a routing calibration API
  and the ticket page; amends [ADR 0116](0116-a-small-model-may-route-a-work-item.md) and
  [ADR 0121](0121-a-decision-is-a-step-recorded-and-measured.md); builds on
  [ADR 0008](0008-idempotence-and-run-id.md) and [ADR 0031](0031-several-workflows-per-project.md) (`migrate`)

## Context

Since 0.22.2 a decision model routes fon's work items in production (ADR 0116 as amended by S52-08): it answers
one `choice` question, the workflow, with a probability, and below the project's `min_confidence` the default
workflow takes the item. The threshold, 0.75, was set on eighteen cases written by hand.

ADR 0121 built the instrument that should tell whether 0.75 is right: a ledger of decisions, the outcome observed
after each, calibration measured from them. The routing does not write to it. Two things stand in the way:

1. **A routing has no run.** The ledger's records hang from a run (`run_id` required, in the contract and in the
   table) and a routing happens at the work item's birth, before any run.
2. **Nothing would observe a wrong routing.** No gesture changes a work item's workflow after its birth: a person
   who sees an access request land in the support workflow can only cancel it, or handle it there. Neither says
   "the routing was wrong", and a work item that ends well in the wrong workflow says the opposite.

## Decision

1. **A work item may be re-routed.** `POST /work-items/{id}/actions` with `action: reroute` and `workflow: <name>`
   moves the work item to the active definition of another workflow of its project, at that workflow's initial
   state. It is ADR 0031's `migrate`, with the mapping `current state → initial state` computed by the API, so the
   interpreter's path and its replay guarantees are the ones that exist. It needs the role that controls a work
   item, is audited (`workitem.reroute`, with the reason), and:
   - **is accepted while a human step waits**: that is when a person sees the misrouting. The interpreter closes
     the pending request as *superseded* — not a verdict, so it judges no decision — and migrates;
   - is accepted on a parked work item, and on one whose interpreter has not started (the pin and the state
     change in the database, and the interpreter will load them);
   - **is refused (409) while a run is in progress** — an agent may be acting in the wrong workflow; stop it or
     let it finish — and on an ended work item, and to its own workflow.
2. **A routing is a decision record.** When a decision model routed a work item (it answered a workflow with a
   probability), one record is written with the work item, in the same transaction: `source: router`,
   `step: routing`, `question: workflow`, `run_id: null`, `answer` the model's choice, `probability` its
   probability, `workflow` and `transition` the workflow the work item was given (the choice, or the default when
   the choice fell below the threshold). An explicit workflow, a rule and a chat model (no probability) write
   nothing. One record per work item: a unique index on `(work_item_id, question)` for the records without a run.
3. **The routing's outcome is the workflow it should have had.**
   - The first re-route fills `overridden`: `true` when the model's answer is not the workflow a person chose.
     Re-routing to the model's answer after the threshold sent the item to the default is therefore a *right*
     answer — the threshold, not the model, was wrong — and the calibration says so.
   - A work item that ends without a re-route fills it with the answer against the workflow it ended in: no one
     moved it, so that workflow was acceptable.
   - A work item cancelled without a re-route is left unobserved: cancelling may be the correction.
   - `terminal` and `reopened` are kept but do not judge a routing: a declined access request was not misrouted.
   - A human step's verdict (`record_human_verdict`) never judges a routing record.
4. **Calibration of the routing**: `GET /projects/{id}/routing/calibration`, the measure of ADR 0121 §7
   (reliability bins, ECE, accuracy and coverage per threshold, a suggested threshold) over the project's routing
   records, beside the project's `min_confidence`. A step's calibration no longer counts routing records.

## Consequences

- The `routing` step name is reserved in the ledger by the source, not by the DSL: a workflow state named
  `routing` keeps its own calibration, which filters on the decision sources.
- Re-routing is useful without a decision model: a project routed by rules or labels gains the gesture too; only a
  routing by a decision model is measured.
- Not observed, and said in `docs/usage.md`: a work item moved by hand in its tracker, a person who handles a
  misrouted item in the wrong workflow without re-routing it (read as agreement), a re-route after the end.
- Changing the workflow of a running agent is not offered: the run is stopped or finished first.
