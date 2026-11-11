---
title: Open work items from another system (HTTP triggers)
description: Let a monitoring tool, a form or any system that can only send JSON open work items in a workflow, with its own token.
sidebar_position: 3
---

# Open work items from another system (HTTP triggers)

Your error tracker raises an alert on `billing-api`; you want a work item opened in the
`ship-a-change` workflow, with the alert's details, without anyone copying it by hand. The error
tracker cannot speak A2A, but it can POST JSON to a URL. An **HTTP trigger** turns that JSON into a
work item.

**What you will be able to do:** declare a trigger, map the sender's JSON to a work item's title,
description, labels and fields, mint the trigger's token, give the URL to the sender, and know what
each answer means.

**Before you start:**

- a project whose tracker is Choregos's own (the internal tracker): a trigger does not open issues in
  an external tracker;
- the right to change the project's connectors, to mint the token;
- a workflow to open the work items in (here `ship-a-change`, whose `metadata.inputs` require a
  `ticket` field).

A trigger has its own token and no person behind it. It answers with the work item's id and reports
nothing afterwards: to follow the work, use [A2A](a2a.md) or the [REST API](rest.md).

## 1. Declare the trigger

Open the project, then **Settings › Connections**, card **Triggers**. In *Triggers (JSON)*, declare
it, then **Save**:

```json
[
  {
    "name": "error-alert",
    "title": "Fix: {/title}",
    "body": "Seen in {/culprit}. Details: {/url}",
    "key": "/id",
    "labels": ["bug", "{/level}"],
    "fields": { "ticket": "/issue_key", "priority": "/priority" },
    "workflow": "ship-a-change",
    "when": { "/level": ["error", "fatal"] }
  }
]
```

| Key | What it does |
| --- | --- |
| `name` | the trigger's name, in its URL: lower-case letters, digits and `-` |
| `title` | the work item's title; `{/pointer}` is replaced by the value at that JSON pointer of the payload |
| `body` | the work item's description, with the same placeholders |
| `key` | a JSON pointer to the sender's own id: while a work item opened with that id is open, another call with it opens nothing |
| `labels` | labels, with placeholders; `trigger:<name>` is always added first |
| `fields` | `field: pointer`: the work item's fields, which keep the value's JSON type and are checked by the workflow's `metadata.inputs`; a missing value is left out |
| `workflow` | the workflow to open it in; without it, the project's routing chooses, then its default |
| `when` | `pointer: value` or `pointer: [values]`: the call opens something only when every condition holds |
| `auth` | `token` (the default) or `signature`, see [Signed calls](#signed-calls) |

A placeholder whose value is missing renders as nothing; a number or an object renders as JSON.

## 2. Mint its token

On the trigger's line, choose **Mint the token**. The token (it starts with `chtrg_`) is shown once:
*Shown once: copy it now. The previous token no longer works.* Minting again replaces it.

The line also shows the URL to call:

```text
POST https://choregos.example.com/api/v1/webhooks/triggers/acme/billing-api/error-alert
```

## 3. Give the URL and the token to the sender

In the sender's webhook settings, set the URL, the method `POST`, the header
`Authorization: Bearer chtrg_...` and the content type `application/json`. A sender that cannot set a
header may send `X-Choregos-Token: chtrg_...` or add `?token=chtrg_...` to the URL instead.

Try it from a terminal:

```bash
curl -s -X POST "https://choregos.example.com/api/v1/webhooks/triggers/acme/billing-api/error-alert" \
  -H "Authorization: Bearer $TRIGGER_TOKEN" -H 'Content-Type: application/json' -d '{
  "id": "4f1c9e2a",
  "issue_key": "ERR-311",
  "title": "ZeroDivisionError in invoice totals",
  "culprit": "billing/invoices.py in total_with_vat",
  "url": "https://errors.example.com/issues/4f1c9e2a",
  "level": "error",
  "priority": "high"
}'
```

You can also fire it from the console, with **Fire** under *Fire by hand with a payload*: the same
mapping applies.

**What you should see:** HTTP `202`, and the work item's id:

```json
{
  "accepted": true,
  "duplicate": false,
  "outcome": "created_work_item",
  "work_item_id": "0b7e6a1c-43f8-7d2a-9c55-1e2f3a4b5c6d",
  "work_item_key": "BIL-13",
  "workflow": "ship-a-change"
}
```

The work item is titled *Fix: ZeroDivisionError in invoice totals*, labelled `trigger:error-alert`,
`bug` and `error`, with the fields `ticket: ERR-311` and `priority: high`.

## What each answer means

| `outcome` | When | What happened |
| --- | --- | --- |
| `created_work_item` | a new call | a work item was opened and started |
| `already_open` | a call whose `key` names a work item still open | nothing new; the answer names that work item |
| `reopened` | a call whose `key` names a work item that has ended | the same work item starts again |
| `ignored` | the payload does not match `when` | nothing was opened |

A delivery sent twice (the same `Idempotency-Key` header, or without one the same body) answers
`"duplicate": true` and starts nothing. Every call, refused ones included, appears in the project's
**Received events**, without the payload's text.

| HTTP | Why | Fix |
| --- | --- | --- |
| `202` | accepted, including `ignored` and duplicates | nothing |
| `401` | an invalid token, or a missing or wrong signature | mint the token again and update the sender |
| `404` | no such organisation, project or trigger, or the trigger has no token yet | check the URL, mint the token |
| `409` | the project's tracker is external | triggers open work items in the internal tracker only |
| `413` | a body larger than 256 KiB | send less |
| `422` | not a JSON object, no text or number at `key`, or fields the workflow refuses (the answer lists them) | fix the mapping or the payload |
| `429` | too many calls from one address (600 a minute by default) | wait for `Retry-After` |

## Signed calls

With `"auth": "signature"`, the trigger takes no bearer token: each call carries
`X-Choregos-Signature: sha256=<HMAC-SHA256 of the body>`, in hex or base64, computed with the trigger's
**signing secret** (it starts with `chsig_`), shown once with the token when you mint it. Use it for
senders that sign their webhooks, such as many monitoring and ticketing tools.

## Go further

- [Start a workflow over A2A](a2a.md), when the caller wants to follow the work and read its result.
- The design decision:
  [ADR 0113](https://github.com/VargaFoundation/choregos/blob/v0.25.3/docs/adr/0113-a-workflow-starts-from-a-trigger.md).
