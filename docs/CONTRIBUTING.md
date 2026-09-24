# Contributing to Choregos

## In five minutes

```bash
git clone https://github.com/VargaFoundation/choregos && cd choregos
make setup          # uv sync + pnpm install
make demo           # the whole chain, no cluster: start here
make ci             # what blocks a PR
```

`make demo` takes a ticket from an issue to production, with simulated connectors. If you
read one thing to understand the platform, read its output.

## Repository layout

The full execution plan is in [`plan/00-index.md`](plan/00-index.md) (French). It describes
the architecture, the contracts, the backlog and the conventions. Structural decisions are in
[`adr/`](adr/). What happens when things break is in [`runbooks/`](runbooks/). The critical
state of the project and the work plan that follows from it are in
[`plan/STATE-OF-THE-PROJECT-2026-09-24.md`](plan/STATE-OF-THE-PROJECT-2026-09-24.md).

## Languages

The reference documentation (`docs/`, ADRs, runbooks) is in **English**; `docs/fr/` archives
the French versions. The code, its comments, `docs/plan/` (STATUS, BLOCKERS) and commit
messages are in French.

## The contract before the code

`packages/contracts` is the source of truth for interfaces. It is not modified casually: a PR
tagged `contract-change`, reviewed by the integrator stream, and `make contracts` to
regenerate the types (to be committed).

If a contract is missing: open the issue, continue with a local workaround marked
`TODO(contract)`, and do not block.

## One story, one PR

- One branch, one PR, one squash.
- The description follows the template: story, change, acceptance criteria ticked, proof
  (commands and results), contracts, findings filed — and **what the change does not prove**.
- Green CI is mandatory. No force-push on `main`.

## What CI checks

| Command | What it guarantees |
| :-- | :-- |
| `make lint` | ruff, formatting included |
| `make typecheck` | `mypy --strict` on all Python |
| `make test` | unit and integration tests (fakes); PostgreSQL tests when `CHOREGOS_TEST_DATABASE_URL` is set |
| `make contracts-check` | generated types are current |
| `make docs-cli` then a clean tree | `docs/cli.md` is current |
| `make charts-lint` | charts render and validate, four environments including the embedded one |
| `make web-ci` | lint, types, tests and build of the front |
| gitleaks | no secret in the history (the documented fake ones are allowlisted by path) |

The nightly adds: e2e on kind, conformance of every ACP backend, playbook evals, replay of
archived Temporal histories, a blocking dependency scan.

## Writing code here

- **Python 3.12**, strict typing, `pydantic` v2, async on the I/O side, `structlog` in JSON.
- **The core does no I/O**: `packages/core` is pure and testable without a service.
- **Everything that talks to the world** is an adapter, with its `Protocol` and its fake.
- **Idempotence**: every Temporal activity and every provisioning step replays without a
  double effect (ADR 0008). A test proves it.
- **A guarantee is a mechanism** (ADR 0010): if you catch yourself writing an instruction in
  a prompt to guarantee a property, look for the mechanism.
- **Nothing is ✅ without a test that fails in its absence** — and for anything an agent
  traverses, without the bench.
- **Secrets**: never hard-coded, never in an agent workspace, never in Git.

## Out of scope

You found a problem that is not in your story? Do not touch it: open a `finding` issue with
the origin, the proof and a proposal. It is exactly what we ask of agents.

## The repository is its own client

Since M1, `choregos` is a Choregos project: stories go through the platform. When you work
here, you also work on the tool that reviews you.
