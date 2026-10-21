"""Backtest end to end: determinism, selection rule, anonymisation, CLI."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from choregos_quant import engine
from choregos_quant.bars import Bar, Series, read_bars, write_bars
from choregos_quant.campaign import parse_campaign
from choregos_quant.cli import main
from choregos_quant.errors import InputError
from choregos_quant.profile import anonymisation_order, build_profile
from choregos_quant.strategies import Strategy, validate_strategies

from .conftest import (  # type: ignore[attr-defined]
    CAMPAIGN,
    STRATEGIES,
    business_days,
    synth_rows,
)

OUTPUTS = ["metrics.json", "results.csv", "report.html", "result.json"]


def run(work: Path, out: str, cmd: str = "backtest") -> int:
    args = [
        cmd,
        "--campaign",
        str(work / "campaign.json"),
        "--bars",
        str(work / "bars.csv"),
        "--out",
        str(work / out),
    ]
    if cmd == "backtest":
        args += ["--strategies", str(work / "strategies.json")]
    return main(args)


def test_backtest_is_byte_identical_twice(workdir: Path) -> None:
    assert run(workdir, "a") == 0 and run(workdir, "b") == 0
    for name in OUTPUTS:
        assert (workdir / "a" / name).read_bytes() == (workdir / "b" / name).read_bytes(), name


def test_metrics_shape(workdir: Path) -> None:
    run(workdir, "o")
    doc = json.loads((workdir / "o" / "metrics.json").read_text())
    facts = doc["facts"]
    assert set(facts) == {
        "products",
        "trials",
        "relevant_count",
        "best_oos_dsr",
        "data_rows",
        "bars_sha256",
        "strategies_sha256",
    }
    assert all(isinstance(v, str | int | float | bool) for v in facts.values())
    assert facts["products"] == 3 and facts["trials"] == 15 and facts["data_rows"] == 2100
    assert set(doc["products"]) == {"AAA", "BBB", "CCC"}
    assert "buy_and_hold" in doc["products"]["AAA"]["strategies"]
    assert facts["relevant_count"] == len(doc["relevant"])
    raw = (workdir / "o" / "metrics.json").read_text()
    assert raw.endswith("}\n") and "\r" not in raw
    assert (workdir / "o" / "result.json").read_text() == json.dumps(
        {"facts": facts}, sort_keys=True, separators=(",", ":")
    ) + "\n"
    assert re.search(r"\.\d{7,}", raw) is None  # at most 6 decimals


def test_metrics_carry_the_thresholds_the_engine_applied() -> None:
    """A product's memo quotes its bar from `metrics.thresholds` (#824): it is the campaign's, as applied."""
    bars = {"AAA": _series(synth_rows("AAA", 5))}
    meta = {"bars_sha256": "x", "strategies_sha256": "y"}
    campaign = parse_campaign(
        {**CAMPAIGN, "symbols": ["AAA"], "min_dsr": 0.5, "max_drawdown": 0.2, "min_trades": 3}
    )
    doc = engine.run_backtest(campaign, bars, validate_strategies(STRATEGIES), meta)
    assert doc["thresholds"] == {"min_dsr": 0.5, "max_drawdown": 0.2, "min_trades": 3}
    defaults = engine.run_backtest(
        parse_campaign({**CAMPAIGN, "symbols": ["AAA"]}), bars, validate_strategies(STRATEGIES), meta
    )
    assert defaults["thresholds"] == {
        "min_dsr": parse_campaign(CAMPAIGN).min_dsr,
        "max_drawdown": parse_campaign(CAMPAIGN).max_drawdown,
        "min_trades": parse_campaign(CAMPAIGN).min_trades,
    }


def test_report_is_static_with_disclaimer(workdir: Path) -> None:
    run(workdir, "o")
    html = (workdir / "o" / "report.html").read_text()
    assert (
        "Research note for internal use. Not investment advice. "
        "Past performance does not predict future results." in html
    )
    assert "<script" not in html and "http://" not in html and "https://" not in html and "src=" not in html


def test_input_changes_the_hashes(workdir: Path) -> None:
    run(workdir, "a")
    s = json.loads((workdir / "strategies.json").read_text())
    s["strategies"][0]["params"]["fast"] = 12
    (workdir / "strategies.json").write_text(json.dumps(s))
    run(workdir, "b")
    fa = json.loads((workdir / "a" / "metrics.json").read_text())["facts"]
    fb = json.loads((workdir / "b" / "metrics.json").read_text())["facts"]
    assert fa["bars_sha256"] == fb["bars_sha256"] and fa["strategies_sha256"] != fb["strategies_sha256"]


def test_cost_lowers_results() -> None:
    bars = {"AAA": _series(synth_rows("AAA", 5))}
    strategies = validate_strategies(STRATEGIES)
    meta = {"bars_sha256": "x", "strategies_sha256": "y"}
    free = engine.run_backtest(
        parse_campaign({**CAMPAIGN, "symbols": ["AAA"], "cost_bps": 0}), bars, strategies, meta
    )
    paid = engine.run_backtest(
        parse_campaign({**CAMPAIGN, "symbols": ["AAA"], "cost_bps": 100}), bars, strategies, meta
    )
    f = free["products"]["AAA"]["strategies"]["s5"]["oos"]["cagr"]
    p = paid["products"]["AAA"]["strategies"]["s5"]["oos"]["cagr"]
    assert p < f


def _series(rows: list[Bar]) -> Series:
    s = Series(rows[0].symbol)
    for r in rows:
        s.add(r)
    return s


def test_best_strategy_is_chosen_on_in_sample_never_on_oos(monkeypatch: pytest.MonkeyPatch) -> None:
    """A is perfect in-sample and absent out-of-sample; B is the reverse. A must be selected."""
    rows = synth_rows("AAA", 11, n=500)
    series = _series(rows)
    camp = parse_campaign(
        {**CAMPAIGN, "symbols": ["AAA"], "min_dsr": 0.0, "min_trades": 1, "max_drawdown": 1.0}
    )
    prices = series.prices
    in_sample = [d < camp.oos_start for d in series.dates]

    def fake(strategy: Strategy, p: list[float]) -> list[int]:
        # sig[t] = 1 when day t+1 is up: perfect foresight, on purpose, to build the case
        up = [1 if i + 1 < len(p) and p[i + 1] > p[i] else 0 for i in range(len(p))]
        want_is = strategy.id == "A"
        return [u if (ins == want_is) else 0 for u, ins in zip(up, in_sample, strict=True)]

    monkeypatch.setattr(engine, "signals", fake)
    strategies = [
        Strategy("A", "h", "ema_trend", {"span": 10}),
        Strategy("B", "h", "ema_trend", {"span": 20}),
    ]
    doc = engine.run_backtest(camp, {"AAA": series}, strategies, {"bars_sha256": "", "strategies_sha256": ""})
    prod = doc["products"]["AAA"]
    assert prod["strategies"]["A"]["in_sample"]["sharpe"] > prod["strategies"]["B"]["in_sample"]["sharpe"]
    assert prod["strategies"]["B"]["oos"]["sharpe"] > prod["strategies"]["A"]["oos"]["sharpe"]
    assert prod["best_in_sample"] == "A"
    # B would pass every gate out-of-sample; A has no OOS trade, so the product is NOT relevant.
    assert prod["strategies"]["B"]["oos"]["trades"] >= 1 and prod["strategies"]["B"]["oos"]["dsr"] > 0.5
    assert prod["relevant"] is False and doc["relevant"] == []
    assert prices  # silence unused


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, True),
        ({"min_dsr": 1.5}, False),
        ({"max_drawdown": 0.0}, False),
        ({"min_trades": 10_000}, False),
    ],
)
def test_each_relevance_gate_blocks(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, Any], expected: bool
) -> None:
    series = _series(synth_rows("AAA", 11, n=500))
    in_sample_cut = "2021-01-01"

    def always_right(strategy: Strategy, p: list[float]) -> list[int]:
        # Perfect foresight, except that it holds through the single worst day (a real drawdown).
        sig = [1 if i + 1 < len(p) and p[i + 1] > p[i] else 0 for i in range(len(p))]
        worst = min(
            range(1, len(p)), key=lambda i: p[i] / p[i - 1] if series.dates[i] >= in_sample_cut else 9
        )
        sig[worst - 1] = 1
        return sig

    monkeypatch.setattr(engine, "signals", always_right)
    camp = parse_campaign(
        {
            **CAMPAIGN,
            "symbols": ["AAA"],
            "oos_start": in_sample_cut,
            "min_dsr": 0.5,
            "min_trades": 5,
            "max_drawdown": 0.9,
            "cost_bps": 0,
            **override,
        }
    )
    doc = engine.run_backtest(
        camp,
        {"AAA": series},
        [Strategy("A", "h", "ema_trend", {"span": 10})],
        {"bars_sha256": "", "strategies_sha256": ""},
    )
    assert doc["products"]["AAA"]["relevant"] is expected


def test_beating_buy_and_hold_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = synth_rows("AAA", 11, n=500, drift=0.003)  # strong uptrend: nothing beats holding it
    series = _series(rows)

    def lag(strategy: Strategy, p: list[int | float]) -> list[int]:
        return [1 if i % 2 == 0 else 0 for i in range(len(p))]  # in the market half the time

    monkeypatch.setattr(engine, "signals", lag)
    camp = parse_campaign(
        {**CAMPAIGN, "symbols": ["AAA"], "min_dsr": 0.0, "min_trades": 1, "max_drawdown": 1.0, "cost_bps": 0}
    )
    doc = engine.run_backtest(
        camp,
        {"AAA": series},
        [Strategy("A", "h", "ema_trend", {"span": 10})],
        {"bars_sha256": "", "strategies_sha256": ""},
    )
    p = doc["products"]["AAA"]
    assert p["strategies"]["A"]["oos"]["excess_cagr_vs_buy_and_hold"] < 0
    assert p["relevant"] is False


def test_profile_leaks_nothing(workdir: Path) -> None:
    assert run(workdir, "p", "profile") == 0
    text = (workdir / "p" / "universe_profile.json").read_text()
    for symbol in CAMPAIGN["symbols"]:  # type: ignore[attr-defined]
        assert symbol not in text
    assert re.search(r"\d{4}-\d{2}-\d{2}", text) is None
    assert set(json.loads(text)["products"]) == {"P01", "P02", "P03"}
    mapping = json.loads((workdir / "p" / "anonymisation.json").read_text())
    assert sorted(mapping.values()) == ["AAA", "BBB", "CCC"]
    assert set(mapping) == {"P01", "P02", "P03"}


def test_profile_ignores_the_out_of_sample_period(workdir: Path) -> None:
    """Changing every price from oos_start on must not change the profile by one byte."""
    run(workdir, "p1", "profile")
    bars = read_bars(workdir / "bars.csv")
    changed: list[Bar] = []
    for s in bars.values():
        for b in s.bars:
            k = 3.0 if b.date >= CAMPAIGN["oos_start"] else 1.0  # type: ignore[operator]
            changed.append(
                Bar(b.symbol, b.date, b.open * k, b.high * k, b.low * k, b.close * k, None, b.volume)
            )
    write_bars(changed, workdir / "bars.csv")
    run(workdir, "p2", "profile")
    assert (workdir / "p1" / "universe_profile.json").read_bytes() == (
        workdir / "p2" / "universe_profile.json"
    ).read_bytes()


def test_anonymisation_order_is_deterministic_and_not_alphabetical() -> None:
    symbols = [f"SYM{i:02d}" for i in range(12)]
    order = anonymisation_order(symbols, "seed")
    assert order == anonymisation_order(list(reversed(symbols)), "seed")
    assert order != sorted(symbols)
    assert order != anonymisation_order(symbols, "other-seed")


def test_profile_clusters_correlated_products() -> None:
    base = synth_rows("AAA", 1, n=300)
    twin = [Bar("BBB", b.date, b.open, b.high, b.low, b.close, None, 1.0) for b in base]
    other = synth_rows("CCC", 99, n=300)
    bars = {s: _series(r) for s, r in (("AAA", base), ("BBB", twin), ("CCC", other))}
    camp = parse_campaign({**CAMPAIGN, "end": "2021-06-30", "oos_start": "2021-01-01"})
    profile, mapping = build_profile(camp, bars)
    cluster = {mapping[p]: v["correlation_cluster"] for p, v in profile["products"].items()}
    assert cluster["AAA"] == cluster["BBB"] != cluster["CCC"]
    assert profile["facts"]["clusters"] == 2


def test_invalid_strategies_exit_2(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (workdir / "strategies.json").write_text(
        json.dumps({"strategies": [{"id": "x", "hypothesis": "h", "family": "magic", "params": {}}]})
    )
    assert run(workdir, "o") == 2
    assert "unknown family" in capsys.readouterr().err
    assert not (workdir / "o" / "metrics.json").exists()


def test_too_many_strategies_for_the_campaign_exit_2(workdir: Path) -> None:
    c = {**CAMPAIGN, "max_strategies": 2}
    (workdir / "campaign.json").write_text(json.dumps(c))
    assert run(workdir, "o") == 2


@pytest.mark.parametrize(
    "patch", [{"oos_start": "2019-01-01"}, {"oos_start": "2021-13-01"}, {"symbols": []}, {"cost_bps": -1}]
)
def test_invalid_campaign_exit_2(workdir: Path, patch: dict[str, Any]) -> None:
    (workdir / "campaign.json").write_text(json.dumps({**CAMPAIGN, **patch}))
    assert run(workdir, "o") == 2


def test_bars_validation(tmp_path: Path) -> None:
    good = "symbol,date,open,high,low,close,adj_close,volume\n"
    cases = {
        "header": "symbol,date,close\n",
        "unsorted": good + "B,2020-01-02,1,1,1,1,,1\nA,2020-01-02,1,1,1,1,,1\n",
        "dup": good + "A,2020-01-02,1,1,1,1,,1\nA,2020-01-02,1,1,1,1,,1\n",
        "negative": good + "A,2020-01-02,1,1,1,-1,,1\n",
        "date": good + "A,2020-1-2,1,1,1,1,,1\n",
    }
    for name, text in cases.items():
        p = tmp_path / f"{name}.csv"
        p.write_text(text)
        with pytest.raises(InputError):
            read_bars(p)


def test_bars_roundtrip_is_canonical(tmp_path: Path) -> None:
    rows = [
        Bar("A", "2020-01-03", 1.0 / 3, 2.0, 0.5, 1.25, None, 10.0),
        Bar("A", "2020-01-02", 1.0, 1.0, 1.0, 1.0, 0.9, 5.0),
    ]
    write_bars(rows, tmp_path / "b.csv")
    raw = (tmp_path / "b.csv").read_bytes()
    assert raw == (
        b"symbol,date,open,high,low,close,adj_close,volume\n"
        b"A,2020-01-02,1,1,1,1,0.9,5\n"
        b"A,2020-01-03,0.3333333333,2,0.5,1.25,,10\n"
    )
    s = read_bars(tmp_path / "b.csv")["A"]
    assert s.prices == [0.9, 1.25]  # empty adj_close falls back to close


def test_business_days_helper() -> None:
    assert business_days("2020-01-03", 3) == ["2020-01-03", "2020-01-06", "2020-01-07"]
