# ADR-0005 — Every connector has an interface and a fake

- **Status**: accepted
- **Concerns**: every workstream

## Context

Thirteen workstreams in parallel, and connectors (GitHub, Tekton, Argo, LiteLLM, Ecphoria)
that do not exist on day one. Waiting for the connectors serialises the project.

## Decision

Every integration is a `Protocol` in `packages/adapters/base.py`, with **two**
implementations: the real one, and an in-memory, **scriptable** `Fake*`. `CHOREGOS_FAKES=1`
switches the whole platform to the fakes.

The fakes are not empty stubs: the tracker keeps a single status comment, the gateway cuts
at the cap, the executor is idempotent by `run_id`, the memory supersedes by subject and
returns an empty pack on failure, the CD knows how to break a canary analysis.

## Consequences

- The front, the orchestrator and the runner are developed and tested without any service.
- `make demo` plays the whole chain on a workstation, with no cluster.
- The fakes must stay faithful: when a real adapter changes behaviour, the fake follows —
  otherwise the tests lie.

## Alternatives discarded

- **Recorded HTTP cassettes only**: fine for one adapter, insufficient to write scenarios
  (a broken canary does not replay from a cassette).
