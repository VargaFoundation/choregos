---
id: connector.forge_account.forgejo-oauth
title: Let members link their Forgejo account
applies_to: {connector: forge_account/forgejo-oauth}
audience: org_admin
version: 1
reviewed: 2026-11-12
vendor_docs:
  - {title: "Forgejo OAuth2 provider", url: "https://forgejo.org/docs/latest/user/oauth2-provider/"}
  - {title: "Forgejo API usage", url: "https://forgejo.org/docs/latest/user/api-usage/"}
values: ["oauth_callback:linked-accounts"]
fields:
  base_url: "your Forgejo's address, https://forge.example"
  client_id: "the OAuth2 application's Client ID"
  client_secret: "the OAuth2 application's Client secret, typed once"
---

## Before you start

- A Forgejo administrator (for an instance-wide application) or the owner of an organisation on it.
- Forgejo is self-hosted: its members usually sign in to it through the same identity provider as the
  platform. Linking proves which Forgejo account is theirs, by its numeric user id.
- The access token is used for one read of the member's user and dropped.

## At Forgejo

1. **Site administration**, **Integrations**, **Applications** (or the organisation's **Settings**,
   **Applications**), **Create a new OAuth2 application**.
2. **Redirect URIs**: `{{oauth_callback:linked-accounts}}`. Keep **Confidential client** ticked.
3. **Create application**, copy the **Client ID** and the **Client secret** (shown once).

## Permissions

The platform asks for `read:user` only: it reads the member's own profile, nothing of their
repositories or organisations.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Member accounts**, type **Forgejo sign-in**.
2. `base_url`: the instance's address, `https://forge.example` (https only).
3. `client_id`: the Client ID. `client_secret`: the Client secret, in the password field.
4. Tick **open the hosts in the egress allowlist**, or open the instance's host yourself.

## Test it

Open **My account**, **Linked accounts**, **Link** next to Forgejo. After signing in at Forgejo you come
back to the page and the account shows with a **Verified** badge and your Forgejo login.

## When it fails

- Forgejo says the redirect URI is not registered: it differs from `{{oauth_callback:linked-accounts}}`.
- `link_error=forge_refused` after consenting: wrong Client secret, or the application was regenerated.
- `link_error=instance_unusable`: the connector's `base_url` is not https, or its secret cannot be read.
- `link_error=taken`: that Forgejo account is already linked to another member of the organisation.
- A timeout: open the instance's host in the egress allowlist.
