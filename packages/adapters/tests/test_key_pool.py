# SPDX-License-Identifier: Apache-2.0
"""Several keys for one field, used in turn (S39-30b, ADR 0089).

The shared `Pool` (failover, round robin, cool-down, bench), what a pooled field resolves to, and two
adapters that adopt it: a market-data provider whose quota answer moves the call to the next key, and
a search backend whose refused key does the same. A pool of one behaves exactly as before.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from choregos_adapters.http import pool as pool_module
from choregos_adapters.http.pool import Pool
from choregos_adapters.marketdata import AlphaVantageProvider, RateLimited
from choregos_adapters.registry import configuration_resolue, validate_config
from choregos_adapters.web_search import BraveSearch, SearchError, SearchRateLimited
from choregos_core.egress import hosts_of_config

from .test_marketdata import ALPHA_DAILY

KEYS = ["key-one-AAAA", "key-two-BBBB", "key-three-CCCC"]


class LimitedError(Exception):
    def __init__(self, message: str = "429", wait: float | None = None) -> None:
        super().__init__(message)
        self.wait = wait


class RefusedError(Exception):
    pass


class ExhaustedError(Exception):
    pass


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    pool_module.reset()
    yield
    pool_module.reset()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _vendor(answers: dict[str, list[str]], pool: Pool) -> Any:
    """A call that answers per key: `ok`, `limited`, `refused`, in the order given."""
    seen: list[str] = []

    async def call() -> str:
        key = pool.current
        seen.append(key)
        outcome = answers[key].pop(0) if len(answers[key]) > 1 else answers[key][0]
        if outcome == "limited":
            raise LimitedError(wait=30)
        if outcome == "refused":
            raise RefusedError("HTTP 401")
        return key

    call.seen = seen  # type: ignore[attr-defined]
    return call


async def _run(pool: Pool, call: Any) -> str:
    result: str = await pool.run(
        call,
        rate_limited=LimitedError,
        refused=RefusedError,
        exhausted=lambda wait: ExhaustedError(f"wait {wait}"),
        retry_after=lambda e: getattr(e, "wait", None),
    )
    return result


async def test_failover_keeps_the_first_key_while_it_answers() -> None:
    pool = Pool(KEYS)
    call = _vendor({k: ["ok"] for k in KEYS}, pool)
    assert [await _run(pool, call) for _ in range(3)] == [KEYS[0]] * 3


async def test_a_rate_limit_moves_the_call_to_the_next_key_and_rests_the_first() -> None:
    clock = Clock()
    pool = Pool(KEYS, clock=clock)
    call = _vendor({KEYS[0]: ["limited"], KEYS[1]: ["ok"], KEYS[2]: ["ok"]}, pool)
    assert await _run(pool, call) == KEYS[1]
    assert await _run(pool, call) == KEYS[1], "the first key rests: it is not asked again"
    assert call.seen == [KEYS[0], KEYS[1], KEYS[1]]
    clock.now += 31
    call2 = _vendor({k: ["ok"] for k in KEYS}, pool)
    assert await _run(pool, call2) == KEYS[0], "rested for its Retry-After, then back first"


async def test_round_robin_spreads_the_calls() -> None:
    pool = Pool(KEYS, strategy="round_robin")
    call = _vendor({k: ["ok"] for k in KEYS}, pool)
    assert [await _run(pool, call) for _ in range(4)] == [KEYS[0], KEYS[1], KEYS[2], KEYS[0]]


async def test_a_refused_key_is_benched_and_named_by_its_position_never_its_value() -> None:
    clock = Clock()
    pool = Pool(KEYS[:2], clock=clock)
    call = _vendor({KEYS[0]: ["refused"], KEYS[1]: ["refused"]}, pool)
    with pytest.raises(RefusedError) as refused:
        await _run(pool, call)
    assert "key 2 of 2" in str(refused.value)
    assert not any(k in str(refused.value) for k in KEYS)
    clock.now += 599
    with pytest.raises(ExhaustedError):
        await _run(pool, call)
    assert len(call.seen) == 2, "a benched key is not asked again within 10 minutes"


async def test_every_key_resting_fails_without_calling_anyone() -> None:
    pool = Pool(KEYS[:2])
    call = _vendor({KEYS[0]: ["limited"], KEYS[1]: ["limited"]}, pool)
    with pytest.raises(ExhaustedError, match="wait"):
        await _run(pool, call)
    with pytest.raises(ExhaustedError):
        await _run(pool, call)
    assert len(call.seen) == 2


async def test_a_pool_of_one_is_the_old_behaviour() -> None:
    pool = Pool(KEYS[0])
    call = _vendor({KEYS[0]: ["limited", "ok"]}, pool)
    with pytest.raises(LimitedError):
        await _run(pool, call)
    assert await _run(pool, call) == KEYS[0], "no cool-down for a single key: the vendor decides"


async def test_the_rest_survives_a_rebuilt_adapter_and_tasks_see_their_own_key() -> None:
    first = Pool(KEYS[:2])
    await _run(first, _vendor({KEYS[0]: ["limited"], KEYS[1]: ["ok"]}, first))
    again = Pool(KEYS[:2])
    seen = await asyncio.gather(*[_run(again, _vendor({k: ["ok"] for k in KEYS}, again)) for _ in range(3)])
    assert seen == [KEYS[1]] * 3, "the same keys in a new pool remember the rest"


# ── what a pooled field resolves to ─────────────────────────────────────────────────────────


def _sealed(values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """The references a connector holds for typed values, and the values the platform read."""
    return {f: f"store:connector/x/{f}" for f in values}, values


def test_the_slots_of_a_pooled_field_become_a_list_in_slot_order() -> None:
    refs, values = _sealed({"api_key.10": "ten", "api_key": "one", "api_key.2": "two"})
    resolved = configuration_resolue("web_search", "brave", {}, refs, preresolved=values)
    assert resolved["api_key"] == ["one", "two", "ten"]
    assert not any("." in k for k in resolved)


def test_one_key_stays_a_string_and_a_type_that_pools_nothing_sees_no_slot() -> None:
    refs, values = _sealed({"api_key": "one"})
    assert configuration_resolue("web_search", "brave", {}, refs, preresolved=values) == {"api_key": "one"}
    refs, values = _sealed({"api_key": "one", "api_key.2": "two"})
    firecrawl = configuration_resolue("web_search", "firecrawl", {}, refs, preresolved=values)
    assert firecrawl == {"api_key": "one"}


def test_key_rotation_is_a_setting_of_a_pooling_type_only() -> None:
    validate_config("web_search", "brave", {"key_rotation": "round_robin"})
    with pytest.raises(Exception, match="failover"):
        validate_config("web_search", "brave", {"key_rotation": "random"})
    with pytest.raises(Exception, match="one key per field"):
        validate_config("marketdata", "yahoo", {"key_rotation": "failover"})


def test_a_list_of_urls_gives_each_host() -> None:
    assert hosts_of_config({"base_url": ["https://a.example/x", "https://b.example:8443"]}) == [
        ("a.example", 443),
        ("b.example", 8443),
    ]


# ── two adapters that adopt it ──────────────────────────────────────────────────────────────


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


async def test_a_refused_search_key_moves_to_the_next_and_no_key_leaks() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        token = request.headers["X-Subscription-Token"]
        seen.append(token)
        if token == KEYS[0]:
            return httpx.Response(401, json={"message": f"bad token {token}"})
        return httpx.Response(200, json={"web": {"results": [{"title": "A", "url": "https://a.example"}]}})

    brave = BraveSearch(api_key=KEYS[:2], transport=httpx.MockTransport(answer))
    found = await brave.search("python")
    assert found[0]["url"] == "https://a.example" and seen == [KEYS[0], KEYS[1]]
    assert KEYS[0] not in brave.scrub(f"echo {KEYS[0]} and {KEYS[1]}")
    assert KEYS[1] not in brave.scrub(f"echo {KEYS[0]} and {KEYS[1]}")


async def test_a_search_with_every_key_limited_is_a_429_with_its_wait() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(429, headers={"retry-after": "40"}))
    brave = BraveSearch(api_key=KEYS[:2], transport=transport)
    with pytest.raises(SearchRateLimited) as limited:
        await brave.search("python")
    assert 30 <= limited.value.retry_after_s <= 41


async def test_a_single_refused_search_key_is_the_old_error() -> None:
    brave = BraveSearch(api_key=KEYS[0], transport=httpx.MockTransport(lambda _r: httpx.Response(403)))
    with pytest.raises(SearchError, match=r"refused \(HTTP 403\)"):
        await brave.search("python")
