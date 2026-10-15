"""S39-07a: what a gateway declares it can do must be true of its behaviour."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters import available, build
from choregos_adapters.base import GATEWAY_CAPABILITIES, GatewayAdapter, LlmGateway
from choregos_adapters.fakes.gateway import FakeGateway
from choregos_adapters.gateway.direct import DirectGateway
from choregos_adapters.gateway.litellm import LiteLlmGateway
from choregos_adapters.gateway.openai_compatible import OpenAiCompatibleGateway


def test_the_protocol_is_documented_under_a_vendor_neutral_name() -> None:
    assert LlmGateway is GatewayAdapter


def test_every_registered_gateway_declares_a_subset_of_the_known_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    config = {"kubernetes": {}, "base_url": "https://gw.example/v1"}
    for type_name in available("gateway"):
        gateway = build("gateway", type_name, config)
        assert isinstance(gateway, GatewayAdapter), type_name
        assert gateway.capabilities <= GATEWAY_CAPABILITIES, type_name


def test_the_declarations_of_the_shipped_gateways() -> None:
    client = httpx.AsyncClient()
    assert LiteLlmGateway("http://x", "k", client=client).capabilities == GATEWAY_CAPABILITIES
    assert DirectGateway().capabilities == frozenset()
    assert OpenAiCompatibleGateway("http://x/v1", client=client).capabilities == {"list_models"}
    assert FakeGateway().capabilities == GATEWAY_CAPABILITIES
    assert FakeGateway(capabilities=frozenset({"list_models"})).capabilities == {"list_models"}


async def test_a_gateway_declaring_hard_budget_gives_the_key_the_budget_asked() -> None:
    """The behavioural check a plugin gateway must pass to declare `virtual_keys` + `hard_budget`."""
    gateways: list[GatewayAdapter] = [FakeGateway()]
    for gateway in gateways:
        assert {"virtual_keys", "hard_budget"} <= gateway.capabilities
        key = await gateway.mint_key({"run_id": "r"}, 2.5, 60, ["platform/standard"])
        assert key.budget_usd == 2.5 and key.models == ["platform/standard"]


async def test_a_gateway_without_spend_readback_reports_an_unmeasured_spend() -> None:
    gateway = DirectGateway()
    assert "spend_readback" not in gateway.capabilities
    assert (await gateway.spend("k")).cost_usd == 0
