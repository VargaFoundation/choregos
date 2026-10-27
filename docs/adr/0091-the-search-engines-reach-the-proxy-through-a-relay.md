# 0091 — The search engines reach an organisation's proxy through an in-cluster relay

- **Status**: accepted, 2026-10-26 — contract only (S40-26d-a); the relay and the route are S40-26d-b.
  Completes [ADR 0087](0087-an-organisation-has-one-outbound-proxy.md) §7
- **Concerns**: `GET /internal/orgs/{org}/outbound-proxy/{kind}` (`getRelayOutboundProxy`), the relay
  (`python -m choregos_api.relay`), the self-hosted SearXNG (`outgoing.proxies`) and Firecrawl
  (`PROXY_SERVER`, `PROXY_USERNAME`, `PROXY_PASSWORD`); builds on
  [ADR 0058](0058-connector-secrets-are-write-only-values.md),
  [ADR 0083](0083-web-search-is-a-governed-tool-whose-backend-declares-what-it-keeps.md) and
  [ADR 0086](0086-firecrawl-is-self-hosted-only.md)

## Context

On the dev deployment `web_search` is served by a self-hosted Firecrawl, which queries a self-hosted
SearXNG, which queries DuckDuckGo, Brave and Wikipedia. Both run outside this repository's runtime, and
the API deliberately does not proxy the Firecrawl backend (ADR 0087 and S40-26b: it is in-cluster). So the
organisation's proxy carries none of the real search traffic. ADR 0087 §7 decided in principle that a
relay closes the gap; this record fixes its shape.

## Decision

1. **A relay is a small asyncio forward proxy** (HTTP `CONNECT` for https targets, absolute-form for http),
   shipped in the API image as its own entrypoint, `python -m choregos_api.relay`, and run as a separate
   Deployment. No new third-party dependency. **One relay serves one organisation**
   (`CHOREGOS_RELAY_ORG`, a slug).
2. **The client chooses the kind of traffic with its proxy username**: `search_engines` (SearXNG) or
   `web_search` (Firecrawl's scrape traffic). The client's proxy password is a **shared relay token**
   (`CHOREGOS_RELAY_CLIENT_TOKEN`), compared in constant time. Anything else is a `407`. This
   token only lets a pod of the cluster use the relay; it is not the organisation's proxy password, and the
   relay's network policy lists who may reach it.
3. **The relay does not hold the database credentials or the secrets master key.** It asks the API, through
   the one internal route `GET /internal/orgs/{org}/outbound-proxy/{kind}`, which returns the resolved proxy
   for (organisation, kind) or `proxy: null`. The route is authenticated by a **relay JWT**: ES256, signed
   with the same signing pair as run tokens, audience `choregos-relay` (a run token has audience `internal`, so neither is accepted for
   the other's routes), `sub` = the organisation's slug, a mandatory `exp`. A token for another
   organisation is a `403`; a run token, a session or an API token is a `401`/`403`. The token is minted by
   an operator (`python -m choregos_api.relay mint --org <slug> --days N`, which needs the run-token key) and
   put in the relay's Secret. **Why this pattern**: the run token is the platform's existing credential for
   `/internal/*`: a signed, audience-bound, expiring claim verified without a database read, whose scope
   is in the token. Rejected: an **API token** (bound to a user and a service account, its scopes are
   meant for the REST and MCP doors, no organisation binding, and it is revocable only by a database row);
   a **static shared secret in the API's environment** (no expiry, one more secret to rotate by hand,
   the organisation binding would be configuration); **Kubernetes TokenReview** (couples the API to
   cluster RBAC, a deployment concern this repository does not own).
4. **Fail closed.** When the proxy applies to that kind and cannot be obtained (the API is down, the route
   answers `503` because the password is lost, the token is refused) or used (upstream `407`, unreachable,
   a refused `CONNECT`), the relay answers `502` naming the proxy endpoint (never a credential) and
   **never connects directly**. When no proxy applies (`proxy: null`), it connects direct, but refuses a
   private, loopback, link-local or cluster address (`is_private_address`, the same check as `web_fetch`),
   after resolving the name itself and pinning the connection to the address it checked.
5. **The resolved configuration is cached for 30 s**, per kind, and never across a failure: a failed fetch
   clears the entry, so the relay does not keep serving a proxy that was removed or disabled for longer
   than the TTL, and does not serve a stale one when the API cannot be asked.
6. **Upstream.** `CONNECT <name>:<port>` to the proxy with `Proxy-Authorization: Basic` (user and password
   as `ProxyConfig` encodes them); an http target is forwarded in absolute form with the header added;
   `scheme: https` means TLS to the proxy.
7. **The password** is returned in clear by this route only (schema `RelayProxyCredentials`, the one schema
   with a `password` in an answer; a contract test pins it to this route). The answer is `no-store`. The
   password is registered for redaction in the API and in the relay, and is never logged or put in an
   error. The route is **not audited**: the relay asks once per kind per 30 s, an audit row each time
   would drown the audit log, and the credential is expiring and per organisation; the route logs
   the organisation and the kind, never the password.
8. **Residual risks, stated.** The relay speaks plain HTTP to its clients inside the cluster; whoever can
   reach it and holds the client token can spend the organisation's proxy, so the network policy and the
   token bound it. A CONNECT through the proxy is resolved by the proxy (ADR 0087 §4); only the direct
   mode is pinned. A relay is per organisation: another organisation needs another Deployment.

## Consequences

- S40-26d-a (this record) is the contract: one operation and two schemas; the conformance test lists the
  operation as contract-only until S40-26d-b.
- The deployment side (SearXNG `outgoing.proxies`, Firecrawl environment, the relay's Deployment, Secret
  and network policy for its egress to the proxy host and to the engines) belongs to the deploy
  repository; `docs/usage.md` lists what to set.
- `search_engines` in `applies_to` takes effect for the organisation whose relay serves SearXNG, and only
  then; the console says so until the relay exists.

## Alternatives rejected

- **Pointing SearXNG at the proxy directly**: the password would live in two places and ignore the
  organisation's `enabled` and `applies_to`.
- **A relay with database access**: it would hold the secrets key; a relay is exposed to engine-facing
  traffic.
- **The relay inside the API process**: engine traffic would share the API's event loop and its network
  position.
