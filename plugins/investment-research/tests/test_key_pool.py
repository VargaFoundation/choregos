# SPDX-License-Identifier: Apache-2.0
"""The market data providers adopt the core's key pool (S39-30b, ADR 0089): a quota answer or a refused
key moves the call to the next key, and a type that holds one key refuses `key_rotation`."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from tranibor_adapters.http import pool as pool_module
from tranibor_adapters.registry import spec_of, validate_config
from tranibor_investment_research.marketdata import AlphaVantageProvider, RateLimited, TiingoProvider

from .test_marketdata import ALPHA_DAILY, TIINGO_ROWS

KEYS = ["key-one-AAAA", "key-two-BBBB", "key-three-CCCC"]


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    pool_module.reset()
    yield
    pool_module.reset()


def test_key_rotation_is_refused_by_a_type_that_holds_one_key() -> None:
    validate_config("marketdata", "tiingo", {"key_rotation": "round_robin"})
    with pytest.raises(Exception, match="one key per field"):
        validate_config("marketdata", "yahoo", {"key_rotation": "failover"})


async def test_a_market_data_quota_answer_moves_to_the_next_key() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["apikey"])
        if request.url.params["apikey"] == KEYS[0]:
            return httpx.Response(200, json={"Information": f"limit reached for {KEYS[0]}"})
        return httpx.Response(200, json=ALPHA_DAILY)

    provider = AlphaVantageProvider(transport=httpx.MockTransport(answer), api_key=KEYS[:2])
    bars = await provider.get_bars("AAPL", "2024-01-02", "2024-01-31")
    assert len(bars["bars"]) == 2 and seen == [KEYS[0], KEYS[1]]
    again = await provider.get_bars("AAPL", "2024-01-02", "2024-01-31")
    assert again["bars"] and seen[-1] == KEYS[1], "the limited key rests"


async def test_market_data_with_every_key_limited_is_a_rate_limit() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(429))
    provider = AlphaVantageProvider(transport=transport, api_key=KEYS[:2])
    with pytest.raises(RateLimited, match="every key is resting"):
        await provider.get_bars("AAPL", "2024-01-02", "2024-01-31")


async def test_tiingo_sends_the_key_of_each_call_and_moves_past_a_refused_one() -> None:
    """Tiingo's key is a header: set once at construction, every call of a pool would carry the first
    key whatever the turn (S39-30d). Set per request, a refused key moves the call to the next."""
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Token ")
        seen.append(token)
        if token == KEYS[0]:
            return httpx.Response(401, json={"detail": "Invalid token"})
        return httpx.Response(200, json=TIINGO_ROWS)

    tiingo = TiingoProvider(transport=httpx.MockTransport(answer), api_key=KEYS[:2])
    bars = await tiingo.get_bars("AAPL", "2020-08-28", "2020-08-31")
    assert len(bars["bars"]) == 2 and seen == [KEYS[0], KEYS[1]]


def test_a_composite_pools_its_tiingo_keys() -> None:
    tiingo, composite = spec_of("marketdata", "tiingo"), spec_of("marketdata", "composite")
    assert tiingo is not None and tiingo.pooled_fields == ("api_key",)
    assert composite is not None and "tiingo_api_key" in composite.pooled_fields
