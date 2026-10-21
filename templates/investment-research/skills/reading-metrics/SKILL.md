---
name: reading-metrics
description: What the backtest's metrics mean, which ones decide, and how to quote them.
---

# Reading the metrics

`metrics.products.<symbol>` (in a product's memo, the work item's field `product` is that one entry,
and `facts` is `metrics.facts`): `best_in_sample` (the strategy chosen on the in-sample Sharpe),
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

`dsr ≥ min_dsr`, `max_drawdown ≤ max_drawdown`, `trades ≥ min_trades` and its CAGR beats
buy-and-hold. The engine writes the bar it applied in `metrics.thresholds` (in a product's memo, the
field `thresholds`); a campaign may lower it, so the memo states it. The engine decides; the memo
reports.

## Quoting

Copy, round, write a fraction as a percentage: allowed. Compute anything new: refused by the
platform — the step fails on a number it cannot find in `metrics`, and on a memo that does not
write each of the three thresholds.
