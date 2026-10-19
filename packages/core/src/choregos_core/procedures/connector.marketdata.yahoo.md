---
id: connector.marketdata.yahoo
title: Use Yahoo Finance for daily market data
applies_to: {connector: marketdata/yahoo}
audience: project_owner
version: 1
reviewed: 2026-10-16
egress: [query1.finance.yahoo.com]
fields:
  timeout_s: "request timeout in seconds"
---

## Before you start

- This uses Yahoo Finance's **unofficial** chart endpoint. It is for personal use, and it **may break** or be
  blocked without notice. Read Yahoo's terms of service before using it for anything else.
- Prefer a source with a stated licence for anything that matters; the `composite` type can cross-check it
  against another.

## At the vendor

Nothing to create: there is no key.

## Permissions

None: it reads public data.

## In Choregos

Connectors, **Market data**, type **Yahoo**. The only option is `timeout_s` (20 by default).

## Test it

Press **Test**, then call `get_bars` for a known ticker.

## When it fails

- Empty answers or `429`: Yahoo is throttling or blocking the platform's address.
- A changed format: the endpoint is unofficial and may have changed; use another source.
- A timeout: the egress allowlist must open `query1.finance.yahoo.com`.
