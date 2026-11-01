# SPDX-License-Identifier: Apache-2.0
"""The per-organisation assertion keys of the MCP gateway (S46-07, ADR 0105)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_api.secret_store import FakeSecretStore, set_secret_store
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login

KEY_URL = "/api/v1/orgs/varga/mcp-gateway/assertion-key"
JWKS_URL = "/api/v1/orgs/varga/mcp-gateway/jwks.json"


@pytest.fixture
def store() -> Iterator[FakeSecretStore]:
    fake = FakeSecretStore()
    set_secret_store(fake)
    yield fake
    set_secret_store(None)


async def _anonymous(app: Any, url: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        return await http.get(url)


async def _organisation(slug: str = "varga") -> Any:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        found = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        session.expunge(found)
        return found


async def _audit(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return [dict(r.payload) for r in rows]


async def test_an_admin_creates_a_key_and_the_jwks_publishes_it(
    client: AsyncClient, admin: str, app: Any, store: FakeSecretStore
) -> None:
    done = await client.post(KEY_URL)
    assert done.status_code == 200, done.text
    keys = done.json()["keys"]
    assert len(keys) == 1 and keys[0]["current"] is True
    published = await _anonymous(app, JWKS_URL)
    assert published.status_code == 200, published.text
    assert published.headers["cache-control"] == "public, max-age=300"
    jwks = published.json()["keys"]
    assert len(jwks) == 1 and jwks[0]["kid"] == keys[0]["kid"]
    assert jwks[0]["kty"] == "EC" and jwks[0]["crv"] == "P-256" and "d" not in jwks[0]


async def test_a_minted_assertion_verifies_against_the_jwks(
    client: AsyncClient, admin: str, app: Any, store: FakeSecretStore
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import mcp_gateway_identity as gw

    await client.post(KEY_URL)
    jwks = (await _anonymous(app, JWKS_URL)).json()
    organisation = await _organisation()
    async with session_scope() as session:
        kid, pem = await gw.signing_key(session, organisation)
    who = gw.CallIdentity(
        org="varga", org_id=organisation.id, subject="run:r1", purpose="tool_call", run="r1", agent="a@1"
    )
    first = gw.mint_assertion(kid, pem, issuer=gw.issuer_of("varga"), audience="gw", identity=who)
    second = gw.mint_assertion(kid, pem, issuer=gw.issuer_of("varga"), audience="gw", identity=who)
    claims = jwt.decode(
        first,
        jwt.PyJWK(jwks["keys"][0]).key,
        algorithms=["ES256"],
        audience="gw",
        issuer=gw.issuer_of("varga"),
    )
    assert claims["exp"] - claims["iat"] == 60
    assert claims["sub"] == "run:r1" and claims["org_id"] == organisation.id and claims["run"] == "r1"
    assert jwt.get_unverified_header(first)["kid"] == kid
    assert jwt.decode(second, options={"verify_signature": False})["jti"] != claims["jti"]


async def test_rotation_keeps_the_previous_key_and_drops_the_older(
    client: AsyncClient, admin: str, app: Any, store: FakeSecretStore
) -> None:
    kids = [(await client.post(KEY_URL)).json()["keys"][0]["kid"] for _ in range(3)]
    assert len(set(kids)) == 3
    published = [k["kid"] for k in (await _anonymous(app, JWKS_URL)).json()["keys"]]
    assert sorted(published) == sorted(kids[1:])
    names = {name for (_, name) in store.values}
    assert names == {f"org/mcp-gateway/assertion-key/{kid}" for kid in kids[1:]}


async def test_only_the_org_admin_rotates(client: AsyncClient, admin: str, store: FakeSecretStore) -> None:
    client.cookies.clear()
    await login(client, "someone@elsewhere.dev")
    assert (await client.post(KEY_URL)).status_code == 403
    assert store.values == {}


async def test_no_secrets_key_is_a_409(client: AsyncClient, admin: str) -> None:
    set_secret_store(FakeSecretStore(available=False))
    try:
        assert (await client.post(KEY_URL)).status_code == 409
    finally:
        set_secret_store(None)
    assert "mcp_gateway_assertion" not in ((await _organisation()).settings or {})


async def test_the_jwks_of_an_unknown_or_keyless_org_is_the_same_404(app: Any, org: str) -> None:
    unknown = await _anonymous(app, "/api/v1/orgs/nope/mcp-gateway/jwks.json")
    keyless = await _anonymous(app, JWKS_URL)
    assert unknown.status_code == keyless.status_code == 404
    same = json.dumps(keyless.json()).replace("varga", "nope")
    assert json.dumps(unknown.json()) == same


async def test_the_rotation_is_audited_without_key_material(
    client: AsyncClient, admin: str, store: FakeSecretStore
) -> None:
    kid = (await client.post(KEY_URL)).json()["keys"][0]["kid"]
    rows = await _audit("mcp_gateway.assertion_key.rotate")
    assert len(rows) == 1 and rows[0]["kid"] == kid
    text = json.dumps(rows)
    assert "PRIVATE KEY" not in text and '"d"' not in text


async def test_signing_key_fails_closed_when_the_pem_is_lost(
    client: AsyncClient, admin: str, store: FakeSecretStore
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import mcp_gateway_identity as gw

    organisation = await _organisation()
    async with session_scope() as session:
        with pytest.raises(gw.AssertionKeyMissing, match="missing"):
            await gw.signing_key(session, organisation)  # no key yet
    await client.post(KEY_URL)
    organisation = await _organisation()
    set_secret_store(FakeSecretStore())  # the sealed PEM is lost
    async with session_scope() as session:
        with pytest.raises(gw.AssertionKeyMissing) as lost:
            await gw.signing_key(session, organisation)
    assert isinstance(lost.value, ConfigurationError)
    assert lost.value.code == "gateway_assertion_key_missing"


async def test_a_renamed_organisation_keeps_its_issuer_and_jwks_url(
    client: AsyncClient, admin: str, app: Any, store: FakeSecretStore
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import mcp_gateway_identity as gw

    before = (await client.post(KEY_URL)).json()
    organisation = await _organisation()
    async with session_scope() as session:
        kid, pem = await gw.signing_key(session, organisation)
    renamed = await client.patch("/api/v1/orgs/varga", json={"slug": "varga2"})
    assert renamed.status_code == 200, renamed.text
    assert (await _anonymous(app, JWKS_URL)).status_code == 200
    after = await _organisation("varga2")
    assert gw.assertion_state(after).issuer == before["issuer"]
    who = gw.CallIdentity(org="varga2", org_id=after.id, subject="s", purpose="tool_call")
    token = gw.mint_assertion(kid, pem, issuer=gw.issuer_of(after), audience="gw", identity=who)
    assert jwt.decode(token, options={"verify_signature": False})["iss"] == before["issuer"]
    assert gw.assertion_state(after).jwks_url == before["jwks_url"]
