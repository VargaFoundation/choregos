---
id: connector.ci.gitlab
title: Read pipeline status from GitLab CI
applies_to: {connector: ci/gitlab}
audience: project_owner
version: 1
reviewed: 2026-11-04
vendor_docs:
  - {title: "GitLab pipelines API", url: "https://docs.gitlab.com/api/pipelines/"}
  - {title: "GitLab project access tokens", url: "https://docs.gitlab.com/user/project/settings/project_access_tokens/"}
  - {title: "GitLab webhooks", url: "https://docs.gitlab.com/user/project/integrations/webhooks/"}
egress: [gitlab.com]
values: ["webhook_url:gitlab"]
fields:
  base_url: "https://gitlab.com, or your self-managed GitLab"
  project: "the project's path with namespace, or its numeric id"
  token: "a project access token, typed once"
---

## Before you start

- A GitLab project with CI pipelines, where you are a Maintainer (to create a token and a webhook).
- The platform's shared webhook secret (`CHOREGOS_GENERIC_WEBHOOK_SECRET`), held by whoever operates
  Choregos: GitLab sends it as the webhook's secret token, and without it the endpoint refuses every
  delivery.

## At GitLab

1. Project, Settings, **Access tokens**, **Add new token**: role **Developer**, scope **api**, and an
   expiry. Copy the token when it is shown.
2. Project, Settings, **Webhooks**, **Add new webhook**: URL `{{webhook_url:gitlab}}`, **Secret token**
   the platform's shared webhook secret, and the trigger **Pipeline events**. If the project already has
   this webhook for its tracker, tick **Pipeline events** on it instead of adding a second one.

## Permissions

- The token needs the `api` scope and the Developer role: it reads pipelines and job traces, and it
  creates pipelines. A `read_api` token can read but cannot trigger.
- The token is project-scoped: it cannot touch another project.

## In Choregos

1. Project settings, Connectors, **CI**, type **GitLab CI**.
2. `base_url`: `https://gitlab.com` (the default) or your self-managed address. `project`: the project's
   path (for example `group/app`) or numeric id.
3. `token`: the access token, in the password field (sealed, shown once).

## Test it

Press **Test**: the connector reads the project and answers `ok`. Then push a commit and watch its
pipeline state change on the work item.

## When it fails

- `401` from GitLab: the token is wrong, expired or revoked.
- `403` or `404` on the project: the token belongs to another project, or the path is wrong.
- Status stays `unknown`: no pipeline exists for that commit (check the project's `.gitlab-ci.yml` rules).
- A webhook that GitLab shows as failing with `401`: the Secret token differs from the platform's shared
  webhook secret.
- A self-managed GitLab with a private certificate authority: the API must trust it.
