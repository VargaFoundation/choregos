# SPDX-License-Identifier: Apache-2.0
"""Backtest: every strategy on every product, a buy-and-hold baseline, in-sample / out-of-sample."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_quant import metrics
from choregos_quant.bars import Series
from choregos_quant.campaign import Campaign
from choregos_quant.canon import dumps, num, r6, sha256_file, write_text
from choregos_quant.report import render_report
from choregos_quant.strategies import Strategy, signals

BASELINE = "buy_and_hold"


@dataclass
class Leg:
    """Daily returns and positions of one (product, strategy) over one period."""

    returns: list[float]
    positions: list[int]
    prior: int


def positions_from_signals(sig: list[int]) -> list[int]:
    """A signal decided on day t is held on day t+1: no look-ahead. Day 0 is flat."""
    return [0, *sig[:-1]] if sig else []


def daily_returns(prices: list[float], pos: list[int], cost: float) -> list[float]:
    """Net return of each day t >= 1: position(t) * market return(t) - cost * |position change|."""
    out = []
    for t in range(1, len(prices)):
        gross = pos[t] * (prices[t] / prices[t - 1] - 1)
        out.append(gross - cost * abs(pos[t] - pos[t - 1]))
    return out


def _period_stats(returns: list[float], pos: list[int], before: int) -> dict[str, float]:
    trades = sum(1 for i, p in enumerate(pos) if p == 1 and (pos[i - 1] if i else before) == 0)
    return {
        "cagr": metrics.cagr(returns),
        "sharpe": metrics.sharpe(returns),
        "max_drawdown": metrics.max_drawdown(returns),
        "trades": float(trades),
        "exposure": metrics.mean([float(p) for p in pos]),
    }


def _split(series: Series, pos: list[int], cost: float, oos_start: str) -> tuple[Leg, Leg]:
    rets = daily_returns(series.prices, pos, cost)
    dates = series.dates[1:]
    cut = sum(1 for d in dates if d < oos_start)
    p = pos[1:]
    before_oos = p[cut - 1] if cut else pos[0]
    return Leg(rets[:cut], p[:cut], pos[0]), Leg(rets[cut:], p[cut:], before_oos)


def _clip(series: Series, start: str, end: str) -> Series:
    out = Series(series.symbol)
    for bar in series.bars:
        if start <= bar.date <= end:
            out.add(bar)
    return out


@dataclass
class _Trial:
    is_leg: Leg
    oos_leg: Leg


def run_backtest(
    campaign: Campaign, bars: dict[str, Series], strategies: list[Strategy], meta: dict[str, Any]
) -> dict[str, Any]:
    """Pure computation of the `metrics.json` document (before canonical rounding of nothing else)."""
    cost = campaign.cost_bps / 10_000
    trials: dict[str, dict[str, _Trial]] = {}
    data_rows = 0
    for symbol in campaign.symbols:
        if symbol not in bars:
            continue
        s = _clip(bars[symbol], campaign.start, campaign.end)
        if len(s.prices) < 3:
            continue
        data_rows += len(s.prices)
        per: dict[str, _Trial] = {}
        flat_long = [1] * len(s.prices)
        per[BASELINE] = _Trial(*_split(s, positions_from_signals(flat_long), cost, campaign.oos_start))
        for strat in sorted(strategies, key=lambda x: x.id):
            pos = positions_from_signals(signals(strat, s.prices))
            per[strat.id] = _Trial(*_split(s, pos, cost, campaign.oos_start))
        trials[symbol] = per

    n_trials = len(strategies) * len(trials)
    per_sr = [
        metrics.sharpe_period(t.oos_leg.returns)
        for per in trials.values()
        for sid, t in per.items()
        if sid != BASELINE
    ]
    sr_var = metrics.stdev(per_sr) ** 2 * (len(per_sr) - 1) / len(per_sr) if per_sr else 0.0

    products: dict[str, Any] = {}
    relevant: list[str] = []
    best_dsrs: list[float] = []
    for symbol, per in trials.items():
        bh_oos = _period_stats(
            per[BASELINE].oos_leg.returns, per[BASELINE].oos_leg.positions, per[BASELINE].oos_leg.prior
        )
        entries: dict[str, Any] = {}
        for sid, trial in per.items():
            ins = _period_stats(trial.is_leg.returns, trial.is_leg.positions, trial.is_leg.prior)
            oos = _period_stats(trial.oos_leg.returns, trial.oos_leg.positions, trial.oos_leg.prior)
            entry: dict[str, Any] = {"in_sample": ins, "oos": oos}
            if sid != BASELINE:
                oos["dsr"] = metrics.deflated_sharpe(trial.oos_leg.returns, n_trials, sr_var)
                oos["excess_cagr_vs_buy_and_hold"] = oos["cagr"] - bh_oos["cagr"]
            entries[sid] = entry
        # Selection on IS Sharpe ONLY (ties: smallest id). The OOS never picks the strategy.
        candidates = sorted(k for k in per if k != BASELINE)
        best = max(candidates, key=lambda k: (entries[k]["in_sample"]["sharpe"], [-ord(c) for c in k]))
        b = entries[best]["oos"]
        ok = bool(
            len(per[best].oos_leg.returns) > 0
            and b["dsr"] >= campaign.min_dsr
            and b["max_drawdown"] <= campaign.max_drawdown
            and b["trades"] >= campaign.min_trades
            and b["cagr"] > bh_oos["cagr"]
        )
        best_dsrs.append(b["dsr"])
        if ok:
            relevant.append(symbol)
        products[symbol] = {"best_in_sample": best, "relevant": ok, "strategies": entries}

    doc = {
        "facts": {
            "products": len(trials),
            "trials": n_trials,
            "relevant_count": len(relevant),
            "best_oos_dsr": max(best_dsrs) if best_dsrs else 0.0,
            "data_rows": data_rows,
            "bars_sha256": meta["bars_sha256"],
            "strategies_sha256": meta["strategies_sha256"],
        },
        "products": products,
        "relevant": sorted(relevant),
    }
    rounded: dict[str, Any] = _round(doc)
    return rounded


def _round(obj: Any) -> Any:
    if isinstance(obj, float):
        return r6(obj)
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v) for v in obj]
    return obj


_CSV_COLUMNS = [
    "symbol",
    "strategy",
    "is_cagr",
    "is_sharpe",
    "is_max_drawdown",
    "is_trades",
    "is_exposure",
    "oos_cagr",
    "oos_sharpe",
    "oos_max_drawdown",
    "oos_trades",
    "oos_exposure",
    "oos_dsr",
    "oos_excess_cagr",
    "selected",
    "relevant",
]


def results_csv(doc: dict[str, Any]) -> str:
    lines = [",".join(_CSV_COLUMNS)]
    for symbol in sorted(doc["products"]):
        prod = doc["products"][symbol]
        for sid in sorted(prod["strategies"], key=lambda k: (k != BASELINE, k)):
            e = prod["strategies"][sid]
            i, o = e["in_sample"], e["oos"]
            selected = sid == prod["best_in_sample"]
            cells = [
                symbol,
                sid,
                *(num(i[k]) for k in ("cagr", "sharpe", "max_drawdown")),
                str(int(i["trades"])),
                num(i["exposure"]),
                *(num(o[k]) for k in ("cagr", "sharpe", "max_drawdown")),
                str(int(o["trades"])),
                num(o["exposure"]),
                num(o["dsr"]) if "dsr" in o else "",
                num(o["excess_cagr_vs_buy_and_hold"]) if "excess_cagr_vs_buy_and_hold" in o else "",
                str(selected).lower(),
                str(selected and prod["relevant"]).lower(),
            ]
            lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def write_outputs(doc: dict[str, Any], campaign: Campaign, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_text(out / "metrics.json", dumps(doc))
    write_text(out / "results.csv", results_csv(doc))
    write_text(out / "report.html", render_report(doc, campaign))
    write_text(out / "result.json", dumps({"facts": doc["facts"]}))


def backtest_files(
    campaign: Campaign,
    bars: dict[str, Series],
    bars_path: Path,
    strategies_path: Path,
    strategies: list[Strategy],
    out: Path,
) -> dict[str, Any]:
    meta = {"bars_sha256": sha256_file(bars_path), "strategies_sha256": sha256_file(strategies_path)}
    doc = run_backtest(campaign, bars, strategies, meta)
    write_outputs(doc, campaign, out)
    return doc
