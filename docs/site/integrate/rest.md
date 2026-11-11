---
title: Script Choregos with the REST API
description: Call the REST API from a script or a CI job with a full-access token, to open a work item, follow it and list a project's work.
sidebar_position: 4
---

# Script Choregos with the REST API

Your CI job should open a work item when a release branch is cut, and your weekly report should list
what is waiting. Both are a few HTTP calls to the **REST API**, the same API the console and the CLI
use.

**What you will be able to do:** get a token for a script, open a work item in a workflow with its
fields, read it back, list a project's work items, and understand the API's errors.

**Before you start:**

- an account with the right to control work items in the project (here `acme:billing-api`);
- `curl`, or the [CLI](../cli/install.md), which wraps the same calls.

## 1. Get a full-access token

The REST API takes a token of scope `*`. It acts with your rights, never more.

**In the console:** open the menu under your name, **My account**, then *Access tokens*, **Create an
API token (90 days)**. Copy it now: it is not shown again.

**With the CLI**, once signed in:

```bash
choregos tokens create --name weekly-report --expires-in-days 30
```

A token with an MCP or A2A scope is refused by the REST API, and the MCP and A2A doors refuse a
full-access token: one token per use.

```bash
export CHOREGOS_API_URL=https://choregos.example.com CHOREGOS_TOKEN=chg_...
```

## 2. Open a work item

```bash
curl -s "$CHOREGOS_API_URL/api/v1/projects/acme:billing-api/work-items" \
  -H "Authorization: Bearer $CHOREGOS_TOKEN" -H 'Content-Type: application/json' -d '{
  "title": "Add credit notes",
  "body": "Customers need a credit note when an invoice is refunded.",
  "workflow": "ship-a-change",
  "fields": { "ticket": "FIN-42", "priority": "high" },
  "labels": ["billing"],
  "start": true
}'
```

| Key | What it does |
| --- | --- |
| `title` | required, 500 characters at most |
| `body` | the description |
| `workflow` | the workflow it is born in; without it, the project's routing reads the `labels`, then its default applies |
| `fields` | checked by the workflow's `metadata.inputs` |
| `labels` | free labels, read by the routing |
| `size`, `risk` | `S`, `M`, `L`, `XL`; `low`, `medium`, `high`, `critical` |
| `budget_usd` | a budget for this work item, which can only tighten the project's |
| `start` | `true` (the default) starts the workflow at once |

**What you should see:** HTTP `201` and the work item, with its `id`, its `tracker_key` (such as
`BIL-12`) and its `state`.

The same with the CLI:

```bash
choregos items create acme:billing-api --workflow ship-a-change --title "Add credit notes" \
  --body "Customers need a credit note when an invoice is refunded." \
  --field ticket=FIN-42 --field priority=high --label billing
```

## 3. Follow it

```bash
curl -s "$CHOREGOS_API_URL/api/v1/work-items/BIL-12" -H "Authorization: Bearer $CHOREGOS_TOKEN"
curl -s "$CHOREGOS_API_URL/api/v1/projects/acme:billing-api/work-items?limit=20" -H "Authorization: Bearer $CHOREGOS_TOKEN"
```

A work item is named by its `id` or its key. The answer carries `state`, `state_display`,
`pending_request` (a decision waiting for a person), `pr_url` once a pull request is open,
`failure` when it failed, and `totals` (what it spent).

Lists are paged with a cursor. The answer is `{"items": [...], "meta": {...}}`, where `meta` holds
`next_cursor` and `has_more`; pass `cursor=<next_cursor>` to get the next page (`limit` from 1 to 200,
50 by default).

```bash
choregos items show BIL-12
choregos items list acme:billing-api
```

## What a project is called in a URL

`/projects/{id}` takes the project's UUID, `org:slug` (`acme:billing-api`), `org/slug`, or its bare
slug when no other organisation has the same one.

## Errors

Errors follow RFC 9457 (`application/problem+json`):

```json
{
  "type": "about:blank",
  "title": "Invalid request",
  "status": 422,
  "detail": "the body or the parameters do not match the contract",
  "errors": [{ "loc": ["body", "title"], "msg": "Field required" }]
}
```

| Status | Usually |
| --- | --- |
| `401` | a missing, unknown, revoked or expired token |
| `403` | a token with another scope than `*`, or a role without the right |
| `404` | no such resource, or one you cannot see |
| `409` | a conflict: a stale version (`code: stale_base`), a state that does not allow the change |
| `422` | a body the API refuses; `errors` says where and why |
| `502` | an external system refused (an external tracker): nothing was created |

Every answer carries `X-Request-Id`: give it to your operator when something fails on their side. All
the codes are in [Error codes](../reference/errors.md#the-rest-api).

## Go further

- [The API overview](../reference/api.md): the resources, and where the OpenAPI document is.
- [Start a workflow over A2A](a2a.md), for an application that should not hold a person's token.
