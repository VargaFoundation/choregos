# 0205 — The gateway key can be delegated: a service account on a shared LiteLLM

- **Status**: accepted, 2026-11-19
- **Concerns**: `LiteLlmGateway` (`packages/adapters/src/tranibor_adapters/gateway/litellm.py`), the
  run key (`activities/run_key.py`), agent sessions, the chart's `global.gateway`, `docs/deployment.md`;
  builds on [ADR 0004](0004-cost-counted-at-the-gateway.md), [ADR 0047](0047-a-budget-is-a-reservation.md)
  (S27-10, the project's day) and [ADR 0084](0084-guardrails-outside-the-agent.md) (the guard)

## Context

The platform mints one virtual key per run (ADR 0004). It does so with the **master key** of a LiteLLM
it owns, in front of the real providers; and it asks that LiteLLM for a *team* per project, whose day
budget every key joins (S27-10). An operator that already runs one shared LiteLLM for several products
(the first operator's `inference` namespace) would end up with two proxies in series, the platform's
own one adding a hop, a database and a second copy of the price list, only to be allowed to mint keys.
Handing the platform the shared gateway's master key is out of the question: it opens every product's
keys, teams and spend.

An experiment on LiteLLM 1.105.5 (open source, no licence) on 2026-11-19 (the operator's
`platform/llm-gateway/SPIKE-DELEGATION.md`) showed that an `internal_user` **service account**, with its own
key, can: mint child keys (`POST /key/generate`, `max_budget` no greater than its own, else 400), read
them (`GET /key/info`, `/spend/logs?api_key=`, 403 or `[]` for any other key), delete them
(`POST /key/delete`), and list its own models (`/model/info`). It cannot create a team or a user
(401), update a team (403), and a key it mints cannot carry a `team_id` (it is not that team's admin).
Three things are **not** checked at creation: `models` and `duration` of a child (the account's own
models apply when the child *calls*), and a missing `max_budget` is silently filled with 1000.

## Decision

1. **A key mode.** `CHOREGOS_GATEWAY_KEY_MODE` (`master`, the default, or `delegated`; the `litellm`
   connector's `key_mode`). In `delegated` mode the credential in `CHOREGOS_GATEWAY_MASTER_KEY` (the
   variable keeps its name) is the service account's key.
2. **What delegated mode never does.** It never calls `/team/new` or `/team/update`, never sends
   `team_id` nor `tags` (not even a connector's configured `team_id`), and refuses to mint a key
   without a positive `max_budget` and a non-empty `models` list: LiteLLM would not. `duration` is
   always sent. The `teams` capability is absent from the adapter.
3. **The project's day is the platform's.** `prepare_stage` already reserves each key from
   `min(…, project day − measured spend − open reservations)` (ADR 0047): a project over its day is
   refused before a key exists, and two runs cannot take the same remainder. What a team added was a
   second stop *inside* a run (the gateway cutting the day mid-run). In delegated mode that second stop
   is gone; the bound on a run's overshoot is its key's cap, the remainder at the moment of minting.
   Callers use `ensure_project_team`, which returns `None` without the `teams` capability.
4. **The guard has one convention.** A run that must have its secrets masked
   (`sandbox.llm_security_analyzer`, ADR 0084) is minted with `metadata.choregos_llm_guard = "redact"`,
   exactly as on the platform's own LiteLLM; the shared gateway's guard acts for that metadata only.
   The `llm_guard` capability is therefore kept.
5. **The chart.** `global.gateway.keyMode` (`master` | `delegated`) selects the secret key to read, in
   `global.gateway.secretRef`: `master-key` or `delegation-key`. `delegated` with `embedded: true` is
   refused at render.

## Consequences

- The platform needs no LiteLLM of its own: the `litellm` subchart is for an operator without a shared
  gateway. Its model *names* are the shared gateway's; mapping the platform's tiers onto them is
  S82-02's.
- The account's cumulative budget (`max_budget` + `budget_duration`) and its `models` are the real
  ceiling; the operator sizes them. A child above the account's budget is refused by LiteLLM (400),
  which surfaces as an `UpstreamError`, not as a budget refusal.
- A run's spend is read with its own key (`/key/info`, `/spend/logs?api_key=`): unchanged.
- The platform's LiteLLM callbacks (`tranibor_core.llm_spans`: per-call trace spans and the context
  breakdown of an ACP CLI's run) run inside *its own* LiteLLM. On a shared gateway they do not run:
  those two signals are lost until the shared gateway loads an equivalent. Costs and caps are not
  affected.
- Not proven here: a real LiteLLM (the adapter is tested against recorded request shapes and a fake
  that refuses what 1.105.5 refuses), several replicas, the shared gateway's guard on a delegated key.

## Alternatives discarded

- **The shared gateway's master key**: opens every product's keys. Refused.
- **A key broker** in front of the shared gateway: one more service to run, for what the account
  already does.
- **A team admin account** (`team_id` on the key): it can also add and remove team members
  (the spike's test b3); the platform needs none of it.
- **Keep the team in delegated mode with a stub**: a day budget that is claimed and not held by anyone.
