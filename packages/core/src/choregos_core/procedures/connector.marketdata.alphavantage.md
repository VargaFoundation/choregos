---
id: connector.marketdata.alphavantage
title: Use Alpha Vantage for daily market data
applies_to: {connector: marketdata/alphavantage}
audience: project_owner
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Alpha Vantage: get a free API key", url: "https://www.alphavantage.co/support/#api-key"}
egress: [www.alphavantage.co]
fields:
  api_key: "your Alpha Vantage API key, typed once"
  timeout_s: "request timeout in seconds"
---

## Before you start

- A free Alpha Vantage API key. The free tier allows about **25 requests a day** and gives **unadjusted**
  daily bars: a backtest over many tickers will exhaust it. A paid plan raises the limit.

## At the vendor

Request a key at alphavantage.co (the support page above). It is shown once on the page and sent by email.

## Permissions

The key only reads market data. It is yours: do not share one key between organisations.

## In Choregos

Connectors, **Market data**, type **Alpha Vantage**. `api_key` in the password field (sealed, shown once).

## Test it

Press **Test**, then call `get_instrument` for a known ticker.

## When it fails

- A note about the call frequency or "standard API call frequency": the daily limit is reached; wait or
  upgrade.
- An invalid key message: the key is wrong or not activated.
- Prices that look off after a split: the bars are unadjusted.
