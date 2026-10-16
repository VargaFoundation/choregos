---
id: connector.documents.confluence
title: Connect Confluence Cloud
applies_to: {connector: documents/confluence}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Manage API tokens for service accounts", url: "https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/"}
  - {title: "Scoped API tokens in Confluence Cloud", url: "https://support.atlassian.com/confluence/kb/scoped-api-tokens-in-confluence-cloud/"}
  - {title: "Manage API tokens for your Atlassian account", url: "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/"}
egress: [api.atlassian.com, "<site>.atlassian.net"]
fields:
  base_url: "https://<site>.atlassian.net"
  email: "the email of the account the token belongs to (the service account's, for a scoped token)"
  auth: "basic for a classic token, scoped for a scoped token"
  cloud_id: "scoped tokens only: the site's cloud id"
  api_token: "the token, typed once"
---

## Before you start

- An Atlassian administrator, for a **service account** (recommended) or a dedicated user.
- The Confluence space (and, if you want only a branch, the page) to read. The account's permissions are
  what Choregos can read: everything it reads in the space becomes readable by everyone the knowledge
  base is open to, whatever the page restrictions say for other people.

## At Atlassian

Two ways; the scoped one is recommended.

**Scoped token on a service account (recommended).** Atlassian Administration, Directory,
**Service accounts**: create one, then **Create credentials**, an **API token with scopes**. The expiry
is between 1 and 365 days: note it. Copy the token when it is shown.

**Classic token on a user account.** On `id.atlassian.com`, Security, **API tokens**, **Create API
token**. It carries all of that user's rights: prefer a Confluence-only user.

## Permissions

- Scopes for a scoped token: `read:space:confluence`, `read:page:confluence`,
  `read:hierarchical-content:confluence`, plus `read:attachment:confluence` when the source indexes
  attachments.
- Give the account **read** on the spaces to index (Space settings, Permissions) and nothing else.
- A scoped token is only accepted through `https://api.atlassian.com/ex/confluence/<cloudId>/…`, with
  Basic authentication using the service account's email. Choregos does that when `auth` is `scoped`.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Documents**, type **Confluence**.
2. `base_url`: `https://<site>.atlassian.net`. `email`: the account's email.
3. `auth`: `basic` (the default) for a classic token, `scoped` for a scoped one. For `scoped`, set
   `cloud_id`; the site publishes it at `https://<site>.atlassian.net/_edge/tenant_info`.
4. `api_token`: paste the token in the password field (sealed, shown once).
5. **Knowledge**, a base, **Add source**, **Confluence**: pick the connector, a space key (for example
   `ENG`; a personal space starts with `~`), optionally a page id (the number in
   `…/wiki/spaces/ENG/pages/<id>/Title`) for that page and everything under it, labels, attachments.

## Test it

Add the source and press **Sync now**: it lists the space once and downloads each page. A first sync
that adds documents proves the token, the scopes, `cloud_id` and the space permission.

## When it fails

- `401` with a scoped token: `auth` is still `basic`. A scoped token is refused on the site's own address.
- `401` with a classic token: wrong email, or a revoked token.
- `403`, or an empty space: the account has no read permission on that space, or a scope is missing.
- `404` on the space: the space key is case-sensitive.
- `429`: Atlassian's rate limit. The sync waits for `Retry-After` and tries again.
- A timeout: the egress allowlist must open `api.atlassian.com` (scoped) or the site (classic).
