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

The object store was then given to the tenant (RGW tenant user `choregos$choregos-app`, 20G quota,
bucket `choregos`, keys through Infisical `/app`; DiametralGroup/choregos-deploy#78).

## 2026-10-20, after the object store — the real model runs, the backtest cannot read it

| # | Step | Result | Consequence |
|---|---|---|---|
| 3 | `t-snapshot`, attempt 3 | 20 symbols, 39,180 rows, `cross_checked=0`, stored: `bars` 3.2 MB, `sha256:e35b79cb…` | `bars_sha256` differs from attempt 2 (`2a4737a3…`) for the same request: Yahoo's adjusted history moved overnight. The snapshot freezes one version, which is the point of storing it |
| 4 | `t-profile` (job) | 20 products, 3 clusters, 1,631 in-sample days | — |
| 5 | `t-strategize`, real model (`platform/standard`) | 4 strategies in the closed grammar (`rsi_reversion`, `ema_trend`, `momentum`, `donchian_breakout`), no ticker named; **39,371 tokens in, 2,195 out, $0.265 (€0.244) on the ledger**, 46 s | the first real-model cost of the use case |
| 6 | `t-backtest` (job) | exit 2: `strategies.json` not found | the model answered the document as a JSON **string**: `output_schema` validated it parsed, the artifact was typed `markdown`. Fixed in VargaFoundation/choregos#766 (template declares `strategies: json`, text declared json is stored parsed); the e2e strategist now answers a string |

Not reached: backtest, verdict, memo. They need #766 deployed on dev (a release after 0.19.0).

With the free Alpha Vantage key, cross-checking spends 20 of the day's 25 requests for nothing over a
multi-year range: the connector can be set `cross_check: false`, or keep Alpha Vantage as a fallback.

## 2026-10-21 — `RESEARCH-3`, a campaign to its verdict

0.19.1 (#766) was deployed to dev on 2026-10-20 (choregos-deploy#80), yet `RESEARCH-2` relaunched
failed the same way: the project's `campaign` workflow is the copy the 0.19.0 template made when the
project was created (2026-10-19, `t-strategize.output_types: {}`), and a work item stays on the
version it started with. The only difference with the template on `main` was the `output_types`
line. It was published as version 2 (`PUT /projects/{id}/workflows/campaign`, `source: template`,
`base_version: 1`); `RESEARCH-2` was rejected and `RESEARCH-3` opened with the same fields on v2.
Nothing told that the project ran an older template: finding #817.

| # | Step | Result | Consequence |
|---|---|---|---|
| 1 | `t-snapshot` (job) | 20 symbols, 39,180 rows, `missing=0`, `cross_checked=0`, provider `yahoo`, `bars_sha256=8c6e5240…`, 20 s | a third version of the same request (`2a4737a3…`, `e35b79cb…`, `8c6e5240…`): Yahoo's adjusted history moves; each campaign freezes its own |
| 2 | `t-profile` (job) | 20 products, 3 clusters, 1,631 in-sample days | — |
| 3 | `t-strategize`, real model (`platform/standard`) | 4 strategies in the grammar (`rsi_reversion`, `momentum`, `ema_trend`, `donchian_breakout`), no ticker named; **32,683 tokens in, 1,821 out, $0.220 (€0.203)**, 42 s; `strategies` stored as **`json`** (1,805 bytes) | #766 works on a real model once the project's workflow carries it |
| 4 | `t-backtest` (job) | 80 trials (4 × 20), best out-of-sample DSR **0.595** (JNJ, `c1-rsi-pullback`, 12 trades), `relevant_count=0`; 3 of 20 selected strategies beat buy-and-hold out of sample (GLD +0.1 %, IEF +0.7 %, XLU +7.0 % CAGR), 14 of 20 trade fewer than 10 times | — |
| 5 | `t-verdict` | `evidence_facts` refuses (`relevant_count` 0 < 1): **`backtested → not_relevant`**, 4 min 36 s after opening | the campaign reached its verdict on dev, on measured facts only |

Checked afterwards, from a workstation:

| # | Check | Result |
|---|---|---|
| 1 | the 8 artifacts downloaded (`/work-items/{id}/artifacts/{name}/content`) | each `sha256` equals its stored digest, each size its stored size |
| 2 | `choregos-quant backtest` replayed locally on the downloaded `bars` and `strategies` (same code as 0.19.1) | `results.csv` and `report.html` identical byte for byte; `metrics.json` identical as a document and in its canonical bytes, but the stored artifact lacks the engine's trailing newline (an inline JSON output is re-serialised by the platform), so `sha256sum metrics.json` ≠ stored digest |
| 3 | ledger (`/projects/{id}/costs`) | 2026-10-21: $0.482145 = RESEARCH-2's second strategist ($0.26167) + RESEARCH-3's ($0.220475); project total $0.747 against a $600 budget |

Not reached on dev: the fan-out, a memo written by a real model, the owner's attested decision —
no product passed, which is the honest outcome of 80 trials on 20 liquid products. Still not proven:
a cross-check (the free Alpha Vantage key has no multi-year history), `preset:regulated` with gVisor
(the project runs `regulated-without-gvisor`).

## 2026-10-21, later — `RESEARCH-4`, the fan-out and three memos by a real model

`RESEARCH-3` exercised no child. To run the rest of the workflow on dev, `RESEARCH-4` was opened
with the same products and dates and **thresholds lowered on purpose** (`min_dsr: 0`,
`min_trades: 1`; `max_drawdown` 0.35 and "beats buy-and-hold out of sample" unchanged). Its title
says so. **What it finds relevant is not a research result**: a DSR of 0.03 passes.

| # | Step | Result |
|---|---|---|
| 1 | snapshot, profile, strategize (real model), backtest | 20 symbols, `bars_sha256=2ed0f39d…` (a fourth version of the same request); a new strategy set, $0.238; 80 trials, best out-of-sample DSR 0.580, `relevant_count=3` (IEF, TLT, XLU) |
| 2 | `t-verdict` then `t-fan-out` | `backtested → relevant`; three children `RESEARCH-4.1` (IEF), `.2` (TLT), `.3` (XLU) in `product-memo` v1, 7 s after the verdict |
| 3 | `t-memo`, real model (`quant-analyst`, `platform/standard`), one per child | the three memos passed `numbers_measured` on the first attempt (against the parent's measured `metrics`), 1 to 3 min each, $0.168 / $0.191 / $0.167 (26,353 to 31,646 tokens in) |
| 4 | the memos, read | each quotes only this product's measured figures (e.g. XLU: Sharpe 1.47, DSR 0.31, CAGR 12.4 % against 5.4 % for buy-and-hold, drawdown 5.8 %, 13 trades — `metrics.products.XLU`), lists what would make it wrong and carries the notice; artifacts match their digests. None says the campaign's thresholds were lowered: a reader of one memo alone sees "relevant" |
| 5 | ledger | 2026-10-21 day total $1.245 = the runs of RESEARCH-2, -3, -4 and its three children, to the cent. The campaign's own `totals` ($0.238) leave out its children, as ADR 0057 decides (each child has its own budget) |

Waiting: each child's attested decision (`t-decide`, group `owner`), then the campaign's. They are a
person's statement ("I read the memo…") and are left to Vincent.

## 2026-10-21, evening — `RESEARCH-5`, `preset:regulated` with gVisor

The dev cluster got gVisor: `runsc` 20261005.0 (sha512-pinned, the release of
`dev/scripts/gvisor.sh`) in K3s's containerd on the three nodes, one at a time, canary first
(DiametralGroup/infra#1224); the `gvisor` RuntimeClass by Argo CD; the runner pool's template
set to `runtimeClass: gvisor` (choregos-deploy#83). The project's policy went back to
`preset:regulated` unchanged (version 3, `sandbox.runtime: gvisor`), and `RESEARCH-5` was opened
with RESEARCH-3's fields (thresholds as shipped).

| # | Step | Result |
|---|---|---|
| 1 | the nodes | a busybox pod `runtimeClassName: gvisor` on each node prints "Starting gVisor..." and reaches DNS and the apiserver's ClusterIP (Cilium); the three nodes Ready, etcd healthy, no pod newly failing |
| 2 | the pool | the warm sandbox `choregos-runner-*` runs under gVisor (`dmesg` inside it: "Starting gVisor...") |
| 3 | snapshot, profile (jobs) | 20 symbols, 39,180 rows, `missing=0`, `bars_sha256=a2f53b77…` (a fifth version of the same request); 20 products, 3 clusters |
| 4 | strategize, real model | $0.175 |
| 5 | backtest (job) | 80 trials, best out-of-sample DSR 0.546, `relevant_count=0` |
| 6 | verdict | `backtested → not_relevant`, 4 min 33 s after opening |

The four steps ran in four sandbox pods, each `runtimeClassName: gvisor` (`kubectl get pods`
right after): pandas and numpy run under gVisor, and so does the network (the snapshot fetched its 20 symbols, `reused=0`).

Not proven by this run: that the runner could not have run under `runc` — the readiness item
`runtime-class` (S38-13d) and the executor's refusal (S38-13b, S38-13e) are what keeps a policy
asking for gVisor off a pool that lacks it.
