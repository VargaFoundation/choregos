# SPDX-License-Identifier: Apache-2.0
"""Closed grammar of strategies: validation, JSON Schema and signal functions.

Long/flat only. A signal computed with the data of day t is applied at day t+1 (see `engine`).
The bounds below are the single source of truth: the JSON Schema is generated from them and a
test refuses the committed schema file diverging.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from choregos_quant.errors import InputError

# family -> param -> (min, max), integers
BOUNDS: dict[str, dict[str, tuple[int, int]]] = {
    "sma_cross": {"fast": (5, 100), "slow": (20, 300)},
    "ema_trend": {"span": (10, 300)},
    "momentum": {"lookback": (20, 252), "skip": (0, 21)},
    "rsi_reversion": {"period": (2, 30), "entry": (5, 40), "exit": (50, 80)},
    "donchian_breakout": {"entry": (10, 120), "exit": (5, 60)},
}
SCHEMA_PATH = Path(__file__).parent / "schemas" / "strategies.schema.json"
_ID_PATTERN = "^[A-Za-z0-9_-]{1,32}$"


@dataclass(frozen=True)
class Strategy:
    id: str
    hypothesis: str
    family: str
    params: dict[str, int]


def build_schema() -> dict[str, Any]:
    """Plain JSON Schema draft 2020-12 of `strategies.json` (structure and bounds only)."""
    variants = []
    for family, params in BOUNDS.items():
        variants.append(
            {
                "type": "object",
                "required": ["id", "hypothesis", "family", "params"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "pattern": _ID_PATTERN},
                    "hypothesis": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "family": {"const": family},
                    "params": {
                        "type": "object",
                        "required": list(params),
                        "additionalProperties": False,
                        "properties": {
                            name: {"type": "integer", "minimum": lo, "maximum": hi}
                            for name, (lo, hi) in params.items()
                        },
                    },
                },
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://choregos.dev/schemas/quant/strategies.schema.json",
        "title": "Strategies proposed for a backtest campaign",
        "type": "object",
        "required": ["strategies"],
        "additionalProperties": False,
        "properties": {"strategies": {"type": "array", "minItems": 1, "items": {"oneOf": variants}}},
    }


def strategies_schema() -> dict[str, Any]:
    """The schema shipped with the package (read from `schemas/strategies.schema.json`)."""
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_strategies(raw: Any, max_strategies: int = 8) -> list[Strategy]:
    """Strict validation of the LLM's output; raises `InputError` on the first problem."""
    if not isinstance(raw, dict) or set(raw) != {"strategies"} or not isinstance(raw["strategies"], list):
        raise InputError('strategies.json must be {"strategies": [...]}')
    items = raw["strategies"]
    if not items:
        raise InputError("at least one strategy is required")
    if len(items) > max_strategies:
        raise InputError(f"too many strategies: {len(items)} > max_strategies={max_strategies}")
    out: list[Strategy] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = f"strategies[{i}]"
        if not isinstance(item, dict) or set(item) != {"id", "hypothesis", "family", "params"}:
            raise InputError(f"{where}: keys must be exactly id, hypothesis, family, params")
        sid, hyp, family, params = item["id"], item["hypothesis"], item["family"], item["params"]
        if not isinstance(sid, str) or not re.fullmatch(_ID_PATTERN, sid):
            raise InputError(f"{where}: id must match {_ID_PATTERN}")
        if sid in seen:
            raise InputError(f"{where}: duplicate id {sid!r}")
        seen.add(sid)
        if not isinstance(hyp, str) or not hyp.strip() or len(hyp) > 1000:
            raise InputError(f"{where}: hypothesis must be a non-empty string of at most 1000 characters")
        if family not in BOUNDS:
            raise InputError(f"{where}: unknown family {family!r}; allowed: {sorted(BOUNDS)}")
        if not isinstance(params, dict) or set(params) != set(BOUNDS[family]):
            raise InputError(f"{where}: params of {family} must be exactly {sorted(BOUNDS[family])}")
        for name, (lo, hi) in BOUNDS[family].items():
            v = params[name]
            if not _is_int(v) or not lo <= v <= hi:
                raise InputError(f"{where}: {family}.{name} must be an integer in [{lo}, {hi}], got {v!r}")
        if family == "sma_cross" and params["fast"] >= params["slow"]:
            raise InputError(f"{where}: sma_cross needs fast < slow")
        out.append(Strategy(sid, hyp, family, dict(params)))
    return out


# --- indicators (pure, causal: value at t uses prices[0..t] only) -------------------------------


def sma(prices: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(prices)
    total = 0.0
    for t, p in enumerate(prices):
        total += p
        if t >= n:
            total -= prices[t - n]
        if t >= n - 1:
            out[t] = total / n
    return out


def ema(prices: list[float], span: int) -> list[float | None]:
    """EMA seeded with the SMA of the first `span` prices; None before."""
    out: list[float | None] = [None] * len(prices)
    if len(prices) < span:
        return out
    alpha = 2.0 / (span + 1)
    value = sum(prices[:span]) / span
    out[span - 1] = value
    for t in range(span, len(prices)):
        value = alpha * prices[t] + (1 - alpha) * value
        out[t] = value
    return out


def rsi(prices: list[float], period: int) -> list[float | None]:
    """Wilder's RSI; None before `period` changes are available."""
    out: list[float | None] = [None] * len(prices)
    if len(prices) <= period:
        return out
    gains = losses = 0.0
    for t in range(1, period + 1):
        change = prices[t] - prices[t - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    def value(g: float, lo: float) -> float:
        return 100.0 if lo == 0 else 100.0 - 100.0 / (1 + g / lo)

    out[period] = value(avg_gain, avg_loss)
    for t in range(period + 1, len(prices)):
        change = prices[t] - prices[t - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[t] = value(avg_gain, avg_loss)
    return out


# --- signals: 1 = long, 0 = flat, decided with the close of day t -------------------------------


def _sig_sma_cross(prices: list[float], p: dict[str, int]) -> list[int]:
    fast, slow = sma(prices, p["fast"]), sma(prices, p["slow"])
    return [1 if f is not None and s is not None and f > s else 0 for f, s in zip(fast, slow, strict=True)]


def _sig_ema_trend(prices: list[float], p: dict[str, int]) -> list[int]:
    line = ema(prices, p["span"])
    return [1 if e is not None and price > e else 0 for price, e in zip(prices, line, strict=True)]


def _sig_momentum(prices: list[float], p: dict[str, int]) -> list[int]:
    out = [0] * len(prices)
    for t in range(p["lookback"] + p["skip"], len(prices)):
        out[t] = 1 if prices[t - p["skip"]] / prices[t - p["skip"] - p["lookback"]] > 1.0 else 0
    return out


def _sig_rsi(prices: list[float], p: dict[str, int]) -> list[int]:
    out, state = [], 0
    for v in rsi(prices, p["period"]):
        if v is not None:
            if state == 0 and v < p["entry"]:
                state = 1
            elif state == 1 and v > p["exit"]:
                state = 0
        out.append(state)
    return out


def _sig_donchian(prices: list[float], p: dict[str, int]) -> list[int]:
    out, state = [], 0
    for t, price in enumerate(prices):
        if state == 0 and t >= p["entry"] and price > max(prices[t - p["entry"] : t]):
            state = 1
        elif state == 1 and t >= p["exit"] and price < min(prices[t - p["exit"] : t]):
            state = 0
        out.append(state)
    return out


SIGNALS: dict[str, Callable[[list[float], dict[str, int]], list[int]]] = {
    "sma_cross": _sig_sma_cross,
    "ema_trend": _sig_ema_trend,
    "momentum": _sig_momentum,
    "rsi_reversion": _sig_rsi,
    "donchian_breakout": _sig_donchian,
}


def signals(strategy: Strategy, prices: list[float]) -> list[int]:
    return SIGNALS[strategy.family](prices, strategy.params)
