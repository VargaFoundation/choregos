# SPDX-License-Identifier: Apache-2.0
"""Tiingo: a documented end-of-day API with a free key (`api_key`, a secret field) and 30+ years of
daily history — the full-history second opinion a free Alpha Vantage key cannot give (S38-22).

`https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=..&endDate=..`, the key in the
`Authorization: Token ..` header, never in the URL. Free plan ("Starter"): 50 requests an hour,
1,000 a day, 500 distinct symbols a month, **internal and personal use only, no redistribution**.

Tiingo's `close` is the raw print and its `adjClose` is split- and dividend-adjusted. The other
sources' `close` (Yahoo) is split-adjusted only, and a composite compares `close`: so `close` here
is the raw close divided by the splits that happened after its date, which is why the request runs
to today whatever `end` is (a split after `end` still rescales the range). `adj_close` is Tiingo's.

Tiingo does not document its error bodies: a 404 is an unknown symbol, a 401/403 a refused key,
a body carrying `detail` is read for "not found" or a limit, anything else is a source failure.
`get_instrument` reads the ticker's metadata (name, exchange); it carries no instrument type.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

from .base import (
    MarketDataError,
    MarketDataProvider,
    RateLimited,
    UnknownSymbol,
    answer,
    finish_bars,
    make_bar,
    number,
    validate_range,
    validate_symbol,
)

BASE = "https://api.tiingo.com/tiingo/daily/"


def split_adjusted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each row's prices divided by the product of the split factors of the LATER rows: a 4-for-1
    split on day D leaves D's prices as they are and divides every earlier one by 4."""
    factor = 1.0
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: str(r.get("date", "")), reverse=True):
        day = str(row.get("date", ""))[:10]
        prices = {k: number(row.get(k)) for k in ("open", "high", "low", "close")}
        volume = number(row.get("volume"))
        out.append(
            {
                "date": day,
                **{k: (v / factor if v is not None else None) for k, v in prices.items()},
                "volume": volume * factor if volume is not None else None,
                "adj_close": row.get("adjClose"),
            }
        )
        split = number(row.get("splitFactor"))
        if split and split > 0:
            factor *= split
    return out


class TiingoProvider(MarketDataProvider):
    name = "tiingo"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self._api_key:
            raise MarketDataError("tiingo: the secret `api_key` is required")
        # A header, not `token=` in the URL: httpx logs URLs, and a URL travels further than a header.
        self._client.headers["Authorization"] = f"Token {self._api_key}"

    async def _get(self, symbol: str, suffix: str, params: dict[str, Any] | None = None) -> Any:
        # The symbol is validated (no `/`, `?`, `#`, `..`) AND percent-encoded: it stays one path segment.
        url = BASE + quote(validate_symbol(symbol).lower(), safe="") + suffix
        response = await self.fetch(url, params)
        if response.status_code == 404:
            raise UnknownSymbol(f"tiingo: unknown symbol {symbol}")
        if response.status_code in (401, 403):
            raise MarketDataError(f"tiingo: the key was refused (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError as error:
            raise MarketDataError(f"tiingo: unreadable answer (HTTP {response.status_code})") from error
        if isinstance(body, dict) and "detail" in body:
            detail = self.scrub(str(body["detail"]))[:200]
            if "not found" in detail.lower():
                raise UnknownSymbol(f"tiingo: unknown symbol {symbol}")
            if any(word in detail.lower() for word in ("limit", "allocation", "exceeded", "run over")):
                raise RateLimited(f"tiingo: {detail}")
            raise MarketDataError(f"tiingo: {detail}")
        if response.status_code != 200:
            raise MarketDataError(f"tiingo: HTTP {response.status_code}")
        return body

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        first, last = validate_range(start, end)
        # To today, not to `end`: a split after `end` rescales the range too (see the module's note).
        body = await self._get(
            symbol,
            "/prices",
            {"startDate": first.isoformat(), "endDate": max(last, date.today()).isoformat()},
        )
        if not isinstance(body, list):
            raise MarketDataError("tiingo: unexpected answer")
        bars = []
        for row in split_adjusted([r for r in body if isinstance(r, dict)]):
            bar = make_bar(
                row["date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["adj_close"],
            )
            if bar is not None:
                bars.append(bar)
        return answer(symbol, self.name, finish_bars(bars, first, last), currency=None, adjusted=True)

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        body = await self._get(symbol, "")
        if not isinstance(body, dict) or not body.get("ticker"):
            raise UnknownSymbol(f"tiingo: unknown symbol {symbol}")
        result: dict[str, Any] = {"symbol": symbol, "type": "unknown", "provider": self.name}
        for key, source in (("name", body.get("name")), ("exchange", body.get("exchangeCode"))):
            if source:
                result[key] = source
        return result

    async def test(self) -> dict[str, Any]:
        await self.get_instrument("SPY")
        return {"ok": True, "provider": self.name}
