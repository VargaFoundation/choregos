# 0135 — A tool call may carry the person's identity: token exchange, consented accounts

- **Status**: proposed, 2026-11-10
- **Concerns**: the broker (`services/broker.py`, `_call`), the connector client
  (`services/plugin_servers.py` `connector_client`), the connector type spec
  (`choregos_adapters.registry.ConnectorTypeSpec`), the sealed store (`secret_store.py`, ADR 0058), the MCP
  door's OAuth (`mcp/oauth.py`), the console's sign-in, a new table `connected_accounts`, the routes
  `/me/connected-accounts/*`, the audit of a tool call; implements [ADR 0107](0107-an-agent-acts-under-an-identity.md)
  point 1 ("on behalf of a person") for conversations and its stories 2 and 3 (S50-01, S50-02) for the
  modes below; builds on [ADR 0058](0058-connector-secrets-are-write-only-values.md),
  [ADR 0096](0096-a-plugin-s-code-runs-as-an-mcp-server-the-platform-runs.md) and
  [ADR 0133](0133-a-plugin-ships-an-assistant-an-organisation-installs-it-for-an-audience.md)

## Context

ADR 0107 decided that an agent acts under an assigned identity, its own, or a person's, and left the person's
mode unbuilt (S50-01 and S50-02 not started). Every connector call today goes out with the connector's
configured credential (`secret_store.resolved_configuration`), or an assertion signed for the run when the
organisation uses an MCP gateway (ADR 0105, `mcp_gateway_identity.identity_of_person` exists for that path
only). An assistant that prepares a meeting must read each source as the person who asks: the source, not
Choregos, decides what that person may read. A source read with a service account would hand the person
whatever the service account sees.

## Decision

1. **Three identity modes per need**, as ADR 0107 named them, spelled as the manifest spells them:
   - `service` — the connector's own credential, as today;
   - `user_exchange` — an RFC 8693 token exchange at each call: the person's token from the identity
     provider is exchanged for a token whose audience is the connector's
     (`token_exchange.audience`), with `act` naming the assistant and its installation when the provider
     allows it;
   - `user_consent` — an OAuth authorization code with PKCE at the source's own authorization server, once
     per person; the refresh token is sealed (ADR 0058) in a `connected_accounts` row of the person's
     personal space.
   A connector declares the modes it supports (`identity_modes`, and for an exchange the audience); a
   binding (ADR 0133) is refused if its connector does not support the need's mode.
2. **The subject token for an exchange** comes from the request: the bearer the MCP door received (when the
   provider accepts it as a subject token), or, for the console and the embedded component, a refresh token
   of the provider sealed per person at sign-in, from which a fresh access token is drawn server-side.
3. **Never a fallback to the service identity.** A call in a person's mode without a valid subject token
   fails with `identity_unavailable`; a `user_consent` need without an account fails with
   `account_not_connected` and the console's consent URL, which the agent passes on to the person before
   going on without that source. Both are typed tool errors, journaled, and never retried under another
   identity.
4. **The exchanged token is used for one call, server-side.** It is never stored, logged, handed to a run
   or to an agent's runtime (ADR 0107 point 7). A plugin server (ADR 0096) in `user_consent` receives the
   person's token in `X-Choregos-User-Token`, next to the platform token that authenticates Choregos; in
   `user_exchange` its bearer is the exchanged token.
5. **The call says who it was for.** The `TOOL_CALLED` event and the reads journal carry `as:
   person:<subject>` and the mode (ADR 0107 point 8); `service` calls carry `assigned:<connector>`.
6. **A person owns their accounts.** `GET /me/connected-accounts`, `POST
   /me/connected-accounts/{connector}/connect` (the redirect), the callback, `DELETE` (revocation at the
   source when it offers it, and the sealed secret deleted at once). The catalogue no longer refuses an
   OAuth server with `needs_oauth` when it is bound to a `user_consent` need.

## Alternatives rejected

- **The connector's service account, filtered by Choregos.** Choregos would decide who reads what about
  whom, which is the source's job and which it cannot know.
- **Handing the person's token to the agent.** Refused by ADR 0107 point 7.
- **Long-lived exchanged tokens cached per person.** A cache of tokens is a store of identities to steal;
  an exchange per call costs one round trip to the provider.

## Consequences

- The deployment's identity provider must allow the platform's client to exchange towards each source's
  audience; a live test (`make test-live`) runs against Keycloak, every other test against a fake provider.
- Sources must accept exchanged tokens and may restrict what they serve when `act` is present.
- A security review of the exchange (S63-07) and of offline tokens (ADR 0137) is required before
  production.
