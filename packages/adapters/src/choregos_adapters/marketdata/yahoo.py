# SPDX-License-Identifier: Apache-2.0
"""Yahoo Finance: the chart endpoint the finance site itself uses.

**Unofficial, personal use only, may break.** It is not a documented API, it has no SLA and no
licence for redistribution; Yahoo changes or blocks it without notice (a 429 or an HTML consent
page is the usual symptom). Use it as a fallback or as the second opinion of a `composite`, not as
the only source of something that matters.

`https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=..&period2=..&interval=1d
&events=div,splits&includeAdjustedClose=true`. No key. Dates are the exchange's local calendar
date (the timestamp shifted by `meta.gmtoffset`). `adj_close` is dividend- and split-adjusted;
`close` is split-adjusted only.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import quote

from .base import (
    MarketDataError,
    MarketDataProvider,
    UnknownSymbol,
    answer,
    finish_bars,
    make_bar,
    validate_range,
    validate_symbol,
)

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_TYPES = {"EQUITY": "stock", "ETF": "etf"}


def _timestamp(day: Any, *, end_of_day: bool = False) -> int:
    moment = datetime.combine(day, time.min, tzinfo=UTC)
    return int((moment + timedelta(days=1 if end_of_day else 0)).timestamp())


class YahooProvider(MarketDataProvider):
    name = "yahoo"

    async def _chart(self, symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        # The symbol is validated (no `/`, `?`, `#`, `..`) AND percent-encoded: it stays one path segment.
        response = await self.fetch(BASE + quote(validate_symbol(symbol), safe=""), params)
        try:
            body = response.json()
            chart = body["chart"]
        except (ValueError, KeyError, TypeError) as erreur:
            raise MarketDataError(f"yahoo: unreadable answer (HTTP {response.status_code})") from erreur
        yahoo_error = chart.get("error")
        if yahoo_error or response.status_code == 404:
            code = (yahoo_error or {}).get("code", "")
            raise UnknownSymbol(f"yahoo: {code or 'not found'} for {symbol}")
        if response.status_code != 200 or not chart.get("result"):
            raise MarketDataError(f"yahoo: HTTP {response.status_code}")
        result: dict[str, Any] = chart["result"][0]
        return result

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        first_day, last_day = validate_range(start, end)
        result = await self._chart(
            symbol,
            {
                "period1": _timestamp(first_day),
                "period2": _timestamp(last_day, end_of_day=True),
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        meta = result.get("meta") or {}
        decalage = int(meta.get("gmtoffset") or 0)
        indicateurs = result.get("indicators") or {}
        cotation = (indicateurs.get("quote") or [{}])[0]
        ajuste = ((indicateurs.get("adjclose") or [{}])[0]).get("adjclose")

        def at(series_data: Any, i: int) -> Any:
            return series_data[i] if isinstance(series_data, list) and i < len(series_data) else None

        bars = []
        for i, ts in enumerate(result.get("timestamp") or []):
            day = (datetime.fromtimestamp(int(ts) + decalage, tz=UTC)).date().isoformat()
            bar = make_bar(
                day,
                at(cotation.get("open"), i),
                at(cotation.get("high"), i),
                at(cotation.get("low"), i),
                at(cotation.get("close"), i),
                at(cotation.get("volume"), i),
                at(ajuste, i),
            )
            if bar is not None:
                bars.append(bar)
        return answer(
            symbol,
            self.name,
            finish_bars(bars, first_day, last_day),
            currency=meta.get("currency"),
            adjusted=ajuste is not None,
        )

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        result = await self._chart(symbol, {"range": "1d", "interval": "1d"})
        meta = result.get("meta") or {}
        response: dict[str, Any] = {
            "symbol": symbol,
            "type": _TYPES.get(str(meta.get("instrumentType", "")).upper(), "unknown"),
            "provider": self.name,
        }
        for key, source in (
            ("name", meta.get("longName") or meta.get("shortName")),
            ("currency", meta.get("currency")),
            ("exchange", meta.get("fullExchangeName") or meta.get("exchangeName")),
        ):
            if source:
                response[key] = source
        return response

    async def test(self) -> dict[str, Any]:
        await self.get_instrument("AAPL")
        return {"ok": True, "provider": self.name}
