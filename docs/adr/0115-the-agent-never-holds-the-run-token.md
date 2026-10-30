# 0115 — The agent never holds the run token: the proxy variables carry an egress token

- **Status**: accepted, 2026-10-30 — contract S44-05a, implementation S44-05. Amends
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md) (proxy credentials = run id and run token)
- **Concerns**: `POST /internal/runs/{id}/egress-token` (`postRunEgressToken`), `getRunNetwork` and
  `postRunNetworkDecisions` (now `runToken` or `egressToken`), `choregos_runner.runner.Runner.run`,
  `choregos_api.security` (`mint_egress_token`, `verify_egress_token`), `choregos_api.deps.NetworkAuth`

## Context

With `CHOREGOS_EGRESS_AUTH=run` (dev and production, 2026-10-30), the runner writes
`run_id:run_token` into `HTTP_PROXY`/`HTTPS_PROXY` so that the egress proxy knows which run is
calling (ADR 0070). The agent is the runner's child and inherits those variables. So the agent held
the run token, and with it the run's whole internal API: post its own result, take the git token
(`contents: write` on the repository), call the run's tools, write its journal — around the runner,
which is where the guardrails, the definition of done and the scope check live.

Found while measuring what the agent can read from the runner under gVisor (S44-01, ADR 0094):
Landlock keeps the agent out of `/proc/<runner>/environ`, but the token was in the agent's own
environment, where no confinement reaches. A job (ADR 0056) receives the run token on purpose
(`CHOREGOS_RUN_TOKEN`): it is the organisation's own command, not a model's.

## Decision

1. **A second token, for the proxy only.** `aud=choregos-egress`, `sub` = the run, the run token's
   expiry, signed with the run-token key — the shape of the relay token (ADR 0091 §3). The API mints
   it at `POST /internal/runs/{id}/egress-token`, for the run token of that run.
2. **Two routes take it**: `GET /internal/runs/{id}/network` and
   `POST /internal/runs/{id}/network/decisions`, which the proxy calls. Every other internal route
   verifies `aud=internal` and refuses it; it does not mint another egress token either.
3. **The runner exchanges before anything starts.** `Runner.run` asks for the egress token, then puts
   it, not the run token, in the proxy variables the agent inherits. The API is reached directly
   (`NO_PROXY` names it by construction), so the exchange needs no proxy credential.
4. **An API older than this record** answers 404: the runner then keeps the run token, as before, and
   says so on its stderr. That only lasts while an API and a runner of different releases meet.

## Consequences

- The agent can still read its run's network policy and post network decisions: it could rewrite the
  proxy's counters for its own run (they are cumulative totals, replaced by each post). The audit of
  what the agent reached is therefore as trustworthy as the proxy's next flush, not more. A proxy
  credential of its own, out of the agent's reach, would close it; not done here.
- Hosted sessions (S41-11) are unchanged: their agent runs in a separate container that never sees
  the runner's environment.
- One more call per run, before the agent starts.

## Alternatives rejected

- **Strip the proxy variables from the agent's environment**: the agent's `git`, `pip` and `npm`
  need them to leave at all (`enforce` egress).
- **A separate uid for the agent**: the token was in the agent's own environment, not only in the
  runner's.
- **Have the orchestrator mint and deliver both tokens**: every executor (`k8s_job`, `agent_sandbox`,
  ACA, Tekton…) would carry one more secret; the exchange touches none of them.
