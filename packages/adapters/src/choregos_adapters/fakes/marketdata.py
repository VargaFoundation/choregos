# SPDX-License-Identifier: Apache-2.0
"""The fake of the `marketdata` family (ADR 0005): deterministic synthetic bars, scriptable.

Bars are a geometric random walk seeded by the symbol (`random.Random(seed)`, the seed a SHA-256
of the symbol — never Python's per-process `hash`), over business days only. The walk always
starts at the same anchor day, so a date has the same bar whatever window is asked: two calls, two
processes, two machines give the same numbers.

Scripting: `fixtures[symbol]` replaces the synthetic bars of a symbol, `instruments[symbol]` its
description, `errors[symbol]` makes it raise, `cross_checks[symbol]` adds the cross-check a
`composite` would report (`{source, common_days, max_rel_diff, diverging_days}`); `calls` records
every call (name and arguments).
Used under `CHOREGOS_FAKES=1`, by the tests, the e2e and the demo.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..marketdata.base import (
    MarketDataError,
    MarketDataProvider,
    UnknownSymbol,
    answer,
    finish_bars,
    validate_range,
    validate_symbol,
)

#: The first day of every synthetic series (a Monday).
ANCHOR = date(2000, 1, 3)
START_PRICE = 100.0
DAILY_DRIFT = 0.0003
DAILY_VOL = 0.012


def seed_of(symbol: str) -> int:
    return int.from_bytes(hashlib.sha256(symbol.upper().encode()).digest()[:8], "big")


def synthetic_bars(symbol: str, start: date, end: date) -> list[dict[str, Any]]:
    """The bars of `symbol` between two dates: business days, a walk the window does not change."""
    rng = random.Random(seed_of(symbol))
    cours = START_PRICE * (0.5 + rng.random())
    bars: list[dict[str, Any]] = []
    jour = ANCHOR
    while jour <= end:
        if jour.weekday() < 5:
            ouverture = cours
            cours = ouverture * math.exp(rng.gauss(DAILY_DRIFT, DAILY_VOL))
            haut = max(ouverture, cours) * (1 + abs(rng.gauss(0, DAILY_VOL / 3)))
            bas = min(ouverture, cours) * (1 - abs(rng.gauss(0, DAILY_VOL / 3)))
            volume = float(rng.randint(100_000, 5_000_000))
            if jour >= start:
                bars.append(
                    {
                        "date": jour.isoformat(),
                        "open": round(ouverture, 4),
                        "high": round(haut, 4),
                        "low": round(bas, 4),
                        "close": round(cours, 4),
                        "adj_close": round(cours, 4),
                        "volume": volume,
                    }
                )
        jour += timedelta(days=1)
    return bars


@dataclass
class FakeMarketData(MarketDataProvider):
    name = "fake"

    fixtures: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    instruments: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    cross_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:  # no HTTP client: nothing to open
        pass

    async def aclose(self) -> None:
        return None

    def __repr__(self) -> str:
        return "<FakeMarketData>"

    def _enter(self, operation: str, symbol: str, **arguments: Any) -> str:
        self.calls.append((operation, {"symbol": symbol, **arguments}))
        validate_symbol(symbol)
        erreur = self.errors.get(symbol)
        if erreur is not None:
            raise erreur
        return symbol

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        self._enter("get_bars", symbol, start=start, end=end)
        debut, fin = validate_range(start, end)
        if symbol in self.fixtures:
            bars = finish_bars([dict(b) for b in self.fixtures[symbol]], debut, fin)
        else:
            bars = synthetic_bars(symbol, debut, fin)
        reponse = answer(symbol, self.name, bars, currency="USD", adjusted=True)
        if symbol in self.cross_checks:
            reponse["cross_check"] = dict(self.cross_checks[symbol])
        return reponse

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        self._enter("get_instrument", symbol)
        if symbol in self.instruments:
            return {"symbol": symbol, "provider": self.name, **self.instruments[symbol]}
        if symbol.startswith("NOPE"):
            raise UnknownSymbol(f"fake: unknown symbol {symbol}")
        return {
            "symbol": symbol,
            "name": f"{symbol.upper()} (synthetic)",
            "type": "stock",
            "currency": "USD",
            "exchange": "FAKE",
            "provider": self.name,
        }

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "fake": "FakeMarketData"}


__all__ = ["FakeMarketData", "MarketDataError", "seed_of", "synthetic_bars"]
