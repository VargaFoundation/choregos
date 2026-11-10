"""Connected accounts and the `user_consent` identity (S63-08, AS-08, ADR 0135 points 1, 3, 4, 6).

A connector that declares `user_consent` (and the source's OAuth settings) is called, from a conversation
whose agent version or whose binding picks it, with an access token drawn from the PERSON's refresh token,
sealed after a consent (authorization code + PKCE) at the source's own authorization server (a fake). With
no account the call fails with `account_not_connected` and the console's connect URL: nothing reaches the
source, under any identity.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from choregos_adapters.fakes.oauth_provider import AUTHORIZE, REVOKE, TOKEN, FakeOAuthProvider
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, without_postgres
from .kits.assistant_install import _bind, _install, _member, _plugin, offline  # noqa: F401
from .test_agent_sessions import (  # noqa: F401 - fixtures imported for their use here
    ORG,
    Upstream,
    _agent,
    _ask,
    upstream,
)
from .test_assistant_rights import ME, OPEN, _as
from .test_user_exchange import SUBJECT, _calls, _person, _tool_events, source  # noqa: F401

CLIENT_SECRET = "oauth-client-secret"
PROVIDER_USER = "person-at-the-source"


@pytest.fixture
def sealed_store() -> Iterator[None]:
    """The real sealed store (AES-GCM envelopes in `org_secrets`) in place of the in-memory fake."""
    import base64

    from choregos_api.secret_store import SealedDbSecretStore, parse_keyring, set_secret_store

    ring = parse_keyring("k1:" + base64.b64encode(b"k" * 32).decode())
    assert ring is not None
    set_secret_store(SealedDbSecretStore(ring))
    yield
    set_secret_store(None)


@pytest.fixture
async def provider(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeOAuthProvider]:
    from choregos_api.services.connected_accounts import set_consent_transport

    fake = FakeOAuthProvider(client_secret=CLIENT_SECRET)
    set_consent_transport(fake.transport())
    monkeypatch.setenv("CONNECTOR_TEST_OAUTH_SECRET", CLIENT_SECRET)
    yield fake
    set_consent_transport(None)


def _consent_config(**extra: Any) -> dict[str, Any]:
    return {
        "url": "https://fournisseur.test/mcp",
        "identity_modes": ["service", "user_consent"],
        "user_consent": {
            "authorization_endpoint": AUTHORIZE,
            "token_endpoint": TOKEN,
            "revocation_endpoint": REVOKE,
            "client_id": "choregos-client",
            "client_secret_ref": "env:CONNECTOR_TEST_OAUTH_SECRET",
            "scopes": ["read", "offline_access"],
            **extra,
        },
    }


async def _declare(client: AsyncClient, config: dict[str, Any] | None = None) -> None:
    body = {"name": "fournisseur", "type": "mcp", "config": config or _consent_config(),
            "secret_refs": {"token": "env:CONNECTOR_TEST_SUPPLIER_KEY"}}  # fmt: skip
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text
    assert (await client.post(f"{ORG}/connectors/fournisseur/discover")).status_code == 200
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/suivi_commande", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text


async def _consent(
    client: AsyncClient, provider: FakeOAuthProvider, seen: list[str], connector: str = "fournisseur"
) -> None:
    """The whole journey of the person: connect, consent at the source, the callback."""
    started = await client.post(f"/api/v1/me/connected-accounts/{connector}/connect", params={"org": "varga"})
    assert started.status_code == 200, started.text
    seen.append(started.text)
    redirect = provider.authorize(started.json()["authorization_url"], PROVIDER_USER)
    parsed = urlparse(redirect)
    done = await client.get(f"{parsed.path}?{parsed.query}")
    seen.append(done.text)
    assert done.status_code == 303, done.text


async def _converse(client: AsyncClient, upstream: Upstream) -> dict[str, Any]:
    await _agent(
        client,
        mcp_servers=[{"connector": "fournisseur", "tools": ["suivi_*"], "identity": "user_consent"}],
    )
    upstream.model.call("fournisseur__suivi_commande", {"numero": "42"}).say("Done.")
    answer = (await _ask(client, "Where is order 42?")).json()
    assert answer["status"] == "completed", answer
    return answer  # type: ignore[no-any-return]


def _told(upstream: Upstream) -> list[str]:
    (_, last) = upstream.model.requests[-1]
    return [m["content"] for m in last["messages"] if m["role"] == "tool"]


async def test_a_simulated_oauth_flow_ends_with_a_tool_call_that_carries_the_persons_token(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    provider: FakeOAuthProvider,
    sealed_store: None,
) -> None:
    from choregos_api.db.models import ConnectedAccount, OrgSecret
    from choregos_api.db.session import ALL_ORGS, session_scope

    await _declare(client)
    seen: list[str] = []
    await _consent(client, provider, seen)

    # PKCE S256 and the client secret reached the source's authorization server.
    ((path, form),) = provider.requests
    assert path == "/token" and form["grant_type"] == "authorization_code"
    assert form["code_verifier"] and form["client_secret"] == CLIENT_SECRET

    listing = await client.get("/api/v1/me/connected-accounts")
    seen.append(listing.text)
    (account,) = listing.json()["items"]
    assert account["connector"] == "fournisseur" and account["subject"] == PROVIDER_USER
    assert account["scopes"] == ["read", "offline_access"] and account["status"] == "connected"

    source.received.clear()
    answer = await _converse(client, upstream)
    (headers,) = _calls(source)
    bearer = headers["authorization"].removeprefix("Bearer ")
    assert bearer in provider.access_tokens and provider.access_tokens[bearer] == PROVIDER_USER
    assert bearer != "supplier-key", "the connector's own key is not what the source received"
    (event,) = await _tool_events(answer["session_id"])
    assert event["as"] == f"person:{SUBJECT}" and event["identity_mode"] == "user_consent"

    # The token is sealed: no column holds it, no route returned it, the store holds ciphertext.
    (refresh,) = provider.refresh_tokens
    assert all(refresh not in text and bearer not in text for text in seen), "a route returned a token"
    async with session_scope(orgs=ALL_ORGS) as session:
        row = (await session.execute(select(ConnectedAccount))).scalar_one()
        assert refresh not in json.dumps([row.secret_ref, row.scopes, row.subject])
        assert row.secret_ref.startswith("store:user/") and row.last_used_at is not None
        sealed = (
            await session.execute(select(OrgSecret).where(OrgSecret.name == row.secret_ref[6:]))
        ).scalar_one()
        assert refresh.encode() not in bytes(sealed.ciphertext)


async def test_disconnecting_revokes_at_the_source_and_the_next_call_has_no_account(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    provider: FakeOAuthProvider,
    sealed_store: None,
) -> None:
    from choregos_api.db.models import ConnectedAccount, OrgSecret
    from choregos_api.db.session import ALL_ORGS, session_scope

    await _declare(client)
    await _consent(client, provider, [])
    (refresh,) = provider.refresh_tokens

    gone = await client.delete("/api/v1/me/connected-accounts/fournisseur", params={"org": "varga"})
    assert gone.status_code == 204
    assert provider.revoked == [refresh] and provider.refresh_tokens == {}, "revoked at the source"
    async with session_scope(orgs=ALL_ORGS) as session:
        assert (await session.execute(select(ConnectedAccount))).first() is None
        assert not [s for s in (await session.execute(select(OrgSecret.name))).scalars() if "consent" in s]
    assert (await client.get("/api/v1/me/connected-accounts")).json() == {"items": []}

    source.received.clear()
    await _converse(client, upstream)
    assert _calls(source) == [], "revocation is immediate: the source received no call"
    assert any("account_not_connected" in text for text in _told(upstream))


async def test_without_an_account_the_error_carries_the_consent_url_and_nothing_is_called(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    provider: FakeOAuthProvider,
) -> None:
    from choregos_api.config import get_settings

    await _declare(client)
    source.received.clear()
    answer = await _converse(client, upstream)

    assert _calls(source) == [], "no fallback to the connector's own identity"
    (text,) = [t for t in _told(upstream) if "account_not_connected" in t]
    url = f"{get_settings().public_url.rstrip('/')}/me/connected-accounts/fournisseur/connect?org=varga"
    assert url in text, text
    (event,) = await _tool_events(answer["session_id"])
    assert event["status_code"] == 422 and event["identity_mode"] == "user_consent"
    assert provider.requests == []


async def test_a_refresh_token_the_source_rotates_is_sealed_again(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    provider: FakeOAuthProvider,
) -> None:
    await _declare(client)
    await _consent(client, provider, [])
    provider.rotate = True
    (first,) = provider.refresh_tokens
    await _converse(client, upstream)
    (second,) = provider.refresh_tokens
    assert second != first, "the source rotated the refresh token"
    upstream.model.call("fournisseur__suivi_commande", {"numero": "43"}).say("Again.")
    assert (await _ask(client, "And order 43?")).json()["status"] == "completed"
    assert len(_calls(source)) == 2, "the new refresh token was sealed: the second call drew from it"
    (third,) = provider.refresh_tokens
    assert third != second


async def test_a_source_that_refuses_the_refresh_token_asks_the_person_to_reconnect(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    provider: FakeOAuthProvider,
) -> None:
    await _declare(client)
    await _consent(client, provider, [])
    provider.refresh_tokens.clear()  # the person withdrew the consent at the source
    source.received.clear()
    await _converse(client, upstream)
    assert _calls(source) == []
    assert any("account_not_connected" in text for text in _told(upstream))
    (account,) = (await client.get("/api/v1/me/connected-accounts")).json()["items"]
    assert account["status"] == "reconnect"


async def test_a_callback_with_a_forged_state_or_no_handshake_is_refused(
    client: AsyncClient, project: dict[str, Any], provider: FakeOAuthProvider, source: Any
) -> None:
    await _declare(client)
    started = await client.post("/api/v1/me/connected-accounts/fournisseur/connect", params={"org": "varga"})
    url = started.json()["authorization_url"]
    redirect = urlparse(provider.authorize(url))
    forged = await client.get(f"{redirect.path}?code=x&state=forged.state")
    assert forged.status_code == 422
    client.cookies.clear()  # the handshake cookie that holds the PKCE verifier
    await login(client, "admin@varga.dev")
    lost = await client.get(f"{redirect.path}?{redirect.query}")
    assert lost.status_code == 422 and provider.requests == [], "the code was never exchanged"


async def test_a_connector_that_does_not_declare_the_mode_cannot_be_connected(
    client: AsyncClient, project: dict[str, Any], provider: FakeOAuthProvider, source: Any
) -> None:
    await _declare(client, {"url": "https://fournisseur.test/mcp", "identity_modes": ["service"]})
    started = await client.post("/api/v1/me/connected-accounts/fournisseur/connect", params={"org": "varga"})
    assert started.status_code == 422
    unknown = await client.post("/api/v1/me/connected-accounts/nothing/connect", params={"org": "varga"})
    assert unknown.status_code == 404


async def test_a_plugin_server_receives_the_persons_token_next_to_the_platforms(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Organization, OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.plugin_servers import (
        SERVER_TOKEN_FIELD,
        connector_client,
        use_plugin_transport,
    )

    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body["method"] == "tools/call":
            result: dict[str, Any] = {"content": [{"type": "text", "text": "hi"}]}
        elif body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "s"}}
        else:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setenv("CONNECTOR_TEST_SERVER_TOKEN", "platform-token")
    use_plugin_transport(httpx.MockTransport(handle))
    try:
        async with session_scope() as session:
            org_id = (await session.execute(select(Organization.id))).scalar_one()
            instance = OrgConnector(
                org_id=org_id,
                name="srv",
                kind="mcp",
                type="mcp",
                config=_consent_config(),
                secret_refs={SERVER_TOKEN_FIELD: "env:CONNECTOR_TEST_SERVER_TOKEN"},
                plugin_server={"url": "http://srv.test/mcp", "plugin": "p", "operations": {}},
            )
            session.add(instance)
            await session.flush()
            plugin = await connector_client(session, instance, user_token="the-persons-token")
            await plugin.call_tool("op", {})
    finally:
        use_plugin_transport(None)
    calls = [r for r in seen if json.loads(r.content)["method"] == "tools/call"]
    (call,) = calls
    assert call.headers["authorization"] == "Bearer platform-token"
    assert call.headers["x-choregos-user-token"] == "the-persons-token"


# ───────────────────────── a need bound with `user_consent` ─────────────────────────


async def test_a_need_bound_for_user_consent_calls_under_the_persons_connected_account(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    upstream: Upstream,
    provider: FakeOAuthProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BINDING's identity decides (the agent's entry names a need): `brokerage` is `user_consent` in the
    manifest, so its tool goes out under the person's account, or answers `account_not_connected`."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    from .test_assistant_genericity import AUDIENCE, _served
    from .test_assistant_genericity import TOOLS as FEED

    tools = [dict(t) for t in FEED] + [
        {
            "name": "list_positions",
            "description": "p",
            "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object"},
        },
    ]
    server = FakeMcpServer(tools=tools, token="")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_FEED_KEY", "feed-key")

    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _plugin(client)
    for name in ("news-feed", "markets"):
        await _served(client, name)
    broker = {"name": "broker", "type": "mcp", "config": _consent_config(),
              "secret_refs": {"token": "env:CONNECTOR_TEST_FEED_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=broker)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/broker/discover")).status_code == 200
    opened = await client.patch(
        f"{ORG}/connectors/broker/operations/list_positions", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    assert (await _install(client, audience_groups=[AUDIENCE])).status_code == 201
    await _bind(client, "news", "news-feed")
    assert (await _bind(client, "market_data", "markets")).json()["status"] == "ready"
    bound = await _bind(client, "brokerage", "broker")
    assert bound.status_code == 200, bound.text

    reader = await _as(app, "reader@varga.dev")
    conversation = (await reader.post(OPEN, json={})).json()["id"]
    server.received.clear()
    upstream.model.call("brokerage__list_positions", {}).say("No account.")
    turn = await reader.post(f"{ME}/conversations/{conversation}/messages", json={"input": "My positions?"})
    assert turn.status_code == 200, turn.text
    assert [h for m, h, _ in server.received if m == "tools/call"] == [], "no account: no call"
    assert any("account_not_connected" in t for t in _told(upstream))

    await _consent(reader, provider, [], "broker")
    upstream.model.call("brokerage__list_positions", {}).say("Here they are.")
    again = await reader.post(f"{ME}/conversations/{conversation}/messages", json={"input": "Again?"})
    assert again.status_code == 200, again.text
    (headers,) = [h for m, h, _ in server.received if m == "tools/call"]
    bearer = headers["authorization"].removeprefix("Bearer ")
    assert provider.access_tokens[bearer] == PROVIDER_USER, "the person's own token, not the connector's key"


# ───────────────────────── the catalogue ─────────────────────────


async def test_the_catalogue_no_longer_opposes_needs_oauth_to_a_user_consent_server(
    client: AsyncClient, admin: str
) -> None:
    base = "/api/v1/orgs/varga/mcp-catalogue"
    refused = await client.post(f"{base}/notion/install", json={})
    assert refused.status_code == 422 and "OAuth" in refused.text, "without consent the refusal stands"

    consent = {
        "authorization_endpoint": AUTHORIZE,
        "token_endpoint": TOKEN,
        "client_id": "choregos-client",
        "client_secret": CLIENT_SECRET,
        "scopes": ["read"],
    }
    installed = await client.post(f"{base}/notion/install", json={"user_consent": consent})
    assert installed.status_code == 201, installed.text
    assert CLIENT_SECRET not in installed.text, "the client secret is sealed, never returned"
    config = installed.json()["config"]
    assert config["identity_modes"] == ["user_consent"]
    assert config["user_consent"]["client_secret_ref"].startswith("store:connector/notion/")
    assert "client_secret" not in config["user_consent"]
    hosting = await client.post(f"{base}/playwright/install", json={"user_consent": consent})
    assert hosting.status_code == 422, "a local process is still the operator's decision"
    gateway = await client.post(f"{base}/notion/install", json={"user_consent": consent, "route": "gateway"})
    assert gateway.status_code == 422


# ───────────────────────── PostgreSQL: RLS ─────────────────────────


async def test_the_table_is_under_forced_rls_with_an_owner_only_policy(client: AsyncClient) -> None:
    """The migration declares both policies (checked in the metadata of the models on any database)."""
    from choregos_api.db.models import ConnectedAccount

    assert ConnectedAccount.__tablename__ == "connected_accounts"
    migration = Path(__file__).parent.parent / "src/choregos_api/migrations/versions"
    text = next(migration.glob("*connected_accounts.py")).read_text()
    assert "FORCE ROW LEVEL SECURITY" in text and "AS RESTRICTIVE" in text
    assert "user_id = choregos_utilisateur_courant()" in text


@pytest.fixture
async def organisation_a(pg_app: Any) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        organisation = Organization(slug="a", name="A")
        session.add(organisation)
        await session.flush()
        return organisation.id


@without_postgres
async def test_another_person_never_reads_someones_connected_account(
    pg_app: Any, organisation_a: str
) -> None:
    from choregos_api.db.models import ConnectedAccount, User
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope
    from sqlalchemy.exc import DBAPIError

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@a.test")  # org_admin of `a`
        await login(http, "dev@a.test")
    async with session_scope(orgs="*") as session:
        users = {
            u.email: u.id
            for u in (await session.execute(select(User))).scalars()
            if u.email.endswith("@a.test")
        }
        session.add(
            ConnectedAccount(
                org_id=organisation_a,
                user_id=users["dev@a.test"],
                connector="fournisseur",
                secret_ref="store:user/x/consent/fournisseur",
                scopes=["read"],
            )
        )
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, users["admin@a.test"])
        assert (await session.execute(select(ConnectedAccount))).all() == [], "not even an administrator"
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, users["dev@a.test"])
        assert len((await session.execute(select(ConnectedAccount))).all()) == 1
    async with session_scope() as session:  # no user named, no scope: nothing
        await limit_to_organisations(session, ["a"])
        assert (await session.execute(select(ConnectedAccount))).all() == []
    with pytest.raises(DBAPIError):  # nor writes one in someone else's name
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["admin@a.test"])
            session.add(
                ConnectedAccount(
                    org_id=organisation_a,
                    user_id=users["dev@a.test"],
                    connector="other",
                    secret_ref="store:x",
                    scopes=[],
                )
            )
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(ConnectedAccount))).all()) == 1, "the platform scope reads"


@pytest.fixture(autouse=True)
def _fresh_catalogue_cache() -> Iterator[None]:
    from choregos_core import mcp_catalogue

    mcp_catalogue.load_with_site.cache_clear()
    yield
    mcp_catalogue.load_with_site.cache_clear()
