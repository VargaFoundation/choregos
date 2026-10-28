# 0089 — A connector's secret field holds several keys, used in turn

- **Status**: accepted, 2026-10-26 — contract S39-30a; implemented by S39-30b (store, resolution, the
  shared pool, the market-data and web-search adapters) and S39-30c (the console); builds on
  [ADR 0058](0058-connector-secrets-are-write-only-values.md) (sealed values) and
  ADR 0088 (who writes a reference, #987)
- **Concerns**: the `{field}` path parameter of `PUT|DELETE …/connectors/{…}/secrets/{field}` (organisation
  and project), the keys of `secret_values` / `secret_refs`, `ConnectorTypeSpec`, the resolution of a
  connector's configuration, `choregos_adapters.http.pool`, egress host derivation

## Context

On 2026-10-26 the project owner asked that the values connectors use — API keys, base URLs, the proxies
of web search — be **multi-valued by design, with rotation and load balancing**. Model keys are left
out on purpose: LiteLLM already routes, falls back and cools down across deployments.

Today one secret field holds one value: one reference, one sealed row (`UNIQUE(org_id, name)`, the name
bound into the AEAD), one string in the adapter's configuration. Replacing a key means a moment where
the old one is gone and the new one not yet proven, and a key that hits its quota stops the connector.

Because a sealed value is **write-only**, a pool cannot be edited as a list: an administrator cannot
re-send the keys already stored, they never come back. Each key must be addressable on its own.

## Decision

1. **A slot is a field.** A type declares which of its secret fields may be pooled
   (`ConnectorTypeSpec.pooled_fields`). Such a field `api_key` takes extra slots named `api_key.2` …
   `api_key.10`. Each slot is written, replaced and cleared exactly like a field today: `PUT|DELETE
   …/secrets/api_key.2`, or `secret_values: {"api_key.2": "…"}`; it is sealed under its own name
   (`connector/<name>/api_key.2`) and listed in the connector's `secrets` state, one entry per slot,
   never a value. The contract changes only the pattern of `{field}`
   (`^[a-z][a-z0-9_]{0,63}(\.([2-9]|10))?$`); the shapes stay. A slot of a field the type does not
   pool is a 422.
2. **The pool is the slots present, in order.** `api_key`, then `api_key.2`, `.3`… whichever exist;
   clearing `api_key` while `api_key.2` exists leaves a pool of one. The adapter receives a list
   when there are several, a string when there is one, so a type that pools nothing is unchanged.
3. **Rotation without a gap.** Add the new key in a free slot, test, clear the old one. No moment
   without a valid key.
4. **Use in turn, with a cool-down.** One primitive, `choregos_adapters.http.pool.Pool`, serves every
   pooled value (keys now, base URLs and proxies later):
   - `failover` (the default): the first usable value; the next one only when it is cooling down;
   - `round_robin`: the next usable value at each call, spreading the load;
   - a 429 cools the value down for its `Retry-After` (60 s without one) and the call is retried at
     once with the next usable value; a 401/403 benches it for 10 minutes and names its slot in the
     error (`api_key.2 refused`), never the value;
   - when every value is cooling down, the call fails with the earliest retry time — no silent
     fallback, no unauthenticated call.
   The strategy is the connector's `config.key_rotation` (`failover` | `round_robin`), accepted on a
   type that pools a field. The cool-down state lives in the process (API, worker): each process
   rotates on its own; nothing is shared or persisted.
5. **A list of URLs is read as a list.** Egress host derivation reads every element of a URL field
   that is an array; today an array silently yields no host. A type may pool a URL field the same way
   (`pooled_fields` covers configuration fields too); none does in S39-30b.
6. **References follow ADR 0088.** A slot may carry a reference (`env:`) only when written by a platform
   administrator.
7. **What a pool is for.** Replacing a key without downtime, failing over between accounts, spreading
   load across keys the organisation is entitled to. Whether a vendor allows several keys or accounts is
   the organisation's responsibility; the procedures of the free market-data sources say theirs do not
   (Tiingo, Alpha Vantage: one free key per person).

## Consequences

- No migration: slots are rows and references like any field; an existing connector is a pool of one.
- Every value of a pool is registered for redaction when resolved, as today.
- Audit `connector.secret.set` / `.clear` names the slot; the value never.
- The outbound proxy of an organisation (ADR 0087, S40-26) is asked to become a pool too and to rotate
  through the same `Pool`; that is its contract's decision, not this one's.

## Alternatives rejected

- **`secret_values: {field: [values]}`.** Replaces the whole pool at each write, and a write-only pool
  cannot be re-sent: adding a key would mean retyping the others, which nobody can read back.
- **A shared, persisted cool-down (database, Redis).** Coordination across processes for a state that
  heals in seconds; a 429 seen twice costs one retry.
- **Rotation inside each adapter.** Every adapter would reinvent cool-down and retry, differently.
