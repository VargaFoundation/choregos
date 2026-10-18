# investment-research — is any product of this universe worth following?

One work item is a **campaign**: a universe of listed products (equities, ETFs), a period, an
out-of-sample start and the thresholds of a verdict, in the work item's fields. Two workflows
(`workflows/campaign.yaml`, and `workflows/product-memo.yaml` for each relevant product), two agents
(`agents/`), two skills (`skills/`).

| # | Step | Actor | Produces | Gate |
|:--|:--|:--|:--|:--|
| 1 | `snapshot` | job `choregos-quant snapshot` | `bars` (csv, hashed), `snapshot` | `outputs_present` |
| 2 | `profile` | job `choregos-quant profile` | `universe_profile` (no names), `anonymisation` | `outputs_present` |
| 3 | `strategize` | agent `quant-strategist` | `strategies` (closed grammar) | `output_schema` |
| 4 | `backtest` | job `choregos-quant backtest` | `metrics`, `results` (csv), `report` (html) | `outputs_present` |
| 5 | `verdict` | the platform | — | `evidence_facts` on **measured** facts: `relevant_count ≥ 1` |
| 6 | `t-fan-out` | the platform (`spawn:`, ADR 0057) | one child work item per product in `metrics.relevant`, in `product-memo` | passes once every child is settled and at least one is `adopted` (else `not_adopted`) |
| 7 | `decide` | a person (`owner`) | adopted / rejected | attested task, after the join |

Each child (`workflow product-memo`, keyed `<campaign>.<rank>`) is followed on its own:

| # | Step | Actor | Produces | Gate |
|:--|:--|:--|:--|:--|
| 1 | `memo` | agent `quant-analyst` | `memo_markdown` for **one** product | `numbers_measured`: every number quoted is one the backtest measured for that product |
| 2 | `decide` | a person (`owner`) | adopted / rejected | attested task |

The platform copies into the child's fields what the backtest measured for its product (`product`,
`facts`) and the dates, for the analyst to read. The memo is **not** checked against those fields:
a person can edit them. `numbers_measured` reads the **parent's** `metrics` document
(`parent.metrics.products.$symbol` and `parent.metrics.facts`, `$symbol` being the child's own
`symbol`), which only the backtest job wrote. So the memo of one product cannot quote another's,
a number edited into the child's fields is refused, and a product-memo created by hand (no parent)
has nothing to quote: its memo goes to review. The campaign writes no memo of its own.

A campaign waits for its slowest child: the owner's SLA (72 h) is on the child's decision, and the
campaign's status says how many are still open. A campaign whose products are all rejected ends in
`not_adopted`.

No verdict rests on a number a model wrote: the jobs (ADR 0056) compute them, the platform records
them as measured, and the gates refuse a declared one.

## The organisation's connector

The snapshot job calls **one** organisation connector, by name, through the internal API with its
run's own token — never a provider key:

| Name | Capability | For |
|:--|:--|:--|
| `marketdata` | `marketdata` (`stooq`, `yahoo`, `alphavantage`, `composite`) | daily bars of the universe |

The sandbox is closed (`preset:regulated`): no agent and no job reaches the internet.

## A campaign every week

A project schedule opens one campaign per window (`schedules:` in the project's configuration,
see `docs/usage.md`); `end: "{window}"` moves the end of the data with each window. The weekly
verdicts are **not independent**: their out-of-sample periods overlap, so ten weekly passes are not
ten confirmations. A schedule with fixed fields repeats the same test, the second round the verdict
forbids.

## What the verdict is worth — read before adopting anything

- **The model knows the past.** The strategist reads an anonymised in-sample profile, writes in a
  closed grammar and never sees a price. That reduces look-ahead, it does not remove it: the agent
  can read the work item (its symbols are in the fields). Only the **out-of-sample period after the
  model's training cut-off** is clean; set `oos_start` after it.
- **No second round.** A campaign whose verdict fails ends in `not_relevant`; it does not go back to
  the strategist. Asking for strategies until one passes on the same out-of-sample period is data
  snooping, which the Deflated Sharpe Ratio of a single round does not count. A new campaign on a
  later out-of-sample period is a new work item.
- **Free sources.** Data from free providers: unofficial endpoints, survivorship bias (today's
  listings), adjustments the provider chose. The snapshot is hashed so a result can be replayed, not
  so that it is right. A re-run of the step reuses the stored snapshot for the same request, byte
  for byte, and fetches nothing (`reused: 1` in its facts); a new window is a new request. With a
  `composite` connector, each symbol's cross-check stays in `snapshot.json` and the facts count the
  symbols a second source confirmed (`cross_checked`), those that diverge (`diverging`) and the
  worst gap (`max_rel_diff`). It is a gate (S38-14): `t-snapshot` refuses the snapshot, and the
  campaign goes to `to_review`, when more symbols diverge than the campaign field
  `max_diverging_symbols` (default 0) allows — `diverging_excess` > 0. A single-source connector has
  nothing to compare and is not refused: read `cross_checked`. On 2026-10-14 Stooq answered scripts with a browser check: a composite of
  Yahoo and Alpha Vantage (free key) is the one that cross-checks (`essai/investissement/`).
- **Assumed costs.** `cost_bps` per change of position; no spread model, no tax, no currency hedge.
- **Internal use.** The memo is a research note, not investment advice, not an offer, not an order.
  Adopting a campaign means following its products on paper.
