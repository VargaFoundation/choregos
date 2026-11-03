"""The MCP door as an OAuth resource server (RFC 9728): the IdP issues the tokens (ADR 0030).

The fake IdP of these tests publishes an OIDC discovery document and a JWKS (respx), and signs its
tokens with an RSA pair generated here: the door must refuse everything a real IdP would not have
given it — a foreign audience, `alg: none`, HS256, an expired token, a wrong issuer — follow a key
rotation, and know only the people who have already signed in to the console.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

ISSUER = "https://idp.test/realms/platform"
AUDIENCE = "choregos-mcp"


def _key_pair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key: Any, kid: str) -> dict[str, Any]:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return {**public, "kid": kid, "use": "sig", "alg": "RS256"}


def _token(private_key: Any, kid: str = "k1", **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "sub-admin",
        "azp": "claude-ai",
        "scope": "openid mcp:read",
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.mcp.oauth import KEYS

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    # The door follows the console's IdP: `mcp_oauth_issuer` is empty.
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_AUDIENCE", AUDIENCE)
    reset_settings_cache()
    KEYS.clear()
    state: dict[str, Any] = {"k1": _key_pair(), "k2": _key_pair(), "published": ["k1"]}
    with respx.mock(assert_all_called=False) as fake:
        fake.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})
        )
        fake.get(f"{ISSUER}/jwks").mock(
            side_effect=lambda _r: httpx.Response(
                200, json={"keys": [_jwk(state[kid], kid) for kid in state["published"]]}
            )
        )
        fake.route(host="test").pass_through()
        yield state
    KEYS.clear()
    reset_settings_cache()


async def _link_admin(sub: str = "sub-admin") -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        admin.oidc_sub = sub


async def _tools(client: AsyncClient, token: str) -> Any:
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_switched_off_the_door_publishes_no_metadata(client: AsyncClient) -> None:
    assert (await client.get("/.well-known/oauth-protected-resource/mcp")).status_code == 404


async def test_the_metadata_says_where_to_get_a_token(idp: dict[str, Any], client: AsyncClient) -> None:
    body = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert body["resource"].endswith("/mcp") and body["authorization_servers"] == [ISSUER]
    without = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert without.status_code == 401
    assert 'resource_metadata="' in without.headers["www-authenticate"]


async def test_an_idp_token_opens_the_door_for_reading(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    await _link_admin()
    client.cookies.clear()
    response = await _tools(client, _token(idp["k1"]))
    assert response.status_code == 200, response.text
    names = {o["name"] for o in response.json()["result"]["tools"]}
    assert "list_projects" in names and "create_work_item" not in names, "scope mcp:read: no write"


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda k: _token(_key_pair()), id="forged-signature"),
        pytest.param(lambda k: _token(k, aud="another-application"), id="foreign-audience"),
        pytest.param(lambda k: _token(k, iss="https://elsewhere.test"), id="wrong-issuer"),
        pytest.param(lambda k: _token(k, exp=int(time.time()) - 60), id="expired"),
        pytest.param(
            lambda k: jwt.encode({"sub": "sub-admin", "aud": AUDIENCE}, "secret", algorithm="HS256"),
            id="hs256",
        ),
        pytest.param(
            lambda k: jwt.encode({"sub": "sub-admin", "aud": AUDIENCE}, None, algorithm="none"), id="alg-none"
        ),
    ],
)
async def test_what_the_idp_did_not_give_for_the_door_is_refused(
    idp: dict[str, Any], client: AsyncClient, admin: str, factory: Any
) -> None:
    await _link_admin()
    client.cookies.clear()
    response = await _tools(client, factory(idp["k1"]))
    assert response.status_code == 401, response.text
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_a_key_rotation_is_followed(idp: dict[str, Any], client: AsyncClient, admin: str) -> None:
    await _link_admin()
    client.cookies.clear()
    assert (await _tools(client, _token(idp["k1"]))).status_code == 200
    idp["published"] = ["k1", "k2"]  # the IdP publishes a new key, and signs with it
    assert (await _tools(client, _token(idp["k2"], kid="k2"))).status_code == 200


async def test_someone_unknown_to_choregos_is_refused(
    idp: dict[str, Any], client: AsyncClient, admin: str
) -> None:
    await _link_admin()
    client.cookies.clear()
    response = await _tools(client, _token(idp["k1"], sub="sub-never-seen"))
    assert response.status_code == 403, response.text


async def test_a_projects_door_publishes_its_own_document(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    door = "/mcp/projects/varga:billing-api"
    without = await client.post(door, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert without.status_code == 401
    document = f"/.well-known/oauth-protected-resource{door}"
    assert f'{document}"' in without.headers["www-authenticate"], "the resource is the URL the client typed"
    assert (await client.get(document)).json()["resource"].endswith(door)


async def test_writing_is_asked_for_and_the_call_is_audited_in_the_clients_name(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert response.status_code == 200, response.text
    await _link_admin()
    token = _token(idp["k1"], scope="openid mcp:write")
    response = await _tools(client, token)
    assert "create_work_item" in {o["name"] for o in response.json()["result"]["tools"]}
    created = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "create_work_item",
                "arguments": {"project": "varga:billing-api", "title": "Through OAuth"},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.json()["result"]["isError"] is False, created.text
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (call,) = [a for a in audit if a["action"] == "mcp.call" and a.get("target_id") == "create_work_item"]
    assert call["payload"]["jeton"] == "oauth:claude-ai:sub-admin"  # lang: fr-ok — audit key, S45-39


async def test_another_idp_does_not_find_anyone_by_their_sub(
    idp: dict[str, Any], client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    await _link_admin()
    # The console signs in elsewhere: the same `sub` no longer designates the same person.
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", "https://console.test/realms/choregos")
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ISSUER", ISSUER)
    reset_settings_cache()
    client.cookies.clear()
    assert (await _tools(client, _token(idp["k1"]))).status_code == 403
    unverified = _token(idp["k1"], email="admin@varga.dev", email_verified=False)
    assert (await _tools(client, unverified)).status_code == 403, "an unverified e-mail links nobody"
    verified = _token(idp["k1"], email="admin@varga.dev", email_verified=True)
    assert (await _tools(client, verified)).status_code == 200


async def test_the_integrations_page_says_where_and_with_which_client_to_connect(
    idp: dict[str, Any], client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before, `/integrations` said `enabled: false` whatever the deployment said: the page only
    offered tokens, even when the door accepted the IdP's."""
    from choregos_api.config import reset_settings_cache

    clients = {
        "claude-code": {"client_id": "choregos-claude-code", "callback_port": 33418},
        "claude-ai": {"client_id": "choregos-claude-ai"},
    }
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_CLIENTS", json.dumps(clients))
    reset_settings_cache()
    oauth = (await client.get("/api/v1/integrations")).json()["oauth"]
    assert oauth == {
        "enabled": True,
        "authorization_server": ISSUER,
        "clients": {
            "claude-ai": {"client_id": "choregos-claude-ai", "callback_port": None},
            "claude-code": {"client_id": "choregos-claude-code", "callback_port": 33418},
        },
    }


def test_an_oauth_client_the_page_does_not_know_stops_the_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`claude_code` instead of `claude-code` would show nothing, silently: refused at startup."""
    from choregos_api.config import Settings
    from pydantic import ValidationError

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_CLIENTS", json.dumps({"claude_code": {"client_id": "x"}}))
    with pytest.raises(ValidationError, match="claude_code"):
        Settings()
    monkeypatch.setenv(
        "CHOREGOS_MCP_OAUTH_CLIENTS", json.dumps({"claude-code": {"client_id": "x", "callback_port": 80}})
    )
    with pytest.raises(ValidationError, match="callback_port"):
        Settings()
