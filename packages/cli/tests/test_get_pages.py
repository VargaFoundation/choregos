# SPDX-License-Identifier: Apache-2.0
"""`get_pages` reads a paged list to its end (ADR 0206): the cursor followed, the filters kept."""

from __future__ import annotations

import httpx
import pytest
from tranibor_cli.client import AsyncClient, Client, Profile

PAGES = {
    None: {"items": [1, 2], "meta": {"has_more": True, "next_cursor": "c1"}},
    "c1": {"items": [3, 4], "meta": {"has_more": True, "next_cursor": "c2"}},
    "c2": {"items": [5], "meta": {"has_more": False, "next_cursor": None}},
}


def _handle(seen: list[dict[str, str]]):  # type: ignore[no-untyped-def]
    def handle(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        return httpx.Response(200, json=PAGES[params.get("cursor")])

    return handle


def test_get_pages_follows_next_cursor_and_keeps_the_filters() -> None:
    seen: list[dict[str, str]] = []
    client = Client(Profile(api_url="http://api.test", token="t", org="acme"))
    client._client = httpx.Client(
        base_url="http://api.test/api/v1", transport=httpx.MockTransport(_handle(seen))
    )
    assert client.get_pages("/runs/r/events", params={"after_seq": 3}) == [1, 2, 3, 4, 5]
    assert [p.get("cursor") for p in seen] == [None, "c1", "c2"]
    assert all(p["after_seq"] == "3" and p["limit"] == "200" for p in seen)


def test_get_pages_returns_a_bare_array_as_it_is() -> None:
    client = Client(Profile(api_url="http://api.test", token="t", org="acme"))
    client._client = httpx.Client(
        base_url="http://api.test/api/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[9])),
    )
    assert client.get_pages("/x") == [9]


@pytest.mark.asyncio
async def test_the_async_get_pages_does_the_same() -> None:
    seen: list[dict[str, str]] = []
    client = AsyncClient(Profile(api_url="http://api.test", token="t", org="acme"))
    client._client = httpx.AsyncClient(
        base_url="http://api.test/api/v1", transport=httpx.MockTransport(_handle(seen))
    )
    assert await client.get_pages("/work-items/w/runs") == [1, 2, 3, 4, 5]
    assert [p.get("cursor") for p in seen] == [None, "c1", "c2"]
