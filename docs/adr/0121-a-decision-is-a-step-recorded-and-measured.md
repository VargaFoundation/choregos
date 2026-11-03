# 0121 — A decision is a step, answered by a decision model through the gateway, recorded and measured

- **Status**: accepted, 2026-11-03 (product owner: "let's use Laya for now: integrate Laya into the models the LLM
  gateway serves, and integrate the decision part into Choregos")
- **Concerns**: `workflow.schema.json` (a fifth actor, `decision`), `decision-record.schema.json`,
  `choregos_contracts.workflow` (`DecisionActor`), the DSL (`actor_kind`, the validator), the interpreter and
  `local.py`, a `decide` activity, a `DecisionClient` adapter and its fake, the model catalogue's usages (`decide`),
  the gateway's capabilities (`systemone`), the LiteLLM chart and the local install (a self-hosted Laya), a
  `decision_records` ledger and a calibration API; builds on [ADR 0004](0004-cost-counted-at-the-gateway.md),
  [ADR 0005](0005-adapters-and-fakes.md), [ADR 0008](0008-idempotence-and-run-id.md),
  [ADR 0010](0010-gates-are-mechanisms.md), [ADR 0060](0060-a-gateway-agnostic-model-catalogue.md),
  [ADR 0084](0084-guardrails-outside-the-agent.md), [ADR 0106](0106-a-workflow-chooses-its-next-step-from-the-agent-s-answer.md),
  [ADR 0109](0109-an-organisation-brings-its-own-model-gateway.md); related to
  [ADR 0116](0116-a-small-model-may-route-a-work-item.md)

## Context

ADR 0106 lets a workflow choose its next step from an agent's outputs: `when: { outcome: pr }`,
`when: { confidence: { gte: 0.8 } }`. Three things are wrong with how such a verdict is obtained today:

1. **It costs an agent run.** Asking "is it finished?", "which team?", "is it risky?" starts a runner pod, a
   sandbox, a model loop and minutes of wall clock, for an answer of one word.
2. **Its confidence is declared, not measured.** ADR 0106 §4 says the platform does not invent a confidence score;
   the number is the one the agent wrote about itself, and a language model's verbalised confidence is poorly
   calibrated. A threshold of 0.8 rests on nothing.
3. **Nothing tells whether the decision was right.** A human who overrides the branch, a work item that ends
   declined or is reopened: none of it is tied back to the decision that sent it there.

A new kind of model appeared in the autumn of 2026: "System One" or decision models (TypeSafe's Jev, OpenAI's
Decisions API, and some fifty open-weight models). They read a context and answer typed questions — yes/no, one
of a list, a score — each with a probability, in milliseconds, and they do not chat. LiteLLM serves them since
1.104.2 on `POST /v1/systemone` (TypeSafe's wire) and `POST /v1/decisions` (OpenAI's), with the virtual keys,
budgets and spend logs it already applies to chat models. Choregos pins 1.105.5.

Their vendors claim calibrated probabilities; an independent evaluation found Jev calibrated when its benchmarks
are pooled and not within one of them, and Laya's own documentation says its checkpoints are "over-confident as
shipped". A decision model is therefore useful only if its decisions are measured on the organisation's own data.

## Decision

1. **A fifth actor, `decision`**, beside `agent`, `human`, `system` and `job`:

   ```yaml
   actor:
     type: decision
     role: route_ticket
     model: platform/decide                 # a catalogue entry with the `decide` usage
     state: [title, description, outputs.summary]
     questions:
       route:  { type: choice, instructions: "Which team handles this request?", options: [billing, tech, other] }
       urgent: { type: yes_no, instructions: "Must it be handled today?" }
   ```

   - `questions` maps an output name to a question of type `yes_no`, `choice` (with `options`) or `score` (with
     `levels`). Each question writes two outputs: `<name>` (the answer) and `<name>_confidence` (its probability,
     between 0 and 1). They are the step's outputs as an agent's are, so `when` (ADR 0106) branches on them
     unchanged: `when: { route: billing, route_confidence: { gte: 0.85 } }`.
   - `state` names what is sent, and nothing else is: the work item's fields, the last stage's outputs
     (`outputs.<name>`), the work item's title and description. A decision step never sees the repository, the
     memory or a knowledge base.
2. **A decision step runs in the orchestrator, not in a pod.** A `decide` activity:
   - mints a virtual key for the step on the project's gateway (ADR 0109), capped at the step's budget and revoked
     when the activity ends (ADR 0004);
   - masks secrets and runs the injection guard on the `state` before it leaves (ADR 0084: the activity is
     platform code, outside any agent, so it is a legitimate place for a guard; the gateway's own guard is a second
     layer, not the only one);
   - calls `POST {gateway}/v1/systemone` and refuses an answer that leaves a question unanswered, names an option
     it was not offered, or carries a probability outside [0, 1]: the run fails with `reason=invalid_decision` and
     the workflow's repair roles apply;
   - writes the run and its `StageResult` (`outputs` as above, `summary` the answers in one line) with a
     deterministic `run_id` (ADR 0008): a replayed activity neither calls the model twice nor writes twice.
   - **The platform never calls a decision model directly**, always through the gateway: the cost is counted at
     the source, the cap is enforced there, and an organisation's own gateway serves its decisions as it serves its
     agents.
3. **The `decide` usage** is added to the model catalogue (ADR 0060), beside `route` (ADR 0116). A decision step's
   model must be a catalogue entry with that usage; a chat model cannot be named by mistake, nor a decision model
   given to an agent.
4. **The gateway capability `systemone`.** A gateway that serves the decision route declares it; only `litellm`
   does. The validator refuses a `decision` step for a project whose gateway does not declare it
   (`decision.gateway_unsupported`), and refuses a `decision` step whose model lacks the usage
   (`decision.model_usage`), a question name that collides with another output, and a `choice` without options.
5. **The first decision model is Laya**, self-hosted: the multilingual checkpoint
   (`convaiinnovations/laya-multilingual`, mmBERT-base, 322M parameters, 100+ languages) served by `laya-serve` on
   CPU, behind LiteLLM as `platform/decide`. The chart ships it as an optional subchart reachable only from LiteLLM
   (a NetworkPolicy), with an internal price so its spend is comparable (ADR 0004); the local install ships it
   behind a compose profile. A hosted decision model (Jev, OpenAI Decisions) can be added later as another
   catalogue entry, without changing the step.
6. **Every decision is recorded and its outcome observed.** A `decision_records` ledger keeps, per question:
   the work item, the run, the step, the model and its version, the answer, its probability and the transition the
   workflow then took. The outcome is filled in afterwards, when it becomes known:
   - **overridden**: a later human step on the same work item contradicts the decision (sends it back, rejects
     it, or picks another branch);
   - **the work item's terminal outcome** (`success`, `declined`, `cancelled`);
   - **reopened**: the work item came back after it ended.
   The `confidence` an agent declares (ADR 0106) is recorded in the same ledger, so an agent and a decision model
   are measured on the same footing.
7. **Calibration is measured, never declared.** For each workflow, step, question and model version, the
   platform computes from the ledger the reliability bins, the expected calibration error, and the accuracy and
   coverage at each threshold, and suggests the threshold that reaches a target accuracy. The validator warns when
   a `when` reads a `*_confidence` output and the state has no default exit to a human step.

## Consequences

- A verdict that cost an agent run costs a few milliseconds of a CPU model and no pod; a workflow can ask a
  question at every step where it used to ask a person or an agent.
- Existing workflows are untouched: no actor changes meaning, `when` and `select_transition` are unchanged, and
  every archived history replays.
- A deployment without the decision subchart, or an organisation whose gateway is `openai-compatible`, cannot
  declare a `decision` step: the validator says why.
- Laya is over-confident as shipped. Until the ledger has measured it on a workflow, a threshold on its
  confidence is a guess, and the console says so.
- What a decision step sends leaves for the gateway and the model it routes to. With a self-hosted Laya it stays
  in the deployment; a hosted model in another jurisdiction is an organisation's choice, made in its catalogue.

## Alternatives rejected

- **A decision as a prompt agent (ADR 0080) asked for JSON.** It is what exists, and it is the problem: a chat
  model's self-reported confidence, an agent's cost, and no measure.
- **Calling Laya directly from the orchestrator.** Simpler, and it loses the cost counted at the source, the cap,
  the catalogue and an organisation's own gateway. The gateway carries this route already.
- **Jev first.** Hosted in the United States only, early access, method unpublished; an open model served in the
  deployment is the defensible first step, and the ledger will compare the two when an organisation wants Jev.
- **Trusting the vendor's calibration.** Both vendors and the independent evaluation say the opposite: calibration
  is a property of a population, and the organisation's population is its own.

## What would make us change our mind

A ledger showing that, for the questions workflows actually ask, a decision model is no better calibrated than an
agent's declared confidence: the step would remain useful for its cost and latency, and the measure would move to
the agents.
