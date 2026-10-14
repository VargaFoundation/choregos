# ADR-0007 — Memory must earn its place before anything depends on it

- **Status**: accepted
- **Concerns**: S10, S11

## Context

Ecphoria (bi-temporal memory, knowledge base) is promising and young: 297 commits, no
external adoption. Building the platform on it is a bet.

## Decision

1. **Reduced scope**: Ecphoria serves as memory and knowledge base. Its "agentic platform"
   part and its auto-RAG proxy are not used (Cargo feature flags disabled in the
   `ecphoria:memory` image).
2. **Fallback with the same interface**: `PgVectorMemory` implements the same
   `MemoryAdapter` in the Choregos database. Switching is a line of configuration.
3. **Never blocking**: reads have a short timeout and a circuit breaker; a failure returns
   an **empty context pack**, never an error that stops a stage.
4. **Governed writes**: the orchestrator writes deterministic facts with provenance; agents
   **propose** (`propose_fact`), a human or a rule validates.
5. **Proof before dependence**: a four-week A/B (with and without context pack) on the
   first-pass merge rate and the cost per ticket. If memory does not pay, it stays optional.

## Consequences

- The context pack is **marked untrusted** in the prompt: data, not instructions.
- The evaluation matrix includes the `with_memory` / `without_memory` variant.
- No critical path depends on Ecphoria.
