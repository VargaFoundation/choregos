# SPDX-License-Identifier: Apache-2.0
"""Alpha Vantage: a documented JSON API with a free key (`api_key`, a secret field).

`https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=..&outputsize=..&apikey=..`.
Free tier: about **25 requests a day** and 5 a minute, daily bars, NOT adjusted (the adjusted
series is premium) — so `adj_close` is null. The free key also gets `outputsize=compact` only
(the last 100 trading days); a request that starts earlier asks for `full`, and a premium-only
answer is reported as a rate limit, not hidden. Quota messages arrive as HTTP 200 with a `Note` or
`Information` field; they are mapped to `RateLimited`. `test()` does not call the API: it would
burn one of the 25.

`outputsize: compact` (S38-21) always asks for the last 100 trading days, whatever the range: the
free key's way to serve a multi-year request at all. Such a provider does not have the full history
(`full_history` is false), so a composite takes it as a cross-check of the recent days only, never
as the source of the bars.

`get_instrument` uses `SYMBOL_SEARCH` (also one request of the quota).
"""

from __future__ import annotations

from datetime import date
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

URL = "https://www.alphavantage.co/query"
COMPACT_DAYS = 100
_TYPES = {"equity": "stock", "etf": "etf"}


class AlphaVantageProvider(MarketDataProvider):
    name = "alphavantage"

    def __init__(self, *, outputsize: str = "auto", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self._api_key:
            raise MarketDataError("alphavantage: the secret `api_key` is required")
        if outputsize not in ("auto", "compact"):
            raise MarketDataError("alphavantage: `outputsize` is `auto` or `compact`")
        self._compact = outputsize == "compact"
        self.full_history = not self._compact

    async def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        response = await self.fetch(URL, {**params, "apikey": self._api_key})
        if response.status_code != 200:
            raise MarketDataError(f"alphavantage: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as erreur:
            raise MarketDataError("alphavantage: unreadable answer") from erreur
        if not isinstance(body, dict):
            raise MarketDataError("alphavantage: unexpected answer")
        message = body.get("Note") or body.get("Information")
        if message:
            raise RateLimited(f"alphavantage: {self.scrub(str(message))[:200]}")
        if "Error Message" in body:
            raise UnknownSymbol(f"alphavantage: unknown symbol {params.get('symbol', '')}")
        return body

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        first_day, last_day = validate_range(start, end)
        clean_symbol = validate_symbol(symbol)
        recent = self._compact or (date.today() - first_day).days <= COMPACT_DAYS
        body = await self._query(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": clean_symbol,
                "outputsize": "compact" if recent else "full",
            }
        )
        series_data = body.get("Time Series (Daily)")
        if not isinstance(series_data, dict):
            raise UnknownSymbol(f"alphavantage: no daily series for {clean_symbol}")
        bars = []
        for day, row in series_data.items():
            bar = make_bar(
                str(day),
                row.get("1. open"),
                row.get("2. high"),
                row.get("3. low"),
                row.get("4. close"),
                row.get("5. volume"),
            )
            if bar is not None:
                bars.append(bar)
        return answer(
            symbol, self.name, finish_bars(bars, first_day, last_day), currency=None, adjusted=False
        )

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        clean_symbol = validate_symbol(symbol)
        body = await self._query({"function": "SYMBOL_SEARCH", "keywords": clean_symbol})
        for candidate in body.get("bestMatches") or []:
            if str(candidate.get("1. symbol", "")).upper() == clean_symbol.upper():
                response: dict[str, Any] = {
                    "symbol": symbol,
                    "type": _TYPES.get(str(candidate.get("3. type", "")).lower(), "unknown"),
                    "provider": self.name,
                }
                for key, source in (
                    ("name", candidate.get("2. name")),
                    ("currency", candidate.get("8. currency")),
                ):
                    if source:
                        response[key] = source
                return response
        raise UnknownSymbol(f"alphavantage: unknown symbol {symbol}")

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "checked": "configuration only (free quota is scarce)"}
