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
   - `claude-code` — public, standard flow, PKCE `S256`, redirect URI
     `http://localhost:<port>/callback`, the port given to
     `claude mcp add --transport http choregos https://<console-host>/mcp --client-id claude-code --callback-port <port>`.

The client's ID lands in the audit log (`mcp.call`, `oauth:<client>:<sub>`): one client per kind
of client keeps the log readable.

## Turning it on

In the API's values (or its environment):

```yaml
choregos-api:
  env:
    CHOREGOS_MCP_OAUTH_ENABLED: "true"
    CHOREGOS_MCP_OAUTH_AUDIENCE: choregos-mcp
    # CHOREGOS_MCP_OAUTH_ISSUER: empty, the console's issuer — set it only for another IdP
```

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
