# SPDX-License-Identifier: Apache-2.0
"""Durable conversations (S63-04, ADR 0134): a key per turn, a cap that holds across turns, a conversation
that goes idle and is resumed with its history, and one that is its opener's alone.

The model is the one of `test_agent_sessions.py`: a `FakeModelServer` behind a transport that books every
request on the `FakeGateway` key it carries. A conversation is opened through the service
(`open_conversation`): the route that opens an assistant's comes with the installations.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from choregos_core import utcnow
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, without_postgres
from .egress_support import as_person
from .test_agent_sessions import ORG, Upstream, _agent, _key_rows, _ledger, upstream  # noqa: F401

ME = "/api/v1/me/conversations"


async def _open(org_slug: str, email: str, agent: str = "helper", **terms: Any) -> str:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal
    from choregos_api.services.conversations import open_conversation

    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        record = await open_conversation(
            session, Principal(user_id=user.id, email=email), organisation, agent, **terms
        )
        return record.id


async def _record(conversation_id: str) -> Any:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation_id)
        assert record is not None
        session.expunge(record)
        return record


async def _events(conversation_id: str) -> list[str]:
    from choregos_api.db.models import AgentSessionEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(AgentSessionEvent.type)
            .where(AgentSessionEvent.session_id == conversation_id)
            .order_by(AgentSessionEvent.seq)
        )
        return list(rows.scalars())


async def _say(client: AsyncClient, conversation_id: str, text: str) -> Any:
    return await client.post(f"{ME}/{conversation_id}/messages", json={"input": text})


# ───────────────────────── a key per turn ─────────────────────────


async def test_each_turn_mints_its_own_key_which_ends_with_the_turn(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Order 42 shipped yesterday.").say("It arrives on Friday.")
    conversation = await _open("varga", admin)
    assert upstream.gateway.keys == {}, "opening reserves nothing and mints nothing"

    first = await _say(client, conversation, "Where is order 42?")
    assert first.status_code == 200, first.text
    assert first.json()["output_text"] == "Order 42 shipped yesterday."
    assert upstream.gateway.keys == {} and len(upstream.gateway.revoked) == 1, "revoked as the turn ends"
    second = await _say(client, conversation, "When does it arrive?")
    assert second.json()["output_text"] == "It arrives on Friday."
    assert upstream.gateway.keys == {} and len(set(upstream.gateway.revoked)) == 2, (
        "another turn, another key"
    )
    bearers = {headers["authorization"] for headers, _ in upstream.model.requests}
    assert len(bearers) == 2, "each turn spoke with its own key"
    assert not any(bearer.removeprefix("Bearer ") in first.text + second.text for bearer in bearers)

    rows = await _key_rows()
    assert sorted(r.run_id for r in rows) == [f"turn:{conversation}:1", f"turn:{conversation}:2"]
    assert all(r.revoked and r.spend_usd > 0 for r in rows)
    lines = [entry for entry in await _ledger() if entry.kind != "tool"]
    assert len(lines) == 2 and {line.session_id for line in lines} == {conversation}
    record = await _record(conversation)
    assert record.turns == 2 and record.cost_usd == pytest.approx(sum(line.cost_usd for line in lines))
    (_, body) = upstream.model.requests[-1]
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"], "the history"

    read = (await client.get(f"{ME}/{conversation}")).json()
    assert read["title"] == "Where is order 42?" and read["turns"] == 2 and read["status"] == "open"
    assert [(m["role"], m["content"]) for m in read["messages"]] == [
        ("user", "Where is order 42?"),
        ("assistant", "Order 42 shipped yesterday."),
        ("user", "When does it arrive?"),
        ("assistant", "It arrives on Friday."),
    ]
    assert "turn.started" in await _events(conversation) and "turn.ended" in await _events(conversation)


async def test_the_conversations_cap_holds_across_turns(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    """The cap is the conversation's, not the turn's: what is left of it bounds each next key, and a turn
    it cannot pay is refused before the model is called."""
    await _agent(client)
    upstream.tokens_in, upstream.tokens_out = 10_000, 2_000  # a turn costs $0.06, over the $0.01 floor
    for _ in range(4):
        upstream.model.say("Ok.")
    probe = await _open("varga", admin)
    assert (await _say(client, probe, "Hi")).status_code == 200
    one_turn = (await _record(probe)).cost_usd
    assert one_turn > 0
    upstream.model.requests.clear()

    # A cap of 2.5 turns: the third key is reserved at the half a turn that is left.
    conversation = await _open("varga", admin, max_usd=one_turn * 2.5)
    first = await _say(client, conversation, "One")
    second = await _say(client, conversation, "Two")
    assert (first.status_code, second.status_code) == (200, 200)
    rows = {r.run_id: r.budget_usd for r in await _key_rows() if r.run_id.startswith(f"turn:{conversation}:")}
    assert rows[f"turn:{conversation}:2"] == pytest.approx(one_turn * 1.5), "the second key is what is left"
    third = await _say(client, conversation, "Three")
    assert third.status_code == 200 and third.json()["status"] == "failed"
    assert third.json()["error"]["code"] == "budget_exceeded", "half a turn does not pay a turn"
    assert (await _record(conversation)).status == "open", "a turn's spent key does not end the conversation"
    assert (await _record(conversation)).cost_usd <= one_turn * 2.5 + 1e-9, "the cap held"
    rows = {r.run_id: r.budget_usd for r in await _key_rows() if r.run_id.startswith(f"turn:{conversation}:")}
    assert rows[f"turn:{conversation}:3"] == pytest.approx(one_turn * 0.5), "what is left, no more"


async def test_a_spent_conversation_refuses_a_turn_before_the_model(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Ok.")
    conversation = await _open("varga", admin, max_usd=0.05)
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        record.cost_usd = 0.045  # what is left, $0.005, is under the floor
    refused = await _say(client, conversation, "Hi")
    assert refused.status_code == 409 and "spent" in refused.text, refused.text
    assert upstream.model.requests == [] and upstream.gateway.keys == {}, "no key, no call"


async def test_a_conversation_keeps_the_revision_it_started_with(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    """A later version that no turn could be reserved under does not reach a conversation of version 1."""
    await _agent(client)
    upstream.model.say("Ok.").say("Still ok.")
    conversation = await _open("varga", admin)
    assert (await _say(client, conversation, "Hi")).status_code == 200
    spec = {"instructions": "Different.", "runtime": {"kind": "prompt"}, "budget": {"run_usd": 0.001}}
    published = await client.post(f"{ORG}/agents/helper/versions", json=spec)
    assert published.status_code == 201, published.text
    again = await _say(client, conversation, "And now?")
    assert again.status_code == 200 and again.json()["output_text"] == "Still ok.", again.text
    record = await _record(conversation)
    assert (record.agent_version, record.agent_revision) == (1, 1)


# ───────────────────────── idle, and resumed ─────────────────────────


async def test_a_conversation_resumed_after_two_hours_has_its_history(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _agent(client)
    upstream.model.say("Order 42 shipped.").say("On Friday.")
    conversation = await _open("varga", admin, idle_close_minutes=60)
    assert (await _say(client, conversation, "Where is order 42?")).status_code == 200
    assert (await client.get(f"{ME}/{conversation}")).json()["status"] == "open"

    later = utcnow() + timedelta(hours=2)
    monkeypatch.setattr("choregos_api.services.conversations.utcnow", lambda: later)
    listed = (await client.get(ME)).json()["items"]
    assert [(c["id"], c["status"]) for c in listed] == [(conversation, "idle")]

    resumed = await _say(client, conversation, "When does it arrive?")
    assert resumed.status_code == 200 and resumed.json()["output_text"] == "On Friday.", resumed.text
    (_, body) = upstream.model.requests[-1]
    assert [m["content"] for m in body["messages"][1:]] == [
        "Where is order 42?",
        "Order 42 shipped.",
        "When does it arrive?",
    ], "the history of before the pause"
    assert (await client.get(f"{ME}/{conversation}")).json()["status"] == "open"
    assert "conversation.resumed" in await _events(conversation)


async def test_the_default_idle_window_is_four_hours(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _agent(client)
    upstream.model.say("Ok.")
    conversation = await _open("varga", admin)
    assert (await _say(client, conversation, "Hi")).status_code == 200
    monkeypatch.setattr(
        "choregos_api.services.conversations.utcnow", lambda: utcnow() + timedelta(hours=3, minutes=59)
    )
    assert (await client.get(f"{ME}/{conversation}")).json()["status"] == "open"
    monkeypatch.setattr(
        "choregos_api.services.conversations.utcnow", lambda: utcnow() + timedelta(hours=4, minutes=1)
    )
    assert (await client.get(f"{ME}/{conversation}")).json()["status"] == "idle"


# ───────────────────────── whose it is ─────────────────────────


async def test_a_conversation_is_its_openers_alone(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Mine.")
    async with as_person(client, "dev@varga.dev", "developer") as other:
        mine = await _open("varga", admin)
        theirs = await _open("varga", "dev@varga.dev")
        assert (await _say(client, mine, "Hi")).status_code == 200

        assert [c["id"] for c in (await client.get(ME)).json()["items"]] == [mine]
        assert [c["id"] for c in (await other.get(ME)).json()["items"]] == [theirs]
        assert (await other.get(f"{ME}/{mine}")).status_code == 404
        assert (await other.delete(f"{ME}/{mine}")).status_code == 404
        assert (await _say(other, mine, "Mine now")).status_code == 404
        assert (await client.get(f"{ME}/{theirs}")).status_code == 404
        assert (await client.get(f"{ME}/nope")).status_code == 404
        # The sessions' own routes do not reach a conversation, nor the other way round.
        assert (await client.get(f"{ORG}/agent-sessions/{mine}")).status_code == 404
    assert len(upstream.model.requests) == 1, "only its owner's turn reached the model"


async def test_the_listing_is_paginated_and_the_most_recent_first(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Ok.")
    ids = [await _open("varga", admin) for _ in range(3)]
    assert (await _say(client, ids[0], "Touch the oldest")).status_code == 200
    page = (await client.get(ME, params={"limit": 2})).json()
    assert page["items"][0]["id"] == ids[0] and page["meta"]["has_more"] is True
    rest = (await client.get(ME, params={"limit": 2, "cursor": page["meta"]["next_cursor"]})).json()
    assert {c["id"] for c in page["items"] + rest["items"]} == set(ids) and rest["meta"]["has_more"] is False


async def test_deleting_a_conversation_erases_it_and_keeps_the_money(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Ok.")
    conversation = await _open("varga", admin)
    assert (await _say(client, conversation, "Hi")).status_code == 200
    assert (await client.delete(f"{ME}/{conversation}")).status_code == 204
    assert (await client.get(f"{ME}/{conversation}")).status_code == 404
    assert (await client.get(ME)).json()["items"] == []
    assert await _events(conversation) == []
    lines = [entry for entry in await _ledger() if entry.kind != "tool"]
    assert len(lines) == 1 and lines[0].cost_usd > 0 and lines[0].session_id is None


async def test_a_conversation_is_held_with_a_prompt_agent_only(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    from choregos_api.errors import ApiError

    await _agent(client, runtime=None)
    with pytest.raises(ApiError) as refused:
        await _open("varga", admin)
    assert refused.value.status_code == 422 and "prompt agent" in str(refused.value.detail)


async def test_a_session_of_responses_still_lives_one_hour_on_one_key(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    """`/responses` is unchanged: a session, not a conversation, and not on the listing."""
    await _agent(client)
    upstream.model.say("Ok.")
    opened = await client.post(
        f"{ORG}/agents/helper/responses", json={"project": "billing-api", "input": "Hi"}
    )
    assert opened.status_code == 200, opened.text
    assert (await client.get(ME)).json()["items"] == []
    record = await _record(opened.json()["session_id"])
    assert record.kind == "session" and record.expires_at - record.created_at < timedelta(hours=1, seconds=5)


# ───────────────────────── PostgreSQL: the database says the same ─────────────────────────


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
async def test_another_person_of_the_organisation_reads_nothing_of_a_conversation(
    pg_app: Any,
    organisation_a: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    """Straight through RLS, whatever the router does: an administrator of the organisation sees neither the
    conversation nor its transcript; its opener and the platform scope do; a request that names no user does
    not; and a `/responses` session of the same person stays as readable as before."""
    from choregos_api.db.models import AgentSessionEvent, AgentSessionRecord, User
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")
        await login(client, "admin@a.test")  # org_admin of `a`, the signed-in person from here
        created = await client.post(
            "/api/v1/orgs/a/agents",
            json={
                "slug": "helper",
                "display_name": "helper",
                "spec": {"instructions": "Be brief.", "runtime": {"kind": "prompt"}},
            },
        )
        assert created.status_code == 201, created.text
        upstream.model.say("Hello.")
        conversation = await _open("a", "dev@a.test")
        assert (await _say(client, conversation, "Hi")).status_code == 404, "admin@a.test: not theirs"
        await login(client, "dev@a.test")
        assert (await _say(client, conversation, "Hi")).status_code == 200

        async with session_scope(orgs="*") as session:
            users = {u.email: u.id for u in (await session.execute(select(User))).scalars()}
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["admin@a.test"])
            assert await session.get(AgentSessionRecord, conversation) is None
            assert (await session.execute(select(AgentSessionRecord.id))).all() == []
            assert (await session.execute(select(AgentSessionEvent.id))).all() == []
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, users["dev@a.test"])
            assert await session.get(AgentSessionRecord, conversation) is not None
            assert len((await session.execute(select(AgentSessionEvent.id))).all()) > 0
        async with session_scope() as session:  # no user named: nobody's conversation
            await limit_to_organisations(session, ["a"])
            assert await session.get(AgentSessionRecord, conversation) is None
        async with session_scope(orgs="*") as session:  # the platform scope: sweeps, purges, the orchestrator
            assert await session.get(AgentSessionRecord, conversation) is not None
