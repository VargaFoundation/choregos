# SPDX-License-Identifier: Apache-2.0
"""An MCP connector signed in with OAuth client credentials, end to end through the API (S70-01).

An enterprise MCP server that only accepts short-lived tokens is declared as a direct `mcp` connector
with `auth_scheme: oauth_client_credentials`: a token endpoint, a client id and a sealed client secret.
The broker fetches the token and holds it; the run never sees it. The fakes are the scriptable
`FakeOAuthProvider` (the token endpoint) and a `FakeMcpServer` that takes only the tokens that provider
issued and has not expired. Each test fails without the guard it names.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.oauth_provider import FakeOAuthProvider
from choregos_adapters.mcp import CONNECTOR_TOKENS
from httpx import AsyncClient

from .egress_support import rules_in_db
from .test_broker import TOOLS

ORG = "/api/v1/orgs/varga"
SECRET = "client-secret-of-the-reader"
CLIENT_ID = "choregos-reader"
CONFIG: dict[str, Any] = {
    "url": "https://data.example.test/mcp",
    "auth_scheme": "oauth_client_credentials",
    "token_url": "https://auth.example.test/oauth/token",
    "client_id": CLIENT_ID,
    "scope": "mcp:read",
}
RUN = "run-client-credentials-1"


@pytest.fixture(autouse=True)
def sealed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's key: a typed client secret is sealed with it (ADR 0058)."""
    from choregos_core.redaction import forget_secrets

    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k1:{base64.b64encode(os.urandom(32)).decode()}")
    forget_secrets()


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOAuthProvider]:
    fake = FakeOAuthProvider(client_id=CLIENT_ID, client_secret=SECRET)
    monkeypatch.setattr(choregos_adapters, "FAKE_OAUTH", fake)
    CONNECTOR_TOKENS.clear()
    yield fake
    CONNECTOR_TOKENS.clear()


@pytest.fixture
def server(provider: FakeOAuthProvider, monkeypatch: pytest.MonkeyPatch) -> FakeMcpServer:
    fake = FakeMcpServer(tools=[dict(t) for t in TOOLS], accepts=provider.accepts)
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", fake)
    return fake


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


def _declare(secret: str = SECRET, **over: Any) -> dict[str, Any]:
    return {
        "name": "data",
        "type": "mcp",
        "config": dict(CONFIG),
        "secret_values": {"client_secret": secret},
        "allow_egress": True,
        **over,
    }


async def _audit_text() -> str:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return json.dumps([(r.action, r.payload) for r in rows], default=str)


async def _run(client: AsyncClient, project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """The connector declared and discovered, its reads open, an agent that names it, a run of it."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    declared = await client.post(f"{ORG}/connectors", json=_declare())
    assert declared.status_code == 201, declared.text
    assert (await client.post(f"{ORG}/connectors/data/discover")).status_code == 200
    for operation in ("suivi_commande", "stock"):
        opened = await client.patch(
            f"{ORG}/connectors/data/operations/{operation}", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text
    spec = {"mcp_servers": [{"connector": "data", "tools": ["*"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "reader", "display_name": "Reader", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#7", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="reader",
                agent_version=1,
            )
        )
    token = mint_run_token(RUN, project_slug="billing-api", work_item_key="varga/x#7", ttl_minutes=30)
    return f"/api/v1/internal/runs/{RUN}/tools", {"Authorization": f"Bearer {token}"}


async def test_declaring_it_opens_the_token_endpoint_with_the_server(
    client: AsyncClient, admin: str, enforce: None, server: FakeMcpServer
) -> None:
    refused = await client.post(f"{ORG}/connectors", json=_declare(allow_egress=False))
    assert refused.status_code == 409, refused.text
    assert {v["host"] for v in refused.json()["verdicts"]} == {"data.example.test", "auth.example.test"}
    created = await client.post(f"{ORG}/connectors", json=_declare())
    assert created.status_code == 201, created.text
    assert created.json()["secret_refs"] == {"client_secret": "store:connector/data/client_secret"}
    assert {"data.example.test", "auth.example.test"} <= {r.host for r in await rules_in_db("allowed")}
    assert SECRET not in created.text and SECRET not in (await client.get(f"{ORG}/connectors/data")).text


async def test_discovery_fetches_one_token_and_the_secret_is_nowhere(
    client: AsyncClient, admin: str, provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    assert (await client.post(f"{ORG}/connectors", json=_declare())).status_code == 201
    first = await client.post(f"{ORG}/connectors/data/discover")
    assert first.status_code == 200, first.text
    assert set(first.json()["added"]) == {t["name"] for t in TOOLS}
    assert (await client.post(f"{ORG}/connectors/data/discover")).status_code == 200
    (grant,) = provider.client_grants()
    assert grant["scope"] == "mcp:read", "one token for both discoveries, with the scope asked for"
    (token,) = provider.client_tokens
    assert {h["authorization"] for _, h, _ in server.received} == {f"Bearer {token}"}
    audit = await _audit_text()
    assert SECRET not in audit and token not in audit


async def test_the_broker_holds_the_token_and_the_run_never_sees_it(
    client: AsyncClient, project: dict[str, Any], provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    base, headers = await _run(client, project)
    listing = await client.get(base, headers=headers)
    answer = await client.post(f"{base}/data__suivi_commande", headers=headers, json={"numero": "42"})
    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert server.calls == [("suivi_commande", {"numero": "42"})]
    (token,) = provider.client_tokens
    run = (await client.get(f"/api/v1/runs/{RUN}")).json()
    assert run["tool_connectors"] == [
        {"name": "data", "type": "mcp", "auth_scheme": "oauth_client_credentials", "account": CLIENT_ID}
    ]
    for seen_by_the_run in (listing.text, answer.text, json.dumps(run)):
        assert token not in seen_by_the_run and SECRET not in seen_by_the_run
    run_token = headers["Authorization"].removeprefix("Bearer ")
    assert all(run_token not in str(h) for _, h, _ in server.received), "the run's token never leaves"


async def test_an_expired_token_is_renewed_after_the_401_and_the_call_goes_through(
    client: AsyncClient, project: dict[str, Any], provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    base, headers = await _run(client, project)
    assert len(provider.client_grants()) == 1
    provider.expire_client_tokens()  # five minutes later: the provider no longer takes the cached token
    answer = await client.post(f"{base}/data__stock", headers=headers, json={})
    assert answer.json()["status_code"] == 200, answer.text
    assert len(provider.client_grants()) == 2, "one new token, fetched after the server's 401"
    assert server.calls == [("stock", {})]


async def test_a_wrong_secret_is_a_named_error_that_carries_no_secret(
    client: AsyncClient, admin: str, provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    assert (await client.post(f"{ORG}/connectors", json=_declare(secret="a-wrong-secret"))).status_code == 201
    refused = await client.post(f"{ORG}/connectors/data/discover")
    assert refused.status_code == 502, refused.text
    for named in ("auth.example.test", CLIENT_ID, "invalid_client"):
        assert named in refused.text, named
    connector = (await client.get(f"{ORG}/connectors/data")).json()
    assert connector["status"] == "error" and "invalid_client" in connector["last_error"]
    for text in (refused.text, json.dumps(connector), await _audit_text()):
        assert "a-wrong-secret" not in text
    assert server.received == [], "nothing reached the server without a token"


async def test_a_token_endpoint_the_organisation_has_not_opened_is_refused(
    client: AsyncClient, admin: str, enforce: None, provider: FakeOAuthProvider, server: FakeMcpServer
) -> None:
    assert (await client.post(f"{ORG}/connectors", json=_declare())).status_code == 201
    rule = next(r for r in await rules_in_db("allowed") if r.host == "auth.example.test")
    assert (await client.delete(f"{ORG}/egress/rules/{rule.id}")).status_code == 200
    refused = await client.post(f"{ORG}/connectors/data/discover")
    assert refused.status_code == 502, refused.text
    assert "auth.example.test" in refused.text and "egress" in refused.text
    assert provider.requests == [], "the token request never left"
    assert server.received == [], "nor did the call it was for"


async def test_the_network_ceiling_bounds_the_token_endpoint_at_declaration(
    client: AsyncClient, admin: str, server: FakeMcpServer
) -> None:
    deny = {"ceiling": {"network": {"deny_domains": ["auth.example.test"]}}}
    assert (await client.put(f"{ORG}/policy-ceiling", json=deny)).status_code == 200
    refused = await client.post(f"{ORG}/connectors", json=_declare())
    assert refused.status_code == 422 and "auth.example.test" in refused.text, refused.text
    assert (await client.get(f"{ORG}/connectors/data")).status_code == 404


@pytest.mark.parametrize(
    "config",
    [
        {**CONFIG, "client_id": ""},
        {key: value for key, value in CONFIG.items() if key != "token_url"},
        {"url": "https://data.example.test/mcp", "token_url": "https://auth.example.test/oauth/token"},
    ],
    ids=["no client id", "no token endpoint", "a token endpoint under bearer"],
)
async def test_an_incoherent_configuration_is_422_before_anything_is_written(
    client: AsyncClient, admin: str, config: dict[str, Any]
) -> None:
    refused = await client.post(f"{ORG}/connectors", json=_declare(config=config))
    assert refused.status_code == 422, refused.text
    assert SECRET not in refused.text
    assert (await client.get(f"{ORG}/connectors/data")).status_code == 404
