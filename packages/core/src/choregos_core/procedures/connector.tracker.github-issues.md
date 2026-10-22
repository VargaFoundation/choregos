---
id: connector.tracker.github-issues
title: Use GitHub Issues and Projects v2 as the board
applies_to: {connector: tracker/github-issues}
audience: project_owner
version: 1
reviewed: 2026-10-19
vendor_docs:
  - {title: "Registering a GitHub App", url: "https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app"}
  - {title: "Permissions required for GitHub Apps", url: "https://docs.github.com/en/rest/overview/permissions-required-for-github-apps"}
  - {title: "Installing your own GitHub App", url: "https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app"}
egress: [api.github.com]
values: ["webhook_url:github"]
fields:
  repo: "owner/repo, picked from the repositories the App's installations show"
  project_number: "the number in the Projects v2 board's address"
  org: "the organisation that owns the Projects v2 board"
  webhook_secret: "not used by the GitHub webhook endpoint: the App's own webhook secret is set on the platform"
---

## Before you start

- A **GitHub App** for the platform: one per installation of Choregos, never a broad personal token.
  Its id, private key and webhook secret are platform settings (`github-app-id`,
  `github-app-private-key`, `github-webhook-secret`), set by the person who installs Choregos.
  If they are set, skip to "Install it".
- The repository and, for a board, the organisation's Projects v2 number.

## At GitHub

1. Organization settings, Developer settings, **GitHub Apps**, **New GitHub App**.
2. **Webhook URL**: `{{webhook_url:github}}`. **Webhook secret**: a random string, the same one the
   platform holds as `github-webhook-secret`. GitHub signs each delivery with it and the platform
   refuses an unsigned one.
3. Set the permissions and events below, then create the App and generate a **private key**.
4. **Install** the App on the repositories Choregos works in, from the console: the repository field's
   **Install on GitHub** button opens GitHub's install page and brings you back. The installation is
   discovered by the platform: there is no number to copy. The App's **Setup URL** is the
   platform's `/api/v1/github/setup`.

## Permissions

Repository permissions:

- `contents`: write
- `issues`: write
- `pull_requests`: write
- `checks`: write
- `metadata`: read

Organization permissions, for the Projects v2 board:

- `organization_projects`: write

Subscribe to these events: `issues`, `issue_comment`, `projects_v2_item`, `pull_request`, `pull_request_review`, `check_suite`, `check_run`.

Add `workflows: write` only if agents must edit files under `.github/workflows`; without it GitHub
refuses such a push.

## In Choregos

1. Project settings, Connectors, **Tracker**, type **GitHub Issues + Projects v2**.
2. `repo`: pick the repository in the list (what the App's installations claimed by your organisation
   see), or type `owner/repo` when the list cannot be read.
3. For a board: `org` and `project_number` (the number in the board's address).
4. Label a ticket `agent-ready`: the platform starts one workflow per ticket.

## Test it

Press **Test** on the connector: it reads the repository through the App. Then label a ticket
`agent-ready` and watch the board.

## When it fails

- `404` on the repository, or the repository missing from the list: the App is not installed on it, or
  its installation is not claimed by your organisation. Press **Install on GitHub** (or add the
  repository to the installation), then **Refresh**.
- `403` and a permission named: grant it in the App's settings and **accept** the new permissions on the
  installation (GitHub asks the owner to approve a change).
- Webhook deliveries refused with `401`: the secret on the App differs from the platform's
  `github-webhook-secret`.
- Nothing starts when a ticket is labelled: check the App's **Recent deliveries**; the URL must be this
  platform's and reachable from GitHub.
- A timeout: the egress allowlist must open `api.github.com`.
