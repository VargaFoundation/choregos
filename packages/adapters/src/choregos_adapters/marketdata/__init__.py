# SPDX-License-Identifier: Apache-2.0
"""The `marketdata` connector family: daily bars and instrument facts from free sources.

Read-only. Types: `stooq`, `yahoo`, `alphavantage`, `composite` (and `fake`, in `fakes/`).
"""

from __future__ import annotations

from .alphavantage import AlphaVantageProvider
from .base import (
    DATE_PATTERN,
    SYMBOL_PATTERN,
    MarketDataError,
    MarketDataProvider,
    RateLimited,
    UnknownSymbol,
)
from .composite import CompositeProvider
from .stooq import StooqProvider
from .yahoo import YahooProvider

#: The provider types a composite can list, by type name.
PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "stooq": StooqProvider,
    "yahoo": YahooProvider,
    "alphavantage": AlphaVantageProvider,
}

__all__ = [
    "DATE_PATTERN",
    "PROVIDERS",
    "SYMBOL_PATTERN",
    "AlphaVantageProvider",
    "CompositeProvider",
    "MarketDataError",
    "MarketDataProvider",
    "RateLimited",
    "StooqProvider",
    "UnknownSymbol",
    "YahooProvider",
]
