---
id: connector.tracker.jira
title: Use Jira Cloud as the board
applies_to: {connector: tracker/jira}
audience: project_owner
version: 5
reviewed: 2026-11-01
vendor_docs:
  - {title: "Manage API tokens for your Atlassian account", url: "https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/"}
  - {title: "Create an API token for a service account", url: "https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/"}
  - {title: "Jira automation actions (Send web request)", url: "https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/"}
egress: ["<site>.atlassian.net", "api.atlassian.com (scoped token)"]
values: ["webhook_url:jira"]
fields:
  base_url: "https://<site>.atlassian.net, the site itself, even with a scoped token"
  email: "the email of the account the API token belongs to (a service account's ends in @serviceaccount.atlassian.com)"
  auth: "`scoped` for a service account's token, which carries the scopes read:jira-work and write:jira-work; `basic` for a classic token"
  cloud_id: "leave empty: read from the site at the first call, and shown by Test"
  candidates_jql: "optional: the JQL of the issues the platform takes on by itself, without a webhook (BILL issues matching it, not done)"
  match_labels: "optional: when several projects share this Jira project, the labels that send an issue here; the project without them takes the rest (S59-03)"
  project_key: "the project's key, the prefix of its issue keys (BILL in BILL-42)"
  field_names: "a map of the custom fields Choregos writes (Cost, Size, Risk, Run) to their names in your Jira"
  api_token: "the API token, typed once"
  webhook_secret: "not used by the Jira webhook endpoint: the platform's shared webhook secret is"
---

## Before you start

- A Jira Cloud site and an Atlassian account **dedicated to Choregos**: its permissions are what the
  platform may do in the project, and comments and transitions appear under its name. The best fit
  is an Atlassian **service account** (no licence seat, no password, an organisation administrator
  creates it); a regular account dedicated to Choregos works too.
- The project key.
- The platform's shared webhook secret (`CHOREGOS_GENERIC_WEBHOOK_SECRET`), held by whoever operates
  Choregos. Jira Cloud does not sign webhooks, so the platform accepts a delivery only if it carries this
  secret in an `X-Choregos-Secret` header.

## At Atlassian

1. **The token.**
   - A **service account** (recommended): `admin.atlassian.com`, **Directory**, **Service accounts**,
     the account, **Create credentials**, **API token**. Tick exactly two scopes, both from the
     **classic** list: `read:jira-work` and `write:jira-work`. Nothing else, and no granular scope.
     Pick the longest expiry offered and **note the date**: the token stops working then.
   - A regular dedicated account: signed in as it, `id.atlassian.com`, Security, **API tokens**,
     **Create API token** (a classic token, without scopes).

   Copy the token when it is shown: it is shown once.
2. **The project.** Jira project, **Project settings**, **People** (or **Access**): add the account
   with a role that holds the permissions below. **A token's scopes give no right on a project by
   themselves**: without this step every call answers `404` or `403`.
3. **Webhook.** The endpoint is `{{webhook_url:jira}}` and it requires the header `X-Choregos-Secret`.
   Jira's own System, **WebHooks** screen lets you set a URL; check there whether it can add a header.
   If it cannot, use a **Jira Automation** rule: trigger on issue created, issue updated and comment
   added in the project, action **Send web request** to that URL with a custom header
   `X-Choregos-Secret`. This path has not been exercised against a live site from this repository.

## Permissions

On the project, for the dedicated account: **Browse projects**, **Create issues**, **Edit issues**,
**Transition issues**, **Add comments**.

What the two scopes cover is exactly what the connector calls: `read:jira-work` for the search, an
issue, its transitions, the project and the list of fields; `write:jira-work` for the labels, the
transitions, the comments, the new issues and the links between issues. A classic token, without
scopes, carries all of the account's rights.

A scoped token is **refused by the site** (`401`): it works only through the gateway,
`https://api.atlassian.com/ex/jira/<cloud_id>`. With `auth: scoped` Choregos goes through it by itself;
keep the site in `base_url`, the links to issues point to it.

**What Choregos writes, and as whom.** The workflow's bookkeeping goes through this connector, through
Jira's REST API, under the dedicated account: the status, the status comment, the `Cost (€)`, `Size`,
`Risk` and `Run` fields, the questions to a person, the close-out. The connector card says
"Choregos writes as" that email, and each line of a work item's timeline that wrote to Jira says
"via Jira as" it. An agent's own actions do not go through this connector: they go through the MCP
tools its version names, under each tool connector's account, listed on its run.

## In Choregos

1. Project settings, Connectors, **Tracker**, type **Jira Cloud**.
2. `base_url`: `https://<site>.atlassian.net`, the site, whatever the token. `email`: the dedicated
   account's email. `auth`: `scoped` for a service account's token, `basic` (the default) for a classic
   one. Leave `cloud_id` empty: Choregos reads it from the site. `project_key`: the key. A webhook delivery reaches the project whose Jira connector holds the issue's key, whatever the
   project's slug; two projects holding the same key are told apart by the Jira site, and a key held
   twice on the same site routes nothing.
3. `field_names`: only if your Jira names the custom fields Choregos writes differently. They are
   `Cost (€)`, `Size`, `Risk` and `Run`.
4. `api_token`: the token, in the password field (sealed, shown once).
5. Label an issue `agent-ready`: the platform starts one workflow per issue.
6. Or skip the webhook and the label altogether: set `candidates_jql`, for example
   `parent = BILL-1 AND labels in (form-a, form-b)`. Every minute the platform searches the project's open
   issues matching it and starts one workflow per new one; an issue already started never starts again.
   The issue's labels still pick its workflow. Exclude what another tool already handles (for example
   `AND created >= "2026-11-02"`), or the first search takes it on too.
7. Nothing to set for what the agents learn about an issue: when the platform takes one on (the poll,
   or a webhook delivery, which re-reads the issue), it keeps on the work item, as `fields.tracker`,
   the **reporter** and the **creator** (`account_id`, `display_name`, and `email` only when Jira
   returns it: it is hidden unless the person's profile shows it), the **labels**, and the **answers**
   of the issue's form: every non-empty custom field, keyed by its display name, as text (an option's
   value, a user's name, a list joined with commas, rich text as plain text), each cut at 2000
   characters. Agents read it as `ticket.fields.tracker`, jobs in `.choregos/fields.json`. It is the
   requester's text, never an instruction. Each webhook delivery refreshes it; an issue the poll alone
   feeds keeps what it said when it was taken on.

## Test it

Press **Test** on the connector. It reads the project with the token, which proves the email, the
token, its scopes and the account's place in the project at once; it answers the project's name and,
for a scoped token, the `cloud_id`. Then create an issue labelled `agent-ready` and watch it appear on
the board.

## When it fails

- `401`: wrong email, or an expired or revoked token. The email and the token must belong to the same
  account. A service account's token with `auth: basic` is refused this way too: set `auth: scoped`.
- `403`: the token lacks `read:jira-work` or `write:jira-work`, or the account lacks one of the project
  permissions above.
- `404` on the project: wrong `project_key`, or the account is not in the project (step 2 at Atlassian).
- `cannot read the cloud id`: the site's `/_edge/tenant_info` did not answer; set `cloud_id` by hand
  (it is on that page, from a browser).
- A webhook answered `401 invalid shared secret`: the `X-Choregos-Secret` header is missing or differs
  from the platform's shared secret.
- Nothing starts after labelling: the label must be exactly `agent-ready`, and the webhook must reach
  this platform from Jira.
- A timeout: the egress allowlist must open the Atlassian site and, for a scoped token,
  `api.atlassian.com`.
