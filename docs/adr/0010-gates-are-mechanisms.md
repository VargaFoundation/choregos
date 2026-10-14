# ADR-0010 — A guarantee is a mechanism, never a prompt

- **Status**: accepted
- **Concerns**: S1, S2, S3

## Context

It is tempting to write "do not modify files outside the scope" in a prompt and consider
the problem solved. A model follows an instruction most of the time; "most of the time" is
not a guarantee.

## Decision

Every property we want to guarantee has a **mechanism** that checks it, independent of the
prompt:

| What we want | The mechanism |
| :-- | :-- |
| the agent stays in its scope | ACP permission refused, **then diff check and revert**, **then** the `scope_respected` gate |
| the tests really pass | the runner **executes** the repository's commands and overwrites declared evidence |
| no secret committed | `no_secrets` on the diff + gitleaks in CI |
| no budget overrun | hard cap on the run's virtual key |
| production is reached only by the train | DSL validation rule (`prod.requires_train`) |
| the agent has no cloud access | no credential + NetworkPolicy + shims that refuse |

The prompt is there to obtain good behaviour; the mechanism is there so that bad behaviour
does not get through.

## Consequences

- An agent that attempts a write outside its scope receives a **reasoned** refusal that
  reminds it of the legitimate tools (`report_finding`, `request_scope_change`).
- A stage's evidence is what the runner measured, not what the agent wrote — even when the
  agent is honest.
- Every gate has a green test and a red test.
