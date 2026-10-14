# SPDX-License-Identifier: Apache-2.0
"""The free market data sources, called for real from a workstation (S38-13).

    uv run python essai/investissement/sources.py [--start 2026-07-01] [--end 2026-09-30]

Each provider alone, then the `composite` the template recommends (yahoo first, a second source
to cross-check). Alpha Vantage takes `ALPHAVANTAGE_API_KEY` from the environment; without it, the
documented public `demo` key, which answers for IBM only. Nothing is written: the JSON goes to
stdout, errors included — a source that refuses is a result, not a crash.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from choregos_adapters.marketdata.alphavantage import AlphaVantageProvider
from choregos_adapters.marketdata.base import MarketDataProvider
from choregos_adapters.marketdata.composite import CompositeProvider
from choregos_adapters.marketdata.stooq import StooqProvider
from choregos_adapters.marketdata.yahoo import YahooProvider

SYMBOLS = ("SPY", "AAPL", "MC.PA", "IBM", "NOSUCHXYZ")


def _resume(answer: dict[str, Any]) -> dict[str, Any]:
    bars = answer.get("bars") or []
    out: dict[str, Any] = {
        "provider": answer.get("provider"),
        "bars": len(bars),
        "first": bars[0]["date"] if bars else None,
        "last": bars[-1]["date"] if bars else None,
        "last_close": bars[-1]["close"] if bars else None,
    }
    for key in ("cross_check", "fallbacks"):
        if key in answer:
            out[key] = answer[key]
    return out


async def _call(provider: MarketDataProvider, symbol: str, start: str, end: str) -> dict[str, Any]:
    try:
        return _resume(await provider.get_bars(symbol, start, end))
    except Exception as error:  # noqa: BLE001 - a refusal is what this essay records
        return {"error": f"{type(error).__name__}: {error}"[:300]}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-09-30")
    args = parser.parse_args()
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "demo")

    def composite() -> CompositeProvider:
        return CompositeProvider(
            [("yahoo", YahooProvider()), ("alphavantage", AlphaVantageProvider(api_key=key))]
        )

    providers: dict[str, MarketDataProvider] = {
        "stooq": StooqProvider(),
        "yahoo": YahooProvider(),
        "alphavantage": AlphaVantageProvider(api_key=key),
        "composite": composite(),
    }
    results: dict[str, Any] = {}
    for name, provider in providers.items():
        symbols = ("IBM",) if name == "alphavantage" and key == "demo" else SYMBOLS
        for symbol in symbols:
            if name == "composite" and key == "demo" and symbol not in ("IBM", "NOSUCHXYZ"):
                continue  # the demo key would only add a refusal per symbol
            results[f"{name}:{symbol}"] = await _call(provider, symbol, args.start, args.end)
        await provider.aclose()
    print(json.dumps({"start": args.start, "end": args.end, "results": results}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
