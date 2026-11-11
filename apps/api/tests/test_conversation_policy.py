"""What the installation says about its conversations, and what a conversation writes about itself (S63-04,
S63-15, ADR 0134 points 3, 5, 7, 8).

The model writes the title from the first exchange on the turn's key (the first message cut when it cannot);
`max_turns`, `retention_days` and `idle_close_minutes` are the installation's policy (manifest, then its
overrides) read at every turn; a turn on an installation that is no longer ready is refused before the
model; two turns of one installation racing its month cap cannot both be admitted (PostgreSQL).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_core import utcnow
from choregos_core.domain import aware
from httpx import AsyncClient
from sqlalchemy import delete, select

from .agent_scopes_kit import as_user
from .conftest import without_postgres
from .kits.assistant_install import (
    MARKET,
    NEWS,
    ORG,
    _bind,
    _connector,
    _install,
    _member,
    _plugin,
    offline,  # noqa: F401
)
from .kits.assistant_rights import ME, OPEN, _as
from .kits.conversations import _events, _open, _record, _say
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

AUDIENCE = "investors"


async def _ready(client: AsyncClient) -> None:
    await _plugin(client)
    await _connector("news-feed", NEWS)
    await _connector("markets", MARKET)
    installed = await _install(client, audience_groups=[AUDIENCE])
    assert installed.status_code == 201, installed.text
    await _bind(client, "news", "news-feed")
    ready = await _bind(client, "market_data", "markets")
    assert ready.json()["status"] == "ready", ready.text
    await as_user(client, "admin@varga.dev")


async def _person(client: AsyncClient, app: Any, email: str = "probe@varga.dev") -> AsyncClient:
    await _member(client, email, [AUDIENCE])
    return await _as(app, email)


async def _opened(person: AsyncClient) -> str:
    opened = await person.post(OPEN, json={})
    assert opened.status_code == 201, opened.text
    return str(opened.json()["id"])


async def _turn(person: AsyncClient, conversation: str, text: str = "Hello") -> Any:
    return await person.post(f"{ME}/conversations/{conversation}/messages", json={"input": text})


# ───────────────────────── the title ─────────────────────────


async def test_the_title_is_written_by_the_model_from_the_first_exchange_on_the_turns_key(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Order 42 shipped yesterday.").say("It arrives on Friday.")
    upstream.model.say_for("conversation_title", '"Order 42 delivery status."')
    conversation = await _open("varga", admin)
    assert (await _say(client, conversation, "Where is order 42? I ordered it last week")).status_code == 200
    assert (await _record(conversation)).title == "Order 42 delivery status"
    (headers, body) = upstream.model.side_requests[0]
    assert body["metadata"] == {"purpose": "conversation_title"}
    turn_key = next(iter(upstream.gateway.revoked), None)
    assert turn_key is not None and len(upstream.gateway.revoked) == 1, "the title used the turn's own key"
    assert headers["authorization"] == f"Bearer {upstream.model.requests[0][0]['authorization'][7:]}"
    sent = [m["content"] for m in body["messages"]]
    assert "Where is order 42?" in sent[1] and "Order 42 shipped yesterday." in sent[1]
    # The title is written once: the second turn asks no title.
    assert (await _say(client, conversation, "And then?")).status_code == 200
    assert len(upstream.model.side_requests) == 1
    assert (await _record(conversation)).title == "Order 42 delivery status"


@pytest.mark.parametrize("failure", ["refused", "empty"])
async def test_a_title_the_model_cannot_write_falls_back_to_the_cut_message(
    failure: str,
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("Fine.")
    if failure == "refused":
        upstream.model.say_for("conversation_title", "no budget", status=400)
    else:
        upstream.model.say_for("conversation_title", "   ")
    conversation = await _open("varga", admin)
    answer = await _say(client, conversation, "Where is my order?")
    assert answer.status_code == 200 and answer.json()["status"] == "completed", "the turn is not failed"
    assert (await _record(conversation)).title == "Where is my order?"
    assert len(upstream.model.side_requests) == 1, "the model was asked"


# ───────────────────────── the installation's policy ─────────────────────────


async def test_max_turns_and_retention_come_from_the_installations_policy(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    person = await _person(client, app)
    await _ready(client)
    upstream.model.say("One.").say("Two.").say("Never said.")
    conversation = await _opened(person)
    record = await _record(conversation)
    assert (record.max_turns, record.retention_days, record.idle_close_minutes) == (
        60,
        30,
        240,
    )  # the manifest's

    # The installation's overrides win over the manifest, and reach a conversation already open.
    changed = await client.patch(
        f"{ORG}/assistants/investment-assistant",
        json={"policy_overrides": {"conversation": {"max_turns": 2, "retention_days": 7}}},
    )
    assert changed.status_code == 200, changed.text
    before = utcnow()
    assert (await _turn(person, conversation)).status_code == 200
    record = await _record(conversation)
    assert record.max_turns == 2 and record.retention_days == 7
    assert before + timedelta(days=7) <= aware(record.retention_until) <= utcnow() + timedelta(days=7)
    assert (await _turn(person, conversation)).status_code == 200
    calls, keys = len(upstream.model.requests), len(upstream.gateway.revoked)
    refused = await _turn(person, conversation)
    assert refused.status_code == 409 and "turn" in refused.text.lower(), refused.text
    assert len(upstream.model.requests) == calls and len(upstream.gateway.revoked) == keys, "no key, no call"


# ───────────────────────── an installation that is no longer ready ─────────────────────────


async def _break(how: str) -> None:
    from choregos_api.db.models import AssistantInstallation, OrgPlugin
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        if how == "incomplete":
            row = (await session.execute(select(AssistantInstallation))).scalar_one()
            row.config = {}  # the plugin's `house_name` is required
        else:
            await session.execute(delete(OrgPlugin))


@pytest.mark.parametrize("how", ["incomplete", "suspended"])
async def test_a_turn_on_an_installation_that_stopped_being_ready_is_refused_before_the_model(
    how: str,
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    person = await _person(client, app)
    await _ready(client)
    upstream.model.say("First.").say("Never said.")
    conversation = await _opened(person)
    assert (await _turn(person, conversation)).status_code == 200
    calls, keys = len(upstream.model.requests), len(upstream.gateway.revoked)

    await _break(how)
    refused = await _turn(person, conversation, "Again")
    assert refused.status_code == 409 and how in refused.text, refused.text
    assert len(upstream.model.requests) == calls and len(upstream.gateway.revoked) == keys, "no key, no call"
    kept = (await person.get(f"{ME}/conversations/{conversation}")).json()
    assert kept["turns"] == 1 and [m["content"] for m in kept["messages"]][-1] == "First."
    assert "turn.started" in await _events(conversation)


# ───────────────────────── the month caps racing ─────────────────────────


@without_postgres
async def test_two_turns_racing_the_installations_month_cap_are_not_both_admitted(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    """The cap holds one turn's reservation. Each turn reads the month before the other booked a cent:
    without the installation's lock and the open reservations counted, both are admitted at the whole cap
    and the month is reserved twice."""
    import httpx
    from choregos_api.services.agent_sessions import set_model_transport

    person = await _person(client, app)
    other = await _person(client, app, "other@varga.dev")
    await _ready(client)
    cap = 1.0
    changed = await client.patch(
        f"{ORG}/assistants/investment-assistant",
        json={"policy_overrides": {"budget": {"per_installation_month_usd": cap}}},
    )
    assert changed.status_code == 200, changed.text
    gate = asyncio.Event()

    async def held(request: httpx.Request) -> httpx.Response:
        await request.aread()
        answer = upstream.handle(request)
        await gate.wait()  # the first turn is still in flight when the second asks for its key
        return answer

    set_model_transport(httpx.MockTransport(held))
    upstream.model.say("A.").say("B.")
    first, second = await _opened(person), await _opened(other)
    racing = [asyncio.ensure_future(_turn(person, first)), asyncio.ensure_future(_turn(other, second))]
    await asyncio.sleep(1.5)
    gate.set()
    answers = await asyncio.gather(*racing)
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    reserved = [float(k.budget_usd) for k in await _turn_keys() if k.run_id.startswith("turn:")]
    assert len(reserved) == 1 and sum(reserved) <= cap + 1e-9, f"the month was reserved twice: {reserved}"


async def _turn_keys() -> list[Any]:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = list((await session.execute(select(GatewayKeyRow))).scalars())
        for row in rows:
            session.expunge(row)
        return rows
