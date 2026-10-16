---
id: connector.scm.github
title: Use GitHub for branches, pull requests and checks
applies_to: {connector: scm/github}
audience: project_owner
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Registering a GitHub App", url: "https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app"}
  - {title: "Permissions required for GitHub Apps", url: "https://docs.github.com/en/rest/overview/permissions-required-for-github-apps"}
  - {title: "Managing a merge queue", url: "https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue"}
egress: [api.github.com]
values: ["webhook_url:github"]
fields:
  repo: "owner/repo"
  installation_id: "the number at the end of the installation's address"
  merge_queue: "true to enqueue pull requests in the repository's merge queue"
  merge_method: "MERGE, SQUASH or REBASE"
---

## Before you start

- The same **GitHub App** as the tracker (see "Use GitHub Issues and Projects v2 as the board"): one App
  serves both. Its id, private key and webhook secret are platform settings.
- The repository the agents change. They work on a branch and open a pull request; nothing is pushed to
  the default branch.

## At GitHub

1. Create the App with the **Webhook URL** `{{webhook_url:github}}`, or reuse the platform's.
2. **Install** it on the repository.
3. For `merge_queue`, enable a **merge queue** in the repository's branch protection (Settings, Branches).
   Without one, set `merge_queue` to false.

## Permissions

Repository permissions:

- `contents`: write
- `issues`: write
- `pull_requests`: write
- `checks`: write
- `metadata`: read

Organization permissions, only if the same App serves the Projects v2 board:

- `organization_projects`: write

Subscribe to these events: `issues`, `issue_comment`, `projects_v2_item`, `pull_request`, `pull_request_review`, `check_suite`, `check_run`.

Add `workflows: write` only if agents edit `.github/workflows`.

## In Choregos

1. Project settings, Connectors, **SCM**, type **GitHub (App)**.
2. `repo`: `owner/repo`. `installation_id`: the installation's number.
3. `merge_queue`: true (the default) enqueues through the merge queue; `merge_method`: `SQUASH` (the
   default), `MERGE` or `REBASE`.

## Test it

Press **Test**: it reads the repository through the App. A run that opens a pull request then proves
the write permissions.

## When it fails

- `404` on the repository: the App is not installed on it.
- `403` on a branch, a pull request or a check: a permission above is missing or its upgrade was not
  accepted on the installation.
- A merge that never happens: the merge queue is not enabled on the branch, or a required check is red.
- A push refused for a workflow file: the App lacks `workflows: write`.
- A timeout: the egress allowlist must open `api.github.com`.
