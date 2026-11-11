"""A conversation's tool call under the person's identity (S63-07, AS-07, ADR 0135).

A connector that declares `user_exchange` is called, from a conversation whose agent version picks it,
with a token the identity provider exchanged (RFC 8693) for the person and the connector's audience.
Without a token of the person it fails with `identity_unavailable` and nothing reaches the source:
there is no fallback to the connector's own key.
"""

# ruff: noqa: F811 - a fixture imported from a kit is redefined by the parameter that names it

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.fakes.identity_provider import FakeIdentityProvider
from choregos_adapters.token_exchange import claims_of
from httpx import AsyncClient

from .kits.user_exchange import (  # noqa: F401
    AUDIENCE,
    EMAIL,
    REFRESH,
    SUBJECT,
    _calls,
    _declare,
    _person,
    _sign_in_sealed,
    _tool_events,
    idp,
    source,
)
from .test_agent_sessions import (  # noqa: F401 - fixtures imported for their use here
    ORG,
    Upstream,
    _agent,
    _ask,
    upstream,
)


async def _converse(
    client: AsyncClient,
    upstream: Upstream,
    identity: str | None,
) -> dict[str, Any]:
    server: dict[str, Any] = {"connector": "fournisseur", "tools": ["suivi_*"]}
    if identity:
        server["identity"] = identity
    await _agent(client, mcp_servers=[server])
    upstream.model.call("fournisseur__suivi_commande", {"numero": "42"}).say("Done.")
    answer = (await _ask(client, "Where is order 42?")).json()
    assert answer["status"] == "completed", answer
    return answer  # type: ignore[no-any-return]


async def test_the_source_receives_a_token_of_the_person_for_its_audience(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client)
    await _sign_in_sealed()
    answer = await _converse(client, upstream, "user_exchange")

    (headers,) = _calls(source)
    bearer = headers["authorization"].removeprefix("Bearer ")
    assert bearer != "supplier-key", "the connector's own key is not what the source received"
    claims = claims_of(bearer)
    assert claims["sub"] == SUBJECT and claims["aud"] == AUDIENCE
    assert claims["act"] == {"sub": "assistant:helper"}, "the assistant is named as the actor"
    exchanges = [f for f in idp.requests if f["grant_type"].endswith("token-exchange")]
    assert len(exchanges) == 1 and exchanges[0]["requested_token_type"].endswith("access_token")
    (event,) = await _tool_events(answer["session_id"])
    assert event["as"] == f"person:{SUBJECT}" and event["identity_mode"] == "user_exchange"
    assert bearer not in str(event), "the token is not journaled"


async def test_each_call_exchanges_anew_and_the_token_is_not_kept(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client)
    await _sign_in_sealed()
    server = {"connector": "fournisseur", "tools": ["suivi_*"], "identity": "user_exchange"}
    await _agent(client, mcp_servers=[server])
    upstream.model.call("fournisseur__suivi_commande", {"numero": "1"}).call(
        "fournisseur__suivi_commande", {"numero": "2"}
    ).say("Done.")
    assert (await _ask(client, "Two orders")).json()["status"] == "completed"
    first, second = (h["authorization"] for h in _calls(source))
    assert first != second, "one exchange per call"
    assert len([f for f in idp.requests if f["grant_type"].endswith("token-exchange")]) == 2


async def test_without_a_token_of_the_person_nothing_is_called_under_the_connectors_identity(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client)
    source.received.clear()  # discovery used the connector's key; the conversation must use no key at all
    answer = await _converse(client, upstream, "user_exchange")  # no refresh token was sealed

    assert _calls(source) == [], "the source received no tool call, under any identity"
    assert [f for f in idp.requests if f["grant_type"].endswith("token-exchange")] == []
    (_, last) = upstream.model.requests[-1]
    told = [m["content"] for m in last["messages"] if m["role"] == "tool"]
    assert any("identity_unavailable" in text for text in told), told
    (event,) = await _tool_events(answer["session_id"])
    assert event["status_code"] == 422 and event["as"] == f"person:{SUBJECT}"


async def test_a_provider_that_refuses_the_audience_is_an_unavailable_identity_not_a_fallback(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client)
    await _sign_in_sealed()
    idp.allowed_audiences = {"another-source"}
    source.received.clear()
    await _converse(client, upstream, "user_exchange")
    assert _calls(source) == []
    (_, last) = upstream.model.requests[-1]
    assert any("identity_unavailable" in m["content"] for m in last["messages"] if m["role"] == "tool")


async def test_a_connector_that_does_not_declare_the_mode_is_never_called_in_it(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client, modes=["service"])
    await _sign_in_sealed()
    source.received.clear()
    await _converse(client, upstream, "user_exchange")
    assert _calls(source) == []
    assert idp.requests == [], "no exchange towards a source that did not ask for one"


async def test_an_agent_version_that_says_nothing_keeps_the_connectors_identity(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    await _declare(client)
    await _sign_in_sealed()
    answer = await _converse(client, upstream, None)
    (headers,) = _calls(source)
    assert headers["authorization"] == "Bearer supplier-key"
    assert idp.requests == []
    (event,) = await _tool_events(answer["session_id"])
    assert event["as"] == "assigned:fournisseur" and event["identity_mode"] == "service"


# ───────────────────────── the subject token ─────────────────────────


async def test_the_bearer_the_door_validated_serves_its_own_person_only(
    client: AsyncClient, project: dict[str, Any], idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.services import identity_exchange as exchange

    user_id, org_id = await _person()
    token = exchange._DOOR.set(None)
    try:
        exchange.door_bearer(user_id, "door-access-token")
        async with session_scope(orgs=ALL_ORGS) as session:
            assert await exchange.subject_token(session, org_id, user_id) == "door-access-token"
            with pytest.raises(exchange.IdentityUnavailable):
                await exchange.subject_token(session, org_id, "someone-else")
    finally:
        exchange._DOOR.reset(token)
    assert idp.requests == []


async def test_a_rotated_refresh_token_is_sealed_again_and_the_old_one_is_gone(
    client: AsyncClient, project: dict[str, Any], idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services import identity_exchange as exchange

    idp.rotate = True
    await _sign_in_sealed()
    user_id, org_id = await _person()
    async with session_scope(orgs=ALL_ORGS) as session:
        access = await exchange.subject_token(session, org_id, user_id)
        name = exchange.refresh_secret_name(user_id)
        kept = (await get_secret_store().get_many(session, org_id, [name]))[name]
    assert idp.access_tokens[access] == SUBJECT
    assert kept != REFRESH and kept in idp.refresh_tokens


async def test_signing_out_forgets_the_sealed_refresh_token(
    client: AsyncClient, project: dict[str, Any], idp: FakeIdentityProvider
) -> None:
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services.identity_exchange import refresh_secret_name
    from choregos_core.secrets import SecretNotFound

    await _sign_in_sealed()
    user_id, org_id = await _person()
    name = refresh_secret_name(user_id)
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    async with session_scope(orgs=ALL_ORGS) as session:
        with pytest.raises(SecretNotFound):
            await get_secret_store().get_many(session, org_id, [name])


# ───────────────────────── declarations ─────────────────────────


def test_a_connector_declares_its_modes_in_its_configuration() -> None:
    from choregos_api.services.identity_exchange import exchange_audience, supports_identity

    class Connector:
        def __init__(self, config: dict[str, Any]) -> None:
            self.config = config

    plain = Connector({})
    both = Connector(
        {"identity_modes": ["service", "user_exchange"], "token_exchange": {"audience": "iris-mcp"}}
    )
    no_audience = Connector({"identity_modes": ["user_exchange"]})
    assert supports_identity(plain, "service") and not supports_identity(plain, "user_exchange")
    assert supports_identity(both, "user_exchange") and exchange_audience(both) == "iris-mcp"
    assert not supports_identity(no_audience, "user_exchange"), "an exchange needs its audience"
    assert not supports_identity(no_audience, "service")


def test_an_absent_identity_leaves_the_agent_versions_digest_unchanged() -> None:
    from choregos_api.schemas.agents import AgentMcpServer

    assert AgentMcpServer(connector="iris").model_dump(mode="json") == {"connector": "iris", "tools": []}
    named = AgentMcpServer(connector="iris", identity="user_exchange").model_dump(mode="json")
    assert named["identity"] == "user_exchange"


# ───────────────────────── security review of the exchange (2026-11-11) ─────────────────────────


def _issuing(idp: FakeIdentityProvider, issue: Any) -> None:
    """The provider answers token exchanges with `issue(audience)`; everything else as the fake does."""
    import json

    import httpx
    from choregos_api.services.identity_exchange import set_exchange_transport

    def respond(request: httpx.Request) -> httpx.Response:
        answer = idp.respond(request)
        form = idp.requests[-1]
        if form.get("grant_type", "").endswith("token-exchange") and form.get("audience"):
            body = json.loads(answer.content)
            body["access_token"] = issue(form["audience"])
            return httpx.Response(200, json=body)
        return answer

    set_exchange_transport(httpx.MockTransport(respond))


def _jwt_for(audience: str) -> str:
    import base64
    import json

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'sub': SUBJECT, 'aud': audience})}.sig-of-the-token"


async def test_a_token_issued_for_another_audience_is_never_handed_to_the_source(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
) -> None:
    """Token confusion: a provider (or a proxy in front of it) that answers with a token whose `aud` is not
    the one asked for must not have that token sent to the connector."""
    await _declare(client)
    await _sign_in_sealed()
    _issuing(idp, lambda audience: _jwt_for("the-finance-api"))
    source.received.clear()
    await _converse(client, upstream, "user_exchange")
    assert _calls(source) == [], "the source received nothing"
    (_, last) = upstream.model.requests[-1]
    assert any("identity_unavailable" in m["content"] for m in last["messages"] if m["role"] == "tool")


async def test_the_deployments_audience_list_bounds_what_a_connectors_editor_can_ask_for(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,
    source: Any,
    idp: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audience is written in the connector's configuration, by whoever edits the connector: with
    `CHOREGOS_TOKEN_EXCHANGE_AUDIENCES` set, an audience outside it is never even asked of the provider."""
    from choregos_api.config import reset_settings_cache

    await _declare(client)
    await _sign_in_sealed()
    monkeypatch.setenv("CHOREGOS_TOKEN_EXCHANGE_AUDIENCES", '["iris-mcp"]')
    reset_settings_cache()
    try:
        source.received.clear()
        await _converse(client, upstream, "user_exchange")
    finally:
        monkeypatch.delenv("CHOREGOS_TOKEN_EXCHANGE_AUDIENCES")
        reset_settings_cache()
    assert _calls(source) == []
    assert [f for f in idp.requests if f["grant_type"].endswith("token-exchange")] == []


async def test_an_exchanged_opaque_token_is_masked_wherever_an_error_would_echo_it(
    client: AsyncClient, project: dict[str, Any], source: Any, idp: FakeIdentityProvider
) -> None:
    """A source's error text can quote the bearer it received; an opaque token has no shape for `redact` to
    recognise, so the platform remembers it."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.services.identity_exchange import Acting, exchanged_token
    from choregos_core.redaction import forget_secrets, redact
    from sqlalchemy import select

    await _declare(client)
    await _sign_in_sealed()
    _issuing(idp, lambda audience: "opaque-exchanged-token-0123456789")
    user_id, org_id = await _person()
    forget_secrets()
    async with session_scope(orgs=ALL_ORGS) as session:
        connector = (await session.execute(select(OrgConnector))).scalars().first()
        assert connector is not None
        token = await exchanged_token(
            session, connector, Acting("user_exchange", user_id=user_id, org_id=org_id)
        )
    assert token == "opaque-exchanged-token-0123456789"
    assert token not in redact(f"the source quotes {token} back")


async def test_a_token_rotated_while_the_person_signs_out_is_killed_not_sealed_again(
    client: AsyncClient, project: dict[str, Any], source: Any, idp: FakeIdentityProvider
) -> None:
    """The same race as a delegation's revocation: a renewal in flight when the sealed token is withdrawn
    must not seal what the provider just issued, and kills it at the provider."""
    import httpx
    from choregos_api.db.session import ALL_ORGS, session_scope
    from choregos_api.secret_store import get_secret_store
    from choregos_api.services.identity_exchange import (
        IdentityUnavailable,
        forget_refresh_token,
        refresh_secret_name,
        set_exchange_transport,
        subject_token,
    )

    await _declare(client)
    await _sign_in_sealed()
    idp.rotate = True
    user_id, org_id = await _person()

    async def respond(request: httpx.Request) -> httpx.Response:
        async with session_scope(orgs=ALL_ORGS) as other:  # the sign-out, committed meanwhile
            await forget_refresh_token(other, [org_id], user_id)
        return idp.respond(request)

    set_exchange_transport(httpx.MockTransport(respond))
    with pytest.raises(IdentityUnavailable):
        async with session_scope(orgs=ALL_ORGS) as session:
            await subject_token(session, org_id, user_id)
    from choregos_core.secrets import SecretNotFound

    async with session_scope(orgs=ALL_ORGS) as session:
        with pytest.raises(SecretNotFound):
            await get_secret_store().get_many(session, org_id, [refresh_secret_name(user_id)])
    assert len(idp.revoked) == 1, "the token the provider just issued was revoked"
