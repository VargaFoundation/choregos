# SPDX-License-Identifier: Apache-2.0
"""Each organisation signs in with its own identity provider (ADR 0107).

Two tenants: `acme` brings an OIDC provider, `globex` a SAML one served by an extension's route.
The resolver is the one an extension would declare; the core must confine whatever it vouches for.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

INSTANCE = "https://instance-idp.example.test/realms/choregos"
ACME = "https://login.acme.example/oauth2"
SAML_START = "/api/v1/saml/globex/login"

DISCOVERY = {
    "issuer": INSTANCE,
    "authorization_endpoint": f"{INSTANCE}/authorize",
    "token_endpoint": f"{INSTANCE}/token",
    "userinfo_endpoint": f"{INSTANCE}/userinfo",
}
ACME_DISCOVERY = {
    "issuer": ACME,
    "authorization_endpoint": f"{ACME}/authorize",
    "token_endpoint": f"{ACME}/token",
    "userinfo_endpoint": f"{ACME}/userinfo",
}


class Directory:
    """The extension's storage, as a test sets it."""

    def __init__(self) -> None:
        from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn

        self.tenants: dict[str, TenantSignIn] = {
            "acme": TenantSignIn(
                org="acme",
                oidc=OidcProvider(issuer=ACME, client_id="acme-client", client_secret="acme-secret"),
            ),
            "globex": TenantSignIn(org="globex", start_path=SAML_START),
        }
        self.domains = {"acme.example": "acme", "globex.example": "globex"}
        self.hints: list[Any] = []

    async def resolve(self, session: Any, hint: Any) -> Any:
        self.hints.append(hint)
        org = hint.org or self.domains.get(hint.email_domain or "")
        return self.tenants.get(org or "")


@pytest.fixture
def directory() -> Iterator[Directory]:
    from choregos_api.edition import reinitialiser
    from choregos_api.tenant_sign_in import declare_sign_in_resolver

    found = Directory()
    declare_sign_in_resolver(found.resolve)
    try:
        yield found
    finally:
        reinitialiser()


@pytest.fixture
async def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/tenants.db")
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", INSTANCE)
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.main import create_app
    from choregos_api.routers.auth import oublier_la_decouverte

    reset_settings_cache()
    oublier_la_decouverte()
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
        oublier_la_decouverte()
        reset_settings_cache()
        os.environ.pop("CHOREGOS_DATABASE_URL", None)


def _query(response: Any) -> dict[str, list[str]]:
    return parse_qs(urlparse(response.headers["location"]).query)


def _mock_discovery(mock: respx.MockRouter) -> None:
    mock.get(f"{INSTANCE}/.well-known/openid-configuration").mock(return_value=Response(200, json=DISCOVERY))
    mock.get(f"{ACME}/.well-known/openid-configuration").mock(return_value=Response(200, json=ACME_DISCOVERY))


async def _acme_sign_in(client: AsyncClient, userinfo: dict[str, Any]) -> tuple[Response, respx.Route]:
    """A whole sign-in through Acme's provider, which answers `userinfo`."""
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        token = mock.post(f"{ACME}/token").mock(
            return_value=Response(200, json={"access_token": "acme-access"})
        )
        mock.get(f"{ACME}/userinfo").mock(return_value=Response(200, json=userinfo))
        start = await client.get("/api/v1/auth/login", params={"login_hint": "bob@acme.example"})
        state = _query(start)["state"][0]
        done = await client.get("/api/v1/auth/callback", params={"code": "the-code", "state": state})
    return done, token


async def _memberships(client: AsyncClient) -> set[tuple[str, str]]:
    me = await client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    return {(m["org"], m["role"]) for m in me.json()["memberships"]}


# ───────────────────────── which provider is asked ─────────────────────────


async def test_the_address_domain_picks_the_tenant_provider(
    client: AsyncClient, directory: Directory
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        response = await client.get("/api/v1/auth/login", params={"login_hint": "Bob@ACME.example"})
    assert response.status_code == 307
    assert response.headers["location"].startswith(f"{ACME}/authorize?")
    query = _query(response)
    assert query["client_id"] == ["acme-client"]
    assert query["login_hint"] == ["Bob@ACME.example"]
    assert directory.hints[-1].email_domain == "acme.example"


async def test_a_sign_in_link_per_organisation_picks_its_provider(
    client: AsyncClient, directory: Directory
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        response = await client.get("/api/v1/auth/login", params={"org": "acme"})
    assert response.headers["location"].startswith(f"{ACME}/authorize?")


async def test_another_protocol_starts_at_the_extension_route(
    client: AsyncClient, directory: Directory
) -> None:
    response = await client.get(
        "/api/v1/auth/login",
        params={"org": "globex", "redirect_to": "/p/globex", "login_hint": "a@globex.example"},
    )
    assert response.status_code == 307
    location = urlparse(response.headers["location"])
    assert location.path == SAML_START
    assert parse_qs(location.query) == {"redirect_to": ["/p/globex"], "login_hint": ["a@globex.example"]}


async def test_an_address_no_tenant_owns_goes_to_the_instance_provider(
    client: AsyncClient, directory: Directory
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        response = await client.get("/api/v1/auth/login", params={"login_hint": "carol@elsewhere.example"})
    assert response.headers["location"].startswith(f"{INSTANCE}/authorize?")
    assert _query(response)["client_id"] == ["choregos"]


async def test_without_a_resolver_the_instance_provider_answers(client: AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        response = await client.get("/api/v1/auth/login", params={"org": "acme"})
    assert response.headers["location"].startswith(f"{INSTANCE}/authorize?")


async def test_a_resolver_answering_another_organisation_is_refused(
    client: AsyncClient, directory: Directory
) -> None:
    directory.tenants["globex"] = directory.tenants["acme"]
    response = await client.get("/api/v1/auth/login", params={"org": "globex"})
    assert response.status_code == 401


async def test_a_tenant_provider_without_discovery_fails_instead_of_being_guessed(
    client: AsyncClient, directory: Directory
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ACME}/.well-known/openid-configuration").mock(return_value=Response(404))
        response = await client.get("/api/v1/auth/login", params={"org": "acme"})
    assert response.status_code == 502
    assert "protocol/openid-connect" not in response.text


async def test_explicit_endpoints_need_no_discovery(client: AsyncClient, directory: Directory) -> None:
    from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn

    directory.tenants["acme"] = TenantSignIn(
        org="acme",
        oidc=OidcProvider(
            issuer=ACME,
            client_id="acme-client",
            authorization_endpoint="https://sso.acme.example/auth",
            token_endpoint="https://sso.acme.example/token",
            userinfo_endpoint="https://sso.acme.example/me",
        ),
    )
    with respx.mock(assert_all_called=True):  # no request at all is expected
        response = await client.get("/api/v1/auth/login", params={"org": "acme"})
    assert response.headers["location"].startswith("https://sso.acme.example/auth?")


# ───────────────────────── what a tenant provider may grant ─────────────────────────


async def test_a_tenant_sign_in_grants_roles_in_its_tenant_only(
    client: AsyncClient, directory: Directory
) -> None:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    done, token = await _acme_sign_in(
        client,
        {
            "sub": "u-1",
            "email": "bob@acme.example",
            "name": "Bob",
            "groups": ["org-admins", "choregos:globex:org-admins"],
        },
    )
    assert done.status_code == 307, done.text
    # The code is exchanged with Acme's client, not the instance's.
    sent = parse_qs(token.calls.last.request.content.decode())
    assert sent["client_id"] == ["acme-client"] and sent["client_secret"] == ["acme-secret"]
    assert await _memberships(client) == {("acme", "org_admin")}
    async with session_scope(orgs="*") as session:
        acme = (await session.execute(select(Organization).where(Organization.slug == "acme"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == "bob@acme.example"))).scalar_one()
    assert user.oidc_sub == f"tenant:{acme.id}:u-1"


async def test_a_tenant_provider_cannot_sign_in_another_tenant_address(
    client: AsyncClient, directory: Directory
) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    done, _ = await _acme_sign_in(
        client, {"sub": "u-2", "email": "eve@globex.example", "groups": ["org-admins"]}
    )
    assert done.status_code == 401
    assert "does not belong to organisation 'acme'" in done.text
    assert client.cookies.get("choregos_session") is None
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(User).where(User.email == "eve@globex.example"))).first() is None


async def test_a_tenant_provider_cannot_sign_in_an_address_no_tenant_owns(
    client: AsyncClient, directory: Directory
) -> None:
    done, _ = await _acme_sign_in(client, {"sub": "u-3", "email": "mallory@gmail.example"})
    assert done.status_code == 401


async def test_an_unverified_address_is_refused(client: AsyncClient, directory: Directory) -> None:
    done, _ = await _acme_sign_in(
        client, {"sub": "u-4", "email": "bob@acme.example", "email_verified": False}
    )
    assert done.status_code == 401
    assert "not verified" in done.text


async def test_a_tenant_that_lost_its_provider_mid_sign_in_is_refused(
    client: AsyncClient, directory: Directory
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        _mock_discovery(mock)
        start = await client.get("/api/v1/auth/login", params={"org": "acme"})
        del directory.tenants["acme"]
        done = await client.get(
            "/api/v1/auth/callback", params={"code": "c", "state": _query(start)["state"][0]}
        )
    assert done.status_code == 401
    assert "no longer signs in with its own OIDC provider" in done.text


# ───────────────────────── another protocol, through the public finish ─────────────────────────


async def _finish(org: str | None, email: str, sub: str, groups: list[str]) -> int:
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.routers.auth import terminer_la_connexion

    try:
        async with session_scope(orgs="*") as session:
            response = await terminer_la_connexion(
                session, email=email, nom="", sujet=sub, groupes=groups, cible="/", canal="saml", org=org
            )
    except ApiError as refused:
        return refused.status_code
    return response.status_code


async def _roles_of(email: str) -> set[tuple[str, str]]:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(Organization.slug, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .join(User, User.id == Membership.user_id)
            .where(User.email == email)
        )
        return {(slug, role) for slug, role in rows.all()}


async def test_a_saml_sign_in_is_confined_like_an_oidc_one(client: AsyncClient, directory: Directory) -> None:
    status = await _finish(
        "globex", "ann@globex.example", "saml-1", ["developers", "choregos:acme:org-admins"]
    )
    assert status == 307
    assert await _roles_of("ann@globex.example") == {("globex", "developer")}

    assert await _finish("globex", "bob@acme.example", "saml-2", ["org-admins"]) == 401
    assert await _roles_of("bob@acme.example") == set()


async def test_two_tenants_may_issue_the_same_subject(client: AsyncClient, directory: Directory) -> None:
    assert await _finish("globex", "ann@globex.example", "same", ["developers"]) == 307
    assert await _finish("acme", "bob@acme.example", "same", ["developers"]) == 307


async def test_without_an_organisation_the_finish_is_unchanged(client: AsyncClient) -> None:
    assert await _finish(None, "ops@instance.example", "s", ["choregos:acme:viewers"]) == 307
    assert await _roles_of("ops@instance.example") == {("acme", "viewer")}


# ───────────────────────── the seam itself ─────────────────────────


def test_a_tenant_sign_in_has_exactly_one_way_in() -> None:
    from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn

    provider = OidcProvider(issuer=ACME, client_id="c")
    with pytest.raises(ValueError, match="not both"):
        TenantSignIn(org="acme", oidc=provider, start_path=SAML_START)
    with pytest.raises(ValueError, match="not both"):
        TenantSignIn(org="acme")
    with pytest.raises(ValueError, match="path on this API"):
        TenantSignIn(org="acme", start_path="https://evil.example/login")
    with pytest.raises(ValueError, match="path on this API"):
        TenantSignIn(org="acme", start_path="//evil.example/login")
    with pytest.raises(ValueError, match="together"):
        OidcProvider(issuer=ACME, client_id="c", token_endpoint=f"{ACME}/token")


def test_the_email_domain_is_lowercased() -> None:
    from choregos_api.tenant_sign_in import email_domain

    assert email_domain("Bob@ACME.Example ") == "acme.example"
    assert email_domain("bob") is None
    assert email_domain("bob@") is None
    assert email_domain(None) is None
