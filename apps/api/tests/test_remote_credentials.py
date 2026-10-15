"""Credentials for remote agents (ADR 0062): DCR, authorisation code with PKCE, sealed refresh tokens.

What these tests are about: the refresh token — the one long-lived secret — reaches the sealed store
and nothing else (no row, no response, no audit line), the PKCE verifier is what the provider checks,
and only an org_admin can do any of it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from choregos_adapters.fakes.a2a import FakeA2aAgent
from httpx import AsyncClient

from .conftest import login
from .conftest_remote import remote_agent, set_sealing_key


@pytest.fixture(autouse=True)
def _sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_sealing_key(monkeypatch)


@pytest.fixture
def remote() -> Iterator[FakeA2aAgent]:
    yield from remote_agent()


BASE = "/api/v1/orgs/varga/remote-credentials"


async def _create_oauth(client: AsyncClient, remote: FakeA2aAgent, name: str = "rovo-oauth") -> Any:
    return await client.post(
        BASE, json={"name": name, "type": "oauth2_authorization_code", "card_url": remote.card_url}
    )


async def _everything_stored() -> str:
    """Every row a secret could leak into, as text: the credential rows, the audit log, the events."""
    from choregos_api.db.models import AuditLog, RemoteCredential
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(RemoteCredential))).scalars().all()
        audit = (await session.execute(select(AuditLog))).scalars().all()
        return json.dumps(
            [[r.config, r.secret_ref, r.authorised_as, r.last_error] for r in rows]
            + [a.payload for a in audit],
            default=str,
        )


async def test_an_oauth_credential_registers_the_platform_at_the_cards_dcr_endpoint(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    created = await _create_oauth(client, remote)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending" and body["type"] == "oauth2_authorization_code"
    assert body["scopes"] == ["chat", "offline_access"]
    assert body["host"] == "agent.remote.test"
    registered = [payload for path, payload in remote.oauth_requests if path == "/dcr/register"]
    assert len(registered) == 1
    assert registered[0]["redirect_uris"] == [
        "http://localhost:8000/api/v1/remote-credentials/oauth/callback"
    ]
    assert "authorization_code" in registered[0]["grant_types"]
    assert (await client.get(BASE)).json()[0]["name"] == "rovo-oauth"


async def test_a_card_that_advertises_no_dcr_cannot_be_registered_at(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "bearer"  # a card with no OAuth flow and no DCR extension
    refused = await _create_oauth(client, remote)
    assert refused.status_code == 422 and "OAuth" in refused.text


async def test_authorising_sends_a_pkce_challenge_and_the_callback_proves_the_verifier(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    await _create_oauth(client, remote)
    started = await client.post(f"{BASE}/rovo-oauth/authorize")
    assert started.status_code == 200, started.text
    url = urlsplit(started.json()["redirect_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{remote.host}/authorize"
    assert query["client_id"] == "fake-client-id" and query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256" and query["scope"] == "chat offline_access"

    done = await client.get(
        "/api/v1/remote-credentials/oauth/callback", params={"code": "the-code", "state": query["state"]}
    )
    assert done.status_code == 303, done.text
    assert done.headers["location"].endswith("/admin/remote-agents?connected=rovo-oauth")
    exchange = next(p for path, p in remote.oauth_requests if path == "/token")
    assert exchange["grant_type"] == "authorization_code" and exchange["code"] == "the-code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest())
    assert challenge.decode().rstrip("=") == query["code_challenge"], "the provider can check the PKCE pair"

    listed = (await client.get(BASE)).json()[0]
    assert listed["status"] == "connected"
    assert listed["last_refresh_at"] is not None


async def test_the_refresh_token_is_sealed_and_in_no_row_no_response_no_audit_line(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.models import Organization, RemoteCredential
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from sqlalchemy import select

    await _create_oauth(client, remote)
    started = await client.post(f"{BASE}/rovo-oauth/authorize")
    state = parse_qs(urlsplit(started.json()["redirect_url"]).query)["state"][0]
    callback = await client.get(
        "/api/v1/remote-credentials/oauth/callback", params={"code": "c", "state": state}
    )
    assert callback.status_code == 303
    refresh = remote.live_refresh_token
    assert refresh, "the provider issued one"

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalars().one()
        row = (await session.execute(select(RemoteCredential))).scalars().one()
        assert row.secret_ref == "store:remote/rovo-oauth/refresh_token", "a reference, not a value"
        sealed = await get_secret_store().get_many(session, org.id, ["remote/rovo-oauth/refresh_token"])
        assert sealed["remote/rovo-oauth/refresh_token"] == refresh
    everywhere = await _everything_stored() + (await client.get(BASE)).text + callback.text + started.text
    assert refresh not in everywhere and "at-" not in everywhere


async def test_a_replayed_or_forged_state_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    await _create_oauth(client, remote)
    await client.post(f"{BASE}/rovo-oauth/authorize")
    forged = await client.get(
        "/api/v1/remote-credentials/oauth/callback", params={"code": "c", "state": "x.y"}
    )
    assert forged.status_code == 403
    assert not [p for path, p in remote.oauth_requests if path == "/token"], "no code was exchanged"


async def test_a_state_from_another_handshake_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    """A valid, signed state whose cookie belongs to a later handshake: the pair must match."""
    await _create_oauth(client, remote)
    first = await client.post(f"{BASE}/rovo-oauth/authorize")
    old_state = parse_qs(urlsplit(first.json()["redirect_url"]).query)["state"][0]
    await client.post(f"{BASE}/rovo-oauth/authorize")  # a second handshake replaces the cookie
    refused = await client.get(
        "/api/v1/remote-credentials/oauth/callback", params={"code": "c", "state": old_state}
    )
    assert refused.status_code == 403
    assert not [p for path, p in remote.oauth_requests if path == "/token"]


async def test_a_provider_that_gives_no_refresh_token_does_not_connect_the_credential(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    await _create_oauth(client, remote)
    started = await client.post(f"{BASE}/rovo-oauth/authorize")
    state = parse_qs(urlsplit(started.json()["redirect_url"]).query)["state"][0]
    original = remote._oauth

    def without_refresh(request: Any) -> Any:
        response = original(request)
        if request.url.path == "/token":
            import httpx

            body = json.loads(response.content)
            body.pop("refresh_token")
            return httpx.Response(200, json=body)
        return response

    remote._oauth = without_refresh  # type: ignore[method-assign]
    refused = await client.get(
        "/api/v1/remote-credentials/oauth/callback", params={"code": "c", "state": state}
    )
    assert refused.status_code == 502 and "offline_access" in refused.text
    assert (await client.get(BASE)).json()[0]["status"] == "pending"


async def test_a_bearer_credential_is_sealed_and_never_returned(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    token = "-".join(["bearer", "7d1f0c9a" * 3])
    created = await client.post(BASE, json={"name": "static", "type": "bearer", "token": token})
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "connected"
    assert token not in created.text + (await client.get(BASE)).text + await _everything_stored()
    assert (await client.post(BASE, json={"name": "nope", "type": "bearer"})).status_code == 422


async def test_revoking_deletes_the_sealed_values(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    from choregos_api.db.models import OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await client.post(BASE, json={"name": "static", "type": "bearer", "token": "a-token-value"})
    async with session_scope() as session:
        assert (await session.execute(select(OrgSecret))).scalars().all()
    assert (await client.delete(f"{BASE}/static")).status_code == 204
    async with session_scope() as session:
        assert not (await session.execute(select(OrgSecret))).scalars().all()
    assert (await client.get(BASE)).json()[0]["status"] == "revoked"


async def test_only_an_org_admin_manages_remote_credentials(
    client: AsyncClient, admin: str, remote: FakeA2aAgent, project: dict[str, Any]
) -> None:
    await _create_oauth(client, remote)
    await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    assert (await client.get(BASE)).status_code == 403
    assert (await _create_oauth(client, remote, "another")).status_code == 403
    assert (await client.post(f"{BASE}/rovo-oauth/authorize")).status_code == 403
    assert (await client.delete(f"{BASE}/rovo-oauth")).status_code == 403
