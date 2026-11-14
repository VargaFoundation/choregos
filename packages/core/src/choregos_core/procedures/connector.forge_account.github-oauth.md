---
id: connector.forge_account.github-oauth
title: Let members link their GitHub account
applies_to: {connector: forge_account/github-oauth}
audience: org_admin
version: 1
reviewed: 2026-11-12
vendor_docs:
  - {title: "Creating an OAuth app", url: "https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app"}
  - {title: "Authorizing OAuth apps", url: "https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps"}
  - {title: "Scopes for OAuth apps", url: "https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps"}
  - {title: "Generating a user access token for a GitHub App", url: "https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app"}
egress: [github.com, api.github.com]
values: ["oauth_callback:linked-accounts"]
fields:
  base_url: "https://github.com, or your GitHub Enterprise Server's address"
  client_id: "the OAuth app's Client ID"
  client_secret: "a client secret of the OAuth app, typed once"
---

## Before you start

- An organisation owner at GitHub (or a person who may create an OAuth app for it).
- What it is for: each member signs in to GitHub once from **My account** and the platform records
  their GitHub numeric user id as a **verified** link. The id is what a signed GitHub comment carries
  when a member pauses, resumes or stops a work item from the issue.
- The platform reads the member's user once, at the link, and keeps neither the access token nor
  anything else than the id, the login, the avatar and the profile address.

## At GitHub

1. Your organisation's settings, **Developer settings**, **OAuth Apps**, **New OAuth App**.
2. **Homepage URL**: `{{public_url}}`. **Authorization callback URL**: `{{oauth_callback:linked-accounts}}`.
3. **Register application**, copy the **Client ID**, then **Generate a new client secret** and copy it:
   it is shown once.

A GitHub App's client id and secret work too (its user authorization uses the same endpoints and the
same callback URL). Give that App no repository or organisation permission: the platform only reads
`/user` with it. Do not reuse the App the platform uses for repositories.

## Permissions

The platform asks for the `read:user` scope and nothing else: it can read the member's public profile,
not their repositories, organisations or e-mail addresses. With a GitHub App the scope is ignored and
the App's own permissions apply: keep them empty.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Member accounts**, type **GitHub sign-in**.
2. `base_url`: leave `https://github.com`, or your GitHub Enterprise Server's address.
3. `client_id`: the Client ID. `client_secret`: the client secret, in the password field.
4. Tick **open the hosts in the egress allowlist** (or open `github.com` and `api.github.com` yourself).

## Test it

Open **My account**, **Linked accounts**, **Link** next to GitHub. After signing in at GitHub you come
back to the page and the account shows with a **Verified** badge and your GitHub login.

## When it fails

- `link_error=forge_refused` and GitHub showed "The redirect_uri is not associated with this
  application": the callback URL at GitHub differs from `{{oauth_callback:linked-accounts}}`.
- `link_error=forge_refused` right after consenting: the client secret is wrong or was regenerated.
- `link_error=instance_unusable`: the connector's secret cannot be read; type it again.
- `link_error=taken`: that GitHub account is already linked to another member of the organisation; an
  administrator removes the other link first.
- A timeout, or `egress_refused` in the logs: open `github.com` and `api.github.com` in the egress
  allowlist.
