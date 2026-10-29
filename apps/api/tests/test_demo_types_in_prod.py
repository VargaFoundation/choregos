"""The demonstration connector types are neither offered nor declarable in staging and prod.

Their factories refuse to build there; the console still listed them, and declaring one succeeded
only for the connector to fail at its first call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

DEMOS = {("mdm", "demo"), ("shipping", "demo"), ("access_control", "demo"), ("cd", "demo")}


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    # the development login is refused in prod: the cookie already set is enough
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    reset_settings_cache()
    yield
    monkeypatch.delenv("CHOREGOS_ENV", raising=False)
    monkeypatch.delenv("CHOREGOS_DEV_LOGIN_ENABLED", raising=False)
    reset_settings_cache()


def _types(body: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(t["kind"], t["type"]) for t in body}


async def test_the_demos_are_offered_outside_staging_and_prod(client: AsyncClient, admin: str) -> None:
    assert _types((await client.get("/api/v1/connectors/types")).json()) >= DEMOS


async def test_prod_offers_no_demo_type(client: AsyncClient, admin: str, prod: None) -> None:
    offered = _types((await client.get("/api/v1/connectors/types")).json())
    assert not DEMOS & offered
    assert ("documents", "confluence") in offered
    catalogue = (await client.get("/api/v1/orgs/varga/connector-catalogue")).json()
    entries = catalogue["entries"] if isinstance(catalogue, dict) else catalogue
    assert not DEMOS & _types(entries)


async def test_prod_refuses_to_declare_a_demo(
    client: AsyncClient, admin: str, project: dict[str, Any], prod: None
) -> None:
    body = {"name": "fleet", "type": "demo", "kind": "mdm", "config": {}}
    refused = await client.post("/api/v1/orgs/varga/connectors", json=body)
    assert refused.status_code == 422, refused.text
    assert "refused in prod" in refused.json()["detail"]

    put = f"/api/v1/projects/{project['id']}/connectors/cd"
    refused = await client.put(put, json={"type": "demo", "config": {}})
    assert refused.status_code == 422, refused.text
    assert "refused in prod" in refused.json()["detail"]
