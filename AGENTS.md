# Choregos — instructions for agents

> This file was **emptied by accident** on 2026-09-24 (commit `98c2a492`): switching the
> documentation to English was meant to turn `CLAUDE.md` into a pointer to `AGENTS.md`, and
> replaced the content of `AGENTS.md` with an import of itself. `CLAUDE.md` is a symbolic link to
> this file; both are therefore the same page, and `tests/docs/test_instructions_des_agents.py`
> refuses to let it be emptied again.

## Context
A general-purpose platform of governed agents (Varga Foundation, Apache 2.0; ADR 0029): workflows
from any trade, carried by agents and humans, under measured guarantees. Plan and status:
`docs/plan/`. Reference documentation: `docs/`. Structural decisions: `docs/adr/`.
Contracts: `packages/contracts` — **do not modify without a `contract-change` PR** (ADR 0001).

## Languages
**Everything is in English** (ADR 0046, superseding ADR 0039 §2-3): code, identifiers, comments,
docstrings, tests, file names, the reference documentation (`docs/`), `docs/plan/` (STATUS,
BLOCKERS), ADRs, commit messages and PR descriptions. `docs/fr/` is an unmaintained archive.
Existing French code is renamed package by package (S34-04); do not mass-translate code your story
does not otherwise touch. `make lint` and CI refuse French in new or modified lines
(`tools/check_english.py`); a line that must stay French (a `workflow.patched` id, a French alias
kept for running workflows) carries `lang: fr-ok` in a comment.

## Commands (the CI's, no others)
- Everything that blocks a PR: `make ci` = `lint` + `typecheck` + `test` + `contracts-check` + `charts-lint`.
- Python: `uv run ruff check packages apps/api apps/orchestrator tools tests` then
  `ruff format --check` on the same paths — **`tests/` is one of them**, an E501 there breaks CI.
  `make lint` also runs `tools/check_english.py` (lines added since `origin/main`).
- Typing: `uv run mypy packages/*/src apps/api/src apps/orchestrator/src tools` (strict).
- Tests: `uv run pytest -q`; `-m "not slow"` leaves out the slow ones (building the
  workspace). Coverage: `make test-cov`, threshold 80 %.
- Front end: `make web-ci` = lint + typecheck + test + build.
- Charts: `make charts-lint` (helm lint + kubeconform). `make charts-test` requires the
  helm-unittest plugin **1.1.2** and Helm ≥ 3.18; without them it **refuses to run** rather than
  give a false verdict, and `tests/charts/` renders the chart and checks it in Python.
- Generated files to commit: `make contracts` (types from the schemas), `make docs-cli`
  (`docs/cli.md` from the CLI). A test refuses any divergence.
- Beyond CI: `make demo` (end to end, in memory), `make dev-up` / `make dev-down`,
  `make e2e`, `make test-cluster` (real cluster), `make test-live` (real services),
  `make conformance`, `make evals`, `make replay-record`.

## Conventions
- Python 3.12, strict typing, pydantic v2, async on all I/O, structlog. Strict TypeScript,
  ESLint, Prettier.
- Conventional commits: `feat(orchestrator): …`, `fix(runner): …`, `contract(schemas): …`.
  One story = one PR = one squash.
- Fakes are mandatory: every integration has its scriptable `Fake*`; `CHOREGOS_FAKES=1` switches
  the whole platform (ADR 0005).
- Idempotence: every Temporal activity and every provisioning step can be replayed without a double
  effect (ADR 0008). No PR on `workflows/` without a replayed history (`tests/replay/`).
- Security: no hard-coded secret; never a broad token in a runner; every tracker write goes through
  the internal API.
- Logs: JSON, with the keys `project`, `work_item`, `run_id`, `stage` when they exist.

## Definition of done (blocking)
`make ci` green, docs up to date, acceptance criteria checked in the PR, no `TODO` without a linked
issue, `docs/plan/STATUS.md` updated **saying what the PR proves and what it does not prove**.

**Nothing is declared ✅ without a test that fails in its absence.** A feature whose guard can
be removed without turning the suite red is not delivered, it is hoped for.

## Out of scope
A problem found outside your story: do not touch it, open a `finding` issue with
`origin: <story>`, the evidence and a proposal. Need a contract: a `contract-change` issue.

## Memory
Read `docs/plan/` and `docs/adr/` before deciding; add an ADR (`docs/adr/NNNN-*.md`) for any
structural decision. When a page and the code contradict each other, **the code wins and the page
is a bug**.
