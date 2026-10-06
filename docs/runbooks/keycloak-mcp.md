# Signing in to the MCP door with Keycloak (claude.ai, Claude Code over OAuth)

## How to know it is this

- claude.ai shows the connector as *disconnected*, or never opens a sign-in window;
- `curl -s -o /dev/null -w '%{http_code}' https://<console-host>/.well-known/oauth-protected-resource/mcp`
  answers `404`: OAuth is off, or the path is not routed to the API (chart ≥ 0.15);
- the API logs `jeton refusé` on `/mcp` calls: audience, issuer, expiry or keys.

## What the door expects

The door (ADR 0030) is a **resource server**: Keycloak issues the tokens, the door checks them.
It needs, in the realm the console already signs in with:

1. **A client scope `choregos-mcp`** with an *Audience* mapper that adds `choregos-mcp` to the
   access token (`Included Custom Audience`, *Add to access token* on). Without it every token is
   refused: the audience is mandatory, so that a token obtained by another application of a shared
   realm does not open the door.
2. **Two optional client scopes `mcp:read` and `mcp:write`**, *Include in token scope* on. The door
   reads them in the `scope` claim; a token with neither reads only.
3. **One client per kind of MCP client**, each with `choregos-mcp` as a *default* client scope and
   `mcp:read`, `mcp:write` as *optional* ones:
   - `claude-ai` — confidential, standard flow, PKCE `S256`, redirect URI
     `https://claude.ai/api/mcp/auth_callback`. Its ID and secret go in claude.ai's connector,
     *Advanced settings*.
   - `claude-code` — public, standard flow, PKCE `S256`, redirect URIs
     `http://localhost:<port>/callback` and `http://127.0.0.1:<port>/callback` (Claude Code
     v2.1.229 sent the second form), the port given to
     `claude mcp add --transport http --client-id claude-code --callback-port <port> choregos https://<console-host>/mcp`.
4. **`offline_access` as an optional scope of both clients.** Claude adds it to its request as soon
   as the realm's metadata lists it, and Keycloak refuses the whole request (`invalid_scope`) when a
   requested scope is not attached to the client.

The client's ID lands in the audit log (`mcp.call`, `oauth:<client>:<sub>`): one client per kind
of client keeps the log readable.

## Turning it on

In the chart's values (chart ≥ 0.16.3):

```yaml
global:
  mcp:
    oauth:
      enabled: true
      audience: choregos-mcp
      # issuer: empty, the console's issuer — set it only for another IdP
      clients:
        claude-code: { clientId: choregos-claude-code, callbackPort: 33418 }
        claude-ai: { clientId: choregos-claude-ai }
```

`clients` names the clients the realm registered, under the Integrations page's names
(`claude-code`, `claude-ai`, `claude-desktop`, `cursor`, `vscode`, `chatgpt`, `other`): the page then
shows the exact setup instead of a token. A name it does not know stops the API at startup — a typo
would otherwise hide the client without a word. Never put a client secret here: the door checks
tokens, it exchanges none.

Without the chart, the same settings are `CHOREGOS_MCP_OAUTH_ENABLED`, `CHOREGOS_MCP_OAUTH_AUDIENCE`,
`CHOREGOS_MCP_OAUTH_ISSUER` and `CHOREGOS_MCP_OAUTH_CLIENTS` (JSON:
`{"claude-code": {"client_id": "…", "callback_port": 33418}}`). Do not set them in
`global.extraEnv` as well: the chart refuses a name set twice.

A person must have signed in to the console once: the door finds them by the token's `sub`, or by
a verified e-mail, and creates nobody.

## How to check it is fixed

```bash
curl -s https://<console-host>/.well-known/oauth-protected-resource/mcp | jq .authorization_servers
# → ["https://<keycloak>/realms/<realm>"]
curl -s -D - -o /dev/null -X POST https://<console-host>/mcp | grep -i www-authenticate
# → Bearer realm="choregos", resource_metadata="https://<console-host>/.well-known/…/mcp"
```

Then add the connector in claude.ai: it opens Keycloak, comes back connected, and `list_projects`
answers. The person's role still bounds what they see; no decision goes through the door.

## Rotating Keycloak's keys

The door caches the realm's keys for an hour and reloads them as soon as a token names a key it
does not know: a rotation needs nothing on Choregos' side. Keep the old key *passive* (not
disabled) until the tokens it signed have expired — five minutes by default.
