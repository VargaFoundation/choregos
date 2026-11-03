# SPDX-License-Identifier: Apache-2.0
"""The organisation's MCP gateway: the setting, its sealed credential, a test through it (S46-08, ADR 0105).

Every guard here is proved by a test that fails without it: the SSRF guard on the endpoint, the sealed
credential that is in no answer and no audit row, the store and egress checks before any write, and the
families an egress rule is opened for."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
from choregos_api.secret_store import FakeSecretStore, set_secret_store
from httpx import AsyncClient
from sqlalchemy import select

from .conftest import login
from .egress_support import ORG, rules_in_db

URL = f"{ORG}/mcp-gateway"
ENDPOINT = "https://gw.example.test/{server}/mcp"
TOOLS = [{"name": f"tool_{n}", "inputSchema": {"type": "object"}} for n in range(3)]
SECRET = "s3cr3t-credential-value"


@pytest.fixture(autouse=True)
def isolated() -> Iterator[FakeSecretStore]:
    """A fresh secret store, token cache and token transport, restored after the test."""
    from choregos_api.services import mcp_gateway as service

    store = FakeSecretStore()
    set_secret_store(store)
    service.clear_token_cache()
    service.set_token_transport(None)
    yield store
    set_secret_store(None)
    service.clear_token_cache()
    service.set_token_transport(None)


def _server() -> FakeMcpServer:
    return FakeMcpServer(outils=[dict(t) for t in TOOLS])


@pytest.fixture
def supplier() -> FakeMcpServer:
    return _server()


@pytest.fixture
def gateway(supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch) -> FakeMcpGateway:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier, token="upstream-own-token")},
        auth="bearer",
        token=SECRET,
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    return fake


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "available",
        "endpoint": ENDPOINT,
        "routing": "path",
        "auth": {"type": "bearer"},
        "secret": SECRET,
    }
    body.update(over)
    return body


def _oauth(token_url: str = "https://gw.example.test/oauth/token", **over: Any) -> dict[str, Any]:
    return _body(
        auth={"type": "oauth_client_credentials", "token_url": token_url, "client_id": "c"},
        secret="client-secret-value",
        **over,
    )


async def _organisation() -> Any:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        found = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.expunge(found)
        return found


async def _audit(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return [dict(r.payload) for r in rows]


async def _all_audit_text() -> str:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog))).scalars()
        return json.dumps([dict(r.payload) for r in rows], default=str)


async def _test(client: AsyncClient, server: str = "supplier") -> dict[str, Any]:
    done = await client.post(f"{URL}/test", json={"server": server})
    assert done.status_code == 200, done.text
    return dict(done.json())


async def test_no_gateway_reads_as_null_with_the_assertion_block(client: AsyncClient, admin: str) -> None:
    got = await client.get(URL)
    assert got.status_code == 200, got.text
    state = got.json()
    assert state["gateway"] is None
    from choregos_api.config import get_settings

    public = get_settings().public_url.rstrip("/")
    assert state["assertion"]["issuer"] == f"{public}/api/v1/orgs/varga/mcp-gateway"
    assert state["assertion"]["keys"] == []


async def test_a_bearer_gateway_is_sealed_and_never_returned(
    client: AsyncClient, admin: str, isolated: FakeSecretStore
) -> None:
    done = await client.put(URL, json=_body())
    assert done.status_code == 200, done.text
    assert done.json()["gateway"]["secret_set"] is True
    got = await client.get(URL)
    for text in (done.text, got.text, await _all_audit_text()):
        assert SECRET not in text
    org = await _organisation()
    assert isolated.values[(org.id, "org/mcp-gateway/credential")][0] == SECRET
    assert SECRET not in json.dumps(org.settings)
    (audit,) = await _audit("mcp_gateway.update")
    assert audit["secret_changed"] is True and audit["auth_type"] == "bearer"
    assert audit["endpoint"] == ENDPOINT and audit["direct_mcp_connectors"] == []


async def test_a_refused_body_does_not_echo_the_secret(client: AsyncClient, admin: str) -> None:
    refused = await client.put(URL, json=_body(endpoint="https://10.0.0.5/{server}/mcp"))
    assert refused.status_code == 422
    assert SECRET not in refused.text
    malformed = await client.put(URL, json=_body(timeout_s=0))
    assert malformed.status_code == 422
    assert SECRET not in malformed.text


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://gw.example.test/{server}/mcp",
        "https://10.0.0.5/{server}/mcp",
        "https://[::1]/{server}/mcp",
        "https://127.1/{server}/mcp",
        "https://2130706433/{server}/mcp",
        "https://gw.mcp.svc/{server}/mcp",
        "https://gw.mcp.svc.cluster.local/{server}/mcp",
        "https://gw.corp.internal/{server}/mcp",
        "https://printer.local/{server}/mcp",
        "https://localhost/{server}/mcp",
        "https://localhost./{server}/mcp",
        "https://gateway/{server}/mcp",
        "https://user:pass@gw.example.test/{server}/mcp",
    ],
)
async def test_endpoints_that_point_inside_are_refused_unless_the_operator_lists_them(
    client: AsyncClient, admin: str, endpoint: str
) -> None:
    refused = await client.put(URL, json=_body(endpoint=endpoint))
    assert refused.status_code == 422, refused.text
    assert refused.json()["errors"][0]["loc"] == ["body", "endpoint"]
    assert (await client.get(URL)).json()["gateway"] is None, "nothing was written"


async def test_a_listed_host_may_be_private_and_plain_http(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    endpoint = "http://gw.mcp.svc:8080/{server}/mcp"
    assert (await client.put(URL, json=_body(endpoint=endpoint))).status_code == 422
    monkeypatch.setenv("CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS", '["GW.mcp.svc"]')
    reset_settings_cache()
    assert (await client.put(URL, json=_body(endpoint=endpoint))).status_code == 200
    other = "http://other.mcp.svc:8080/{server}/mcp"
    assert (await client.put(URL, json=_body(endpoint=other))).status_code == 422, "only the listed host"


async def test_the_token_endpoint_is_held_to_the_same_rule(client: AsyncClient, admin: str) -> None:
    refused_urls = (
        "https://10.0.0.5/token",
        "http://auth.example.test/token",
        "https://auth.svc/token",
        "https://user:pass@auth.example.test/token",
        "https://auth.example.test/token?tenant=a",
        "https://auth.example.test/token#frag",
        "ftp://auth.example.test/token",
        "https://auth!.example.test/token",
    )
    for token_url in refused_urls:
        refused = await client.put(URL, json=_oauth(token_url=token_url))
        assert refused.status_code == 422, (token_url, refused.text)
        assert refused.json()["errors"][0]["loc"] == ["body", "auth", "token_url"]
        assert "pass" not in refused.text.replace("password", "")


async def test_a_host_that_is_not_a_valid_name_is_refused(client: AsyncClient, admin: str) -> None:
    for endpoint in ("https://gw!.example.test/{server}/mcp", "https://g\u00e9.example.test/{server}/mcp"):
        refused = await client.put(URL, json=_body(endpoint=endpoint))
        assert refused.status_code == 422, (endpoint, refused.text)


async def test_the_network_ceiling_bounds_the_endpoint_and_the_token_endpoint(
    client: AsyncClient, admin: str
) -> None:
    ceiling = f"{ORG}/policy-ceiling"
    deny = {"ceiling": {"network": {"deny_domains": ["auth.example.test"]}}}
    assert (await client.put(ceiling, json=deny)).status_code == 200
    refused = await client.put(URL, json=_oauth(token_url="https://auth.example.test/oauth/token"))
    assert refused.status_code == 422 and "auth.example.test" in refused.text, refused.text
    deny = {"ceiling": {"network": {"deny_domains": ["gw.example.test"]}}}
    assert (await client.put(ceiling, json=deny)).status_code == 200
    refused = await client.put(URL, json=_body())
    assert refused.status_code == 422 and "gw.example.test" in refused.text, refused.text
    assert (await client.get(URL)).json()["gateway"] is None


@pytest.mark.parametrize(
    "body",
    [
        _body(endpoint="https://gw.example.test/mcp"),
        _body(endpoint="https://{server}.example.test/mcp"),
        _body(endpoint="https://gw.example.test/{server}/{server}"),
        _body(routing="header", endpoint="https://gw.example.test/{server}/mcp", server_header="X-S"),
        _body(routing="header", endpoint="https://gw.example.test/mcp"),
        _body(auth={"type": "header"}),
        _body(auth={"type": "oauth_client_credentials", "client_id": "c"}),
        _body(auth={"type": "oauth_client_credentials", "token_url": "https://gw.example.test/t"}),
        _body(auth={"type": "jwt_assertion"}, secret=None),
        _body(auth={"type": "jwt_assertion"}, audience="gw"),
        _body(identity_header="X-Identity"),
        _body(secret=None),
    ],
    ids=[
        "path routing without {server}",
        "{server} in the host",
        "two {server}",
        "header routing with {server}",
        "header routing without server_header",
        "header auth without header_name",
        "oauth without token_url",
        "oauth without client_id",
        "jwt without audience",
        "jwt with a secret",
        "identity header without audience",
        "a first bearer write without a secret",
    ],
)
async def test_incoherent_settings_are_422(client: AsyncClient, admin: str, body: dict[str, Any]) -> None:
    refused = await client.put(URL, json=body)
    assert refused.status_code == 422, refused.text
    assert (await client.get(URL)).json()["gateway"] is None


async def test_a_change_of_auth_type_needs_a_new_secret_and_the_same_type_keeps_it(
    client: AsyncClient, admin: str
) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    kept = await client.put(URL, json=_body(secret=None, mode="required"))
    assert kept.status_code == 200 and kept.json()["gateway"]["secret_set"] is True
    changed = _body(auth={"type": "header", "header_name": "X-Api-Key"}, secret=None)
    assert (await client.put(URL, json=changed)).status_code == 422
    assert (await client.get(URL)).json()["gateway"]["auth"]["type"] == "bearer", "the stored one stays"


async def test_a_jwt_gateway_needs_a_secrets_key(client: AsyncClient, admin: str) -> None:
    set_secret_store(FakeSecretStore(available=False))
    body = _body(auth={"type": "jwt_assertion"}, secret=None, audience="gw")
    refused = await client.put(URL, json=body)
    assert refused.status_code == 409, refused.text
    settings = (await _organisation()).settings or {}
    assert "mcp_gateway" not in settings and "mcp_gateway_assertion" not in settings
    with_identity = await client.put(URL, json=_body(audience="gw", identity_header="X-Identity"))
    assert with_identity.status_code == 409, "a bearer secret needs the key too"
    bearer_only = _body(secret=None)
    assert (await client.put(URL, json=bearer_only)).status_code == 422


async def test_a_secret_needs_a_secrets_key_before_anything_is_written(
    client: AsyncClient, admin: str
) -> None:
    set_secret_store(FakeSecretStore(available=False))
    refused = await client.put(URL, json=_body())
    assert refused.status_code == 409, refused.text
    assert "mcp_gateway" not in ((await _organisation()).settings or {})


async def test_no_egress_rule_is_opened_when_the_secrets_key_is_missing(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    set_secret_store(FakeSecretStore(available=False))
    refused = await client.put(URL, json=_body(allow_egress=True))
    assert refused.status_code == 409, refused.text
    assert await rules_in_db("allowed") == []


async def test_the_gateway_host_is_a_governed_add_for_api_and_workers_only(
    client: AsyncClient, admin: str, enforce: None, isolated: FakeSecretStore
) -> None:
    refused = await client.put(URL, json=_body())
    assert refused.status_code == 409, refused.text
    assert "gw.example.test" in refused.text and "egress" in refused.text.lower()
    org = await _organisation()
    assert "mcp_gateway" not in (org.settings or {}), "nothing is written before the egress check"
    assert isolated.values == {}, "the credential is not sealed before the egress check"
    assert await rules_in_db("allowed") == []

    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(OrgConnector(org_id=org.id, name="direct", kind="mcp", type="mcp", config={}))
        session.add(
            OrgConnector(
                org_id=org.id,
                name="from-plugin",
                kind="mcp",
                type="mcp",
                config={},
                plugin_server={"image": "ghcr.io/x/y"},
            )
        )
    allowed = await client.put(URL, json=_body(mode="required", allow_egress=True))
    assert allowed.status_code == 200, allowed.text
    (rule,) = [r for r in await rules_in_db("allowed") if r.host == "gw.example.test"]
    assert rule.used_by == ["api", "workers"] and rule.origin == "mcp-gateway"
    (audit,) = await _audit("mcp_gateway.update")
    assert audit["direct_mcp_connectors"] == ["direct"], "a plugin's server is not a direct upstream"

    oauth = _oauth(token_url="https://auth.example.test/oauth/token", allow_egress=True)
    assert (
        await client.put(URL, json=_oauth(token_url="https://auth.example.test/oauth/token"))
    ).status_code == 409
    done = await client.put(URL, json=oauth)
    assert done.status_code == 200, done.text
    (auth_rule,) = [r for r in await rules_in_db("allowed") if r.host == "auth.example.test"]
    assert auth_rule.used_by == ["api", "workers"]


async def test_the_test_lists_a_servers_tools_through_the_gateway(
    client: AsyncClient, admin: str, gateway: FakeMcpGateway, supplier: FakeMcpServer
) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    result = await _test(client)
    assert result["ok"] is True and result["tools"] == 3, result
    assert result["protocol_version"] and result["error"] is None
    assert gateway.calls and all(c.headers["authorization"] == f"Bearer {SECRET}" for c in gateway.calls)
    seen = [value for _, headers, _ in supplier.recues for value in headers.values()]
    assert supplier.recues and SECRET not in " ".join(seen), "the upstream never saw the caller's credential"
    (audit,) = await _audit("mcp_gateway.test")
    assert audit == {"server": "supplier", "ok": True}
    assert SECRET not in await _all_audit_text()


async def test_a_test_with_no_gateway_set_is_404(client: AsyncClient, admin: str) -> None:
    assert (await client.post(f"{URL}/test", json={"server": "supplier"})).status_code == 404


async def test_the_test_ignores_the_mode(client: AsyncClient, admin: str, gateway: FakeMcpGateway) -> None:
    assert (await client.put(URL, json=_body(mode="off"))).status_code == 200
    assert (await _test(client))["ok"] is True


async def test_header_routing_sends_the_server_name_in_its_header(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        routing="header",
        server_header="X-MCP-Server",
        token=SECRET,
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    body = _body(routing="header", endpoint="https://gw.example.test/mcp", server_header="X-MCP-Server")
    assert (await client.put(URL, json=body)).status_code == 200
    assert (await _test(client))["ok"] is True
    assert {c.headers["x-mcp-server"] for c in fake.calls} == {"supplier"}


async def test_a_custom_header_carries_the_secret(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)}, auth="header", token=SECRET
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    body = _body(auth={"type": "header", "header_name": "X-Api-Key"})
    assert (await client.put(URL, json=body)).status_code == 200
    assert (await _test(client))["ok"] is True
    assert fake.calls and all(c.headers["x-api-key"] == SECRET for c in fake.calls)


async def test_client_credentials_are_fetched_once_and_refreshed_after_a_401(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        auth="oauth_client_credentials",
        client_id="c",
        client_secret="client-secret-value",
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    assert (await client.put(URL, json=_oauth())).status_code == 200
    assert (await _test(client))["ok"] is True
    assert (await _test(client))["ok"] is True
    assert len(fake.token_requests) == 1, "the second test reuses the cached token"
    assert fake.token_requests[0]["resource"] == "https://gw.example.test"
    fake.revoke_tokens()
    assert (await _test(client))["ok"] is True
    assert len(fake.token_requests) == 2, "one more token request after the 401"
    assert "client-secret-value" not in await _all_audit_text()


async def test_a_new_secret_drops_the_cached_token(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        auth="oauth_client_credentials",
        client_id="c",
        client_secret="client-secret-value",
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    assert (await client.put(URL, json=_oauth())).status_code == 200
    assert (await _test(client))["ok"] is True
    assert (await client.put(URL, json=_oauth(timeout_s=20))).status_code == 200
    assert (await _test(client))["ok"] is True
    assert len(fake.token_requests) == 2, "a write clears the token cache"


async def test_a_jwt_gateway_receives_a_verifiable_assertion(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _body(auth={"type": "jwt_assertion"}, secret=None, audience="gw")
    done = await client.put(URL, json=body)
    assert done.status_code == 200, done.text
    assert done.json()["gateway"]["secret_set"] is False
    jwks = (await client.get(f"{URL}/jwks.json")).json()
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        auth="jwt_assertion",
        jwks=jwks,
        audience="gw",
        issuer=done.json()["assertion"]["issuer"],
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    assert (await _test(client))["ok"] is True
    claims = fake.calls[-1].claims
    assert claims is not None
    assert claims["org"] == "varga" and claims["purpose"] == "test"
    assert claims["sub"] == "person:admin@varga.dev" and claims["server"] == "supplier"


async def test_an_identity_header_carries_a_second_signed_assertion(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _body(audience="gw", identity_header="X-Identity")
    done = await client.put(URL, json=body)
    assert done.status_code == 200, done.text
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        token=SECRET,
        jwks=(await client.get(f"{URL}/jwks.json")).json(),
        audience="gw",
        issuer=done.json()["assertion"]["issuer"],
        identity_header="X-Identity",
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    assert (await _test(client))["ok"] is True
    claims = fake.calls[-1].identity_header_claims
    assert claims is not None and claims["sub"] == "person:admin@varga.dev"
    assert fake.calls[-1].headers["authorization"] == f"Bearer {SECRET}"


async def test_a_failing_gateway_is_ok_false_not_an_http_error(
    client: AsyncClient, admin: str, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(upstreams={"supplier": GatewayUpstream(server=supplier)}, token="another-token")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    assert (await client.put(URL, json=_body())).status_code == 200
    result = await _test(client)
    assert result["ok"] is False and result["tools"] is None
    assert "401" in result["error"] and "varga" in result["error"]
    assert SECRET not in json.dumps(result)
    assert (await _audit("mcp_gateway.test"))[0]["ok"] is False


async def test_a_lost_credential_is_ok_false_naming_the_remedy(
    client: AsyncClient, admin: str, gateway: FakeMcpGateway, isolated: FakeSecretStore
) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    isolated.values.clear()
    result = await _test(client)
    assert result["ok"] is False and "credential" in result["error"]
    assert gateway.calls == [], "nothing left without its credential"


async def test_the_test_is_made_inside_the_organisations_egress_scope(
    client: AsyncClient, admin: str, enforce: None, gateway: FakeMcpGateway
) -> None:
    assert (await client.put(URL, json=_body(allow_egress=True))).status_code == 200
    assert (await _test(client))["ok"] is True
    rule = next(r for r in await rules_in_db("allowed") if r.host == "gw.example.test")
    assert (await client.delete(f"{ORG}/egress/rules/{rule.id}")).status_code == 200
    reached = len(gateway.calls)
    refused = await _test(client)
    assert refused["ok"] is False and "egress refused" in refused["error"], refused
    assert len(gateway.calls) == reached, "the refused call never left"


async def test_the_token_request_is_checked_against_the_egress_list(
    client: AsyncClient, admin: str, enforce: None, supplier: FakeMcpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMcpGateway(
        upstreams={"supplier": GatewayUpstream(server=supplier)},
        auth="oauth_client_credentials",
        client_id="c",
        client_secret="client-secret-value",
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    body = _oauth(token_url="https://auth.example.test/oauth/token", allow_egress=True)
    assert (await client.put(URL, json=body)).status_code == 200
    rule = next(r for r in await rules_in_db("allowed") if r.host == "auth.example.test")
    assert (await client.delete(f"{ORG}/egress/rules/{rule.id}")).status_code == 200
    refused = await _test(client)
    assert refused["ok"] is False and "egress refused" in refused["error"], refused
    assert fake.token_requests == [], "the token request never left"


async def test_delete_is_refused_while_gateway_connectors_exist(
    client: AsyncClient, admin: str, gateway: FakeMcpGateway, isolated: FakeSecretStore
) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    created = await client.post(
        f"{ORG}/connectors", json={"name": "gh", "type": "mcp-gateway", "config": {"server": "github"}}
    )
    assert created.status_code == 201, created.text
    refused = await client.delete(URL)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "gateway_in_use" and refused.json()["connectors"] == ["gh"]
    assert isolated.values, "the credential is still there"
    assert (await client.delete(f"{ORG}/connectors/gh")).status_code in {200, 204}
    assert (await client.delete(URL)).status_code == 204
    assert isolated.values == {}, "the sealed credential is gone"
    assert (await client.get(URL)).json()["gateway"] is None
    assert (await client.delete(URL)).status_code == 204, "idempotent"
    assert (await _audit("mcp_gateway.delete"))[0]["endpoint"] == ENDPOINT


async def test_delete_keeps_the_assertion_keys(client: AsyncClient, admin: str) -> None:
    body = _body(auth={"type": "jwt_assertion"}, secret=None, audience="gw")
    assert (await client.put(URL, json=body)).status_code == 200
    assert (await client.delete(URL)).status_code == 204
    assert len((await client.get(URL)).json()["assertion"]["keys"]) == 1


async def test_a_new_credential_replaces_the_old_and_jwt_drops_it(
    client: AsyncClient, admin: str, isolated: FakeSecretStore
) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    assert (await client.put(URL, json=_body(secret="second-" + SECRET))).status_code == 200
    assert [v[0] for v in isolated.values.values()] == ["second-" + SECRET]
    jwt_body = _body(auth={"type": "jwt_assertion"}, secret=None, audience="gw")
    assert (await client.put(URL, json=jwt_body)).status_code == 200
    credentials = [k for k in isolated.values if k[1] == "org/mcp-gateway/credential"]
    assert credentials == [], "a signed gateway holds no credential"


async def test_only_the_org_admin_reads_or_writes_it(client: AsyncClient, admin: str) -> None:
    assert (await client.put(URL, json=_body())).status_code == 200
    await login(client, "someone@elsewhere.dev")
    assert (await client.get(URL)).status_code == 403
    assert (await client.put(URL, json=_body())).status_code == 403
    assert (await client.delete(URL)).status_code == 403
    assert (await client.post(f"{URL}/test", json={"server": "supplier"})).status_code == 403
