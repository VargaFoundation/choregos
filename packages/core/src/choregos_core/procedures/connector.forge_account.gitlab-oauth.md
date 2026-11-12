---
id: connector.forge_account.gitlab-oauth
title: Let members link their GitLab account
applies_to: {connector: forge_account/gitlab-oauth}
audience: org_admin
version: 1
reviewed: 2026-11-12
vendor_docs:
  - {title: "Configure GitLab as an OAuth 2.0 authentication identity provider", url: "https://docs.gitlab.com/integration/oauth_provider/"}
  - {title: "OAuth 2.0 identity provider API", url: "https://docs.gitlab.com/api/oauth2/"}
  - {title: "Users API: get the current user", url: "https://docs.gitlab.com/api/users/"}
egress: [gitlab.com]
values: ["oauth_callback:linked-accounts"]
fields:
  base_url: "https://gitlab.com, or your self-managed instance's address"
  client_id: "the application's Application ID"
  client_secret: "the application's Secret, typed once"
---

## Before you start

- A GitLab group owner (for a group application) or an instance administrator (for an instance-wide
  application on a self-managed GitLab).
- What it is for: each member signs in to GitLab once from **My account** and the platform records their
  GitLab numeric user id as a **verified** link. The access token is used for that one read and dropped.

## At GitLab

1. The group's **Settings**, **Applications** (or, on a self-managed instance, **Admin**, **Applications**),
   **Add new application**.
2. **Redirect URI**: `{{oauth_callback:linked-accounts}}`. Keep **Confidential** ticked.
3. **Scopes**: tick `read_user` only. **Save application**, copy the **Application ID** and the **Secret**.

## Permissions

`read_user` reads the member's own profile: id, username, avatar, profile page. It gives no access to
projects, groups or the API on the member's behalf beyond that read.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Member accounts**, type **GitLab sign-in**.
2. `base_url`: leave `https://gitlab.com`, or your instance's address (`https://gitlab.example`).
3. `client_id`: the Application ID. `client_secret`: the Secret, in the password field.
4. Tick **open the hosts in the egress allowlist**, or open the instance's host yourself.

## Test it

Open **My account**, **Linked accounts**, **Link** next to GitLab. After signing in at GitLab you come
back to the page and the account shows with a **Verified** badge and your GitLab username.

## When it fails

- GitLab says "The redirect URI included is not valid": the Redirect URI differs from
  `{{oauth_callback:linked-accounts}}`.
- GitLab says "The requested scope is invalid": `read_user` is not ticked on the application.
- `link_error=forge_refused` after consenting: wrong Secret, or the application was renewed.
- `link_error=taken`: that GitLab account is already linked to another member of the organisation.
- A timeout: open the instance's host in the egress allowlist.
