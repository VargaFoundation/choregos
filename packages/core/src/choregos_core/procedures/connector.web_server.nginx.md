---
id: connector.web_server.nginx
title: Read nginx configuration from the GitOps repository
applies_to: {connector: web_server/nginx}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "nginx documentation", url: "https://nginx.org/en/docs/"}
egress: [api.github.com]
fields:
  repo: "owner/repo of the GitOps repository"
  ref: "the branch or commit read, main by default"
  root: "the directory that holds the configuration, nginx by default"
  api_url: "the forge's API, https://api.github.com by default"
  token: "a token that can read, and open pull requests on, the repository, typed once"
---

## Before you start

- The configuration lives in a **GitOps repository**; the platform reads the files from there, not from the
  live server.
- Changes are proposed as pull requests (`propose_nginx_config`). Validation (`nginx -t`) runs in the
  runner on the agent's workspace; files that exist only on the server, such as certificates, are
  reported as missing: the report is shown, not hidden.

## At the forge

Create a token (a fine-grained personal access token on the repository, or a GitHub App's) that can read
contents and open pull requests there.

## Permissions

Contents read, and pull requests write on that repository. Nothing reaches the server: the release
process applies what is merged.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Web server**, type **nginx**.
2. `repo`: `owner/repo`. `ref`, `root` if your layout differs. `api_url` for a self-hosted forge.
3. `token`: in the password field.

## Test it

Press **Test**, then call `list_nginx_files` and `read_nginx_file`.

## When it fails

- `404` on the repository or the root: wrong `repo`, `ref` or `root`, or the token cannot see it.
- `401` or `403`: the token is wrong, expired or too narrow.
- `nginx -t` reports a missing file: a certificate or an include that exists only on the server.
