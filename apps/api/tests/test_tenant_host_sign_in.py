# SPDX-License-Identifier: Apache-2.0
"""A person signs in and uses the console on an organisation's verified host (ADR 0175, S74-02).

`acme` owns `agentic.acme.example` (verified) and has claimed `claimed.acme.example` without proving it;
`globex` owns `portal.globex.example`. The instance is `choregos.example.test`, its API
`api.choregos.example.test`. The host reaches the API as the console's relay sends it: `X-Forwarded-Host`.
What must hold: a verified host gets its own callback, cookie scope and origin; anything else, a spoofed
header included, gets exactly what it got before.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from choregos_api import tenant_host
from choregos_api.db.models import Membership, Organization, User
from choregos_api.db.session import session_scope
from choregos_api.security import read_session
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select

INSTANCE_IDP = "https://instance-idp.example.test/realms/choregos"
ACME_IDP = "https://login.acme.example/oauth2"
CONSOLE = "https://choregos.example.test"
API = "https://api.choregos.example.test"
ACME_HOST = "agentic.acme.example"
GLOBEX_HOST = "portal.globex.example"
CLAIMED_HOST = "claimed.acme.example"
SAML_START = "/api/v1/saml/globex/login"
INTERNAL = "/api/v1/internal/runs/r1/network"

DISCOVERY = {
    "issuer": INSTANCE_IDP,
    "authorization_endpoint": f"{INSTANCE_IDP}/authorize",
    "token_endpoint": f"{INSTANCE_IDP}/token",
    "userinfo_endpoint": f"{INSTANCE_IDP}/userinfo",
}
ACME_DISCOVERY = {
    "issuer": ACME_IDP,
    "authorization_endpoint": f"{ACME_IDP}/authorize",
    "token_endpoint": f"{ACME_IDP}/token",
    "userinfo_endpoint": f"{ACME_IDP}/userinfo",
}


class Claims:
    """The extension's table: which hosts are verified, and how often the core asked."""

    def __init__(self) -> None:
        self.verified: dict[str, Any] = {ACME_HOST: "acme", GLOBEX_HOST: "globex"}
        self.asked: list[str] = []

    def resolve(self, session: Any, host: str) -> Any:
        self.asked.append(host)
        return self.verified.get(host)


@pytest.fixture
def claims() -> Iterator[Claims]:
    from choregos_api import edition

    found = Claims()
    tenant_host.declare_host_resolver(found.resolve)
    try:
        yield found
    finally:
        edition.reset()


def _on(host: str, **more: str) -> dict[str, str]:
    """The headers of a request that came in under `host`, as the console's relay forwards it."""
    return {"x-forwarded-host": host, **more}


@pytest.fixture
async def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/hosts.db")
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", INSTANCE_IDP)
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", CONSOLE)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_GUARD", "enforce")
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_URL", "http://choregos-api:8000")
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.session import create_all
    from choregos_api.main import create_app
    from choregos_api.routers.auth import forget_discovery

    reset_settings_cache()
    forget_discovery()
    tenant_host.reset()
    await db_session.dispose_engine()
    application = create_app()
    await create_all()
    async with session_scope(orgs="*") as session:
        session.add_all([Organization(slug="acme", name="Acme"), Organization(slug="globex", name="Globex")])
    try:
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
            yield http
    finally:
        await db_session.dispose_engine()
        forget_discovery()
        tenant_host.reset()
        reset_settings_cache()
        os.environ.pop("CHOREGOS_DATABASE_URL", None)


def _query(response: Any) -> dict[str, list[str]]:
    return parse_qs(urlparse(response.headers["location"]).query)


def _discovery(mock: respx.MockRouter) -> None:
    mock.get(f"{INSTANCE_IDP}/.well-known/openid-configuration").mock(
        return_value=Response(200, json=DISCOVERY)
    )
    mock.get(f"{ACME_IDP}/.well-known/openid-configuration").mock(
        return_value=Response(200, json=ACME_DISCOVERY)
    )


def _set_cookies(response: Any) -> list[str]:
    return response.headers.get_list("set-cookie")


async def _sign_in(
    client: AsyncClient,
    *,
    login_on: str | None,
    callback_on: str | bool | None = True,
    groups: list[str],
    email: str = "bob@acme.example",
    redirect_to: str | None = None,
) -> tuple[Response, Response, respx.Route]:
    """A whole sign-in at the instance's provider: login on `login_on`, callback on `callback_on`
    (default: the same host). Returns the login, the callback and the token request's route."""
    callback_host = login_on if callback_on is True else callback_on
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        token = mock.post(f"{INSTANCE_IDP}/token").mock(
            return_value=Response(200, json={"access_token": "at"})
        )
        mock.get(f"{INSTANCE_IDP}/userinfo").mock(
            return_value=Response(
                200, json={"sub": f"sub-{email}", "email": email, "email_verified": True, "groups": groups}
            )
        )
        params = {"redirect_to": redirect_to} if redirect_to else {}
        start = await client.get(
            "/api/v1/auth/login", params=params, headers=_on(login_on) if login_on else {}
        )
        assert start.status_code == 307, start.text
        cookie = start.cookies.get("choregos_oidc") or ""
        done = await client.get(
            "/api/v1/auth/callback",
            params={"code": "c", "state": _query(start)["state"][0]},
            headers=_on(callback_host) if callback_host else {},
            cookies={"choregos_oidc": cookie},
        )
    return start, done, token


# ───────────────────────── the redirect URI and the handshake ─────────────────────────


async def test_a_verified_host_signs_in_with_its_own_callback(client: AsyncClient, claims: Claims) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        response = await client.get("/api/v1/auth/login", headers=_on(ACME_HOST))
    assert response.status_code == 307
    query = _query(response)
    assert query["redirect_uri"] == [f"https://{ACME_HOST}/api/v1/auth/callback"]
    state = read_session(
        query["state"][0], __import__("choregos_api.config").config.get_settings(), purpose="oidc-state"
    )
    assert state is not None
    assert state["h"] == ACME_HOST  # signed: the callback must come back to this host
    assert state["r"] == f"https://{ACME_HOST}/"  # no target: the host's root, not the instance's


async def test_the_instances_own_host_is_unchanged(client: AsyncClient, claims: Claims) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        response = await client.get("/api/v1/auth/login", headers=_on("choregos.example.test"))
    assert _query(response)["redirect_uri"] == [f"{API}/api/v1/auth/callback"]
    assert claims.asked == ["choregos.example.test"]  # asked, answered "not a tenant host"


@pytest.mark.parametrize(
    "spoof",
    [
        {"x-forwarded-host": CLAIMED_HOST},  # claimed, not proved
        {"x-forwarded-host": "unknown.example.org"},
        {"x-forwarded-host": "evil.example, " + ACME_HOST},  # the first entry is the client-facing one
        {"x-forwarded-host": "10.0.0.1"},
        {"x-forwarded-host": "localhost"},
        {"host": CLAIMED_HOST},
    ],
)
async def test_an_unverified_host_gets_no_callback_of_its_own(
    client: AsyncClient, claims: Claims, spoof: dict[str, str]
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        response = await client.get("/api/v1/auth/login", headers=spoof)
    assert _query(response)["redirect_uri"] == [f"{API}/api/v1/auth/callback"]
    state = read_session(
        _query(response)["state"][0],
        __import__("choregos_api.config").config.get_settings(),
        purpose="oidc-state",
    )
    assert state is not None and "h" not in state


async def test_the_handshake_cookie_is_host_only(client: AsyncClient, claims: Claims) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        response = await client.get("/api/v1/auth/login", headers=_on(ACME_HOST))
    (cookie,) = [c for c in _set_cookies(response) if c.startswith("choregos_oidc=")]
    assert "domain=" not in cookie.lower()
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()


async def test_the_host_implies_its_organisation_whatever_org_says(
    client: AsyncClient, claims: Claims
) -> None:
    from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn, declare_sign_in_resolver

    asked: list[Any] = []

    async def resolve(session: Any, hint: Any) -> Any:
        asked.append(hint.org)
        if hint.org == "acme":
            return TenantSignIn(
                org="acme", oidc=OidcProvider(issuer=ACME_IDP, client_id="acme-client", client_secret="s")
            )
        return None

    declare_sign_in_resolver(resolve)
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        response = await client.get("/api/v1/auth/login", params={"org": "globex"}, headers=_on(ACME_HOST))
    assert response.headers["location"].startswith(f"{ACME_IDP}/authorize?")
    assert _query(response)["client_id"] == ["acme-client"]
    assert _query(response)["redirect_uri"] == [f"https://{ACME_HOST}/api/v1/auth/callback"]
    assert asked[0] == "acme"


async def test_a_saml_tenant_signs_in_on_the_instances_host(client: AsyncClient, claims: Claims) -> None:
    from choregos_api.tenant_sign_in import TenantSignIn, declare_sign_in_resolver

    async def resolve(session: Any, hint: Any) -> Any:
        return TenantSignIn(org="globex", start_path=SAML_START) if hint.org == "globex" else None

    declare_sign_in_resolver(resolve)
    response = await client.get(
        "/api/v1/auth/login",
        params={"redirect_to": f"https://{GLOBEX_HOST}/p", "login_hint": "a@globex.example"},
        headers=_on(GLOBEX_HOST),
    )
    location = urlparse(response.headers["location"])
    # SAML's entity id and assertion consumer service stay the canonical ones: the sign-in runs there,
    # and a target on the tenant host (no cookie would be valid there) falls back to the instance's.
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{API}{SAML_START}"
    assert parse_qs(location.query)["redirect_to"] == [CONSOLE]


# ───────────────────────── the callback ─────────────────────────


async def test_a_member_signs_in_on_the_host_and_the_cookie_stays_on_it(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, token = await _sign_in(
        client, login_on=ACME_HOST, groups=["choregos:acme:developers"], redirect_to="/projects"
    )
    assert done.status_code == 307, done.text
    assert done.headers["location"] == "/projects"
    exchanged = token.calls.last.request.content.decode()
    assert f"redirect_uri=https%3A%2F%2F{ACME_HOST}%2Fapi%2Fv1%2Fauth%2Fcallback" in exchanged
    (session_cookie,) = [c for c in _set_cookies(done) if c.startswith("choregos_session=")]
    assert "domain=" not in session_cookie.lower()  # host-only: no sub-domain, no sibling gets it
    assert "httponly" in session_cookie.lower() and "samesite=lax" in session_cookie.lower()
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(User))).scalars().one().email == "bob@acme.example"


async def test_a_sign_in_without_target_lands_on_the_host_not_the_instance(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, _ = await _sign_in(client, login_on=ACME_HOST, groups=["choregos:acme:developers"])
    assert done.headers["location"] == f"https://{ACME_HOST}/"


async def test_a_member_of_another_organisation_only_is_refused_and_stored_nowhere(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, _ = await _sign_in(
        client, login_on=ACME_HOST, groups=["choregos:globex:developers"], email="eve@globex.example"
    )
    assert done.status_code == 403
    assert "not a member" in done.json()["detail"]
    assert not [c for c in _set_cookies(done) if c.startswith("choregos_session=")]
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(User))).scalars().all() == []
        assert (await session.execute(select(Membership))).scalars().all() == []


async def test_the_same_person_signs_in_on_the_instance_with_no_such_rule(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, token = await _sign_in(
        client, login_on=None, groups=["choregos:globex:developers"], email="eve@globex.example"
    )
    assert done.status_code == 307
    assert [c for c in _set_cookies(done) if c.startswith("choregos_session=")]
    assert (
        "redirect_uri=https%3A%2F%2Fapi.choregos.example.test%2Fapi"
        in token.calls.last.request.content.decode()
    )


async def test_a_member_of_several_organisations_lands_on_the_hosts(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, _ = await _sign_in(
        client, login_on=ACME_HOST, groups=["choregos:globex:developers", "choregos:acme:viewers"]
    )
    assert done.status_code == 307


@pytest.mark.parametrize(
    ("login_on", "callback_on"),
    [
        (ACME_HOST, None),  # begun on the host, the answer arrives on the instance
        (None, ACME_HOST),  # begun on the instance, the answer arrives on a host
        (ACME_HOST, GLOBEX_HOST),  # begun on one host, answered on another
        (ACME_HOST, CLAIMED_HOST),  # answered on a host that is only claimed
    ],
)
async def test_a_sign_in_must_come_back_to_the_host_it_began_on(
    client: AsyncClient, claims: Claims, login_on: str | None, callback_on: str | None
) -> None:
    _, done, token = await _sign_in(
        client, login_on=login_on, callback_on=callback_on, groups=["choregos:acme:developers"]
    )
    assert done.status_code == 401
    assert not [c for c in _set_cookies(done) if c.startswith("choregos_session=")]
    assert token.call_count == 0  # the code was never exchanged


async def test_a_claim_withdrawn_between_login_and_callback_refuses_the_sign_in(
    client: AsyncClient, claims: Claims
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _discovery(mock)
        start = await client.get("/api/v1/auth/login", headers=_on(ACME_HOST))
    del claims.verified[ACME_HOST]
    done = await client.get(
        "/api/v1/auth/callback",
        params={"code": "c", "state": _query(start)["state"][0]},
        headers=_on(ACME_HOST),
        cookies={"choregos_oidc": start.cookies.get("choregos_oidc") or ""},
    )
    assert done.status_code == 401


# ───────────────────────── open redirect ─────────────────────────


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/x",
        f"https://{ACME_HOST}.evil.example/",
        f"https://{ACME_HOST}@evil.example/",
        f"http://{ACME_HOST}/",  # not the scheme the instance serves
        f"https://{GLOBEX_HOST}/",  # another organisation's host
        f"https://{CLAIMED_HOST}/",  # a claimed one
        f"https://{ACME_HOST}:8443/",
        "//evil.example/x",
        "/\\evil.example",
        "javascript:alert(1)",
    ],
)
async def test_a_redirect_target_off_the_host_is_never_followed(
    client: AsyncClient, claims: Claims, target: str
) -> None:
    _, done, _ = await _sign_in(
        client, login_on=ACME_HOST, groups=["choregos:acme:developers"], redirect_to=target
    )
    assert done.status_code == 307
    assert done.headers["location"] == f"https://{ACME_HOST}/"


@pytest.mark.parametrize("target", [f"https://{ACME_HOST}/projects?x=1", f"https://{ACME_HOST}", "/a/b"])
async def test_a_target_on_the_host_or_relative_is_followed(
    client: AsyncClient, claims: Claims, target: str
) -> None:
    _, done, _ = await _sign_in(
        client, login_on=ACME_HOST, groups=["choregos:acme:developers"], redirect_to=target
    )
    assert done.headers["location"] == target


async def test_a_tenant_host_is_no_redirect_target_for_the_instances_sign_in(
    client: AsyncClient, claims: Claims
) -> None:
    _, done, _ = await _sign_in(
        client,
        login_on=None,
        groups=["choregos:acme:developers"],
        redirect_to=f"https://{ACME_HOST}/projects",
    )
    assert done.headers["location"] == CONSOLE  # the session cookie would not be valid there


# ───────────────────────── the origin guard ─────────────────────────


async def _post(client: AsyncClient, origin: str | None, **headers: str) -> int:
    all_headers = {**headers}
    if origin is not None:
        all_headers["origin"] = origin
    response = await client.post(
        "/api/v1/auth/logout", headers=all_headers, cookies={"choregos_session": "x"}
    )
    return response.status_code


async def test_a_verified_hosts_own_origin_may_change_things(client: AsyncClient, claims: Claims) -> None:
    assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 204
    assert await _post(client, f"https://{ACME_HOST}", host=ACME_HOST) == 204  # no relay: `Host` itself


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        (f"https://{CLAIMED_HOST}", CLAIMED_HOST),  # claimed, not proved: still refused
        ("https://evil.example", ACME_HOST),  # a foreign page
        (f"https://{GLOBEX_HOST}", ACME_HOST),  # another verified host: not this request's
        (f"https://{ACME_HOST}", "choregos.example.test"),  # the request was not made for that host
        (f"https://evil.{ACME_HOST}", ACME_HOST),  # a sub-domain of a verified host
        (f"http://{ACME_HOST}", ACME_HOST),  # wrong scheme
        (f"https://{ACME_HOST}:8443", ACME_HOST),  # a port
        ("null", ACME_HOST),
    ],
)
async def test_any_other_origin_is_refused(
    client: AsyncClient, claims: Claims, origin: str, host: str
) -> None:
    assert await _post(client, origin, **_on(host)) == 403


async def test_without_a_resolver_a_host_origin_is_refused(client: AsyncClient) -> None:
    assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 403


async def test_the_allowlist_follows_the_claims_within_the_cache_delay(
    client: AsyncClient, claims: Claims, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(tenant_host.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 204
    assert claims.asked.count(ACME_HOST) == 1  # cached: the resolver is not asked per request
    del claims.verified[ACME_HOST]
    assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 204  # still within the delay
    clock[0] += tenant_host.CACHE_TTL_S + 1
    assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 403  # withdrawn: refused


async def test_the_cache_is_bounded(
    client: AsyncClient, claims: Claims, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tenant_host, "CACHE_MAX_ENTRIES", 5)
    for index in range(40):
        await tenant_host.verified_host_cached(f"h{index}.example.org")
    assert len(tenant_host._CACHE) <= 5


async def test_a_failing_resolver_opens_nothing(client: AsyncClient) -> None:
    def broken(session: Any, host: str) -> Any:
        raise RuntimeError("storage down")

    tenant_host.declare_host_resolver(broken)
    assert await _post(client, f"https://{ACME_HOST}", **_on(ACME_HOST)) == 403


# ───────────────────────── the run's API ─────────────────────────


async def test_the_runs_api_does_not_answer_on_a_verified_host(client: AsyncClient, claims: Claims) -> None:
    # the instance's own hosts are public by configuration; a verified tenant host is public by proof
    for host in (ACME_HOST, GLOBEX_HOST):
        response = await client.get(INTERNAL, headers=_on(host))
        assert response.status_code == 404 and response.json() == {"detail": "Not Found"}, host


async def test_the_runs_api_still_answers_where_it_did(client: AsyncClient, claims: Claims) -> None:
    # unverified host, the Service's name: served as before (the route itself answers, here 401: no token)
    for host in (CLAIMED_HOST, "choregos-api:8000"):
        response = await client.get(INTERNAL, headers={"host": host})
        assert response.status_code != 404 or response.json() != {"detail": "Not Found"}, host


# ───────────────────────── the seam ─────────────────────────


async def test_a_header_that_is_no_dns_name_never_reaches_the_resolver(
    client: AsyncClient, claims: Claims
) -> None:
    for value in ("10.1.2.3", "localhost", "a..b.com", "x" * 300 + ".com"):
        await client.post(
            "/api/v1/auth/logout",
            headers={"origin": f"https://{ACME_HOST}", **_on(value)},
            cookies={"choregos_session": "x"},
        )
    assert claims.asked == []
