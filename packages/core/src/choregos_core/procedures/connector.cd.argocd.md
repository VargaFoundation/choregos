---
id: connector.cd.argocd
title: Promote through Argo CD
applies_to: {connector: cd/argocd}
audience: operator
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Argo CD user management (API tokens)", url: "https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/"}
  - {title: "Argo CD webhooks and notifications", url: "https://argo-cd.readthedocs.io/en/stable/operator-manual/notifications/"}
egress: []
values: ["webhook_url:argocd"]
fields:
  gitops_repo: "owner/repo of the GitOps repository the promotions are written to"
  base_url: "the Argo CD server's address"
  app_pattern: "how an application is named, {app}-{env} by default"
  path_prefix: "the directory of the applications in the GitOps repository, apps by default"
  token: "an Argo CD API token, read only, typed once"
---

## Before you start

- Argo CD, and a **GitOps repository** it watches. Choregos never runs `kubectl apply`: a promotion is a
  pull request on that repository, and Argo CD applies it.
- The GitHub App of the platform installed on the GitOps repository (see the GitHub procedures): the
  writes go through it.
- The platform's shared webhook secret (`CHOREGOS_GENERIC_WEBHOOK_SECRET`).

## At Argo CD

1. Create a local account or use a project role for the platform, and generate an **API token** for it
   (`argocd account generate-token --account <name>`). Give it read access to the applications only.
2. Send application health events to `{{webhook_url:argocd}}` with the header `X-Choregos-Secret` set to
   the shared secret (an Argo CD Notifications webhook service does this).
3. Name applications by one pattern. The connector looks for `{app}-{env}` (for example `billing-prod`)
   unless `app_pattern` says otherwise.

## Permissions

- The API token: `get` on applications, nothing more. Writes never use it.
- The GitHub App: `contents` and `pull_requests` write on the GitOps repository.

## In Choregos

1. Project settings, Connectors, **CD**, type **Argo CD**.
2. `gitops_repo`: `owner/repo`. `base_url`: the Argo CD server's address. `path_prefix` and `app_pattern`
   if your layout differs from `apps/` and `{app}-{env}`.
3. `token`: the API token, in the password field.

## Test it

Press **Test**: it calls Argo CD's `/api/version` and shows the version. A promotion on a test
application then proves the GitOps write.

## When it fails

- `401` or `403` on the test: the token is wrong, expired, or the account has no `get` right.
- The pull request is not opened: the GitHub App is not installed on the GitOps repository, or lacks
  `pull_requests: write`.
- Health never changes on the work item: Argo CD's events do not reach the webhook, or the application's
  name does not follow `app_pattern`.
