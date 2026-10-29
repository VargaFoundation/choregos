# SPDX-License-Identifier: Apache-2.0
"""GET /platform/identity: the instance-level sign-in configuration, read-only and secret-free."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login
from .test_coutures_identite import _connecter, _etat, client_greffe, greffon  # noqa: F401

SECRET = "s3cr3t-value-that-must-never-leak"
ISSUER = "https://idp.example.test/realms/x"
URL = "/api/v1/platform/identity"


def _by_key(body: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {r["key"]: r for r in body["items"]}


def _oidc(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_OIDC_CLIENT_ID", "choregos-web")
    monkeypatch.setenv("CHOREGOS_API_URL", "https://api.example.test")
    for k, v in extra.items():
        monkeypatch.setenv(f"CHOREGOS_{k}", v)
    reset_settings_cache()


async def test_rows_carry_issuer_client_id_and_redirect_uri(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _oidc(monkeypatch, OIDC_CLIENT_SECRET=SECRET, BOOTSTRAP_ADMINS="a@x.test,b@x.test")
    response = await client.get(URL)
    assert response.status_code == 200, response.text
    rows = _by_key(response.json())
    assert rows["oidc.issuer"]["value"] == ISSUER
    assert rows["oidc.issuer"]["source"] == "settings"
    assert rows["oidc.client_id"]["value"] == "choregos-web"
    assert rows["oidc.redirect_uri"]["value"] == "https://api.example.test/api/v1/auth/callback"
    assert rows["oidc.redirect_uri"]["source"] == "derived"
    assert rows["oidc.discovery"]["value"] == f"{ISSUER}/.well-known/openid-configuration"
    assert rows["bootstrap.admins"]["value"] == "2"


async def test_the_secret_is_never_in_the_body(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _oidc(monkeypatch, OIDC_CLIENT_SECRET=SECRET, BOOTSTRAP_ADMINS="leak@x.test")
    response = await client.get(URL)
    assert SECRET not in response.text
    assert "leak@x.test" not in response.text
    assert _by_key(response.json())["oidc.client_secret"]["value"] == "set"


async def test_a_missing_secret_says_missing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _oidc(monkeypatch)
    assert _by_key((await client.get(URL)).json())["oidc.client_secret"]["value"] == "missing"


async def test_unconfigured_oidc_says_so(client: AsyncClient, admin: str) -> None:
    rows = _by_key((await client.get(URL)).json())
    assert rows["oidc.issuer"]["value"] == "not configured"
    assert rows["oidc.client_secret"]["value"] == "not configured"
    assert rows["oidc.issuer"]["source"] == "default"


async def test_the_card_says_whether_organisations_bring_their_own_provider(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.edition import reinitialiser
    from choregos_api.tenant_sign_in import declare_sign_in_resolver

    key = "oidc.per_organisation"
    assert _by_key((await client.get(URL)).json())[key]["value"] == "instance provider only"
    declare_sign_in_resolver(lambda session, hint: None)
    try:
        assert _by_key((await client.get(URL)).json())[key]["value"] == "declared by an extension"
    finally:
        reinitialiser()


async def test_mcp_rows_follow_mcp_oauth_enabled(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _oidc(monkeypatch)
    off = _by_key((await client.get(URL)).json())
    assert off["mcp.oauth.enabled"]["value"] == "disabled"
    assert not [k for k in off if k.startswith("mcp.oauth.") and k != "mcp.oauth.enabled"]

    _oidc(
        monkeypatch,
        MCP_OAUTH_ENABLED="true",
        MCP_OAUTH_AUDIENCE="choregos-mcp",
        MCP_OAUTH_CLIENTS='{"claude-code": {"client_id": "cc-client", "callback_port": 33418}}',
    )
    on = _by_key((await client.get(URL)).json())
    assert on["mcp.oauth.enabled"]["value"] == "enabled"
    assert on["mcp.oauth.issuer"]["value"] == ISSUER
    assert on["mcp.oauth.audience"]["value"] == "choregos-mcp"
    assert on["mcp.oauth.client.claude-code"]["value"] == "cc-client, callback port 33418"


async def test_a_member_who_is_not_a_platform_admin_is_refused(client: AsyncClient, admin: str) -> None:
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    assert (await client.get(URL)).status_code == 403


async def test_a_plugin_authority_grants_access(client_greffe: AsyncClient) -> None:  # noqa: F811
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _connecter(client_greffe, "admin@varga.dev")
    async with session_scope(orgs="*") as session:
        second = Organization(slug="seconde", name="S")
        session.add(second)
        await session.flush()
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        session.add(Membership(user_id=admin.id, org_id=second.id, role="org_admin"))
    await _connecter(client_greffe, "ops@varga.dev")
    assert (await client_greffe.get(URL)).status_code == 403
    _etat()["admins"].add("ops@varga.dev")
    assert (await client_greffe.get(URL)).status_code == 200
    assert "greffon_identite" in sys.modules
