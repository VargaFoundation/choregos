---
id: connector.marketdata.composite
title: Combine several market data sources
applies_to: {connector: marketdata/composite}
audience: project_owner
version: 1
reviewed: 2026-10-16
egress: ["the hosts of the sources it combines"]
fields:
  sources: "the providers, in the order they are tried: stooq, yahoo, alphavantage"
  source_configs: "the configuration of each provider, by name"
  cross_check: "true to compare the closes of two sources"
  tolerance: "the largest relative difference of two closes before they disagree, 0.02 by default"
  stooq_api_key: "Stooq's key, if any, typed once"
  alphavantage_api_key: "Alpha Vantage's key, typed once"
---

## Before you start

- Decide which sources to combine: `stooq`, `yahoo` and `alphavantage`. Each has its own limits and terms:
  see their procedures. The composite tries them **in order** and falls back to the next when one fails.
- With `cross_check` on, the closes of two sources are compared and a difference above `tolerance` (2 % by
  default) is reported, which catches a broken or unadjusted source.

## At the vendors

Only what each source needs: nothing for Stooq and Yahoo, an API key for Alpha Vantage.

## Permissions

They read public market data. Nothing is written.

## In Choregos

Connectors, **Market data**, type **composite**. `sources`: for example `["stooq", "alphavantage"]`.
`source_configs`: a sub-configuration per provider, for example `{"stooq": {"suffix_map": {}}}`.
`cross_check` and `tolerance`. Keys in their password fields: `stooq_api_key`, `alphavantage_api_key`.

## Test it

Press **Test**, then call `get_bars` and compare with a source you trust.

## When it fails

- A disagreement reported between sources: one is unadjusted (Alpha Vantage free), or a corporate action
  happened; check the dates before trusting either.
- Everything falls back to the last source: the first ones fail; see their procedures.
- A rate limit: the free tiers are small; order the sources so the limited one is last.
