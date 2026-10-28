# 0105 — Choregos consumes an MCP gateway; it never is one

- **Status**: accepted, 2026-10-27, by the project owner's request (stream S46); implemented by S46-03
  onwards
- **Concerns**: an organisation's MCP gateway (the `mcp_gateway` setting of the organisation, the
  `mcp-gateway` connector type, per-organisation assertion keys and their JWKS, the MCP client in
  `choregos_adapters.mcp`, the broker, governed writes, connector discovery, the install routes of the MCP
  catalogue, the chart's `global.mcpGateway`) and the boundary test
  `tests/licences/test_mcp_gateway_boundary.py`; amends
  [ADR 0022](0022-a-federable-catalogue.md), [ADR 0028](0028-the-governed-execution-of-change.md) (point 2),
  [ADR 0029](0029-a-platform-for-governed-agents.md) and
  [ADR 0065](0065-catalogues-of-mcp-servers-and-skills.md): what they refuse is building or being a
  gateway, not consuming one; builds on [ADR 0034](0034-connectors-by-capability.md),
  [ADR 0035](0035-governed-actions-in-the-core.md),
  [ADR 0058](0058-connector-secrets-are-write-only-values.md),
  [ADR 0066](0066-the-organisation-egress-allowlist.md),
  [ADR 0070](0070-network-access-is-bounded-and-enforced-per-run.md),
  [ADR 0074](0074-catalogue-rights.md), [ADR 0077](0077-delegated-identity-between-steps.md),
  [ADR 0079](0079-choregos-and-graal-are-autonomous-products.md),
  [ADR 0087](0087-an-organisation-has-one-outbound-proxy.md),
  [ADR 0093](0093-an-agent-ships-as-a-plugin.md) and
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md)

## Context

Four places say "no gateway", each about something different:

- ADR 0022 discards **a runtime MCP gateway in front of the sidecar**: a broker that gives the agent the real
  server names and the discovery the tool catalogue refuses.
- ADR 0028 (point 2), carried by ADR 0029, says Choregos **does not build** an identity provider or a
  general-purpose MCP gateway.
- ADR 0065 discards **a runtime MCP gateway (aggregators)** as a source of the MCP catalogue.
- `docs/positioning.md` says Choregos is not a general-purpose MCP gateway.

All four refuse to **be** a gateway, or to put one **between the agent and the platform's own tools**. None
says what an organisation that already runs a gateway may do with Choregos, because nobody had asked.
Organisations do run them (Obot, Docker's MCP gateway, IBM ContextForge, agentgateway, ToolHive and MCPJungle
are examples): a gateway hosts the servers that speak stdio, holds their upstream credentials and OAuth
grants, and applies the network rules of the servers' side. The project owner asked on 2026-10-27 that
Choregos use such a gateway where an organisation has one.

What exists today, on `origin/main`:

- **No agent ever talks to a third-party MCP server.** `StageInput.tools.mcp` only holds the pod-local
  `choregos` sidecar and `memory`. An organisation's MCP servers are reached by the **API pod**, through the
  hand-written MCP client (`choregos_adapters.mcp`) inside the broker (`services/courtier.py`), and reach the
  agent as sidecar tools `<connector>__<operation>` through `/internal/runs/{id}/tools`.
- **That broker path carries all the governance**: born-forbidden operations, approvals, governed writes
  (ADR 0035), argument schemas, result bounding, the injection guard, the cost ledger line `mcp:<connector>`,
  the `TOOL_CALLED` digests, spans and the network journal.
- **Stdio servers (`needs_hosting`) and OAuth servers (`needs_oauth`) of the MCP catalogue cannot be
  installed.** Choregos runs no stdio process for the catalogue and holds no OAuth grant for it.
- The client speaks MCP 2025-03-26 to 2025-11-25. MCP 2026-07-28
  ([ADR 0017](0017-mcp-2026-07-28-conformance.md)) is not implemented.

A gateway is therefore a way to give those servers a home without Choregos becoming a hosting product, if
the line between "consuming" and "being" is written down and guarded. It is the same shape as ADR 0079 for
graal: two products, each alone, meeting through public interfaces, with a test.

## Decision

1. **The gateway is another product, met only through public interfaces.** MCP Streamable HTTP (2024-11-05
   to 2026-07-28, negotiated) and standard HTTP authentication: a bearer token, an API-key header, OAuth 2.0
   client credentials with an RFC 8707 `resource`, or a short-lived **JWT bearer assertion** signed by a
   per-organisation key whose public half Choregos publishes as a JWKS. No vendor SDK, admin API, header or
   code path: a vendor name may appear in `docs/` as an example and **nowhere else**. Choregos stays
   **consumer only**, and what it refuses to be is written down here:
   - it hosts no MCP server for others: the process of a catalogue server is its operator's to run (the
     servers a plugin brings, which the platform runs under ADR 0096, are the one exception, point 9);
   - it aggregates no servers behind one endpoint and defines no virtual server;
   - it offers no passthrough: its inbound MCP door, its A2A door and its broker serve Choregos's own tools,
     workflows and run-scoped tool calls, never a third-party MCP server's, and route no traffic by server
     name across organisations.

   The boundary test `tests/licences/test_mcp_gateway_boundary.py` enforces it, as ADR 0079 does for graal:
   no vendor name in what ships, no import from the two doors to anything that reaches an organisation's
   connectors, no route that selects a third-party server, and nothing in the orchestrator or the runner that
   reads the gateway.
2. **Agents never reach the gateway.** The broker stays the agent-to-tool policy point; the gateway is the
   policy point for hosting, for the upstream credentials and OAuth grants, and for the upstream network.
   An agent calls `<connector>__<operation>` through `/internal/runs/{id}/tools` exactly as it does for a
   direct connector, and every governance step above still runs before anything leaves for the gateway. The
   gateway sees the call after Choregos has decided it; it decides nothing Choregos decides.
3. **The gateway's host is never a run's.** It is opened for the `api` and `workers` families (governed
   writes run in the workers) and never for `runners`. This is enforced **in the API**, not only by the chart:
   the host is denied in every run's effective network, an opening of it for `runners` is refused, and the
   egress rules the gateway itself created (origin `mcp-gateway`) are revoked when the gateway moves or is
   deleted. `CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS` is the only way to aim the platform at an `http`, private
   or in-cluster host (the SSRF guard); the chart's `global.mcpGateway` renders the NetworkPolicy for the
   API and the workers only.
4. **The gateway is the policy point for hosting, upstream credentials, upstream network and upstream rate
   limits; Choregos stores none of them.** The only credential Choregos holds is the one that
   authenticates **itself** to the gateway (a bearer token, an API key or an OAuth client secret), sealed and
   write-only ([ADR 0058](0058-connector-secrets-are-write-only-values.md)); with the JWT assertion it
   stores no shared secret at all, only a signing key whose public half it publishes. **No upstream
   credential of a server the gateway serves is ever stored in Choregos.** Per-host politeness towards an
   upstream and its rate limits are the gateway's job; the platform's per-call budget and cost ledger still
   apply to every call.
5. **One gateway per organisation**, an `Organization.settings` key `mcp_gateway` (the outbound proxy's
   pattern, [ADR 0087](0087-an-organisation-has-one-outbound-proxy.md)): the endpoint, the routing (`path`,
   with `{server}` in the URL, or `header`), the authentication mode, the sealed write-only credential, the
   audience, the timeout, and a mode:
   - `off`: the setting is kept, calls through it are refused;
   - `available`: direct `mcp` connectors and `mcp-gateway` connectors both work;
   - `required`: new direct `mcp` connectors are refused and so are calls to existing ones (reads, writes
     and approvals alike).

   Only an `org_admin` writes it, every change is audited without the secret, and
   `POST /orgs/{org}/mcp-gateway/test` runs `initialize` and `tools/list` through it. The YAML tool
   catalogue of [ADR 0014](0014-a-platform-held-tool-catalogue.md) is out of `required` mode's scope.
6. **A connector type `mcp-gateway`** (kind `mcp`, configuration `server`, no secret field and no URL of its
   own). Because the kind stays `mcp`, discovery, drift detection, per-operation policy (a discovered tool is
   born closed, [ADR 0034](0034-connectors-by-capability.md)) and governed writes are unchanged. A
   catalogue entry that is `needs_hosting` or `needs_oauth` gains an organisation-specific `install_routes`
   value `gateway` when the organisation has a gateway: the gateway hosts it, and the install declares an
   `mcp-gateway` connector. Moving a direct connector behind the gateway means declaring a new `mcp-gateway`
   connector. The `DELETE` of the gateway is refused (409 `gateway_in_use`) while `mcp-gateway` connectors
   exist.
7. **Every refusal before a call is typed, and never a crash.** A `GatewayRefusal` carries a code
   (`gateway_required`, `gateway_not_configured`, `gateway_assertion_key_missing`) and becomes: a 403 tool
   error of type `network_policy` on reads and writes, a 403 before an approval-gated action exists, a 409
   at declaration and at discovery, a final `EffetRefuse` in the workers. It never creates an action row or a
   cost-ledger line, never answers 500 and never triggers a Temporal retry. The JWKS answers the same 404 for
   an unknown organisation and for one without a key. Rotating the key publishes the new key and keeps the
   previous one in the JWKS.
8. **The client and its proofs.** The MCP client keeps its own list of the versions it speaks (the core's
   `VERSIONS` stays what Choregos's own servers announce), never sends `MCP-Protocol-Version` on `initialize`,
   refuses an unknown negotiated version, initialises again once after a 404 (a lost session on a
   load-balanced gateway) and takes per-request headers (the OAuth token cache, a fresh assertion per call).
   `FakeMcpGateway` is a scriptable fake for every authentication mode with JWKS-verified assertions
   ([ADR 0005](0005-adapters-and-fakes.md)), used by CI, by the demo's fakes server and by the local install's `mcp-gateway` profile; a
   nightly live test against a real gateway **fails**, and does not skip, when a gateway is configured and
   unreachable. No database migration and no Temporal workflow change: effects are activity code.
9. **The servers the platform itself runs are not an upstream, and are not behind the gateway.** ADR 0096's
   plugin servers are allowed: they run in the cluster, are governed as organisation `mcp` connectors with
   `org_connectors.plugin_server` set, are reached at the Service URL the platform wrote, and are never put
   behind the gateway. The gateway's `required` mode does not apply to them. This does not make Choregos a
   gateway: it routes, aggregates and re-serves no **third-party** MCP server, and a plugin server is
   reached only by the platform's own paths (the broker, governed-action effects, discovery, the plugin
   tracker adapter and the plugin webhook), never by a door.

## Consequences

- **What is now allowed**: consuming an organisation's gateway behind the broker, through MCP and standard
  authentication. **What stays refused**: building or being one (hosting, aggregating, virtual servers,
  passthrough, multi-tenant routing of third-party servers), listing a gateway product as a catalogue entry,
  and letting an agent or a runner hold the gateway's address. ADRs 0022, 0028, 0029 and 0065 and
  `docs/positioning.md` say so, each next to the sentence it amends. No Status line changes its first word.
- Stdio and OAuth servers of the catalogue become installable for an organisation that has a gateway, without
  Choregos running a process or holding a grant. An organisation without one loses nothing.
- Governance is unchanged, because the gateway is reached from the broker's path and the connector kind stays
  `mcp`: the same approvals, born-closed tools, ledger lines, digests and journal.
- The gateway sees every call's arguments, after Choregos has decided them: it is the organisation's own
  component, and the organisation chooses to run it. A gateway that is down is a typed refusal on its
  connectors (the runbook is S46-20's), not an outage of direct connectors.
- The vendor-neutral surface costs a line that a vendor's richer admin API cannot cross: where a product
  offers more than MCP and standard HTTP authentication, Choregos does not use it.
- The catalogue's exclusions `gateway` and `aggregator` stay, with a new reason: a gateway product is
  consumed as the organisation's gateway, never a catalogue entry.
- The doors and the broker get a test that fails if a route selects a third-party server by name, if a door
  imports the broker, the MCP client or a plugin server, or if the orchestrator or the runner reads the
  gateway. Platform-run servers and the two forward proxies (ADR 0091's relay, ADR 0070's egress proxy) are
  listed in that test with the ADR that allows each.
- The work is stream S46: the client (S46-03), the fake (S46-04), the connector type (S46-05), the contract
  (S46-06), the assertion keys (S46-07), the setting (S46-08), the broker, governed writes and discovery
  (S46-09), the catalogue installs (S46-10), the end-to-end proof (S46-11), the chart (S46-12), the console
  (S46-13 and S46-14), the local install (S46-15), the live test (S46-16), the documentation (S46-17), the
  broker's rename (S46-18), the network enforcement (S46-19) and the readiness flags with the runbook
  (S46-20). This record proves nothing about any of them: it is the decision and its guard.

## Alternatives rejected

- **Being a gateway** (hosting stdio servers, aggregating servers behind one endpoint, virtual servers,
  multi-tenant routing). Refused by ADR 0028 and ADR 0029 for the same reasons: identity, network and data
  vendors already absorb that market, and it would make Choregos a hosting product that holds third parties'
  credentials.
- **A vendor integration** (a product's SDK, admin API or headers). It would make Choregos's releases follow
  a vendor's, and make the second vendor a rewrite. MCP and HTTP authentication are the public contracts
  every gateway already speaks; ADR 0079 took the same line for graal.
- **Agents calling the gateway directly.** It removes the broker as the policy point: no approval, no
  born-forbidden operation, no injection guard, no ledger line, and a gateway credential in a runner's pod.
- **Storing the upstream credentials in Choregos** so that it can host or relay what the gateway would
  host. It is the hosting product above, with the credentials of every server in one database.
- **Listing gateway products in the MCP catalogue** (ADR 0065). A gateway is not a server an agent may call,
  and an aggregator re-opens the discovery ADR 0022 refuses.
- **One gateway per project or per connector.** Not asked; one per organisation is the outbound proxy's
  shape (ADR 0087), and a connector already names its server.
- **A flag on the direct `mcp` connector** instead of a type. The direct connector holds a URL and a
  credential; a gateway connector holds neither, and a type is what `required` mode and the network checks
  can refuse by name.
