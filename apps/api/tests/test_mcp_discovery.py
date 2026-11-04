"""The `mcp` type: a real MCP server, declared by the organisation (ADR 0034, S19-03).

Its operations are DISCOVERED, and a discovered tool is born closed: a server does not add a
capability behind the administrator's back. A schema that drifts closes the operation again. What
goes to the server is THE CONNECTOR's key, never a human's nor a run's.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

TOOLS: list[dict[str, Any]] = [
    {"name": "track_order", "description": "where the order stands", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "order_device", "description": "orders a PC",
     "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}}},
    {"name": "cancel_order", "inputSchema": {"type": "object"}},
]  # fmt: skip


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The supplier's agent, as a fake: it demands the connector's key."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(o) for o in TOOLS], token="supplier-key")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_KEY_SUPPLIER", "supplier-key")
    yield server


async def _declare(client: AsyncClient) -> None:
    body = {"name": "supplier", "type": "mcp", "config": {"url": "https://supplier.test/mcp"},
             "secret_refs": {"token": "env:CONNECTOR_TEST_KEY_SUPPLIER"}}  # fmt: skip
    created = await client.post("/api/v1/orgs/varga/connectors", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["operations"] == [], "an MCP server declares nothing: it is discovered"


async def _operations(client: AsyncClient) -> dict[str, dict[str, Any]]:
    instance = (await client.get("/api/v1/orgs/varga/connectors/supplier")).json()
    return {o["name"]: o for o in instance["operations"]}


async def test_a_discovered_tool_is_born_closed(
    client: AsyncClient, project: dict[str, Any], supplier: Any
) -> None:
    await _declare(client)
    diff = await client.post("/api/v1/orgs/varga/connectors/supplier/discover")
    assert diff.status_code == 200, diff.text
    assert sorted(diff.json()["added"]) == ["cancel_order", "order_device", "track_order"]
    operations = await _operations(client)
    assert {n: o["policy"] for n, o in operations.items()} == dict.fromkeys(operations, "forbidden")
    assert operations["track_order"]["access"] == "read"
    assert operations["order_device"]["access"] == "write", "without readOnlyHint: a write"
    assert operations["order_device"]["schema_digest"].startswith("sha256:")
    views = (await client.get(f"/api/v1/projects/{project['id']}/operations")).json()
    assert {o["effective_policy"] for o in views} == {"forbidden"}


async def test_a_drifting_schema_closes_the_operation(client: AsyncClient, admin: str, supplier: Any) -> None:
    await _declare(client)
    await client.post("/api/v1/orgs/varga/connectors/supplier/discover")
    opened = await client.patch(
        "/api/v1/orgs/varga/connectors/supplier/operations/order_device", json={"policy": "approval"}
    )
    assert opened.status_code == 200
    nothing = (await client.post("/api/v1/orgs/varga/connectors/supplier/discover")).json()
    assert nothing == {"added": [], "changed": [], "removed": [], "unchanged": 3}
    assert (await _operations(client))["order_device"]["policy"] == "approval", "nothing moved"

    # The server widens `order_device` (a quantity) and removes `cancel_order`.
    supplier.tools[1]["inputSchema"]["properties"]["quantity"] = {"type": "integer"}
    supplier.tools.pop(2)
    diff = (await client.post("/api/v1/orgs/varga/connectors/supplier/discover")).json()
    assert diff == {
        "added": [],
        "changed": ["order_device"],
        "removed": ["cancel_order"],
        "unchanged": 1,
    }
    operations = await _operations(client)
    assert operations["order_device"]["policy"] == "forbidden", "closed by the drift"
    assert "cancel_order" not in operations


async def test_only_the_connectors_key_goes_to_the_server(
    client: AsyncClient, admin: str, supplier: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _declare(client)
    assert (await client.post("/api/v1/orgs/varga/connectors/supplier/discover")).status_code == 200
    for method, headers, _ in supplier.received:
        assert headers["authorization"] == "Bearer supplier-key", method
        assert "cookie" not in headers and "chg_" not in str(headers), method

    monkeypatch.delenv("CONNECTOR_TEST_KEY_SUPPLIER")
    outage = await client.post("/api/v1/orgs/varga/connectors/supplier/discover")
    assert outage.status_code == 502 and "CONNECTOR_TEST_KEY_SUPPLIER" in outage.text
    assert (await client.get("/api/v1/orgs/varga/connectors/supplier")).json()["status"] == "error"


async def test_discovering_is_an_administrators_gesture(
    client: AsyncClient, admin: str, supplier: Any
) -> None:
    await _declare(client)
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert member.status_code in {200, 201}
    await login(client, "po@varga.dev")
    assert (await client.post("/api/v1/orgs/varga/connectors/supplier/discover")).status_code == 403
