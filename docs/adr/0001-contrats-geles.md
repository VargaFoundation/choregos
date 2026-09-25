# ADR-0001 — Contracts are frozen at M0 and versioned

- **Status**: accepted (week 1)
- **Concerns**: every workstream

## Context

Thirteen workstreams move in parallel. If they negotiate their interfaces as they go,
every integration becomes a renegotiation — the classic failure mode of a project split
across teams.

## Decision

`packages/contracts` is the **single source of truth** for interfaces: JSON Schema 2020-12,
OpenAPI 3.1, generated Python and TypeScript types. Contracts are frozen at the end of
week 1. Any change goes through a PR tagged `contract-change`, reviewed by the integration
workstream, and regenerates the types.

Three mechanisms make the freeze effective rather than declarative:

1. the examples (`schemas/examples/`) are validated in CI **against the schema and against
   the Python model** — a drift between the two breaks CI;
2. `make contracts-check` fails when the generated types are stale;
3. a test checks that the API implements exactly the paths and `operationId`s of the
   contract — no more, no less.

## Consequences

- A workstream blocked by a missing contract opens a `contract-change` issue and carries
  on with a local workaround marked `TODO(contract)`.
- The front never redefines an API type: it imports the generated ones.
- The cost of the freeze is deliberate friction on interface changes.

## Alternatives discarded

- **Shared types in a Python package only**: the front would have redefined its own.
- **Generation from FastAPI**: the API would have become the specification, and the front
  would have waited for the API to start.
