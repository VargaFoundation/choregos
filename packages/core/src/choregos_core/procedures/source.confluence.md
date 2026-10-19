---
id: source.confluence
title: Add a Confluence space or page tree to a knowledge base
applies_to: {source: confluence}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Scoped API tokens in Confluence Cloud", url: "https://support.atlassian.com/confluence/kb/scoped-api-tokens-in-confluence-cloud/"}
egress: [api.atlassian.com, "<site>.atlassian.net"]
fields:
  connector: "the organisation's documents/confluence connector, picked from a list"
  space_key: "the space's key, for example ENG"
  page_id: "a page's number, to read that page and everything under it"
  include_root: "false to leave the page itself out"
  include_attachments: "true to index pdf, docx, pptx, xlsx, md, txt and html attachments"
  labels: "keep only pages carrying one of these labels"
---

## Before you start

- A **Confluence connector** declared by an organisation administrator (see "Connect Confluence Cloud").
  It holds the service account's token; the source form never asks for a secret.
- The service account must have **read** on the space.
- A space larger than 5 000 pages is refused until a `page_id` narrows it.

## At Atlassian

Nothing more than the connector's setup. To read a **sub-space**, open the branch's root page: its number
is in the address, `…/wiki/spaces/ENG/pages/<id>/Title`.

## Permissions

The source mirrors what the connector's account can read. A page that is deleted, archived, moved out of
the chosen tree or restricted against the account leaves the base at the next sync.

## In Choregos

1. **Knowledge**, the base, **Add source**, kind **Confluence**.
2. Pick the **connector**, then `space_key`; optionally `page_id` (and `include_root`), `labels` (one request
   per page) and `include_attachments`.
3. Choose the refresh: an interval, a cron expression, or manual.

## Test it

Press **Sync now**. Each sync lists the space once and downloads only pages whose version changed.

## When it fails

- `404` on the space: the key is case-sensitive; a personal space starts with `~`.
- `403`, or a base that is empty: the account has no read permission, or a scope is missing on its token.
- "refused until a page_id narrows it": the space has more than 5 000 pages.
- A sync that stops part-way: a listing that fails stops the sync and deletes nothing.
- `401`: see the connector's procedure (a scoped token needs `auth: scoped`).
