"""Helpers shared by the user identity exchange tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from choregos_adapters.fakes.identity_provider import FakeIdentityProvider
from httpx import AsyncClient

from ..test_agent_sessions import (  # noqa: F401 - fixtures imported for their use here
    ORG,
    Upstream,
    _agent,
    _ask,
    upstream,
)
from ..test_broker import TOOLS

AUDIENCE = "supplier-mcp"


EMAIL = "admin@varga.dev"


SUBJECT = f"dev|{EMAIL}"


REFRESH = "refresh-of-the-person"


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The source: it accepts any bearer and records them, so a test reads WHICH one arrived."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(o) for o in TOOLS], token="")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_SUPPLIER_KEY", "supplier-key")
    yield server


@pytest.fixture
async def idp() -> AsyncIterator[FakeIdentityProvider]:
    from choregos_api.services.identity_exchange import set_exchange_transport

    provider = FakeIdentityProvider(refresh_tokens={REFRESH: SUBJECT})
    set_exchange_transport(provider.transport())
    yield provider
    set_exchange_transport(None)


async def _person() -> tuple[str, str]:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import ALL_ORGS, session_scope
    from sqlalchemy import select

    async with session_scope(orgs=ALL_ORGS) as session:
        user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        return user.id, org.id


async def _sign_in_sealed(refresh: str = REFRESH) -> None:
    """What the console's sign-in does with the provider's refresh token."""
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.services.identity_exchange import seal_refresh_token

    user_id, org_id = await _person()
    async with session_scope(orgs=ALL_ORGS) as session:
        await seal_refresh_token(session, [org_id], user_id, refresh)


async def _declare(client: AsyncClient, *, modes: list[str] | None = None, audience: bool = True) -> None:
    config: dict[str, Any] = {
        "url": "https://fournisseur.test/mcp",
        "identity_modes": modes or ["service", "user_exchange"],
    }
    if audience:
        config["token_exchange"] = {"audience": AUDIENCE}
    body = {"name": "fournisseur", "type": "mcp", "config": config,
            "secret_refs": {"token": "env:CONNECTOR_TEST_SUPPLIER_KEY"}}  # fmt: skip
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text
    assert (await client.post(f"{ORG}/connectors/fournisseur/discover")).status_code == 200
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/suivi_commande", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text


def _calls(source: Any) -> list[dict[str, str]]:
    """The headers of every `tools/call` the source received."""
    return [headers for method, headers, _ in source.received if method == "tools/call"]


async def _tool_events(session_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts.enums import EventType
    from sqlalchemy import select

    async with session_scope() as db:
        query = select(Event).where(Event.type == EventType.TOOL_CALLED.value, Event.subject == session_id)
        return [dict(e.payload) for e in (await db.execute(query)).scalars()]
