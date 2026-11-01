# SPDX-License-Identifier: Apache-2.0
"""Stooq: daily CSV, free, no account (personal / internal use).

`https://stooq.com/q/d/l/?s={symbol}&d1=YYYYMMDD&d2=YYYYMMDD&i=d`. Stooq names its instruments
`<ticker>.<market>`; the caller gives a ticker the usual way (`AAPL`, `MC.PA`, `^SPX`) and
`suffix_map` turns it into Stooq's:

- no dot (`AAPL`, `BRK-B`)         -> the `""` entry of the map, `.us` by default;
- a dotted suffix (`VOD.L`)         -> the map entry for that suffix (case-insensitive);
- a suffix Stooq already uses       -> kept (`aapl.us`);
- an index or a currency (`^SPX`, `EURUSD=X`) -> lower-cased, the `=X` dropped, no suffix.

The default map is `.L`->`.uk`, `.DE`->`.de`, `.HK`->`.hk`, `.T`->`.jp`, `.WA`->`.pl`, `.PA`->`.fr`.
Only `.us` was exercised by the author's notes; **the others, and the very existence of a Paris
market on Stooq, are not verified** — that is exactly why the map is configurable.

Stooq may now demand an `apikey` on CSV downloads (obtained once through its site): configure the
secret `api_key` and it is appended as `&apikey=`. Without it, the "get your apikey" page is
reported as such. Since 2026-10 at least, Stooq may also answer a JavaScript browser check (an
HTML page) to any script: reported as a source failure, never as an unknown symbol, and not worked
around. Prices are split-adjusted, not dividend-adjusted, so `adj_close` is null and
`adjusted` is false. Stooq carries no currency or instrument metadata: `get_instrument` says
`unknown`.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .base import (
    MarketDataError,
    MarketDataProvider,
    RateLimited,
    UnknownSymbol,
    answer,
    finish_bars,
    make_bar,
    validate_range,
    validate_symbol,
)

URL = "https://stooq.com/q/d/l/"
DEFAULT_SUFFIX_MAP: dict[str, str] = {
    "": ".us",
    ".L": ".uk",
    ".DE": ".de",
    ".HK": ".hk",
    ".T": ".jp",
    ".WA": ".pl",
    ".PA": ".fr",
}
_STOOQ_SUFFIXES = {".us", ".uk", ".de", ".hk", ".jp", ".pl", ".fr", ".hu"}


class StooqProvider(MarketDataProvider):
    name = "stooq"

    def __init__(self, *, suffix_map: dict[str, str] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Keys compared case-insensitively; a configured map extends the default, it does not replace it.
        merged = {**DEFAULT_SUFFIX_MAP, **(suffix_map or {})}
        self._suffixes = {key.lower(): value.lower() for key, value in merged.items()}

    def stooq_symbol(self, symbol: str) -> str:
        clean_symbol = validate_symbol(symbol).lower()
        if clean_symbol.startswith("^"):
            return clean_symbol
        if clean_symbol.endswith("=x"):
            return clean_symbol[:-2]
        if "." not in clean_symbol:
            return clean_symbol + self._suffixes.get("", ".us")
        base, _, last_day = clean_symbol.rpartition(".")
        suffix = "." + last_day
        if suffix in self._suffixes:
            return base + self._suffixes[suffix]
        if suffix in _STOOQ_SUFFIXES:
            return clean_symbol
        raise MarketDataError(f"stooq: no market for the suffix {suffix} — add it to `suffix_map`")

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        first_day, last_day = validate_range(start, end)
        stooq = self.stooq_symbol(symbol)
        params: dict[str, Any] = {
            "s": stooq,
            "d1": first_day.strftime("%Y%m%d"),
            "d2": last_day.strftime("%Y%m%d"),
            "i": "d",
        }
        if self._api_key:
            params["apikey"] = self._api_key
        response = await self.fetch(URL, params)
        if response.status_code != 200:
            raise MarketDataError(f"stooq: HTTP {response.status_code}")
        bars = self._parse(response.text, symbol)
        return answer(
            symbol, self.name, finish_bars(bars, first_day, last_day), currency=None, adjusted=False
        )

    def _parse(self, text: str, symbol: str) -> list[dict[str, Any]]:
        body = text.strip()
        lowered = body.lower()
        if "exceeded the daily hits limit" in lowered:
            raise RateLimited("stooq: daily hits limit exceeded")
        if "apikey" in lowered and not lowered.startswith("date"):
            raise MarketDataError("stooq: an `api_key` is required for CSV downloads (see the module doc)")
        if not body or lowered.startswith("no data"):
            raise UnknownSymbol(f"stooq: no data for {symbol}")
        if lowered.startswith(("<!doctype", "<html")):
            # Seen on 2026-10-14: a JavaScript proof-of-work page in place of the CSV, for every
            # symbol. The source is down for a script, the symbol is not unknown: saying "unknown"
            # would let a composite drop its cross-check without a word.
            raise MarketDataError("stooq: an HTML page (browser check) in place of the CSV")
        if not lowered.startswith("date"):
            raise MarketDataError(f"stooq: unreadable answer for {symbol}: {body[:80]!r}")
        bars = []
        for row in csv.DictReader(io.StringIO(body)):
            bar = make_bar(
                (row.get("Date") or "").strip(),
                row.get("Open"),
                row.get("High"),
                row.get("Low"),
                row.get("Close"),
                row.get("Volume"),
            )
            if bar is not None:
                bars.append(bar)
        return bars

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        self.stooq_symbol(symbol)
        return {"symbol": symbol, "type": "unknown", "provider": self.name}

    async def test(self) -> dict[str, Any]:
        await self.get_bars("AAPL", "2024-01-02", "2024-01-05")
        return {"ok": True, "provider": self.name}
