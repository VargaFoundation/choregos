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

## 2026-10-19 — the first campaign on the dev tenant (`campaign_dev.py`)

The tenant runs 0.18.8 and its pods hold the free Alpha Vantage key (choregos-deploy#60). With an
administrator's API token, `campaign_dev.py prepare` made the org connector `marketdata`
(`composite`, yahoo → alphavantage, key by `env:CONNECTOR_ALPHAVANTAGE_API_KEY`) and the project
`varga:research` from the template, then `open` started `RESEARCH-1`: 20 US products, 2019-01-02 to
2026-10-16, out of sample from 2025-07-01.

| # | Step | Result | Consequence |
|---|---|---|---|
| 1 | `prepare` | connector, project, internal tracker, opencode and model profiles: all accepted; `/requirements` shows nothing missing | the setup is the API's, no hand edit in the database |
| 2 | `t-snapshot` | **never started**: the executor refuses `start_run` five times — the policy (`preset:regulated`) asks for RuntimeClass `gvisor`, the warm pool runs the cluster default, and the dev cluster has no `gvisor` RuntimeClass | the campaign stays `queued`; no data fetched, no Alpha Vantage request spent, no cost |
| 3 | what a user sees | the work item says `workflow_status: FAILED` with the executor's message in `failure`; but its run reads `queued` forever and the timeline shows only `run: queued` | finding #750 |

## Not proven, still

- A real model, a 20-product campaign run to its verdict, the ledger cost: blocked by row 2. Two
  ways out, both Vincent's call: the project's policy without gVisor on dev (`sandbox.runtime:
  default`, network still closed — a weaker sandbox, to state in STATUS), or gVisor installed on the
  dev cluster with `runner.runtimeClass: gvisor` (every dev project would then run under it).
- The free Alpha Vantage key serves `compact` only (the last 100 days); `full` is premium and comes
  back as `RateLimited`. Over a multi-year range the composite's cross-check by Alpha Vantage fails,
  so `cross_checked` will read 0 while each symbol still spends one of the 25 daily requests.
  Setting `cross_check: false` on the connector saves the quota; a cross-check needs a range within
  100 days or a paid key.

## 2026-10-19, later — `RESEARCH-2`, the project's policy without gVisor

Vincent set the project's policy to `regulated-without-gvisor` (`sandbox.runtime: default`, the
rest of `preset:regulated` unchanged, network still closed): the dev cluster has no gVisor
RuntimeClass. `RESEARCH-1` stays `FAILED` (the interpreter does not restart); `RESEARCH-2` opened
with the same fields.

| # | Step | Result | Consequence |
|---|---|---|---|
| 1 | `t-snapshot` | the job ran in the warm pool and called `marketdata__get_bars` through the API; **every call took 123.2 s** and the job timed out at 900 s (`job exited with 124`) | `campaign → to_review`, an approval to relaunch waits; cost 0 |
| 2 | why | from the API pod, `query1.finance.yahoo.com` and `www.alphavantage.co` answer `ConnectError [Errno 101] Network is unreachable`: the tenant's Cilium egress (`DiametralGroup/infra`, `tenant-base`) opens only GitHub, Atlassian and diametral.com to the API | the data sources need their FQDNs opened on dev (infra change, Vincent) |
| 3 | what a user sees | `job exited with 124: timed out (900 s)`; nothing says that the 20 calls each hit a network wall | the connector's failure reaches the job only after its own timeouts; to record with #750 |

## 2026-10-20 — `RESEARCH-2` relaunched, the egress open (DiametralGroup/infra#1201)

| # | Step | Result | Consequence |
|---|---|---|---|
| 1 | `t-snapshot`, attempt 2 | the 20 `get_bars` answered: 20 symbols, 39,180 rows, `bars_sha256=2a4737a3…`, `missing=0`, **`cross_checked=0`** (as expected: the free Alpha Vantage key has no `full` history), 16.6 s | the data path works for real, through the platform; 20 of the day's 25 Alpha Vantage requests spent for no cross-check |
| 2 | storing `bars` | `PUT /internal/runs/…/outputs/bars` → **503 "Object store unavailable"** after 60 s; the run fails `missing_outputs` and the campaign goes back to `to_review` | the dev tenant has **no object store**: `objectStore.endpoint` is empty (AWS), with no `secretRef`; no artifact of any workflow can be stored there |

To go further, the tenant needs an S3 bucket it can reach: a Ceph RGW user and bucket for
`choregos` (the namespace's egress already opens the RGW, `10.204.40.10:7480`), its keys in a
Secret through Infisical, and `global.objectStore.endpoint` / `secretRef` in
`values-diametral-dev.yaml`. Until then S38-13 cannot pass the snapshot.

Also to fix in the template: with the free key, cross-checking spends the quota for nothing over a
multi-year range. The connector can be set `cross_check: false`, or keep Alpha Vantage as a
fallback only.
