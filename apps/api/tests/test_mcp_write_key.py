"""An extension's tool is a write tool when it says `write`, or `ecriture` as extensions declared it
before S45-03; the daily write budget counts the audit rows under both keys (ADR 0098 §4).

The MCP door of external clients (ADR 0030) only announces a write tool to an `mcp:write` token:
a tool an extension marks with the English key must not slip through to an `mcp:read` one.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

ADMIN = "admin@varga.dev"
DOOR = "/mcp/projects/varga:billing-api"


async def _token(client: AsyncClient, scope: str) -> str:
    created = await client.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": [scope]})
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


async def _tools(client: AsyncClient, token: str) -> dict[str, Any]:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = await client.post(DOOR, json=message, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return {tool["name"]: tool for tool in response.json()["result"]["tools"]}


async def test_extension_tool_marked_write_or_ecriture_is_a_write_tool(  # lang: fr-ok - names the key
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import greffons

    async def list_tools(_session: Any, _principal: Any, _project: Any, _org: Any) -> list[dict[str, Any]]:
        return [
            {"name": "ext_read", "description": "Reads."},
            {"name": "ext_write", "description": "Writes.", "write": True},
            {"name": "ext_former_write", "description": "Writes.", "ecriture": True},  # lang: fr-ok
        ]

    async def call_tool(*_arguments: Any) -> tuple[int, dict[str, Any]]:
        return 200, {}

    # The extension registry of this test only: nothing leaks into the next one.
    monkeypatch.setattr(greffons, "_FOURNISSEURS_HUMAINS", {})
    greffons.declarer_des_outils_pour_les_humains("test-extension", list_tools, call_tool)
    read_token, write_token = await _token(client, "mcp:read"), await _token(client, "mcp:write")
    client.cookies.clear()

    read = await _tools(client, read_token)
    assert "ext_read" in read, "the extension's tools are announced on the project's door"
    assert not {"ext_write", "ext_former_write"} & set(read), "a write tool is hidden from mcp:read"

    write = await _tools(client, write_token)
    hints = {
        name: write[name]["annotations"]["readOnlyHint"]
        for name in ("ext_read", "ext_write", "ext_former_write")
    }
    assert hints == {"ext_read": True, "ext_write": False, "ext_former_write": False}


async def test_writes_today_counts_write_and_ecriture(  # lang: fr-ok - names the persisted audit key
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.db.models import AuditLog, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.garde_fous import ecritures_du_jour
    from choregos_core import utcnow
    from sqlalchemy import select

    payloads: list[dict[str, Any] | None] = [
        {"write": True},
        {"ecriture": True},  # lang: fr-ok - the audit key persisted before S45-39
        {"write": False},
        {"ecriture": False},  # lang: fr-ok
        {},
        None,
    ]
    async with session_scope() as session:
        user_id = (await session.execute(select(User.id).where(User.email == ADMIN))).scalar_one()
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        for payload in payloads:
            session.add(
                AuditLog(
                    actor_id=user_id,
                    org_id=org_id,
                    action="mcp.call",
                    target_type="mcp_tool",
                    target_id="create_work_item",
                    payload=payload,
                    ts=utcnow(),
                )
            )
    async with session_scope() as session:
        assert await ecritures_du_jour(session, user_id) == 2
