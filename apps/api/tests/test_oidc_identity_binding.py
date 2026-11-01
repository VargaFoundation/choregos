"""Console sign-in: mandatory discovery and identity bound to the provider's subject (S47-04, ADR 0100)."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from choregos_api.config import reset_settings_cache
from choregos_api.db.models import AuditLog, User
from choregos_api.db.session import session_scope
from choregos_api.routers.auth import oublier_la_decouverte
from httpx import AsyncClient, Response
from sqlalchemy import select

ISSUER = "https://idp.example.test/realms/x"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "userinfo_endpoint": f"{ISSUER}/userinfo",
}
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"


def _id_token(**claims: Any) -> str:
    def b64(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    body = {"sub": "u-1", "iss": ISSUER, "aud": "choregos"} | claims
    return f"{b64({'alg': 'RS256'})}.{b64(body)}.c2ln"


@pytest.fixture(autouse=True)
def _provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    oublier_la_decouverte()
    yield
    reset_settings_cache()
    oublier_la_decouverte()


async def _sign_in(
    client: AsyncClient,
    userinfo: dict[str, Any],
    *,
    token_claims: dict[str, Any] | None = None,
    id_token: bool = True,
) -> Response:
    """A whole sign-in: the login redirect, then the callback.

    The ID token carries the real nonce of the redirect and the userinfo's `sub`, so only what a test
    overrides in `token_claims` can make the callback refuse. `id_token=False`: the provider returns none.
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.get(DISCOVERY_URL).mock(return_value=Response(200, json=DISCOVERY))
        start = await client.get("/api/v1/auth/login")
        params = parse_qs(urlparse(start.headers["location"]).query)
        tokens: dict[str, Any] = {"access_token": "at"}
        if id_token:
            claims = {"sub": userinfo.get("sub"), "nonce": params["nonce"][0]} | (token_claims or {})
            tokens["id_token"] = _id_token(**claims)
        mock.post(f"{ISSUER}/token").mock(return_value=Response(200, json=tokens))
        mock.get(f"{ISSUER}/userinfo").mock(return_value=Response(200, json=userinfo))
        return await client.get("/api/v1/auth/callback", params={"code": "c", "state": params["state"][0]})


async def _users() -> list[User]:
    async with session_scope(orgs="*") as session:
        return list((await session.execute(select(User))).scalars())


async def test_a_failed_discovery_is_an_error_and_is_not_cached(client: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(DISCOVERY_URL).mock(side_effect=[Response(500), Response(200, json=DISCOVERY)])
        first = await client.get("/api/v1/auth/login")
        second = await client.get("/api/v1/auth/login")
    assert first.status_code == 503
    assert "CHOREGOS_OIDC_AUTHORIZATION_ENDPOINT" in first.text
    assert second.status_code == 307
    assert second.headers["location"].startswith(f"{ISSUER}/authorize?")


async def test_a_discovery_document_for_another_issuer_is_refused(client: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(DISCOVERY_URL).mock(
            return_value=Response(200, json=DISCOVERY | {"issuer": "https://other.example"})
        )
        response = await client.get("/api/v1/auth/login")
    assert response.status_code == 503


async def test_explicit_endpoints_skip_discovery(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_OIDC_AUTHORIZATION_ENDPOINT", "https://sso.example/auth")
    monkeypatch.setenv("CHOREGOS_OIDC_TOKEN_ENDPOINT", "https://sso.example/token")
    monkeypatch.setenv("CHOREGOS_OIDC_USERINFO_ENDPOINT", "https://sso.example/me")
    reset_settings_cache()
    with respx.mock(assert_all_called=False):
        response = await client.get("/api/v1/auth/login")
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://sso.example/auth?")


def test_explicit_endpoints_are_all_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import Settings

    with pytest.raises(ValueError, match="CHOREGOS_OIDC_TOKEN_ENDPOINT"):
        Settings(oidc_authorization_endpoint="https://sso.example/auth")


async def test_an_unverified_email_does_not_take_over_an_account(client: AsyncClient, org: str) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="victim@varga.dev", display_name="Victim", oidc_sub=None))
    response = await _sign_in(
        client, {"sub": "attacker-sub", "email": "victim@varga.dev", "email_verified": False}
    )
    assert response.status_code == 401, response.text
    assert "not verified" in response.text
    (victim,) = [u for u in await _users() if u.email == "victim@varga.dev"]
    assert victim.oidc_sub is None


async def test_an_email_bound_to_another_subject_is_refused(client: AsyncClient, org: str) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="victim@varga.dev", display_name="Victim", oidc_sub="victim-sub"))
    response = await _sign_in(
        client, {"sub": "attacker-sub", "email": "victim@varga.dev", "email_verified": True}
    )
    assert response.status_code == 401, response.text
    assert "belongs to another identity" in response.text
    (victim,) = await _users()
    assert victim.oidc_sub == "victim-sub"


async def test_the_id_token_and_userinfo_must_name_the_same_subject(client: AsyncClient, org: str) -> None:
    response = await _sign_in(client, {"sub": "u-1", "email": "jo@varga.dev"}, token_claims={"sub": "x"})
    assert response.status_code == 401, response.text
    assert "two different subjects" in response.text


async def test_the_nonce_is_sent_and_a_wrong_one_is_refused(client: AsyncClient, org: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(DISCOVERY_URL).mock(return_value=Response(200, json=DISCOVERY))
        start = await client.get("/api/v1/auth/login")
    assert parse_qs(urlparse(start.headers["location"]).query)["nonce"][0]
    response = await _sign_in(
        client, {"sub": "u-1", "email": "jo@varga.dev"}, token_claims={"nonce": "another-nonce"}
    )
    assert response.status_code == 401, response.text
    assert "nonce" in response.text


@pytest.mark.parametrize(
    ("claims", "detail"),
    [
        ({"aud": "someone-else"}, "not issued for this client"),
        ({"aud": ["a", "b"]}, "not issued for this client"),
        ({"aud": 7}, "not issued for this client"),
        ({"iss": "https://other.example"}, "another identity provider"),
    ],
)
async def test_an_id_token_for_another_audience_or_issuer_is_refused(
    client: AsyncClient, org: str, claims: dict[str, Any], detail: str
) -> None:
    response = await _sign_in(client, {"sub": "u-1", "email": "jo@varga.dev"}, token_claims=claims)
    assert response.status_code == 401, response.text
    assert detail in response.text


async def test_an_audience_list_naming_the_client_is_accepted(client: AsyncClient, org: str) -> None:
    response = await _sign_in(
        client, {"sub": "u-1", "email": "jo@varga.dev"}, token_claims={"aud": ["x", "choregos"]}
    )
    assert response.status_code == 307, response.text


async def test_a_returning_subject_keeps_its_account_after_an_email_change(
    client: AsyncClient, org: str
) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="old@varga.dev", display_name="Jo", oidc_sub="u-1"))
    response = await _sign_in(client, {"sub": "u-1", "email": "new@varga.dev", "name": "Jo"})
    assert response.status_code == 307, response.text
    (user,) = await _users()
    assert (user.oidc_sub, user.email) == ("u-1", "new@varga.dev")


async def test_a_returning_subject_does_not_adopt_an_unverified_email(client: AsyncClient, org: str) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="old@varga.dev", display_name="Jo", oidc_sub="u-1"))
    response = await _sign_in(client, {"sub": "u-1", "email": "ceo@varga.dev", "email_verified": False})
    assert response.status_code == 307, response.text
    (user,) = await _users()
    assert user.email == "old@varga.dev"


async def test_a_new_account_is_not_created_for_an_unverified_email(client: AsyncClient, org: str) -> None:
    response = await _sign_in(client, {"sub": "u-9", "email": "new@varga.dev", "email_verified": False})
    assert response.status_code == 401, response.text
    assert "not verified" in response.text
    assert await _users() == []


async def test_a_verified_email_links_an_unbound_account(client: AsyncClient, org: str) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="jo@varga.dev", display_name="Jo", oidc_sub=None))
    response = await _sign_in(client, {"sub": "u-1", "email": "jo@varga.dev", "email_verified": True})
    assert response.status_code == 307, response.text
    (user,) = await _users()
    assert user.oidc_sub == "u-1"


@pytest.mark.parametrize("sub", ["dev|jo@varga.dev", "tenant:some-org:u-1"])
async def test_the_instance_provider_cannot_issue_a_reserved_subject(
    client: AsyncClient, org: str, sub: str
) -> None:
    response = await _sign_in(client, {"sub": sub, "email": "jo@varga.dev"})
    assert response.status_code == 401, response.text
    assert "reserved" in response.text


async def test_an_idp_migration_relinks_a_verified_email_only_when_switched_on(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_scope(orgs="*") as session:
        session.add(User(email="jo@varga.dev", display_name="Jo", oidc_sub="old-idp-sub"))
    verified = {"sub": "new-idp-sub", "email": "jo@varga.dev", "email_verified": True}
    refused = await _sign_in(client, verified)
    assert refused.status_code == 401, "the default must refuse a re-link"
    monkeypatch.setenv("CHOREGOS_OIDC_RELINK_VERIFIED_EMAIL", "true")
    reset_settings_cache()
    # The switch is no way round `email_verified`: a provider that omits the claim is still refused.
    omitted = await _sign_in(client, {"sub": "new-idp-sub", "email": "jo@varga.dev"})
    assert omitted.status_code == 401
    relinked = await _sign_in(client, verified)
    assert relinked.status_code == 307, relinked.text
    (user,) = await _users()
    assert user.oidc_sub == "new-idp-sub"
    async with session_scope(orgs="*") as session:
        actions = [row.action for row in (await session.execute(select(AuditLog))).scalars()]
    assert "user.relink" in actions


async def test_an_unreachable_discovery_url_is_a_503_not_a_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", "https://idp.example:not-a-port/realm")
    reset_settings_cache()
    response = await client.get("/api/v1/auth/login")
    assert response.status_code == 503, response.text
