# 0020 — Trajectory evals in the nightly matrix

- **Status**: proposed, 2026-09-25 — not implemented
- **Concerns**: `EvalMatrix` (orchestrator), `choregos_playbooks.evals.runner`, the eval cases

## Context

The nightly `EvalMatrix` runs backend × model × memory on a project's fixtures and scores
each case on its **result**: `must_contain`, a forbidden list, `min_score` (default 0.8),
optionally an LLM judge (`judge_case`). It says whether the agent arrived. It says nothing
about how.

The bench of 2026-09-24/25 found every defect that mattered in the *how*: five result
repairs in eight runs, ninety-one permission decisions with zero refusals because the
guardrail fell open, ten catalogue listings and zero tool calls, a stage "over budget" that
never ran a pod. Not one of those facts is a result. All of them are in the journal:
`session/request_permission`, `tool.called`, `result.repair`, `gate.outcome`,
`cost.recorded`, `choregos.security.injection_suspected`.

## Decision

1. **A case may declare trajectory expectations**, scored from the run's journal, next to
   the result expectations it already has:

   ```yaml
   trajectory:
     must_call: [verifier_adresse]      # from the registry, as gate `tool_called` reads it
     max_tool_calls: 40
     max_repairs: 0                     # `result.repair` events
     out_of_scope_writes: 0             # guardrail decisions `reject` or `reverted`
     max_cost_eur: 0.50                 # ledger, never the agent's claim (ADR 0004)
     no_injection_suspected: true
     must_end_in: done                  # the state the ticket reaches
   ```

2. **Scoring is a mechanism** (ADR 0010): each line is a predicate on journal rows, gate
   outcomes and ledger rows, computed by the same code that computes the gates
   (`choregos_core`), never by a judge. The LLM judge stays where it is: on prose.
3. **The matrix reports two numbers per cell**: result score and trajectory score, and
   publishes both. A backend that arrives by writing outside its scope scores 1.0 on result
   and fails on trajectory, and the report says which line.
4. **Bench histories become cases.** The redacted histories in `tests/replay/histories` are
   the first fixtures: `RH-1f` (never admitted) and `DEMO-1e` (401) are negative cases the
   matrix must classify as platform failures, not agent failures — `failure_owner:
   platform` in the case.

## Conditions

- A trajectory score needs a real agent; the nightly matrix runs only where a gateway key
  exists (`live` tests). Without it, the predicates are unit-tested on recorded journals and
  the matrix says "not run", never "passed".
- `must_call` names tools of the registry; the case validator refuses a name the catalogue
  does not know, as the workflow validator does for gates.

## Consequences

- The question "which backend follows the rules" gets an answer per week, not per incident.
- A change to the context pack or the guardrails has a measurable effect on repairs and
  refusals, in the same report.

## Alternatives discarded

- **Trajectory judged by an LLM** ("did the agent behave?"): a prompt is not a guarantee
  (ADR 0010), and the facts are already structured.
- **A separate eval product**: the matrix, the journal and the ledger exist; the work is the
  predicates and the report, not a platform.
