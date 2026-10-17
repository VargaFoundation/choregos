# SPDX-License-Identifier: Apache-2.0
"""Campaign file: what to screen, over which period, and the gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_quant.bars import parse_date
from choregos_quant.errors import InputError


@dataclass(frozen=True)
class Campaign:
    symbols: list[str]
    start: str
    end: str
    oos_start: str
    cost_bps: float = 10.0
    min_dsr: float = 0.95
    max_drawdown: float = 0.35
    min_trades: int = 10
    max_strategies: int = 8
    max_diverging_symbols: int = 0
    seed: str = "choregos"


def _number(raw: dict[str, Any], key: str, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputError(f"campaign.{key} must be a number")
    return float(value)


def parse_campaign(raw: Any) -> Campaign:
    if not isinstance(raw, dict):
        raise InputError("campaign must be a JSON object")
    symbols = raw.get("symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or not all(isinstance(s, str) and s for s in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise InputError("campaign.symbols must be a non-empty list of distinct strings")
    dates = {}
    for key in ("start", "end", "oos_start"):
        value = raw.get(key)
        if not isinstance(value, str):
            raise InputError(f"campaign.{key} is required (YYYY-MM-DD)")
        dates[key] = parse_date(value, f"campaign.{key}")
    if not dates["start"] < dates["oos_start"] <= dates["end"]:
        raise InputError("campaign needs start < oos_start <= end")
    min_trades = _number(raw, "min_trades", 10)
    max_strategies = _number(raw, "max_strategies", 8)
    max_diverging = _number(raw, "max_diverging_symbols", 0)
    if max_diverging < 0:
        raise InputError("campaign.max_diverging_symbols must be >= 0")
    seed = raw.get("seed", "choregos")
    if not isinstance(seed, str):
        raise InputError("campaign.seed must be a string")
    cost = _number(raw, "cost_bps", 10)
    if cost < 0:
        raise InputError("campaign.cost_bps must be >= 0")
    return Campaign(
        symbols=sorted(symbols),
        start=raw["start"],
        end=raw["end"],
        oos_start=raw["oos_start"],
        cost_bps=cost,
        min_dsr=_number(raw, "min_dsr", 0.95),
        max_drawdown=_number(raw, "max_drawdown", 0.35),
        min_trades=int(min_trades),
        max_strategies=int(max_strategies),
        max_diverging_symbols=int(max_diverging),
        seed=seed,
    )


def load_json(path: str | Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"{what}: file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{what}: invalid JSON ({exc})") from exc


def read_campaign(path: str | Path) -> Campaign:
    return parse_campaign(load_json(path, "campaign"))
