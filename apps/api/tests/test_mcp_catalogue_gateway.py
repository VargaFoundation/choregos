# SPDX-License-Identifier: Apache-2.0
"""MCP catalogue entries install through the organisation's gateway (S46-10, ADR 0105).

Each entry says how this organisation can install it (`install_routes`). With a gateway, stdio and
OAuth entries install through it too, as an `mcp-gateway` connector that carries no credential."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.secret_store import FakeSecretStore, set_secret_store
from choregos_core import mcp_catalogue
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/mcp-catalogue"
GATEWAY = "/api/v1/orgs/varga/mcp-gateway"


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    from choregos_api.services import mcp_gateway as service

    mcp_catalogue.load_with_site.cache_clear()
    set_secret_store(FakeSecretStore())
    service.clear_token_cache()
    yield
    set_secret_store(None)
    service.clear_token_cache()
    mcp_catalogue.load_with_site.cache_clear()


async def _put_gateway(client: AsyncClient, mode: str) -> None:
    body = {
        "mode": mode,
        "endpoint": "https://gw.example.test/{server}/mcp",
        "routing": "path",
        "auth": {"type": "jwt_assertion"},
        "audience": "gw",
    }
    done = await client.put(GATEWAY, json=body)
    assert done.status_code == 200, done.text


async def _entries(client: AsyncClient) -> dict[str, dict[str, Any]]:
    listed = await client.get(BASE)
    assert listed.status_code == 200, listed.text
    return {e["id"]: e for e in listed.json()["entries"]}


def _first(entries: dict[str, dict[str, Any]], install: str, n: int = 1) -> list[str]:
    ids = [i for i, e in entries.items() if e["install"] == install]
    assert len(ids) >= n
    return ids[:n]


async def test_without_a_gateway_routes_are_today_s(client: AsyncClient, admin: str) -> None:
    entries = await _entries(client)
    for entry in entries.values():
        expected = ["direct"] if entry["install"] == "installable" else []
        assert entry["install_routes"] == expected
    hosted = entries[_first(entries, "needs_hosting")[0]]
    assert hosted["can"]["install"] is False
    assert "local process" in hosted["cannot_reason"]


async def test_with_a_gateway_every_entry_installs_through_it(client: AsyncClient, admin: str) -> None:
    before = await _entries(client)
    await _put_gateway(client, "available")
    entries = await _entries(client)
    for entry_id, entry in entries.items():
        assert entry["install"] == before[entry_id]["install"]
        if entry["install"] == "installable":
            assert entry["install_routes"] == ["direct", "gateway"]
        else:
            assert entry["install_routes"] == ["gateway"]
            assert entry["can"]["install"] is True
            assert entry["cannot_reason"] is None


async def test_a_gateway_only_entry_shows_the_gateway_procedure(client: AsyncClient, admin: str) -> None:
    await _put_gateway(client, "available")
    entries = await _entries(client)
    hosted = entries[_first(entries, "needs_hosting")[0]]
    assert hosted["procedure"] == "connector.mcp.mcp-gateway"
    direct = entries[_first(entries, "installable")[0]]
    assert direct["procedure"] != "connector.mcp.mcp-gateway"


async def test_required_mode_leaves_only_the_gateway(client: AsyncClient, admin: str) -> None:
    await _put_gateway(client, "required")
    entries = await _entries(client)
    assert entries
    assert all(e["install_routes"] == ["gateway"] for e in entries.values())


async def test_a_gateway_install_creates_an_mcp_gateway_connector_without_credential(
    client: AsyncClient, admin: str
) -> None:
    await _put_gateway(client, "available")
    entries = await _entries(client)
    version = mcp_catalogue.load().meta.catalogue_version
    first, second = _first(entries, "needs_hosting", 2)
    done = await client.post(f"{BASE}/{first}/install", json={})
    assert done.status_code == 201, done.text
    body = done.json()
    assert body["type"] == "mcp-gateway"
    assert body["config"] == {"server": first}
    assert not body.get("secret_refs")
    assert body["catalogue_entry"] == f"{first}@{version}"
    named = await client.post(
        f"{BASE}/{second}/install", json={"name": "gh-prod", "gateway_server": "gh-prod"}
    )
    assert named.status_code == 201, named.text
    assert named.json()["config"] == {"server": "gh-prod"}


async def test_a_gateway_install_with_a_secret_is_refused(client: AsyncClient, admin: str) -> None:
    await _put_gateway(client, "available")
    entries = await _entries(client)
    hosted = _first(entries, "needs_hosting")[0]
    refused = await client.post(
        f"{BASE}/{hosted}/install", json={"route": "gateway", "secret_values": {"token": "x"}}
    )
    assert refused.status_code == 422, refused.text
    assert (await client.get("/api/v1/orgs/varga/connectors")).json() == []


async def test_a_direct_install_in_required_mode_is_409(client: AsyncClient, admin: str) -> None:
    await _put_gateway(client, "required")
    entries = await _entries(client)
    entry = next(i for i in _first(entries, "installable", 5) if entries[i]["auth"] == "none")
    refused = await client.post(f"{BASE}/{entry}/install", json={"route": "direct"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "gateway_required"


async def test_a_gateway_install_without_gateway_is_409(client: AsyncClient, admin: str) -> None:
    entries = await _entries(client)
    hosted = _first(entries, "needs_hosting")[0]
    refused = await client.post(f"{BASE}/{hosted}/install", json={"route": "gateway"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "gateway_not_configured"
