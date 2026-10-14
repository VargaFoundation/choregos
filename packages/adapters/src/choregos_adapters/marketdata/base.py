# SPDX-License-Identifier: Apache-2.0
"""What every market-data provider shares: input validation, bar normalisation, one HTTP helper.

The inputs come from an agent, so they are validated here a second time (the broker already applies
the JSON schema): a symbol can never change the host or the path of the request, a date is ISO
`YYYY-MM-DD` and nothing else, and `start` is not after `end`.

Secrets: a provider key is a constructor argument, appended to the query by the provider that needs
it. It never appears in a result, in an error message (`scrub`), in a log line or in `repr()`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import httpx

from ..errors import AdapterError

#: What a symbol may be: letters, digits and `. ^ = _ -` (`BRK-B`, `^GSPC`, `EURUSD=X`, `MC.PA`).
SYMBOL_PATTERN = r"^[A-Za-z0-9.^=_-]{1,20}$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_SYMBOL = re.compile(SYMBOL_PATTERN)
_DATE = re.compile(DATE_PATTERN)

USER_AGENT = "choregos-marketdata/1.0 (+https://github.com/varga-foundation/choregos)"
#: Total attempts for a transient failure (5xx, network): one call, two retries — free tiers are
#: small, and a retry storm is how a quota is burnt. A 429 is never retried.
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER_S = 5.0


class _RedactApiKey(logging.Filter):
    """httpx logs every request at INFO with its full URL — query included, so `apikey=` too.
    The key is blanked in the record before any handler sees it."""

    _KEY = re.compile(r"(apikey|api_key|token)=[^&\s\"']+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self._KEY.search(message):
            record.msg, record.args = self._KEY.sub(r"\1=***", message), None
        return True


logging.getLogger("httpx").addFilter(_RedactApiKey())


class MarketDataError(AdapterError):
    """A provider could not answer."""


class UnknownSymbol(MarketDataError):  # noqa: N818 - a named refusal, rendered as is
    """The provider does not know this symbol (or has no data for it)."""


class RateLimited(MarketDataError):  # noqa: N818 - a named refusal, rendered as is
    """The provider refused for volume or quota: retrying now would not help."""


def validate_symbol(symbol: Any) -> str:
    """A symbol that cannot change the URL: the pattern, and no path-like dots (`.`, `..`)."""
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise MarketDataError(f"symbol refused: it must match {SYMBOL_PATTERN}")
    if symbol.startswith(".") or ".." in symbol:
        raise MarketDataError("symbol refused: it cannot start with a dot or contain `..`")
    return symbol


def parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise MarketDataError(f"{field} refused: an ISO date YYYY-MM-DD is expected")
    try:
        return date.fromisoformat(value)
    except ValueError as erreur:
        raise MarketDataError(f"{field} refused: {value} is not a calendar date") from erreur


def validate_range(start: Any, end: Any) -> tuple[date, date]:
    debut, fin = parse_date(start, "start"), parse_date(end, "end")
    if debut > fin:
        raise MarketDataError("start is after end")
    return debut, fin


def number(value: Any) -> float | None:
    """A float, or None when the source has nothing (empty, `null`, `-`, `NaN`)."""
    if value is None or value in {"", "-", "null", "N/A"}:
        return None
    try:
        converti = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(converti) else converti


def make_bar(
    day: str,
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    volume: Any = None,
    adj_close: Any = None,
) -> dict[str, Any] | None:
    """One normalised bar, or None when the source gave no close (a holiday row, a null)."""
    cloture = number(close)
    if cloture is None:
        return None
    return {
        "date": day,
        "open": number(open_) if number(open_) is not None else cloture,
        "high": number(high) if number(high) is not None else cloture,
        "low": number(low) if number(low) is not None else cloture,
        "close": cloture,
        "adj_close": number(adj_close),
        "volume": number(volume) if number(volume) is not None else 0.0,
    }


def finish_bars(bars: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    """Inside [start, end], sorted by date, one bar per date (the last one read wins)."""
    par_date = {b["date"]: b for b in bars if start.isoformat() <= b["date"] <= end.isoformat()}
    return [par_date[d] for d in sorted(par_date)]


def answer(
    symbol: str,
    provider: str,
    bars: list[dict[str, Any]],
    *,
    currency: str | None,
    adjusted: bool,
) -> dict[str, Any]:
    reponse: dict[str, Any] = {"symbol": symbol, "provider": provider}
    if currency:
        reponse["currency"] = currency
    reponse.update(adjusted=adjusted, bars=bars)
    return reponse


class MarketDataProvider:
    """The contract of a market-data connector: two reads, called by name, plus `test()`."""

    #: The provider's name in the answers (`provider`).
    name = "marketdata"

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout_s, transport=transport, headers={"User-Agent": USER_AGENT}
        )
        self._backoff_s = backoff_s
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── what a subclass provides ────────────────────────────────────────────────────────────
    async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        raise NotImplementedError

    async def get_instrument(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "provider": self.name}

    # ── the adapter surface the broker calls ────────────────────────────────────────────────
    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation == "get_bars":
            if set(params) - {"symbol", "start", "end"}:
                raise MarketDataError("get_bars: unexpected argument")
            return await self.get_bars(params.get("symbol"), params.get("start"), params.get("end"))  # type: ignore[arg-type]
        if operation == "get_instrument":
            if set(params) - {"symbol"}:
                raise MarketDataError("get_instrument: unexpected argument")
            return await self.get_instrument(params.get("symbol"))  # type: ignore[arg-type]
        raise AdapterError(f"{type(self).__name__} has no operation {operation}")

    # ── helpers ─────────────────────────────────────────────────────────────────────────────
    def scrub(self, text: str) -> str:
        """The key never leaves in a message, whatever the provider echoes."""
        return text.replace(self._api_key, "***") if self._api_key else text

    async def fetch(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """A GET with a bounded retry on 5xx and network errors. 429 raises `RateLimited` at once.

        Any other 4xx is returned to the caller, which knows what its provider means by it."""
        derniere: str = ""
        for tentative in range(MAX_ATTEMPTS):
            try:
                reponse = await self._client.get(url, params=params)
            except httpx.HTTPError as panne:
                # The class only: an httpx message can carry the URL, and the URL carries the key.
                derniere = f"network error ({type(panne).__name__})"
            else:
                if reponse.status_code == 429:
                    raise RateLimited(f"{self.name}: rate limited (429)")
                if reponse.status_code < 500:
                    return reponse
                derniere = f"HTTP {reponse.status_code}"
            if tentative + 1 < MAX_ATTEMPTS:
                await self._sleep(min(self._backoff_s * 2**tentative, MAX_RETRY_AFTER_S))
        raise MarketDataError(f"{self.name}: unavailable after {MAX_ATTEMPTS} attempts ({derniere})")
