---
id: connector.tracker.jira
title: Use Jira Cloud as the board
applies_to: {connector: tracker/jira}
audience: project_owner
version: 2
reviewed: 2026-10-28
vendor_docs:
  - {title: "Manage API tokens for your Atlassian account", url: "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/"}
  - {title: "Jira automation actions (Send web request)", url: "https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/"}
egress: ["<site>.atlassian.net"]
values: ["webhook_url:jira"]
fields:
  base_url: "https://<site>.atlassian.net"
  email: "the email of the account the API token belongs to"
  project_key: "the project's key, the prefix of its issue keys (BILL in BILL-42)"
  field_names: "a map of the custom fields Choregos writes (Cost, Size, Risk, Run) to their names in your Jira"
  api_token: "a classic API token, typed once"
  webhook_secret: "not used by the Jira webhook endpoint: the platform's shared webhook secret is"
---

## Before you start

- A Jira Cloud site and an Atlassian account **dedicated to Choregos**: its permissions are what the
  platform may do in the project, and comments and transitions appear under its name.
- The project key.
- The platform's shared webhook secret (`CHOREGOS_GENERIC_WEBHOOK_SECRET`), held by whoever operates
  Choregos. Jira Cloud does not sign webhooks, so the platform accepts a delivery only if it carries this
  secret in an `X-Choregos-Secret` header.

## At Atlassian

1. As the dedicated account: `id.atlassian.com`, Security, **API tokens**, **Create API token**. Copy the
   token when it is shown.
2. In the Jira project, give that account the permissions below.
3. **Webhook.** The endpoint is `{{webhook_url:jira}}` and it requires the header `X-Choregos-Secret`.
   Jira's own System, **WebHooks** screen lets you set a URL; check there whether it can add a header.
   If it cannot, use a **Jira Automation** rule: trigger on issue created, issue updated and comment
   added in the project, action **Send web request** to that URL with a custom header
   `X-Choregos-Secret`. This path has not been exercised against a live site from this repository.

## Permissions

On the project, for the dedicated account: **Browse projects**, **Create issues**, **Edit issues**,
**Transition issues**, **Add comments**.

A classic API token carries all of the account's rights. A scoped API token works only through
`https://api.atlassian.com/ex/jira/<cloudId>` as the `base_url`; that variant is **not proven** here, so
use a classic token unless you have tested it.

## In Choregos

1. Project settings, Connectors, **Tracker**, type **Jira Cloud**.
2. `base_url`: `https://<site>.atlassian.net`. `email`: the dedicated account's email. `project_key`: the
   key. A webhook delivery reaches the project whose Jira connector holds the issue's key, whatever the
   project's slug; two projects holding the same key are told apart by the Jira site, and a key held
   twice on the same site routes nothing.
3. `field_names`: only if your Jira names the custom fields Choregos writes differently. They are
   `Cost (€)`, `Size`, `Risk` and `Run`.
4. `api_token`: the token, in the password field (sealed, shown once).
5. Label an issue `agent-ready`: the platform starts one workflow per issue.

## Test it

Press **Test** on the connector (it reads the project). Then create an issue labelled `agent-ready` and
watch it appear on the board.

## When it fails

- `401`: wrong email or a revoked token. The email and the token must belong to the same account.
- `403`: the account lacks one of the project permissions above.
- `404` on the project: wrong `project_key`, or the account cannot browse it.
- A webhook answered `401 invalid shared secret`: the `X-Choregos-Secret` header is missing or differs
  from the platform's shared secret.
- Nothing starts after labelling: the label must be exactly `agent-ready`, and the webhook must reach
  this platform from Jira.
- A timeout: the egress allowlist must open the Atlassian site.
