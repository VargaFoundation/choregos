# SPDX-License-Identifier: Apache-2.0
"""What bounds a conversation's turns (S63-04b, ADR 0134): real streaming, `max_turns`, `retention_until`,
compression, cancelling a turn, and two turns of one conversation at once.

Same model as `test_conversations.py`: a `FakeModelServer` (now streaming) behind a transport that books each
request on the `FakeGateway` key it carries. The slow tests wrap that transport so that a model call waits.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from choregos_core import utcnow
from httpx import ASGITransport, AsyncClient

from .conftest import login, without_postgres
from .kits.conversations import ME, _events, _open, _record, _say
from .kits.slow_model import Slow, _slow  # noqa: F401
from .test_agent_sessions import Upstream, _agent, _key_rows, _ledger, upstream  # noqa: F401


def _sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


# ───────────────────────── streaming ─────────────────────────


async def test_a_turn_streams_one_delta_per_chunk_the_model_sent(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say_chunks("Order ", "42 ", "shipped.")
    conversation = await _open("varga", admin)
    streamed = await client.post(f"{ME}/{conversation}/messages", json={"input": "Where?", "stream": True})
    assert streamed.status_code == 200, streamed.text
    events = _sse(streamed.text)
    deltas = [data["delta"] for name, data in events if name == "response.output_text.delta"]
    assert deltas == ["Order ", "42 ", "shipped."], "one delta per chunk, in order"
    assert events[0][0] == "response.created" and events[-1][0] == "response.completed"
    assert events[-1][1]["output_text"] == "Order 42 shipped."
    (_, body) = upstream.model.requests[-1]
    assert body["stream"] is True, "the gateway was asked to stream"
    read = (await client.get(f"{ME}/{conversation}")).json()
    assert read["messages"][-1] == {"role": "assistant", "content": "Order 42 shipped."}
    assert len(upstream.gateway.revoked) == 1 and upstream.gateway.keys == {}, "the turn's key ended"
    assert all(r.spend_usd > 0 for r in await _key_rows()), "the streamed usage was booked"


async def test_a_streamed_turn_keeps_its_tool_calls_journaled(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    """The tool call is assembled from streamed fragments and goes through the loop, which journals it."""
    await _agent(client)
    upstream.model.call("lookup", {"order": 42}).say_chunks("Not ", "available.")
    conversation = await _open("varga", admin)
    streamed = await client.post(f"{ME}/{conversation}/messages", json={"input": "Where?", "stream": True})
    events = _sse(streamed.text)
    assert [d["delta"] for n, d in events if n == "response.output_text.delta"] == ["Not ", "available."]
    assert "prompt.tool_refused" in await _events(conversation), "the call was seen whole by the loop"
    (_, second) = upstream.model.requests[-1]
    tool = [m for m in second["messages"] if m["role"] == "tool"]
    assert tool and "lookup" in tool[0]["content"]
    calling = next(m for m in second["messages"] if m.get("tool_calls"))
    assert calling["tool_calls"][0]["function"]["arguments"] == json.dumps({"order": 42})


async def test_a_streamed_turn_the_cap_refuses_is_409_before_the_stream(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    conversation = await _open("varga", admin, max_turns=1)
    upstream.model.say("Ok.")
    assert (await _say(client, conversation, "One")).status_code == 200
    refused = await client.post(f"{ME}/{conversation}/messages", json={"input": "Two", "stream": True})
    assert refused.status_code == 409 and "turn" in refused.text.lower()


# ───────────────────────── max_turns, retention ─────────────────────────


async def test_the_turn_past_max_turns_is_refused_before_the_model(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("One.").say("Two.").say("Three.")
    conversation = await _open("varga", admin, max_turns=2)
    assert (await _say(client, conversation, "a")).status_code == 200
    assert (await _say(client, conversation, "b")).status_code == 200
    calls, keys = len(upstream.model.requests), len(await _key_rows())
    refused = await _say(client, conversation, "c")
    assert refused.status_code == 409, refused.text
    assert len(upstream.model.requests) == calls and len(await _key_rows()) == keys, "no key, no call"
    assert (await _record(conversation)).turns == 2


async def test_retention_until_moves_forward_at_each_turn(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    upstream.model.say("One.").say("Two.")
    custom = await _open("varga", admin, retention_days=10)
    default = await _open("varga", admin)
    opened = (await _record(custom)).retention_until
    assert opened is not None, "set at the opening, so a conversation with no turn is purged too (S63-16)"
    await asyncio.sleep(0.05)
    await _say(client, custom, "a")
    first = (await _record(custom)).retention_until
    assert first is not None and first > opened
    assert (
        timedelta(days=9, hours=23)
        < first.replace(tzinfo=None) - utcnow().replace(tzinfo=None)
        < timedelta(days=10, hours=1)
    )
    await asyncio.sleep(0.05)
    await _say(client, custom, "b")
    second = (await _record(custom)).retention_until
    assert second is not None and second > first
    upstream.model.say("Three.")
    await _say(client, default, "a")
    kept = (await _record(default)).retention_until
    assert kept is not None
    assert (
        timedelta(days=29, hours=23)
        < kept.replace(tzinfo=None) - utcnow().replace(tzinfo=None)
        < timedelta(days=30, hours=1)
    )


# ───────────────────────── compression ─────────────────────────


async def test_a_long_history_is_summarised_and_the_model_gets_the_summary(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.services import conversation_turns

    monkeypatch.setattr(conversation_turns, "COMPRESS_AFTER_TOKENS", 300)
    await _agent(client)
    conversation = await _open("varga", admin)
    for number in range(6):
        upstream.model.say(f"Answer {number} " + "y" * 200)
        sent = await _say(client, conversation, f"Question {number} " + "x" * 200)
        assert sent.status_code == 200, sent.text
    (_, body) = upstream.model.requests[-1]
    texts = [str(m["content"]) for m in body["messages"]]
    assert not any("Question 0" in t and not t.startswith("Summary") for t in texts), "old turns not resent"
    summary = [m for m in body["messages"] if m["role"] == "system" and m["content"].startswith("Summary")]
    assert len(summary) == 1 and "Question 0" in summary[0]["content"], "the model gets the summary"
    assert "Question 5" in texts[-1], "the latest turn is verbatim"
    assert [m["role"] for m in body["messages"]][:2] == ["system", "system"]
    assert "conversation.compressed" in await _events(conversation)
    read = (await client.get(f"{ME}/{conversation}")).json()
    said = [m["content"][:10] for m in read["messages"] if m["role"] == "user"]
    assert said == [f"Question {n}" for n in range(6)], "the person still reads the whole conversation"


def test_compression_is_a_noop_under_the_threshold_and_keeps_the_latest_turns() -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.services.conversation_turns import KEEP_TURNS, compress_history

    record = AgentSessionRecord()
    record.messages = [{"role": "system", "content": "Be brief."}]
    assert compress_history(record, "hello") == 0
    huge = "z" * 40_000
    for n in range(KEEP_TURNS):
        record.messages += [{"role": "user", "content": f"{huge}{n}"}, {"role": "assistant", "content": "ok"}]
    assert compress_history(record, "hi") == 0, "the latest turns are never summarised"


# ───────────────────────── cancel ─────────────────────────


async def test_cancel_stops_a_slow_turn_and_books_its_spend(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    # The first call asks for a tool the agent was not offered (journaled), the second hangs.
    upstream.model.call("lookup", {"order": 42}).say("never read")
    transport = _slow(upstream, after=1)
    conversation = await _open("varga", admin)
    turn = asyncio.ensure_future(_say(client, conversation, "Where is order 42?"))
    await asyncio.wait_for(transport.waiting.wait(), 10)
    cancelled = await client.post(f"{ME}/{conversation}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    answer = await asyncio.wait_for(turn, 10)
    assert answer.status_code == 200
    assert answer.json()["status"] == "failed" and answer.json()["error"]["code"] == "cancelled"
    assert upstream.gateway.keys == {} and len(upstream.gateway.revoked) == 1, "the key is revoked"
    rows = await _key_rows()
    assert len(rows) == 1 and rows[0].revoked and rows[0].spend_usd > 0
    lines = [entry for entry in await _ledger() if entry.session_id == conversation]
    assert len(lines) == 1 and lines[0].cost_usd == pytest.approx(rows[0].spend_usd)
    events = await _events(conversation)
    assert "prompt.tool_refused" in events and "turn.cancelled" in events and "turn.ended" in events
    record = await _record(conversation)
    assert record.cost_usd > 0 and record.turn_lease_until is None
    assert [m["role"] for m in (await client.get(f"{ME}/{conversation}")).json()["messages"]] == [], (
        "the half turn leaves nothing in the history"
    )
    # Nothing runs any more: cancelling again is a conflict, and the conversation takes its next turn.
    assert (await client.post(f"{ME}/{conversation}/cancel")).status_code == 409
    from choregos_api.services.agent_sessions import set_model_transport

    set_model_transport(httpx.MockTransport(upstream.handle))
    upstream.model.say("Back.")
    assert (await _say(client, conversation, "Again")).json()["output_text"] == "Back."


async def test_cancel_is_the_openers_alone(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    assert (await client.post(f"{ME}/nope/cancel")).status_code == 404


# ───────────────────────── concurrency ─────────────────────────


@without_postgres
async def test_two_concurrent_turns_never_take_the_same_number(
    pg_app: Any,
    upstream: Upstream,  # noqa: F811
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        session.add(Organization(slug="a", name="A"))
    transport = _slow(upstream, after=0, delay=1.5)
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")
        await login(client, "admin@a.test")
        created = await client.post(
            "/api/v1/orgs/a/agents",
            json={
                "slug": "helper",
                "display_name": "helper",
                "spec": {"instructions": "Be brief.", "runtime": {"kind": "prompt"}},
            },
        )
        assert created.status_code == 201, created.text
        await login(client, "dev@a.test")
        upstream.model.say("First.").say("Second.")
        conversation = await _open("a", "dev@a.test")
        both = await asyncio.gather(_say(client, conversation, "x"), _say(client, conversation, "y"))
        assert sorted(r.status_code for r in both) == [200, 409], [r.text for r in both]
        assert transport.calls == 1, "the refused turn never reached the model"
        record = await _record(conversation)
        assert record.turns == 1 and record.turn_lease_until is None
        from choregos_api.db.models import GatewayKeyRow
        from sqlalchemy import select

        async with session_scope(orgs="*") as session:
            runs = list((await session.execute(select(GatewayKeyRow.run_id))).scalars())
        assert runs == [f"turn:{conversation}:1"]
        # The lease is gone with the turn: the next one takes number 2.
        transport.after = 10
        assert (await _say(client, conversation, "z")).status_code == 200
        assert (await _record(conversation)).turns == 2
