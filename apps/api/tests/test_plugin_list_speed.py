"""An organisation's plugin list never waits for a plugin source.

The list asked each installed plugin's source for its newest version: one index pull and signature check
per plugin, at every listing. A source the deployment cannot reach answered after 20 s, and the list of
four such plugins took 80 s on an operator's dev and prod, past the ingress's minute: a 504. A remote
source's index is now read once per ten minutes, an unreadable one included, and the list is answered
from what is already known.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.services import plugin_sources
from choregos_api.services import plugin_supply as supply_module
from choregos_api.services.plugin_supply import SourcesSupply
from choregos_core.plugins.sources import PluginsConfig, SourceConfig, Trust

REMOTE = SourceConfig("varga", "oci://registry.invalid/agents/index:latest", Trust("none"))


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    supply_module._index_cache.clear()
    supply_module._index_refresh.clear()
    monkeypatch.setattr(plugin_sources, "plugins_config", lambda: PluginsConfig(sources=(REMOTE,)))
    yield
    supply_module._index_cache.clear()
    supply_module._index_refresh.clear()


class _UnreachableSource(SourcesSupply):
    """Every read of the source is held until released, then fails as an unreachable registry does."""

    def __init__(self) -> None:
        self.reads = 0
        self.release = asyncio.Event()

    async def _read_index(self, source: str) -> tuple[float, Any]:
        self.reads += 1
        await self.release.wait()
        read = supply_module._index_cache[source] = (time.monotonic(), None)
        return read


async def test_a_list_is_answered_without_waiting_for_a_source() -> None:
    supply = _UnreachableSource()
    answers = await asyncio.wait_for(
        asyncio.gather(*(supply.latest("varga", name, wait=False) for name in ("a", "b", "c", "d"))),
        timeout=1,
    )
    assert answers == [None, None, None, None]
    assert supply.reads == 1, "each plugin of the list started its own read of the source"
    supply.release.set()
    await supply_module._index_refresh["varga"]


async def test_an_unreadable_source_is_read_once_for_every_plugin() -> None:
    supply = _UnreachableSource()
    supply.release.set()
    for name in ("a", "b", "c", "d"):
        assert await supply.latest("varga", name) is None
    assert supply.reads == 1, "an unreadable source was read again for each plugin"


async def test_an_expired_index_is_served_and_read_again_behind_the_reader() -> None:
    supply = _UnreachableSource()
    supply_module._index_cache["varga"] = (time.monotonic() - 3600, None)
    assert await asyncio.wait_for(supply.latest("varga", "a"), timeout=1) is None
    await asyncio.sleep(0)
    assert supply.reads == 1, "an expired index was not read again"
    supply.release.set()
    await supply_module._index_refresh["varga"]
