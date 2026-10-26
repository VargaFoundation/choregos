# 0087 — An organisation has one outbound proxy, for its web search and web reading

- **Status**: accepted, 2026-10-26, by the project owner's choice of scope A (below) — contract only
  (S40-26a); implemented by S40-26b onwards
- **Concerns**: `GET/PUT/DELETE /orgs/{org}/outbound-proxy`, `POST /orgs/{org}/outbound-proxy/test`,
  `web_fetch`, the `web_search` backends, the knowledge bases' web crawl, the self-hosted SearXNG; builds
  on [ADR 0034](0034-connectors-by-capability.md) ,
  [ADR 0058](0058-connector-secrets-are-write-only-values.md), [ADR 0066](0066-the-organisation-egress-allowlist.md),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md),
  [ADR 0083](0083-web-search-is-a-governed-tool-whose-backend-declares-what-it-keeps.md) and
  [ADR 0086](0086-firecrawl-is-self-hosted-only.md)

## Context

The project owner will bring residential proxies (for example Decodo, `gate.decodo.com:7000`, user and
password, the provider's options encoded in the username) and wants them set from the console, for
the platform's external traffic. Surveyed on `origin/main` on 2026-10-26:

- **No outbound proxy support exists anywhere.**
- **An environment variable would not do.** httpx honours `HTTP(S)_PROXY` only when no `transport` is
  given (httpx 0.28, `_client.py`: `allow_env_proxies = trust_env and transport is None`). A variable on
  the API pod would reach `web_fetch` and the search backends and silently skip every client built by
  `guarded()` (`choregos_adapters/http/egress.py`), which always passes a transport. The setting is
  therefore explicit, per organisation, and wired explicitly into each client.
- `web_fetch` resolves the host, checks every address, then pins the connection to the address it
  checked (`services/web_fetch.py`, `_check_host`). A proxy changes who resolves.

## Scope (the owner's choice)

**A: web search and web reading only.** Not agent runs' egress (`choregos-egress`, ADR 0070), which
stays as it is.

## Decision

1. **An organisation has at most one outbound proxy**, a setting of the organisation (like the policy
   ceiling): `endpoint` (`host:port`, no scheme, no credentials), `scheme` (`http` | `https`: the protocol
   spoken *to the proxy*; an `https` target goes through `CONNECT`), `username`, `password`, `enabled`,
   `applies_to` (a non-empty subset of `web_fetch`, `web_search`, `knowledge_web`, `search_engines`; default
   all four). The password is **sealed** (ADR 0058), write-only, never returned (`password_set` says it
   exists), registered for redaction; an absent `password` on a write keeps the stored one and is a 422 on
   the first write. The setting is provider-neutral: country, sticky session and the like live in the
   `username` as the provider documents them; Choregos does not build them.
2. **Fail closed.** When the proxy is enabled for a kind of traffic, that traffic goes through it or not at
   all. An unreachable proxy, a 407 or a refused `CONNECT` is an error that names the proxy (endpoint, never
   the password); there is never a silent direct call. Disabling the proxy or removing a kind from
   `applies_to` is the only way back to direct.
3. **The target stays checked; the proxy is a deployment concern.** The organisation's egress allowlist
   (ADR 0066), the network ceiling (ADR 0070) and the private-address checks apply to the **target** exactly
   as today. The proxy's own host is *not* added to the allowlist or the ceiling automatically: the
   allowlist names what an organisation's *targets* may be, and adding the proxy would let an administrator
   widen it by naming a host; the proxy must be reachable from the cluster (network policy, a deployment
   concern, documented by S40-26b).
4. **Pinning with a proxy.** `web_fetch` still resolves the target name locally and refuses any private
   address, as today. Through the proxy it sends `CONNECT <name>:<port>` (the **name**, so the proxy
   resolves and a residential exit sees the host it expects, TLS verifying the name). The proxy's own
   resolution is **not pinned**: a name whose answers differ between the platform's resolver and the proxy's
   is a residual risk, accepted because the private ranges are unreachable from a provider's exit, not from
   the cluster; it is stated in the procedure.
5. **A `test` action.** `POST /orgs/{org}/outbound-proxy/test` fetches `https://ipinfo.io/json` through the
   stored proxy (the target is fixed by the platform, never by the caller) and returns `ok`, `exit_ip`,
   `country`, `error`, `elapsed_ms` and `target`. A failing proxy is a 200 with `ok: false`, not an HTTP
   error; 404 when none is set. The test ignores `enabled` (one can test before enabling).
6. **Audit.** `outbound_proxy.update`, `outbound_proxy.delete`, `outbound_proxy.test`, each with the
   endpoint and the username, **never the password**. All four routes need `org_admin`.
7. **SearXNG (`search_engines`).** SearXNG runs outside this repository's runtime and its engine traffic
   cannot learn an organisation's setting. Decided in principle, S40-26d: an in-cluster relay that
   SearXNG's `outgoing.proxies` points at, which applies the organisation's proxy and fails closed. Its
   internal route is a later contract change; until then `search_engines` in `applies_to` is stored but has
   no effect, and the console says so.
8. **Out of scope**: agent runs' egress (ADR 0070); several proxies, per-project or per-country proxies;
   rotation and health monitoring (the provider's gateway rotates).

## Consequences

- S40-26a (this record) is the contract only: the four operations and four schemas in `openapi.yaml`.
  The conformance test lists them as contract-only until S40-26b, which implements the routes, the sealed
  store and the `web_fetch` and `web_search` wiring; S40-26c the knowledge crawl and the console; S40-26d
  the SearXNG relay.
- Every platform client that carries this traffic takes the proxy explicitly; a test must fail if a new
  `guarded()` client for these kinds ignores it (the httpx trap above).

## Alternatives rejected

- **`HTTP(S)_PROXY` on the pods.** Skips every `guarded()` client; global, not per organisation; not
  settable from the console.
- **Several proxies, per project or per country.** Not asked; the username already carries the provider's
  country option.
- **Adding the proxy host to the egress allowlist.** See 3.
- **Pinning the proxy's resolution.** Impossible: the proxy resolves by design.
