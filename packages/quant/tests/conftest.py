from __future__ import annotations

import datetime as dt
import json
import random
from collections.abc import Callable
from pathlib import Path

import pytest
from choregos_quant.bars import Bar, write_bars

Rows = list[Bar]


def business_days(start: str, n: int) -> list[str]:
    day = dt.date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


def synth_rows(
    symbol: str, seed: int, n: int = 700, drift: float = 0.0004, start: str = "2020-01-01"
) -> Rows:
    rng = random.Random(seed)
    price = 100.0
    rows: Rows = []
    for d in business_days(start, n):
        price *= 1 + rng.gauss(drift, 0.012)
        rows.append(
            Bar(
                symbol,
                d,
                price,
                price * 1.01,
                price * 0.99,
                price,
                None if rng.random() < 0.5 else price,
                1000.0,
            )
        )
    return rows


CAMPAIGN = {
    "symbols": ["AAA", "BBB", "CCC"],
    "start": "2020-01-01",
    "end": "2023-12-31",
    "oos_start": "2021-09-01",
    "cost_bps": 10,
    "min_dsr": 0.95,
    "max_drawdown": 0.35,
    "min_trades": 10,
    "max_strategies": 8,
}

STRATEGIES = {
    "strategies": [
        {"id": "s1", "hypothesis": "trend", "family": "sma_cross", "params": {"fast": 10, "slow": 50}},
        {"id": "s2", "hypothesis": "ema", "family": "ema_trend", "params": {"span": 40}},
        {"id": "s3", "hypothesis": "mom", "family": "momentum", "params": {"lookback": 60, "skip": 5}},
        {
            "id": "s4",
            "hypothesis": "mean reversion",
            "family": "rsi_reversion",
            "params": {"period": 5, "entry": 30, "exit": 60},
        },
        {
            "id": "s5",
            "hypothesis": "breakout",
            "family": "donchian_breakout",
            "params": {"entry": 20, "exit": 10},
        },
    ]
}


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    rows = synth_rows("AAA", 1) + synth_rows("BBB", 2) + synth_rows("CCC", 3)
    write_bars(rows, tmp_path / "bars.csv")
    (tmp_path / "campaign.json").write_text(json.dumps(CAMPAIGN))
    (tmp_path / "strategies.json").write_text(json.dumps(STRATEGIES))
    return tmp_path


@pytest.fixture
def make_rows() -> Callable[..., Rows]:
    return synth_rows
