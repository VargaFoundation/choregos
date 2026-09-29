# 0026 — What the core's row-level security covers, and what it leaves to the enterprise edition

- **Status**: accepted, 2026-09-28 — refines a consequence of [ADR 0024](0024-deux-editions.md)
- **Concerns**: `apps/api/src/choregos_api/migrations`, `apps/api/tests/test_rls_postgres.py`

## Context

ADR 0024 keeps fail-closed RLS in the community edition (decision 4) and says the enterprise
edition "must finish the isolation — the thirteen tables". Read literally, the two conflict: some
of those thirteen tables carry an organisation's data, and leaving them readable across
organisations in the core would be the "knowingly weaker security posture" decision 4 refuses.

Looking at what each table actually holds settles it. Of the twelve tables still outside RLS on
2026-09-28 (`audit_log` joined the core on 2026-09-26):

- **three carry an organisation's data through a project**: `run_events` (an agent's journal —
  commands, outputs, a customer's code), `deployments`, `gateway_keys`;
- **five are catalogues of the instance or plumbing** with no organisation in them:
  `agent_backends`, `executors`, `templates`, `model_profiles` (only `scope=platform` rows are
  ever written), `webhook_deliveries`;
- **four are identity**: `organizations`, `users`, `memberships`, `api_tokens`.

Identity cannot be put under RLS by a migration alone. The principal is resolved from
`memberships` *before* any scope is set, and `exiger_admin_de_plateforme` counts **all**
organisations to decide who administers the instance: under RLS it would count only the caller's,
and the administrator of a single organisation would become administrator of the instance. A
policy on those tables would weaken security, not strengthen it.

## Decision

1. **The core puts `run_events`, `deployments` and `gateway_keys` under forced RLS**, through the
   project they belong to, like the original twelve. It costs a single-organisation install
   nothing and makes the database hold when a route forgets a filter.
2. **Catalogues and plumbing stay outside RLS**, by decision, each with its reason.
3. **Identity stays outside RLS in the core.** Isolating it needs identity resolution under a
   platform scope and a real platform-administrator role — multi-organisation semantics, which is
   the enterprise edition's work (ADR 0024, decision 3).
4. **Every table is either under forced RLS or exempted in `EXEMPTEES` with its reason**, and
   `test_chaque_table_est_sous_rls_ou_exemptee` enforces it on PostgreSQL. A table added tomorrow
   without a policy turns the suite red.
5. **Schema has one owner.** An edition or plugin that needs tables ships them as its own Alembic
   branch (`choregos.migrations`, `python -m choregos_api.migrer`) and never alters a core table.
   Policies on core tables live in the core.

## Consequences

- "The thirteen tables" of the enterprise plan become **four identity tables** plus the lifecycle
  and ceilings work; the rest is done, in the open, where it protects everyone.
- The enterprise edition must define a platform-administrator role before multi-organisation
  `/platform/*` is usable: until then `exiger_admin_de_plateforme` refuses everyone once a second
  organisation exists, which is the right default.
