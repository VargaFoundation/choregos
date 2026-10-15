---
name: strategy-grammar
description: The closed grammar of strategies the backtest engine runs, their parameters and bounds.
---

# Strategy grammar

The engine runs **five families**, long-only, daily bars, signal on the close, position taken on
the next bar, costs charged on every change of position. Nothing else exists: a strategy outside
this grammar fails the step before the backtest.

| Family | Parameters (integers) | Bounds | Long when |
|:--|:--|:--|:--|
| `sma_cross` | `fast`, `slow` | 5–100, 20–300 | the fast moving average is above the slow one |
| `ema_trend` | `span` | 10–300 | the close is above its exponential average |
| `momentum` | `lookback`, `skip` | 20–252, 0–21 | the return over `lookback` days, skipping the last `skip`, is positive |
| `rsi_reversion` | `period`, `entry`, `exit` | 2–30, 5–40, 50–80 | enters when the RSI falls below `entry`, exits when it rises above `exit` |
| `donchian_breakout` | `entry`, `exit` | 10–120, 5–60 | the close tops the highest close of the last `entry` days; exits below the lowest of the last `exit` |

## Shape

```json
{"strategies": [
  {"id": "trend-200", "family": "sma_cross", "params": {"fast": 50, "slow": 200},
   "hypothesis": "Cluster C1 shows positive lag-1 autocorrelation: trends persist."}
]}
```

`id`: letters, digits, `-` and `_`, at most 32. `hypothesis`: why, on this universe — not what.

## Rules

- **Every strategy is a trial.** The Deflated Sharpe Ratio discounts the best result by the number
  of trials: eight strategies need a much better Sharpe than two to pass.
- **The strategy is chosen in sample, judged out of sample.** The engine picks, per product, the
  strategy with the best in-sample Sharpe; the out-of-sample period only judges it. A strategy
  tuned to the profile's numbers will look good in sample and fail out of sample.
- **Buy-and-hold is always the baseline**: a product is relevant only if its strategy beats it.
