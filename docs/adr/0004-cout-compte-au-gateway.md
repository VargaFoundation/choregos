# ADR-0004 — Cost is counted at the gateway, not by the agent

- **Status**: accepted
- **Concerns**: S4, S1

## Context

An agent can be wrong about its consumption, or lie. A platform that bills from what the
agent declares does not know what it spends.

## Decision

Every model call goes through LiteLLM. Each run receives a dedicated **virtual key** with a
**hard cap** equal to the stage budget and a short lifetime. Cost is read at the gateway
(`/key/info`, `/spend/logs`), never from the `StageResult` — the contract does not even have
a field for it.

When the cap is reached LiteLLM refuses: the agent gets an error, the runner finishes
`failed(reason=budget)`, the orchestrator escalates to a human.

## Consequences

- The cost shown on the ticket is the real cost, request by request.
- A runaway agent costs at most its stage budget.
- A local model is accounted with a configured **internal price**, to stay comparable.
- The key is revoked as soon as the run ends: it is worthless if it leaks.

## Alternatives discarded

- **Counting tokens in the runner**: duplicates pricing logic and misses the cache.
- **Trusting the `StageResult`**: it would be the only unverified measurement in the chain.
