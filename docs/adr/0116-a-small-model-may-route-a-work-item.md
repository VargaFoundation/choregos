# 0116 — A small model may route a work item to its workflow

- **Status**: accepted, 2026-11-02. Amends [ADR 0031](0031-several-workflows-per-project.md) §2 (the
  order of birth)
- **Concerns**: `projects.routing_strategy` and `projects.routing_hints`, `getWorkflowRouting`,
  `putWorkflowRouting` and `previewWorkflowRouting`, `services/routing.py` (`route`),
  `services/llm_routing.py`, the model catalogue's usages, the console's workflows page; related to
  [ADR 0004](0004-cost-counted-at-the-gateway.md), [ADR 0005](0005-adapters-and-fakes.md),
  [ADR 0008](0008-idempotence-and-run-id.md), [ADR 0060](0060-a-gateway-agnostic-model-catalogue.md),
  [ADR 0092](0092-distillation-is-a-job-and-its-spend-is-booked.md)

## Context

A project carries several workflows (ADR 0031). A work item that names none is born in the first
routing rule it matches (any of these labels, all of these labels, a type), else in the default. On
`fon` the requests come from a tracker whose labels are sparse and inconsistent: a person writes
"new hire starting Monday" with no label, and the request lands in the default. Writing a rule for
every phrasing is not a fix; reading the request is.

## Decision

1. **A routing strategy per project**, `projects.routing_strategy`:
   - `labels` (the default, and the behaviour before this ADR): explicit, then the rules, then the
     default;
   - `rules_then_llm`: explicit, then the rules, then **the model**, then the default — the model is
     asked only when no rule matched, so a project keeps its rules for what they settle and pays
     only for the rest;
   - `llm`: explicit, then the model, then the default; the rules are kept but not read.
   An explicit `workflow` always wins, and the default is always the last resort.
2. **What the model reads.** The work item's title, body (truncated), labels and type; for each
   active workflow, its name, its `metadata.description`, and a **routing hint** written in the
   console (`projects.routing_hints`, one short text of at most 500 characters per workflow name). A
   `PUT` that leaves out `strategy` or `hints` keeps them, so a client written before this ADR does not
   reset them. It answers one JSON object,
   `{"workflow": <name or null>, "reason": <one sentence>}`.
3. **The model cannot invent a target.** An answer that names no active workflow, `null`, a
   timeout, an error, a model the organisation's catalogue refuses, or no gateway key: the work item
   is born in the default. The birth never fails because of the model.
4. **Which model.** The organisation's `cheap` tier (`/admin/models`), else the deployment's `cheap`
   profile, checked against a new catalogue usage, `route` (ADR 0060). It is called on the
   deployment's gateway, as the distiller and the drafter are, until the gateway-agnostic model
   resolution (S39-07) replaces the three call sites together.
5. **Its spend is booked** (ADR 0004, 0092): one cost line `kind="route"`, `agent_slug`
   `workflow-router`, on the project and the work item, in the transaction that creates the work
   item. A preview (`POST /projects/{id}/workflow-routing/preview`) books its line on the project
   alone: it is real spend.
6. **Asked once per birth** (ADR 0008). The decision is kept in the database session by a digest of
   the project, the model, the item and the candidates: checking a birth before creating an external
   issue (`verifier_la_naissance`) and creating the work item ask once. A replay after a rollback
   leaves neither a work item nor a cost line twice. No Temporal workflow is added: the choice happens
   before one starts.
7. **The work item says how it was routed**: `documents.routing` = `{by: explicit|rule|llm|default,
   reason, model}`.

## Consequences

- Routing by labels is unchanged for every existing project: the column defaults to `labels`.
- A webhook delivery waits for the model when no rule matched (capped by a 15-second timeout).
- A curated catalogue must enable the `route` usage on its `cheap` model; until then the model is
  refused and requests go to the default, the reason saying so.

## Alternatives rejected

- **The model as one rule among the rules.** It mixes a deterministic list with a judgement whose
  place in the order is hard to read; a mode per project says it in one word.
- **A Temporal workflow that routes.** The work item, its state and its pinned version are created
  in one transaction by the single constructor (ADR 0031); splitting the choice out would make a
  work item exist before it knows its workflow.
- **The organisation's own gateway (ADR 0109).** Right in the end, but the distiller and the drafter
  call the deployment's; S39-07 moves the three together.

## Amendment (2026-11-03, S52-08): a decision model may route

Measured on 18 of the Fondation's own requests (project `fon`; the numbers are in a comment on PR #1183): `gpt-6-luna`
asked as a decision model was as right as the chat model (18/18). Its probabilities were low exactly
on the ambiguous requests (0.20) and high on the clear ones (0.96 to 1.00). The self-hosted Laya
reached 16/18 at best and was confident when wrong. Hence:

- When the catalogue enables an entry for both `route` and `decide` (ADR 0121 §3), the router asks
  it on `/v1/systemone` instead of asking the `cheap` tier on `/v1/chat/completions`. If several
  entries qualify, the first by name is asked. The question is one `choice`; its options are the
  active workflows, each with its description and hint. The same route carries a self-hosted model
  and an OpenAI one, because LiteLLM translates it.
- A project may set `min_confidence`: below it, the model's choice is not taken and the default takes
  the item, the reason saying so. A chat model's answer carries no probability and is never held to
  it.
- The minimum is compared with the probability of the choice, `probabilities[choice]`, as a decision
  step reads it (ADR 0121 §2), never with the answer's `confidence`: LiteLLM's gpt-6-luna reports the
  margin over the runner-up there, Laya a normalised entropy (#1303). With two workflows the choice is
  always at least 0.5, so a useful minimum sits above it (0.75 keeps a margin of 0.5).
- `documents.routing` and the preview gain `confidence`.

Unchanged: the order (explicit, rules, model, default), asked once per birth, one `route` line in the
costs, an unknown name, an error or a timeout leaves the item to the default. The decisions are not
in ADR 0121's ledger yet (S52-05): until then the minimum is set from samples like this one, not from
measured calibration.

## What would make us change our mind

Measured misroutes: if a project's requests are often moved by hand to another workflow after a
model's choice, the hints and the model tier are the first levers, then an eval of the router.
