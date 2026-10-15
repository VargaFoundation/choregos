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


def _timestamp(jour: Any, *, fin_de_journee: bool = False) -> int:
    moment = datetime.combine(jour, time.min, tzinfo=UTC)
    return int((moment + timedelta(days=1 if fin_de_journee else 0)).timestamp())


class YahooProvider(MarketDataProvider):
    name = "yahoo"

    async def _chart(self, symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        # The symbol is validated (no `/`, `?`, `#`, `..`) AND percent-encoded: it stays one path segment.
        reponse = await self.fetch(BASE + quote(validate_symbol(symbol), safe=""), params)
        try:
            corps = reponse.json()
            chart = corps["chart"]
        except (ValueError, KeyError, TypeError) as erreur:
            raise MarketDataError(f"yahoo: unreadable answer (HTTP {reponse.status_code})") from erreur
        erreur_yahoo = chart.get("error")
        if erreur_yahoo or reponse.status_code == 404:
            code = (erreur_yahoo or {}).get("code", "")
            raise UnknownSymbol(f"yahoo: {code or 'not found'} for {symbol}")
        if reponse.status_code != 200 or not chart.get("result"):
            raise MarketDataError(f"yahoo: HTTP {reponse.status_code}")
        resultat: dict[str, Any] = chart["result"][0]
        return resultat

    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        debut, fin = validate_range(start, end)
        resultat = await self._chart(
            symbol,
            {
                "period1": _timestamp(debut),
                "period2": _timestamp(fin, fin_de_journee=True),
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            },
        )
        meta = resultat.get("meta") or {}
        decalage = int(meta.get("gmtoffset") or 0)
        indicateurs = resultat.get("indicators") or {}
        cotation = (indicateurs.get("quote") or [{}])[0]
        ajuste = ((indicateurs.get("adjclose") or [{}])[0]).get("adjclose")

        def at(serie: Any, i: int) -> Any:
            return serie[i] if isinstance(serie, list) and i < len(serie) else None

        bars = []
        for i, ts in enumerate(resultat.get("timestamp") or []):
            jour = (datetime.fromtimestamp(int(ts) + decalage, tz=UTC)).date().isoformat()
            bar = make_bar(
                jour,
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
            finish_bars(bars, debut, fin),
            currency=meta.get("currency"),
            adjusted=ajuste is not None,
        )

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        resultat = await self._chart(symbol, {"range": "1d", "interval": "1d"})
        meta = resultat.get("meta") or {}
        reponse: dict[str, Any] = {
            "symbol": symbol,
            "type": _TYPES.get(str(meta.get("instrumentType", "")).upper(), "unknown"),
            "provider": self.name,
        }
        for cle, source in (
            ("name", meta.get("longName") or meta.get("shortName")),
            ("currency", meta.get("currency")),
            ("exchange", meta.get("fullExchangeName") or meta.get("exchangeName")),
        ):
            if source:
                reponse[cle] = source
        return reponse

    async def test(self) -> dict[str, Any]:
        await self.get_instrument("AAPL")
        return {"ok": True, "provider": self.name}
