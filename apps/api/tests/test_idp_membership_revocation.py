"""An IdP sign-in revokes the organisation roles the IdP granted and no longer maps (S47-05, ADR 0100).

Only rows the provider created (`memberships.source = idp`) are ever revoked. Every test here fails
when the revocation, the provenance column or the last-admin guard is taken out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
import structlog.testing
from httpx import AsyncClient, Response
from sqlalchemy import select

from .conftest import login

ISSUER = "https://idp.example.test/realms/x"
EMAIL = "jo@varga.dev"


@pytest.fixture
async def idp(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """An instance provider whose userinfo answers `state["groups"]`."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import oublier_la_decouverte

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "http://front.test")
    reset_settings_cache()
    oublier_la_decouverte()
    state: dict[str, Any] = {"groups": []}
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
        mock.post(f"{ISSUER}/token").mock(return_value=Response(200, json={"access_token": "at-1"}))
        mock.get(f"{ISSUER}/userinfo").mock(
            side_effect=lambda _request: Response(
                200, json={"sub": "u-1", "email": EMAIL, "name": "Jo", "groups": state["groups"]}
            )
        )
        yield state
    reset_settings_cache()
    oublier_la_decouverte()


async def _sign_in(client: AsyncClient, idp: dict[str, Any], groups: list[str]) -> None:
    idp["groups"] = groups
    start = await client.get("/api/v1/auth/login")
    assert start.status_code == 307, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state})
    assert done.status_code == 307, done.text


async def _memberships(client: AsyncClient) -> set[tuple[str, str]]:
    me = (await client.get("/api/v1/me")).json()
    return {(m["org"], m["role"]) for m in me["memberships"]}


async def _revocations() -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        stmt = select(AuditLog).where(AuditLog.action == "member.revoke")
        return list((await session.execute(stmt)).scalars().all())


async def _seed(slug: str, role: str, source: str, email: str = EMAIL) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        found = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        user = found or User(email=email, display_name=email)
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=role, source=source))


async def _source(slug: str, email: str = EMAIL) -> str | None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        stmt = (
            select(Membership.source)
            .join(User, User.id == Membership.user_id)
            .join(Organization, Organization.id == Membership.org_id)
            .where(User.email == email, Organization.slug == slug, Membership.project_id.is_(None))
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def test_a_group_withdrawn_at_the_idp_withdraws_the_role(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    await _sign_in(client, idp, ["choregos:varga:release-captains"])
    assert await _memberships(client) == {("varga", "release_captain")}
    assert await _source("varga") == "idp"
    await _sign_in(client, idp, [])
    assert await _memberships(client) == set()
    revoked = await _revocations()
    assert len(revoked) == 1
    assert revoked[0].payload["role"] == "release_captain"


async def test_a_role_moved_to_another_org_at_the_idp_moves(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="autre", name="Autre"))
    await _sign_in(client, idp, ["choregos:varga:developers"])
    assert await _memberships(client) == {("varga", "developer")}
    await _sign_in(client, idp, ["choregos:autre:developers"])
    assert await _memberships(client) == {("autre", "developer")}


async def test_a_manual_membership_survives_a_sign_in_without_groups(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    await _seed("varga", "org_admin", "manual")
    await _sign_in(client, idp, [])
    assert await _memberships(client) == {("varga", "org_admin")}
    assert await _revocations() == []


async def test_a_bootstrap_admin_mapped_by_the_idp_stays_manual(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    await _seed("varga", "org_admin", "manual")
    await _seed("varga", "org_admin", "manual", email="other@varga.dev")
    await _sign_in(client, idp, ["choregos:varga:developers"])
    assert await _memberships(client) == {("varga", "developer")}  # the role follows the provider
    assert await _source("varga") == "manual"
    await _sign_in(client, idp, [])
    assert await _memberships(client) == {("varga", "developer")}
    assert await _source("varga") == "manual"
    assert await _revocations() == []


async def test_the_last_org_admin_granted_by_the_idp_is_kept_and_logged(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    await _sign_in(client, idp, ["choregos:varga:org-admins"])
    assert await _memberships(client) == {("varga", "org_admin")}
    with structlog.testing.capture_logs() as logs:
        await _sign_in(client, idp, [])
    assert await _memberships(client) == {("varga", "org_admin")}
    assert any(entry["event"] == "idp revocation skipped: last org_admin" for entry in logs)
    assert await _revocations() == []


async def test_an_idp_org_admin_goes_when_another_administrator_exists(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    await _seed("varga", "org_admin", "manual", email="other@varga.dev")
    await _sign_in(client, idp, ["choregos:varga:org-admins"])
    await _sign_in(client, idp, [])
    assert await _memberships(client) == set()
    assert len(await _revocations()) == 1


async def test_the_development_login_revokes_nothing(client: AsyncClient, org: str) -> None:
    await _seed("varga", "release_captain", "idp", email="dev@varga.dev")
    await login(client, "dev@varga.dev")
    first = await _memberships(client)
    await login(client, "dev@varga.dev")
    assert await _memberships(client) == first
    assert await _source("varga", "dev@varga.dev") == "idp"
    assert await _revocations() == []


async def test_an_instance_sign_in_leaves_an_organisation_with_its_own_provider_alone(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    """An instance sign-in does not speak for an organisation that has a provider of its own."""
    from choregos_api import tenant_sign_in
    from choregos_api.tenant_sign_in import OidcProvider, TenantSignIn

    await _sign_in(client, idp, ["choregos:varga:developers"])

    async def resolver(_session: Any, hint: Any) -> TenantSignIn | None:
        if hint.org == "varga":
            return TenantSignIn(org="varga", oidc=OidcProvider(issuer=ISSUER, client_id="c"))
        return None

    tenant_sign_in.declare_sign_in_resolver(resolver)
    try:
        await _sign_in(client, idp, [])
        assert await _memberships(client) == {("varga", "developer")}
        assert await _revocations() == []
    finally:
        tenant_sign_in.reset()


async def test_a_group_naming_a_former_slug_keeps_its_role(
    client: AsyncClient, org: str, idp: dict[str, Any]
) -> None:
    from .test_org_slug_aliases import _rename

    # The administrator who renames `varga` signs in through the provider (the development login is off).
    await _sign_in(client, idp, ["choregos:varga:org-admins"])
    await _rename(client)
    await _sign_in(client, idp, ["choregos:varga:developers"])
    await _sign_in(client, idp, ["choregos:varga:developers"])
    assert await _memberships(client) == {("fondation", "developer")}
    assert await _revocations() == []
