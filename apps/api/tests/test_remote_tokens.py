"""The access token a remote agent's run presents (ADR 0062): exchanged, rotated, never kept."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from choregos_adapters.fakes.a2a import FakeA2aAgent
from httpx import AsyncClient

from .conftest_remote import remote_agent, set_sealing_key


@pytest.fixture(autouse=True)
def _sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_sealing_key(monkeypatch)


@pytest.fixture
def remote() -> Iterator[FakeA2aAgent]:
    yield from remote_agent()


BASE = "/api/v1/orgs/varga/remote-credentials"


async def _connected(client: AsyncClient, remote: FakeA2aAgent) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await client.post(
        BASE, json={"name": "rovo", "type": "oauth2_authorization_code", "card_url": remote.card_url}
    )
    started = await client.post(f"{BASE}/rovo/authorize")
    state = parse_qs(urlsplit(started.json()["redirect_url"]).query)["state"][0]
    await client.get("/api/v1/remote-credentials/oauth/callback", params={"code": "c", "state": state})
    async with session_scope() as session:
        return (await session.execute(select(Organization.id))).scalar_one()


async def test_each_refresh_rewrites_the_rotated_refresh_token(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services.remote_tokens import access_token_for

    org_id = await _connected(client, remote)
    first_refresh = remote.live_refresh_token
    async with session_scope() as session:
        one = await access_token_for(session, org_id, "rovo")
    async with session_scope() as session:
        two = await access_token_for(session, org_id, "rovo")  # would fail if the old token were presented
    assert one.token != two.token and one.token.startswith("at-")
    assert remote.live_refresh_token not in {None, first_refresh}
    async with session_scope() as session:
        stored = (await get_secret_store().get_many(session, org_id, ["remote/rovo/refresh_token"]))[
            "remote/rovo/refresh_token"
        ]
    assert stored == remote.live_refresh_token, "the store holds the newest refresh token"


async def test_a_provider_that_refuses_the_refresh_says_reconnect(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_tokens import RemoteCredentialError, access_token_for

    org_id = await _connected(client, remote)
    remote.live_refresh_token = "revoked-by-the-user"
    async with session_scope() as session:
        with pytest.raises(RemoteCredentialError, match="reconnect"):
            await access_token_for(session, org_id, "rovo")


async def test_a_credential_nobody_authorised_gives_no_token(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_tokens import RemoteCredentialError, access_token_for
    from sqlalchemy import select

    await client.post(
        BASE, json={"name": "rovo", "type": "oauth2_authorization_code", "card_url": remote.card_url}
    )
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        with pytest.raises(RemoteCredentialError, match="never authorised"):
            await access_token_for(session, org_id, "rovo")


async def test_a_bearer_and_a_none_credential(client: AsyncClient, admin: str, remote: FakeA2aAgent) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_tokens import access_token_for
    from sqlalchemy import select

    await client.post(BASE, json={"name": "static", "type": "bearer", "token": "the-static-token"})
    await client.post(BASE, json={"name": "open", "type": "none"})
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        assert (await access_token_for(session, org_id, "static")).token == "the-static-token"
        assert (await access_token_for(session, org_id, "open")).token == ""


async def test_an_api_key_credential_gives_its_key_as_it_was_sealed(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_tokens import access_token_for
    from sqlalchemy import select

    created = await client.post(
        BASE, json={"name": "servicenow", "type": "api_key", "token": "the-api-key", "header": "x-sn-apikey"}
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        assert (await access_token_for(session, org_id, "servicenow")).token == "the-api-key"
    assert remote.oauth_requests == [], "a static key is exchanged with nobody"
