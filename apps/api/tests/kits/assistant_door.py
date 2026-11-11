"""What the assistant-door tests share (S63-11): a ready assistant and a fake identity provider."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "plugins" / "investment-assistant"
PLUGIN = {"source": "offline", "name": "investment-assistant", "version": "0.1.0"}
ISSUER = "https://idp.test/realms/platform"
AUDIENCE = "choregos-mcp"


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Path]:
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    root = tmp_path / "plugins"
    shutil.copytree(FIXTURE, root / "investment-assistant" / "0.1.0")
    use_plugin_supply(OfflineDirectory(root))
    yield root
    use_plugin_supply(None)


async def install_plugin(client: AsyncClient) -> None:
    from ..agent_scopes_kit import as_user

    await as_user(client, "admin@varga.dev")
    response = await client.post(f"{ORG}/plugins/install", json=PLUGIN)
    assert response.status_code == 201, response.text


async def connector(name: str, operations: tuple[str, ...]) -> None:
    from choregos_api.db.models import ConnectorOperation, Organization, OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        row = OrgConnector(
            org_id=org_id, name=name, kind="mcp", type="mcp", config={"url": f"https://{name}.test/mcp"}
        )
        session.add(row)
        await session.flush()
        for operation in operations:
            session.add(
                ConnectorOperation(
                    org_id=org_id, connector_id=row.id, name=operation, access="read", policy="allowed"
                )
            )


def _jwk(private_key: Any, kid: str) -> dict[str, Any]:
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return {**public, "kid": kid, "use": "sig", "alg": "RS256"}


def idp_token(private_key: Any, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "azp": "claude-ai",
        "scope": "openid mcp:read",
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """An identity provider that publishes one key; the door follows the console's issuer."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.mcp.oauth import KEYS

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_AUDIENCE", AUDIENCE)
    reset_settings_cache()
    KEYS.clear()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with respx.mock(assert_all_called=False) as fake:
        fake.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"})
        )
        fake.get(f"{ISSUER}/jwks").mock(return_value=httpx.Response(200, json={"keys": [_jwk(key, "k1")]}))
        fake.route(host="test").pass_through()
        yield key
    KEYS.clear()
    reset_settings_cache()
