# SPDX-License-Identifier: Apache-2.0
"""The `marketdata` family reaches the platform only through the extension (S83-08, ADR 0181 O4-4).

The core's adapters ship no `marketdata` type; the distribution declares its `choregos.plugins` entry
point under the plugin's name, and loading it is what registers the five types, their fake (so
`CHOREGOS_FAKES=1` switches every `marketdata` connector, ADR 0005) and the eval scene. Drop the entry
point, or a registration from `extension.register`, and a test here turns red.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib import metadata

import pytest
from tranibor_adapters import build, load_extensions, spec_of
from tranibor_adapters import registry as registry_module
from tranibor_adapters.fakes import scenes as scenes_module
from tranibor_adapters.fakes.scenes import scene, scene_type
from tranibor_adapters.specs import _SHIPPED_SPECS
from tranibor_investment_research.extension import NAME
from tranibor_investment_research.marketdata import fake as fake_module

TYPES = {"stooq", "yahoo", "alphavantage", "tiingo", "composite", "fake"}


@pytest.fixture
def unregistered(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The process as the core alone leaves it: no `marketdata` type, no `marketdata` scene."""
    monkeypatch.setattr(
        registry_module,
        "_REGISTRY",
        {k: v for k, v in registry_module._REGISTRY.items() if k[0] != "marketdata"},
    )
    monkeypatch.setattr(
        registry_module, "_SPECS", {k: v for k, v in registry_module._SPECS.items() if k[0] != "marketdata"}
    )
    monkeypatch.setattr(
        scenes_module, "_KINDS", {k: v for k, v in scenes_module._KINDS.items() if k != "marketdata"}
    )
    yield


def test_the_core_ships_no_marketdata_type() -> None:
    assert not [pair for pair in _SHIPPED_SPECS if pair[0] == "marketdata"]


def test_the_distribution_declares_its_entry_point_under_the_plugins_name() -> None:
    (point,) = [p for p in metadata.entry_points(group="choregos.plugins") if p.name == NAME]
    assert point.value == "tranibor_investment_research.extension:register"
    assert point.dist is not None and point.dist.name == "tranibor-investment-research"


@pytest.mark.usefixtures("unregistered")
def test_loading_the_extensions_registers_the_family_and_its_scene() -> None:
    assert spec_of("marketdata", "stooq") is None
    assert NAME in load_extensions()
    assert {t for (k, t) in registry_module._REGISTRY if k == "marketdata"} == TYPES
    stooq = spec_of("marketdata", "stooq")
    assert stooq is not None and {o.name for o in stooq.operations} == {"get_bars", "get_instrument"}
    assert scene_type("marketdata") == "composite"
    played = scene("marketdata")
    assert {o.name for o in played.operations} == {"get_bars", "get_instrument"}


@pytest.mark.usefixtures("unregistered")
def test_a_scene_asked_for_loads_the_extensions_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The playbooks' scripted matrix plays `marketdata` cases in a process that never started the API."""
    monkeypatch.setattr(scenes_module, "_extensions_loaded", False)
    assert scene("marketdata").kind == "marketdata"


def test_an_extension_cannot_take_over_a_core_kinds_scenes() -> None:
    from tranibor_adapters.errors import AdapterError

    with pytest.raises(AdapterError, match="core's own scenes"):
        scenes_module.declare_scene("firewall", "pfsense", frozenset({"default"}), lambda _n: (None, None))


async def test_fakes_switch_every_marketdata_connector_to_the_shared_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    scripted = fake_module.FakeMarketData()
    monkeypatch.setattr(fake_module, "FAKE", scripted)
    for type_name in ("stooq", "composite", "tiingo"):
        assert build("marketdata", type_name, {}) is scripted
    bars = await scripted.get_bars("AAPL", "2024-01-02", "2024-01-05")
    assert bars["bars"] and scripted.calls
