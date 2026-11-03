"""The workflow library loads fast (S55-04).

Every listing re-parsed every core, template, plugin and shared workflow, and read every plugin source's
index (an OCI pull and its signatures, or every offline archive) as soon as one agent resolved nowhere —
with a one-minute cache. The page took seconds. A text is now parsed once, and the sources' index is
served from a ten-minute copy refreshed behind the reader, one refresh at a time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.services import plugin_supply as supply_module
from choregos_api.services import workflow_catalogue
from choregos_api.services.plugin_supply import Shipped, SourcesSupply
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/workflow-catalogue"


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    workflow_catalogue._parsed.cache_clear()
    workflow_catalogue._requirements.cache_clear()
    supply_module._shipping_cache.clear()
    supply_module._shipping_refresh.clear()
    yield
    supply_module._shipping_cache.clear()
    supply_module._shipping_refresh.clear()


async def test_a_second_listing_parses_nothing_again(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = workflow_catalogue.parse_workflow

    def counting(text: str, **kwargs: Any) -> Any:
        calls.append(text)
        return real(text, **kwargs)

    monkeypatch.setattr(workflow_catalogue, "parse_workflow", counting)
    first = await client.get(BASE)
    assert first.status_code == 200, first.text
    parsed_once = len(calls)
    assert parsed_once > 0, "the listing parsed nothing: the test measures nothing"
    second = await client.get(BASE)
    assert second.json() == first.json()
    assert len(calls) == parsed_once, "the second listing parsed every workflow again"


class _CountingSupply(SourcesSupply):
    def __init__(self) -> None:
        self.reads = 0
        self.release = asyncio.Event()

    async def _every_agent(self) -> dict[str, Shipped]:
        self.reads += 1
        await self.release.wait()
        return {"ghost-agent": Shipped("offline", "ghosts", f"1.{self.reads}.0")}


async def test_readers_waiting_for_the_first_index_share_one_read() -> None:
    supply = _CountingSupply()
    readers = [asyncio.create_task(supply.shipping({"ghost-agent"})) for _ in range(5)]
    await asyncio.sleep(0)
    supply.release.set()
    answers = await asyncio.gather(*readers)
    assert supply.reads == 1
    assert all(a == {"ghost-agent": Shipped("offline", "ghosts", "1.1.0")} for a in answers)


async def test_an_expired_index_is_served_at_once_and_refreshed_behind_the_reader() -> None:
    supply = _CountingSupply()
    stale = {"ghost-agent": Shipped("offline", "ghosts", "0.9.0")}
    supply_module._shipping_cache["sources"] = (time.monotonic() - 3600, stale)
    # The refresh is held: the reader must not wait for it.
    answer = await asyncio.wait_for(supply.shipping({"ghost-agent"}), timeout=1)
    assert answer == stale
    again = await asyncio.wait_for(supply.shipping({"ghost-agent"}), timeout=1)
    assert again == stale
    supply.release.set()
    await supply_module._shipping_refresh["sources"]
    assert supply.reads == 1, "two readers of an expired index started two refreshes"
    assert await supply.shipping({"ghost-agent"}) == {"ghost-agent": Shipped("offline", "ghosts", "1.1.0")}


async def test_a_fresh_index_is_not_read_again() -> None:
    supply = _CountingSupply()
    supply.release.set()
    await supply.shipping({"ghost-agent"})
    await supply.shipping({"ghost-agent"})
    assert supply.reads == 1
