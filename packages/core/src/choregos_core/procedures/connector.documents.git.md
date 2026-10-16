---
id: connector.documents.git
title: Connect a private git repository as a documents source
applies_to: {connector: documents/git}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Managing your personal access tokens (fine-grained)", url: "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens"}
  - {title: "Registering a GitHub App", url: "https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app"}
egress: [github.com, api.github.com]
fields:
  host: "the git host the credential is for, github.com by default"
  auth: "token for an access token, github_app for a GitHub App"
  token: "a fine-grained access token with Contents: read, typed once"
  app_id: "the GitHub App's id, for auth github_app"
  installation_id: "the App's installation on the repository, optional"
  private_key: "the App's private key, typed once"
  api_url: "the GitHub API address, https://api.github.com by default (change it for GitHub Enterprise Server)"
---

## Before you start

- A **private** git repository whose documentation (Markdown by default) a knowledge base should read. A
  public repository needs no connector: a git source reads it directly.
- This is an organisation connector: an organisation administrator declares it once. The credential is
  **pinned to its host**: a source whose repository is on another host is refused, so a token is never
  sent elsewhere. Declare one connector per host.

## At the forge

**Token (simplest).** GitHub: Settings, Developer settings, **Fine-grained personal access tokens**,
Generate new token. Pick **only the repository** and the repository permission **Contents: Read-only**.
Set an expiry and note it. The token is sent to git as HTTP Basic with the user name `x-access-token`; that
is what GitHub expects, and it has **not been verified here against other forges**, so test a GitLab or
Gitea host before relying on it.

**GitHub App (no token to rotate).** Create or reuse an App with the repository permission
**Contents: Read-only**, install it on the repository, and note its **App ID**, the **installation id**
(the number at the end of the installation's address) and a generated **private key**. At each read the
platform mints a one-hour installation token limited to that repository and to `contents: read`.

## Permissions

- Contents read on the one repository, nothing else. No write permission is needed or used.
- The credential travels to git in the environment, never on a command line, in an error message or in
  `.git/config`.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Documents**, type **Git over https**.
2. `host`: `github.com` (the default) or your host.
3. `auth`: `token`, then paste the token in `token`. Or `auth`: `github_app`, then `app_id`, optionally
   `installation_id`, and the App's key in `private_key`. For GitHub Enterprise Server also set `api_url`.
4. **Knowledge**, a base, **Add source**, kind **Git**: pick this connector, then the repository's https
   address, a `ref` (a branch is followed, a commit is pinned) and `paths` (globs; markdown by default).

## Test it

Add the source and press **Sync now**. A repository whose files match `paths` adds documents; a
repository where no file matches is reported unreadable and **deletes nothing**.

## When it fails

- A refusal saying the address is not on the connector's host: the repository is on another host than
  the connector's `host`, by design.
- `Authentication failed` or `404` from git: the token is wrong, expired or has no access to that
  repository; for an App, it is not installed on the repository.
- "not an https address": a git source is fetched over https only.
- Unreadable, nothing deleted: no file matches `paths`, or the `ref` does not exist.
- A timeout: the git fetch is cut at 5 minutes, or the egress allowlist does not open the host.
