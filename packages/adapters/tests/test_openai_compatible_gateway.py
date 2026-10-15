"""S39-07a: the OpenAI-compatible gateway, against a mocked transport."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.gateway.openai_compatible import OpenAiCompatibleGateway


def _gateway(handler: httpx.MockTransport, **options: str) -> OpenAiCompatibleGateway:
    return OpenAiCompatibleGateway(
        "https://gw.example/v1/", client=httpx.AsyncClient(transport=handler), **options
    )


async def test_list_models_maps_the_models_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "gpt-x", "owned_by": "acme"}, {"id": ""}]})

    gateway = _gateway(httpx.MockTransport(handle), api_key="k", headers={"X-Team": "t"})  # type: ignore[arg-type]
    models = await gateway.list_models()
    assert [(m.model_name, m.litellm_model, m.owned_by, m.source) for m in models] == [
        ("gpt-x", "gpt-x", "acme", "gateway")
    ]
    assert str(seen[0].url) == "https://gw.example/v1/models"
    assert seen[0].headers["authorization"] == "Bearer k"
    assert seen[0].headers["x-team"] == "t"


async def test_an_error_status_is_an_upstream_error() -> None:
    gateway = _gateway(httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(UpstreamError) as caught:
        await gateway.list_models()
    assert caught.value.status_code == 401


async def test_each_run_gets_its_own_key_id_over_the_shared_run_key() -> None:
    gateway = _gateway(httpx.MockTransport(lambda r: httpx.Response(200, json={})), run_key="run-secret")
    first = await gateway.mint_key({"run_id": "r1"}, 1.0, 60, ["m"])
    second = await gateway.mint_key({"run_id": "r2"}, 1.0, 60, ["m"])
    assert first.key == second.key == "run-secret"
    assert first.key_id != second.key_id


async def test_nothing_is_measured_nor_capped() -> None:
    gateway = _gateway(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert gateway.capabilities == frozenset({"list_models"})
    spend = await gateway.spend("oai:r1")
    assert spend.cost_usd == 0 and spend.requests == 0
