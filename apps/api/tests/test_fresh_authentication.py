"""Is a FRESH authentication really fresh?

`authenticated_at` used to read the session's `iat`: the time Choregos opened it. After a silent SSO
reconnection (the IdP still has a session and hands control back without asking anything), that
time is new although the user has not authenticated for hours. A door that demands a recent
authentication (approving a release, validating a risky action) was therefore satisfied by a plain
round trip to `/auth/login`.

Now the session carries `auth_time`, the authentication time the IdP writes in the ID token, and
`reauth=1` DEMANDS that it be recent: otherwise the IdP did not honour `prompt=login`.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import AsyncClient, Response

ISSUER = "https://idp.example.test/realms/x"


def _id_token(**claims: Any) -> str:
    """An ID token as the `token` endpoint returns it (the API does not read the signature)."""

    def b64(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    payload = {"sub": "u-1", "iss": ISSUER, "aud": "choregos", **claims}
    return f"{b64({'alg': 'RS256'})}.{b64(payload)}.c2lnbmF0dXJl"


@pytest.fixture
def idp(org: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import forget_discovery

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    forget_discovery()
    tokens: dict[str, Any] = {"access_token": "at-1"}
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        )
        mock.post(f"{ISSUER}/token").mock(side_effect=lambda _: Response(200, json=tokens))
        mock.get(f"{ISSUER}/userinfo").mock(
            return_value=Response(200, json={"sub": "u-1", "email": "jo@varga.dev", "name": "Jo"})
        )
        yield tokens
    reset_settings_cache()
    forget_discovery()


async def _sign_in(client: AsyncClient, *, reauth: bool) -> Response:
    start = await client.get("/api/v1/auth/login", params={"reauth": "1"} if reauth else {})
    parameters = parse_qs(urlparse(start.headers["location"]).query)
    assert ("prompt" in parameters) is reauth
    return await client.get("/api/v1/auth/callback", params={"code": "c", "state": parameters["state"][0]})


def _session(client: AsyncClient) -> dict[str, Any]:
    from choregos_api.security import read_session

    payload = read_session(client.cookies.get("choregos_session", ""))
    assert payload is not None, "no session opened"
    return payload


async def _authenticated_at(client: AsyncClient) -> int | None:
    """What a door sees: `Principal.authenticated_at`, resolved by the real dependency."""
    from choregos_api.config import get_settings
    from choregos_api.db.session import session_scope
    from choregos_api.deps import current_principal
    from starlette.requests import Request

    cookie = f"choregos_session={client.cookies.get('choregos_session')}"
    request = Request({"type": "http", "headers": [(b"cookie", cookie.encode())], "query_string": b""})
    async with session_scope() as session:
        principal = await current_principal(request, session, get_settings(), None)
    return principal.authenticated_at


async def test_an_sso_reconnection_keeps_the_idps_authentication_time(
    client: AsyncClient, idp: dict[str, Any]
) -> None:
    an_hour_ago = int(time.time()) - 3600
    idp["id_token"] = _id_token(auth_time=an_hour_ago)
    response = await _sign_in(client, reauth=False)
    assert response.status_code == 307, response.text
    session = _session(client)
    assert session["iat"] > an_hour_ago + 3000, "the session is new..."
    assert session["auth_time"] == an_hour_ago, "...the authentication is not"
    assert await _authenticated_at(client) == an_hour_ago


async def test_reauth_refusedes_an_idp_that_did_not_ask_again(
    client: AsyncClient, idp: dict[str, Any]
) -> None:
    idp["id_token"] = _id_token(auth_time=int(time.time()) - 3600)
    refused = await _sign_in(client, reauth=True)
    assert refused.status_code == 401, refused.text
    assert "prompt=login" in refused.text
    assert not client.cookies.get("choregos_session")


async def test_reauth_without_auth_time_is_refusedde(client: AsyncClient, idp: dict[str, Any]) -> None:
    refused = await _sign_in(client, reauth=True)
    assert refused.status_code == 401, refused.text


async def test_a_fresh_reauth_opens_a_session_that_says_so(client: AsyncClient, idp: dict[str, Any]) -> None:
    now = int(time.time())
    idp["id_token"] = _id_token(auth_time=now)
    response = await _sign_in(client, reauth=True)
    assert response.status_code == 307, response.text
    assert _session(client)["auth_time"] == now
    assert await _authenticated_at(client) == now


async def test_without_auth_time_the_time_stays_the_sessions(
    client: AsyncClient, idp: dict[str, Any]
) -> None:
    """An IdP that says nothing keeps the former behaviour: `iat`, for lack of better."""
    response = await _sign_in(client, reauth=False)
    assert response.status_code == 307, response.text
    session = _session(client)
    assert "auth_time" not in session
    assert await _authenticated_at(client) == session["iat"]
