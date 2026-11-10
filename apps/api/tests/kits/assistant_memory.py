"""Helpers shared by the assistant memory tests (S63-09)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ..kits.assistant_install import (
    ORG,
    _bind,
    _install,
    _plugin,
)
from ..test_agent_sessions import Upstream, upstream  # noqa: F401

AUDIENCE = "investors"


ISIN = "US0378331005"


EXTRA = """  - { name: instrument_views, scope: user_subject, subject: { type: instrument, key: isin },
      kind: facts, writes: agent }
  - { name: house_view, scope: installation, kind: facts, writes: owner_only }
"""


@pytest.fixture
def markets(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    tools = [
        {
            "name": n,
            "description": n,
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object"},
        }
        for n in ("get_quote", "get_history", "lookup_instrument")
    ]
    server = FakeMcpServer(tools=tools, token="markets-key")
    server.structured["lookup_instrument"] = {"name": "Apple", "subject": {"type": "instrument", "key": ISIN}}
    server.structured["get_quote"] = {
        "relations": [{"subject": {"type": "instrument", "key": "FR0000120073"}}]
    }
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_MARKETS_KEY", "markets-key")
    yield server


async def _groups(email: str, groups: list[str]) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _assistant(client: AsyncClient, *, with_markets: bool = False) -> str:
    """The plugin installed, the assistant installed and (when asked) its resolver need bound to a connector
    served by the fake MCP server. Returns the installation id."""
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope

    await _agent(client)  # the agent of the conversations, unless the test made its own first
    await _plugin(client)
    assert (await _install(client, audience_groups=[AUDIENCE])).status_code == 201
    await _groups("admin@varga.dev", [AUDIENCE])  # `assistant:use` needs a group of the audience (S63-15)
    if with_markets:
        body = {"name": "markets", "type": "mcp", "config": {"url": "https://markets.test/mcp"},
                "secret_refs": {"token": "env:CONNECTOR_TEST_MARKETS_KEY"}}  # fmt: skip
        assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
        assert (await client.post(f"{ORG}/connectors/markets/discover")).status_code == 200
        for operation in ("get_quote", "get_history", "lookup_instrument"):
            opened = await client.patch(
                f"{ORG}/connectors/markets/operations/{operation}", json={"policy": "allowed"}
            )
            assert opened.status_code == 200, opened.text
        bound = await _bind(client, "market_data", "markets")
        assert bound.status_code == 200, bound.text
    async with session_scope(orgs="*") as session:
        return (await session.execute(select(AssistantInstallation.id))).scalar_one()


async def _open(installation_id: str, email: str = "admin@varga.dev", agent: str = "helper") -> str:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal
    from choregos_api.services.conversations import open_conversation

    async with session_scope(orgs="*") as session:
        organisation = (await session.execute(select(Organization))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        record = await open_conversation(
            session,
            Principal(user_id=user.id, email=email),
            organisation,
            agent,
            installation_id=installation_id,
        )
        return record.id


async def _tool(conversation: str, name: str, **arguments: Any) -> dict[str, Any]:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_memory import call_tool

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        answer = json.loads(await call_tool(session, record, name, arguments))
    assert isinstance(answer, dict)
    return answer


async def _agent(client: AsyncClient, **spec: Any) -> None:
    if (await client.get(f"{ORG}/agents/helper")).status_code == 200:
        return
    body = {"instructions": "Help.", "runtime": {"kind": "prompt"}, **spec}
    created = await client.post(f"{ORG}/agents", json={"slug": "helper", "display_name": "h", "spec": body})
    assert created.status_code == 201, created.text


SUBJECT = {"type": "instrument", "key": ISIN}


async def _stored(space: str) -> list[Any]:
    from choregos_api.db.models import MemoryFact, MemoryStore
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(MemoryFact)
            .join(MemoryStore, MemoryStore.id == MemoryFact.memory_store_id)
            .where(MemoryStore.slug == space)
            .order_by(MemoryFact.created_at, MemoryFact.id)
        )
        found = list(rows.scalars())
        for fact in found:
            session.expunge(fact)
        return found
