# SPDX-License-Identifier: Apache-2.0
"""Alpha Vantage: a documented JSON API with a free key (`api_key`, a secret field).

`https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=..&outputsize=..&apikey=..`.
Free tier: about **25 requests a day** and 5 a minute, daily bars, NOT adjusted (the adjusted
series is premium) — so `adj_close` is null. The free key also gets `outputsize=compact` only
(the last 100 trading days); a request that starts earlier asks for `full`, and a premium-only
answer is reported as a rate limit, not hidden. Quota messages arrive as HTTP 200 with a `Note` or
`Information` field; they are mapped to `RateLimited`. `test()` does not call the API: it would
burn one of the 25.

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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self._api_key:
            raise MarketDataError("alphavantage: the secret `api_key` is required")

    async def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        reponse = await self.fetch(URL, {**params, "apikey": self._api_key})
        if reponse.status_code != 200:
            raise MarketDataError(f"alphavantage: HTTP {reponse.status_code}")
        try:
            corps = reponse.json()
        except ValueError as erreur:
            raise MarketDataError("alphavantage: unreadable answer") from erreur
        if not isinstance(corps, dict):
            raise MarketDataError("alphavantage: unexpected answer")
        message = corps.get("Note") or corps.get("Information")
        if message:
            raise RateLimited(f"alphavantage: {self.scrub(str(message))[:200]}")
        if "Error Message" in corps:
            raise UnknownSymbol(f"alphavantage: unknown symbol {params.get('symbol', '')}")
        return corps

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        debut, fin = validate_range(start, end)
        symbole = validate_symbol(symbol)
        recent = (date.today() - debut).days <= COMPACT_DAYS
        corps = await self._query(
            {
                "function": "TIME_SERIES_DAILY",
                "symbol": symbole,
                "outputsize": "compact" if recent else "full",
            }
        )
        serie = corps.get("Time Series (Daily)")
        if not isinstance(serie, dict):
            raise UnknownSymbol(f"alphavantage: no daily series for {symbole}")
        bars = []
        for jour, ligne in serie.items():
            bar = make_bar(
                str(jour),
                ligne.get("1. open"),
                ligne.get("2. high"),
                ligne.get("3. low"),
                ligne.get("4. close"),
                ligne.get("5. volume"),
            )
            if bar is not None:
                bars.append(bar)
        return answer(symbol, self.name, finish_bars(bars, debut, fin), currency=None, adjusted=False)

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        symbole = validate_symbol(symbol)
        corps = await self._query({"function": "SYMBOL_SEARCH", "keywords": symbole})
        for candidat in corps.get("bestMatches") or []:
            if str(candidat.get("1. symbol", "")).upper() == symbole.upper():
                reponse: dict[str, Any] = {
                    "symbol": symbol,
                    "type": _TYPES.get(str(candidat.get("3. type", "")).lower(), "unknown"),
                    "provider": self.name,
                }
                for cle, source in (
                    ("name", candidat.get("2. name")),
                    ("currency", candidat.get("8. currency")),
                ):
                    if source:
                        reponse[cle] = source
                return reponse
        raise UnknownSymbol(f"alphavantage: unknown symbol {symbol}")

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name, "checked": "configuration only (free quota is scarce)"}
