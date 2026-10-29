# 0099 — The API is a dependency that only shrinks: ratchets on the imports that cross layers

- **Status**: accepted, 2026-10-29
- **Concerns**: `apps/orchestrator`, `packages/*/src`, `apps/api/src/choregos_api` (services and
  routers); `tests/architecture/`, `apps/api/tests/test_layering_*.py`,
  `apps/api/tests/test_one_organisation_lookup.py`; `docs/development.md`

## Context

Several layers import code that sits above them or beside them. Measured with `ast` at
`87ea1831d`:

- the orchestrator has 153 `choregos_api` import statements in 28 files, and
  `apps/orchestrator/pyproject.toml` depends on `choregos-api`;
- `packages/*/src` has 22, all in five files under `choregos_ontology/service/`;
- one service imports a router (`services/readiness.py`);
- 7 `ImportFrom` statements import a private name from another router;
- 12 routers each carry their own module-level organisation lookup (`_organisation`, `_org`).

Stories that remove these couplings run in parallel tracks. A guard that asserts equality would
turn another track's CI red on every removal, and a guard that baselines module names would break
on every rename.

## Decision

1. **Ratchets count statements and assert `count <= CEILING`.** A removal never breaks a guard;
   the story that removes imports lowers the `CEILING` in the same PR, so its test still fails
   without the change. One guard per file, so parallel stories do not collide.
2. **Hard bans on the HTTP layer.** The orchestrator never imports `choregos_api.routers`,
   `deps`, `main`, `mcp`, `a2a`, or `fastapi`. Only `choregos_ontology/service` may import the API
   from `packages/*/src`. Services never import routers, with one exception that is a ratchet
   (Decision 1) and not a ban: `services/readiness.py` still imports one, and S47-08 removes it.
3. **Router scans are recursive.** They read `routers/**/*.py`, and an import between two modules of
   the same router package (`from ._router import router`) is exempt, so splitting `agents.py` or
   `knowledge.py` into packages stays covered.
4. **The way down is extraction** to `packages/core`, or to a persistence package later, then
   lowering the `CEILING`. Routers share helpers through services; there will be one organisation lookup
   (`services.organisations.organisation_by_slug`, which does not exist yet: S47-09 creates it).
5. **Hand-off rule.** A PR from any track that removes a counted import lowers that guard's
   `CEILING`. A PR that adds one is refused, unless it is a mechanical rename that splits one
   statement (raise the `CEILING` by that number and say so in the PR body) or this ADR is amended.

The orchestrator and extensions (entry-point packages such as `choregos_ontology`) may import the
API's persistence and services; they never import its HTTP layer.

## Alternatives rejected

- **import-linter**: a new dependency and a configuration file every track edits.
- **Module allowlists**: the S45 renames would break them, and they say nothing about how many
  imports there are.
- **Equality ratchets**: a removal by another track would turn this one red.
- **Extracting a `choregos-persistence` package now**: the right end state, too large for a
  guard story; the ratchets make the move measurable when it happens.

## Consequences

Five guards freeze today's coupling. They reduce none of it: the stories that extract shared code
(S47-08, S47-09, S47-10) do. A guard whose `CEILING` is above the real count is slack that nobody
will notice, so a reducing PR lowers the number to the new measure.
