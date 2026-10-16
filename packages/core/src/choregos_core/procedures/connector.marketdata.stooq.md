---
id: connector.marketdata.stooq
title: Use Stooq for daily market data
applies_to: {connector: marketdata/stooq}
audience: project_owner
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Stooq", url: "https://stooq.com/"}
egress: [stooq.com]
fields:
  suffix_map: "ticker suffix to Stooq market suffix, for example {\"\": \".us\", \".L\": \".uk\"}"
  timeout_s: "request timeout in seconds"
  api_key: "an optional key, typed once"
---

## Before you start

- Stooq serves **daily** bars as CSV, free. Read Stooq's terms of use before relying on it.
- It is the right default for a study or a backtest on daily closes; it is not a real-time feed.

## At the vendor

Stooq may demand an `apikey` on CSV downloads, obtained once through its site. Without one, the endpoint can
answer a "get your apikey" page instead of data. Get the key there and put it in `api_key`.

## Permissions

None: it only reads public market data. Nothing is written.

## In Choregos

Connectors, **Market data**, type **Stooq**. `suffix_map` maps your tickers to Stooq's market suffixes
(for example the empty suffix to `.us`, `.L` to `.uk`). `timeout_s` defaults to 20.

## Test it

Press **Test**, then call `get_instrument` and `get_bars` for a known ticker.

## When it fails

- A "get your apikey" answer: set `api_key`.
- No bars: the ticker's suffix is not in `suffix_map`, so Stooq does not know the symbol.
- Empty or truncated data: the market was closed, or the date range has no sessions.
- A timeout: the egress allowlist must open `stooq.com`.
