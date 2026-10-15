# Investment research — the free sources called for real, 2026-10-14 (S38-13, partial)

**Question**: do the free sources the `marketdata` family wraps answer a script today, and does the
`composite` cross-check work on real data?

**Answer**: one source out of two answers without a key. Stooq is closed to scripts; Yahoo answers;
Alpha Vantage answers with a key, and the cross-check Yahoo vs Alpha Vantage agrees to 4e-8 on 64
days. A campaign therefore needs the **Alpha Vantage key** to be cross-checked at all.

Run: `uv run python essai/investissement/sources.py` from a workstation, window 2026-07-01 →
2026-09-30, raw output in `sources-2026-10-14.json`.

## What was seen

| # | Source | Result | Consequence |
|---|---|---|---|
| 1 | `stooq` | every symbol, `AAPL` included: an HTML page with a JavaScript proof-of-work ("This site requires JavaScript to verify your browser") instead of the CSV, HTTP 200 | **defect fixed**: the adapter reported it as `UnknownSymbol` ("no data for SPY"): a source that is down read as a symbol that does not exist. It is now a source failure (`MarketDataError`); not worked around |
| 2 | `yahoo` | SPY, AAPL, IBM: 64 days; MC.PA: 66 days (Paris calendar); `NOSUCHXYZ`: `UnknownSymbol` | works today, unofficial endpoint, personal use only |
| 3 | `alphavantage`, public `demo` key | IBM: 64 days, close 219.93 on 2026-09-30, same as Yahoo; any other symbol refused by the demo key | parsing proven on a real answer; quota not exercised; the key value is masked in the refusal message |
| 4 | `composite` yahoo → alphavantage | IBM: `cross_check: {common_days: 64, max_rel_diff: 4.1e-08, diverging_days: 0}` | the cross-check works on real data |
| 5 | `composite` yahoo → stooq (before the fix) | SPY answered by Yahoo, `cross_check: null`, the stooq failure in `fallbacks` as "no data for SPY" | unchecked bars, and the snapshot job dropped both fields: nothing downstream knew |

## What changed because of it

- `stooq`: an HTML answer, or anything that is not the CSV, is a source failure, never an unknown
  symbol (`packages/adapters/tests/test_marketdata.py::test_stooq_named_errors`, mutant killed).
- The snapshot job kept the bars and dropped the composite's cross-check. It now keeps each
  symbol's `cross_check` in `snapshot.json` and states two measured facts: `cross_checked` (symbols
  a second source confirmed) and `diverging` (symbols with a close beyond the tolerance)
  (`packages/quant/tests/test_snapshot.py`, three mutants killed). They are **recorded, not gated**:
  a campaign with `cross_checked: 0` still runs — the reviewer reads it in the snapshot.

## What is not proven

- Nothing here ran on the dev tenant: no runner pod, no `k8s_job` executor, no egress policy, no
  real model, no ledger cost. The 20-product campaign of the plan was not played.
- Alpha Vantage with a real free key: the 25 requests a day cap a campaign at about 20 symbols
  with one cross-check each — not measured.
- Yahoo's terms allow personal use only; it may close the endpoint the way Stooq closed its CSV.

## Needed from Vincent

1. A free Alpha Vantage key in the tenant's secret (`alphavantage_api_key`).
2. The dev tenant with the platform built from this branch, to run a campaign with a real model.
