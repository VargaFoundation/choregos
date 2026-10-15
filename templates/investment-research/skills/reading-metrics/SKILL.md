---
name: reading-metrics
description: What the backtest's metrics mean, which ones decide, and how to quote them.
---

# Reading the metrics

`metrics.products.<symbol>`: `best_in_sample` (the strategy chosen on the in-sample Sharpe),
`relevant` (the engine's verdict) and, per strategy, `in_sample` and `oos` statistics:

| Metric | Meaning |
|:--|:--|
| `sharpe` | annualised return over volatility, net of costs |
| `dsr` | Deflated Sharpe Ratio (out of sample): the probability that the true Sharpe is positive, given the number of trials — the decisive one |
| `cagr` | compound annual growth rate, net of costs, as a fraction (0.08 = 8 %) |
| `excess_cagr_vs_buy_and_hold` | `cagr` minus buy-and-hold's over the same period |
| `max_drawdown` | worst peak-to-trough loss, as a fraction |
| `trades` | changes of position |
| `exposure` | share of days invested |

`metrics.facts`: the campaign's totals — `products`, `trials`, `relevant_count`, `best_oos_dsr` —
and the sha256 of the bars and of the strategies the numbers come from.

## A product is relevant when, out of sample

`dsr ≥ min_dsr`, `max_drawdown ≤ max_drawdown`, `trades ≥ min_trades` (the work item's fields) and
its CAGR beats buy-and-hold. The engine decides; the memo reports.

## Quoting

Copy, round, write a fraction as a percentage: allowed. Compute anything new: refused by the
platform — the step fails on a number it cannot find in `metrics` or in the work item's fields.
