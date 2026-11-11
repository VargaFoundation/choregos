"""What the assistant-installation tests share (S63-07): the plugin, connectors and installation helpers."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from ..agent_scopes_kit import add_member, as_user

ORG = "/api/v1/orgs/varga"
FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "plugins" / "investment-assistant"
PLUGIN = {"source": "offline", "name": "investment-assistant", "version": "0.1.0"}
OWNERS = "investment-owners"
NEWS = ("search_news", "get_article")
MARKET = ("get_quote", "get_history", "lookup_instrument")
BANK = ("list_accounts", "list_transactions")


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURE, root / "investment-assistant" / "0.1.0")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


async def _plugin(client: AsyncClient) -> None:
    await as_user(client, "admin@varga.dev")
    response = await client.post(f"{ORG}/plugins/install", json=PLUGIN)
    assert response.status_code == 201, response.text


async def _connector(
    name: str, operations: tuple[str, ...], *, access: str = "read", identity_modes: list[str] | None = None
) -> None:
    from choregos_api.db.models import ConnectorOperation, Organization, OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        config: dict[str, Any] = {"url": f"https://{name}.test/mcp"}
        if identity_modes is not None:
            config["identity_modes"] = identity_modes
        connector = OrgConnector(org_id=org_id, name=name, kind="mcp", type="mcp", config=config)
        session.add(connector)
        await session.flush()
        for operation in operations:
            session.add(
                ConnectorOperation(
                    org_id=org_id, connector_id=connector.id, name=operation, access=access, policy="allowed"
                )
            )


async def _install(client: AsyncClient, **extra: Any) -> Any:
    body = {
        "plugin": "investment-assistant",
        "assistant": "investment-assistant",
        "config": {"house_name": "Varga"},
    }
    return await client.post(f"{ORG}/assistants", json={**body, **extra})


async def _kinds(client: AsyncClient, slug: str = "investment-assistant") -> list[tuple[str, str | None]]:
    response = await client.get(f"{ORG}/assistants/{slug}/readiness")
    assert response.status_code == 200, response.text
    return [(i["kind"], i["need"] or i["key"]) for i in response.json()["items"]]


async def _bind(client: AsyncClient, need: str, connector: str, slug: str = "investment-assistant") -> Any:
    return await client.put(f"{ORG}/assistants/{slug}/bindings/{need}", json={"connector": connector})


async def _member(client: AsyncClient, email: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await add_member(client, email, "developer")
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups
