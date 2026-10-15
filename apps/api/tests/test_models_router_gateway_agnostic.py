"""S39-07a: `/platform/models` asks the deployment's gateway, not LiteLLM whatever was configured."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from choregos_core.domain import GatewayModel
from httpx import AsyncClient


async def test_the_catalogue_comes_from_the_configured_gateway_type(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red when the router builds `litellm`: the models listed are the other gateway's."""
    from choregos_adapters import register

    asked: list[str] = []

    class Scripted:
        async def list_models(self) -> list[GatewayModel]:
            asked.append("scripted")
            return [GatewayModel(model_name="acme/chat", litellm_model="acme/chat", provider="acme")]

    register("gateway", "scripted-test")(lambda cfg: Scripted())
    monkeypatch.setenv("CHOREGOS_FAKES", "")
    monkeypatch.setenv("CHOREGOS_GATEWAY_TYPE", "scripted-test")
    response = await client.get("/api/v1/platform/models")
    assert response.status_code == 200, response.text
    assert [m["model_name"] for m in response.json()] == ["acme/chat"]
    assert asked == ["scripted"]


async def test_an_openai_compatible_gateway_is_listed_through_its_models_endpoint(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "")
    monkeypatch.setenv("CHOREGOS_GATEWAY_TYPE", "openai-compatible")
    monkeypatch.setenv("CHOREGOS_GATEWAY_URL", "https://gw.example/v1")
    monkeypatch.setenv("CHOREGOS_GATEWAY_MASTER_KEY", "discovery-key")
    with respx.mock() as mock:
        route: Any = mock.get("https://gw.example/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "gpt-x", "owned_by": "acme"}]})
        )
        response = await client.get("/api/v1/platform/models")
    assert response.status_code == 200, response.text
    assert [m["model_name"] for m in response.json()] == ["gpt-x"]
    assert route.calls.last.request.headers["authorization"] == "Bearer discovery-key"
