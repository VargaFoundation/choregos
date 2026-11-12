---
id: connector.tracker.github-issues
title: Use GitHub Issues and Projects v2 as the board
applies_to: {connector: tracker/github-issues}
audience: project_owner
version: 3
reviewed: 2026-11-12
vendor_docs:
  - {title: "Registering a GitHub App", url: "https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app"}
  - {title: "Permissions required for GitHub Apps", url: "https://docs.github.com/en/rest/overview/permissions-required-for-github-apps"}
  - {title: "Installing your own GitHub App", url: "https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app"}
  - {title: "Registering a GitHub App from a manifest", url: "https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest"}
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
  The person who operates Choregos creates it, either from the console (*Administration, GitHub App*:
  the platform registers it on GitHub from a manifest with the permissions and events below, and keeps
  its credentials sealed) or by hand as platform settings (`github-app-id`, `github-app-private-key`,
  `github-webhook-secret`). If the App exists, skip to step 4, "Install".
- The repository and, for a board, the organisation's Projects v2 number.

## At GitHub

Skip steps 1 to 3 when the App was created from the console: the manifest set them.

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
- `statuses`: read

`statuses` lets the platform read a CI that reports commit statuses (Tekton, Jenkins); an App without it
keeps working, and those statuses are not read.

Organization permissions, for the Projects v2 board:

- `organization_projects`: write

Subscribe to these events: `issues`, `issue_comment`, `projects_v2_item`, `pull_request`, `pull_request_review`, `check_suite`, `check_run`.

Add `workflows: write` only if agents must edit files under `.github/workflows`; without it GitHub
refuses such a push.

**What Choregos writes, and as whom.** The workflow's bookkeeping goes through this connector, through
GitHub's REST API, as the Choregos GitHub App (`<app>[bot]` on GitHub): the board column and labels,
the status comment, the questions to a person, the close-out. The connector card names the account
whose installation it goes through, and each line of a work item's timeline that wrote to GitHub says
"via GitHub Issues". An agent's own actions do not go through this connector: they go through the MCP
tools its version names, under each tool connector's account, listed on its run.

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
