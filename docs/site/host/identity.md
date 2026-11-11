---
title: Identity
description: Connect Choregos to your OIDC identity provider, give roles through groups, and let MCP clients sign in with OAuth.
sidebar_position: 3
---

# Identity

People sign in to Choregos through your identity provider, and their roles come from its groups. No
password is stored by Choregos.

**What you will be able to do:** register Choregos at your identity provider, map groups to roles,
and let claude.ai or Claude Code sign in to the MCP door with single sign-on.

## 1. Register the client

At your identity provider, register a **confidential** client:

- redirect URI: `https://choregos.example.com/api/v1/auth/callback` (your `apiUrl` followed by
  `/api/v1/auth/callback`);
- scopes: `openid profile email groups`;
- a `groups` claim in the userinfo answer, and `sub` and `email`.

Put its secret in a Secret under the key `client-secret`, and point the chart at it:

```yaml
global:
  oidc:
    issuer: https://sso.example.com/realms/acme
    clientId: choregos
    secretRef: choregos-oidc
```

The provider must publish a discovery document whose `issuer` equals `global.oidc.issuer`. Choregos
signs in with PKCE, a single-use `state` and a `nonce`. A person is bound to their `sub`: a changed
e-mail keeps the account. **Admin › Identity** shows what the instance reads (issuer, client, redirect
URI to register, whether the secret is set), never a secret's value.

## 2. Give roles through groups

| Group at the identity provider | Role in Choregos |
| --- | --- |
| `choregos:<org>:org-admins` | `org_admin` of the organisation |
| `choregos:<org>:product-owners` | `project_owner` |
| `choregos:<org>:release-captains` | `release_captain` |
| `choregos:<org>:developers` | `developer` |
| `choregos:<org>:viewers` | `viewer` |

The same claim carries the human groups workflows name: a step for `group: architects` is offered to
members carrying `architects` or `choregos:acme:architects`. Groups without the `choregos:<org>:`
prefix count only when `global.oidc.defaultOrg` names the organisation.

The first administrators come from `global.bootstrap.admins` ([Install](install.md#1-write-your-values)).

## Signing in to the MCP door with OAuth

claude.ai, Claude on mobile and ChatGPT call the MCP door from their vendor's cloud and sign in with
OAuth; Claude Code can too, instead of a pasted token. The door becomes an OAuth resource server with:

```yaml
global:
  mcp:
    oauth:
      enabled: true
      audience: choregos-mcp
      clients:
        claude-code: { clientId: choregos-claude-code, callbackPort: 33418 }
        claude-ai: { clientId: choregos-claude-ai }
```

- `audience` is the audience your provider puts in the door's tokens: a token issued for another
  application does not open the door.
- `clients` names the clients you registered at your provider, by the console's names
  (`claude-code`, `claude-desktop`, `claude-ai`, `cursor`, `vscode`, `chatgpt`, `other`): the
  Integrations page then shows each one's exact setup. An unknown name stops the API at start-up.
- `issuer` is empty by default: the console's own issuer.

The door publishes `/.well-known/oauth-protected-resource/mcp`. For claude.ai and ChatGPT, it and
`/mcp` must be reachable on a public HTTPS address. A person must have signed in to the console once
before the door knows them. Setting up Keycloak for it:
[runbook](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/runbooks/keycloak-mcp.md).

## What can go wrong

| Symptom | Likely cause |
| --- | --- |
| sign-in answers `503` | the discovery document cannot be read, or its `issuer` differs from `global.oidc.issuer` |
| sign-in answers `401` | the ID token's issuer, audience or nonce does not match the sign-in |
| a person signs in with no role | no `choregos:<org>:…` group, or an unprefixed group without `defaultOrg` |
| an existing account is not linked | the provider says `email_verified: false` |
| `401 invalid_token` at the MCP door with OAuth | a wrong audience or issuer, or keys the provider no longer publishes |

Everything about identity, including moving to a new provider and per-organisation providers:
[deployment reference, Identity](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/deployment.md#identity-what-the-oidc-provider-must-give-us).
