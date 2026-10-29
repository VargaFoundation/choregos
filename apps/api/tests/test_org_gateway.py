"""An organisation connects its own model gateway (S44-04).

One per organisation, never the `direct` one in staging or prod, and built with the key typed in the
console: the store's value, not the bare `store:` reference.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga/connectors"
KEY = "sk-acme-gateway-0000"


@pytest.fixture(autouse=True)
def sealed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's key: without it nothing can be typed (ADR 0058)."""
    from choregos_core.redaction import forget_secrets

    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k1:{base64.b64encode(os.urandom(32)).decode()}")
    forget_secrets()


def _gateway(name: str, type_name: str = "openai-compatible") -> dict[str, object]:
    return {
        "name": name,
        "kind": "gateway",
        "type": type_name,
        "config": {"base_url": "https://llm.acme.example/v1"},
        "secret_values": {"api_key": KEY},
        "allow_egress": True,
    }


async def test_the_organisation_gateway_is_built_with_the_key_typed_in_the_console(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.gateway import org_gateway_and_type
    from sqlalchemy import select

    built: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "choregos_api.services.gateway.build",
        lambda kind, type_name, config: built.append((kind, type_name, config)) or object(),
    )

    created = await client.post(ORG, json=_gateway("acme-llm"))
    assert created.status_code == 201, created.text
    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        kind, _ = await org_gateway_and_type(session, org.id)
    assert kind == "openai-compatible"
    [(_, _, config)] = built
    assert config["base_url"] == "https://llm.acme.example/v1"
    assert config["api_key"] == KEY, "the store's value, not the `store:` reference"


async def test_an_organisation_brings_one_gateway(client: AsyncClient, admin: str) -> None:
    assert (await client.post(ORG, json=_gateway("acme-llm"))).status_code == 201
    second = await client.post(ORG, json=_gateway("other-llm"))
    assert second.status_code == 409
    assert "acme-llm" in second.json()["detail"]


async def test_the_direct_gateway_is_refused_in_prod(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import config

    monkeypatch.setattr(config.get_settings(), "env", "prod")
    refused = await client.post(
        ORG, json={**_gateway("acme-llm", "direct"), "config": {}, "secret_values": {}}
    )
    assert refused.status_code == 422
    assert "direct" in refused.json()["detail"]
