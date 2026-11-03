# The MCP gateway is down or refusing

An organisation can route its MCP servers through its own gateway (ADR 0105). This page is for the
day that gateway does not answer, or answers with a refusal.

## How to know it is this

- A tool call fails with an error of type `network_policy` and code `gateway_required` (the
  organisation's mode is `required` and the connector is a direct `mcp` one) or
  `gateway_not_configured` (an `mcp-gateway` connector, but the gateway is `off`, absent, or its
  assertion key is lost).
- `POST /api/v1/orgs/{org}/mcp-gateway/test` fails.
- `network_decisions` rows of layer `broker` on the gateway host are denials, or there are none at
  all while runs call tools.
- A project's readiness shows the `mcp_gateway` item as `todo` (direct connectors a required gateway
  refuses) or `check` (the gateway is off).

A plugin's server (ADR 0096, ADR 0097) is never refused by the gateway mode. A failure there is a
plugin-server matter ([docs/plugins.md#servers](../plugins.md#servers), the `choregos-plugins`
namespace), not a gateway one.

## Commands

1. Run the test endpoint and read the failure it names:
   `curl -X POST -H "Authorization: Bearer $TOKEN" $API/api/v1/orgs/$ORG/mcp-gateway/test`
2. The gateway's own egress: check the `global.mcpGateway` NetworkPolicy lets the API reach the
   gateway host (`kubectl -n choregos get networkpolicy`).
3. The organisation's egress rules of origin `mcp-gateway` must name the gateway host: list them on
   the organisation's network page, or query the `network_rules` table.
4. With `jwt_assertion`, the gateway must reach the JWKS URL of the API to verify the assertion:
   `curl -sS $API/.well-known/jwks.json` from the gateway's network.
5. With `oauth_client_credentials`, the API must reach the OAuth token endpoint configured in the
   setting, and the client secret must still be in the secret store.
6. A mode `required` that refuses direct connectors is working as intended: declare them as
   `mcp-gateway` connectors, or set the mode to `available` while the gateway is repaired.

## How to check it is fixed

- `POST .../mcp-gateway/test` answers `ok`.
- A tool call through an `mcp-gateway` connector succeeds; its `network_decisions` row is `allow`.
- The project's readiness shows `mcp_gateway` as `done`.
