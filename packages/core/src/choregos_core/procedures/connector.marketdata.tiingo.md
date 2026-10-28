---
id: connector.marketdata.tiingo
title: Use Tiingo for daily market data
applies_to: {connector: marketdata/tiingo}
audience: project_owner
version: 1
reviewed: 2026-10-26
vendor_docs:
  - {title: "Tiingo: pricing and limits", url: "https://www.tiingo.com/about/pricing"}
  - {title: "Tiingo: end-of-day API", url: "https://www.tiingo.com/documentation/end-of-day"}
egress: [api.tiingo.com]
fields:
  api_key: "your Tiingo API token, typed once"
  timeout_s: "request timeout in seconds"
---

## Before you start

- A free Tiingo account and its API token. The free plan gives **30+ years** of daily history, 50 requests an
  hour, 1,000 a day and 500 distinct symbols a month: a 20-symbol campaign takes 20 requests.
- Its licence is **internal and personal use only**: the data may not be shown or passed to anyone else. Keep
  the connector to your own research; a project whose results are shared needs a commercial licence from Tiingo.

## At the vendor

Sign up at tiingo.com, then copy the token from your account's API page. One account, one token: do not open
several free accounts to add up their limits.

## Permissions

The token only reads market data. It is yours: do not share one token between organisations.

## In Choregos

Connectors, **Market data**, type **Tiingo**. `api_key` in the password field (sealed, shown once). In a
**composite**, list it after the primary source, for example `["yahoo", "tiingo"]`, with the token in
`tiingo_api_key`: it then cross-checks the whole range, not only the recent days.

## Test it

Press **Test** (it reads SPY's metadata, one request), then call `get_bars` for a known ticker.

## When it fails

- "the key was refused": the token is wrong or revoked.
- A message about the hourly or daily allocation: the free limits are reached; wait for the next hour or day.
- An unknown symbol: Tiingo writes share classes with a dash (`BRK-B`).
