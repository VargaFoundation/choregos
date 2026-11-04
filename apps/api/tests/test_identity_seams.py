# SPDX-License-Identifier: Apache-2.0
"""The identity seams: revoking a session, designating a platform administrator, demanding a fresh
authentication, each proved by an INSTALLED extension.

The extension is a real module with its `.dist-info`; `create_app()` loads it as at start-up.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from .kits.identity_seams import connect, state_of


async def test_the_session_carries_its_authentication_time(extended_client: AsyncClient) -> None:
    before = int(time.time())
    await connect(extended_client, "admin@varga.dev")
    assert (await extended_client.get("/api/v1/me")).status_code == 200
    (payload,) = state_of()["seen"]
    assert before <= payload["iat"] <= int(time.time())


async def test_a_revoked_session_is_refused_on_the_next_request(extended_client: AsyncClient) -> None:
    await connect(extended_client, "admin@varga.dev")
    assert (await extended_client.get("/api/v1/me")).status_code == 200
    state_of()["revoked"].add("admin@varga.dev")
    refused = await extended_client.get("/api/v1/me")
    assert refused.status_code == 401
    assert "session revoked by the administrator" in refused.text


async def test_a_validation_that_is_down_does_not_count_as_acceptance(extended_client: AsyncClient) -> None:
    await connect(extended_client, "admin@varga.dev")
    state_of()["outage"] = True
    # Since S26-02 the failure is answered as a problem+json 500 rather than propagated: still a refusal.
    response = await extended_client.get("/api/v1/me")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_an_extension_grants_platform_administration_without_withdrawing_it(
    extended_client: AsyncClient,
) -> None:
    """Two organisations: the core's rule (admin of ALL) refuses the head of `varga` alone; the
    extension can grant it to someone else, without withdrawing the right from whom the core gives it."""
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await connect(extended_client, "admin@varga.dev")
    # A second organisation, put in the database: the community edition refuses to create it through the API.
    async with session_scope(orgs="*") as session:
        second = Organization(slug="second", name="S")
        session.add(second)
        await session.flush()
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        session.add(Membership(user_id=admin.id, org_id=second.id, role="org_admin"))
    # The admin administers both: the core grants it, extension or not.
    assert (await extended_client.get("/api/v1/platform/gateway/keys")).status_code == 200

    await connect(extended_client, "ops@varga.dev")
    assert (await extended_client.get("/api/v1/platform/gateway/keys")).status_code == 403
    state_of()["admins"].add("ops@varga.dev")
    assert (await extended_client.get("/api/v1/platform/gateway/keys")).status_code == 200


ISSUER = "https://idp.example.test/realms/x"


async def test_reauth_asks_the_idp_to_authenticate_again(
    extended_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import forget_discovery

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    forget_discovery()
    try:
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
            ordinary = parse_qs(
                urlparse((await extended_client.get("/api/v1/auth/login")).headers["location"]).query
            )
            assert "prompt" not in ordinary and "max_age" not in ordinary
            fresh = await extended_client.get("/api/v1/auth/login", params={"reauth": "1"})
            query = parse_qs(urlparse(fresh.headers["location"]).query)
            assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    finally:
        reset_settings_cache()
        forget_discovery()


SAML_EXTENSION = '''
from fastapi import APIRouter

from choregos_api.deps import Db
from choregos_api.extensions import declare_router
from choregos_api.routers.auth import terminer_la_connexion  # lang: fr-ok

router = APIRouter()


@router.post("/extension/acs")
async def acs(session: Db, cible: str = "/"):
    """Like a SAML service, AFTER verifying the assertion (here: nothing to verify)."""
    return await terminer_la_connexion(
        session,
        email="marie@varga.dev",
        nom="Marie",
        sujet="saml|marie",
        groupes=["choregos:varga:org-admins"],
        cible=cible,
        canal="saml",
    )


def plug_in():
    declare_router(router)
'''


@pytest.fixture
async def saml_client(tmp_path: pathlib.Path) -> AsyncIterator[AsyncClient]:
    from choregos_api.extensions import reset

    root = tmp_path / "site-saml"
    root.mkdir()
    (root / "extension_saml.py").write_text(SAML_EXTENSION, encoding="utf-8")
    info = root / "extension_saml-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: extension_saml\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_saml = extension_saml:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/saml.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.main import create_app

    reset_settings_cache()
    await db_session.dispose_engine()
    try:
        application = create_app()
        await create_all()
        async with session_scope() as session:
            session.add(Organization(slug="varga", name="Varga"))
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
            yield http
    finally:
        await db_session.dispose_engine()
        reset_settings_cache()
        sys.path.remove(str(root))
        sys.modules.pop("extension_saml", None)
        reset()


async def test_another_protocol_opens_a_session_exactly_like_oidc(saml_client: AsyncClient) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from choregos_api.security import read_session
    from sqlalchemy import select

    response = await saml_client.post("/api/v1/extension/acs", params={"cible": "https://evil.example/theft"})
    assert response.status_code == 307
    # The target is bounded as for OIDC: no open redirect through this path.
    assert not response.headers["location"].startswith("https://evil.example")
    cookie = saml_client.cookies.get("choregos_session")
    assert cookie, "no session set"
    payload = read_session(cookie)
    assert payload is not None and isinstance(payload.get("iat"), int)

    me = (await saml_client.get("/api/v1/me")).json()
    assert me["email"] == "marie@varga.dev"
    assert {(m["org"], m["role"]) for m in me["memberships"]} == {("varga", "org_admin")}
    async with session_scope(orgs="*") as session:
        (trail,) = (await session.execute(select(AuditLog).where(AuditLog.action == "auth.login"))).scalars()
    assert trail.payload == {"canal": "saml"}
