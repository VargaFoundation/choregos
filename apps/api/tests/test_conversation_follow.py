"""The REST turn says what the door says; a turn can be followed from elsewhere (S67-08, ADR 0154 §4).

- G1: the answer of `POST /me/conversations/{id}/messages` (whole or streamed) carries `citations`,
  `pending_actions` and `missing_accounts`, read by the same functions as the MCP door's answer.
- G2: a turn publishes its events to a run that any client reads from a position (`Last-Event-ID`); a client
  attached mid-turn gets the rest; a `detach`ed turn is booked once when its client left, and one that is not
  detached is cancelled as before. The model stalls in the middle of its stream where that is the point.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from httpx import AsyncClient
from sqlalchemy import select

from .kits.conversations import ME, _open, _record
from .test_agent_sessions import Upstream, _agent, _key_rows, upstream  # noqa: F401
from .test_mcp_assistants import (  # noqa: F401
    _call,
    _installation_knowledge,
    installed,
    offline,
)


def _events(text: str) -> list[tuple[str, str, dict[str, Any]]]:
    """`(id, event, data)` of a server-sent stream, in order."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in fields:
            found.append((fields.get("id", ""), fields["event"], json.loads(fields.get("data", "{}"))))
    return found


class _Halfway(httpx.AsyncByteStream):
    """A model stream that sends its first chunks, waits for `gate`, then sends the rest."""

    def __init__(self, head: bytes, tail: bytes, gate: asyncio.Event) -> None:
        self.head, self.tail, self.gate = head, tail, gate

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.head
        await self.gate.wait()
        yield self.tail


def _halfway(upstream: Upstream, after: str, gate: asyncio.Event) -> None:  # noqa: F811
    """The model's streamed answer is cut after the chunk whose text is `after` until `gate` is set."""
    from tranibor_api.services.agent_sessions import set_model_transport

    async def handler(request: httpx.Request) -> httpx.Response:
        whole = upstream.handle(request)
        parts = whole.content.split(b"\n\n")
        cut = next(i for i, part in enumerate(parts) if after.encode() in part) + 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_Halfway(b"\n\n".join(parts[:cut]) + b"\n\n", b"\n\n".join(parts[cut:]), gate),
        )

    set_model_transport(httpx.MockTransport(handler))


async def _proposal(conversation_id: str, email: str, summary: str) -> str:
    """A write the assistant proposed and the person has not decided."""
    from tranibor_api.db.models import AgentSessionRecord, ConversationAction, User
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation_id)
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        assert record is not None
        row = ConversationAction(
            org_id=record.org_id,
            conversation_id=conversation_id,
            user_id=user.id,
            need="orders",
            operation="order",
            arguments={},
            summary=summary,
            status="proposed",
            kind="write",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _principal(email: str) -> Any:
    from tranibor_api.db.models import User
    from tranibor_api.db.session import session_scope
    from tranibor_api.rbac import Principal, Role

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        return Principal(user_id=user.id, email=email, org_roles={"northwind": Role.ORG_ADMIN})


async def _next(stream: AsyncIterator[dict[str, str]]) -> dict[str, str]:
    return await asyncio.wait_for(anext(stream), timeout=10)


async def _rest(stream: AsyncIterator[dict[str, str]]) -> list[tuple[str, str]]:
    return [(e["id"], e["event"]) async for e in stream]


# ─────────────────────────── G1: what the door says ───────────────────────────


async def test_the_rest_turn_says_what_the_door_says_for_the_same_turn(
    client: AsyncClient,
    installed: str,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    document_id = await _installation_knowledge()
    asked = {"assistant": "northwind:investment-assistant", "message": "What is the limit per instrument?"}
    upstream.model.say("Opening turn.")
    opened = await _call(client, installed, "ask_assistant", asked)
    conversation = opened["conversation_id"]
    await _proposal(conversation, "ines@northwind.dev", "Order two laptops")
    search = {"base": "policy", "query": "limit per instrument"}
    upstream.model.call("knowledge_search", search).say("At most ten percent [RISK POLICY, v3].")
    upstream.model.call("knowledge_search", search).say_chunks("At most ", "ten percent.")

    whole = await client.post(f"{ME}/{conversation}/messages", json={"input": "And per instrument?"})
    assert whole.status_code == 200, whole.text
    door = await _call(client, installed, "get_conversation", {"conversation_id": conversation})
    answer = whole.json()
    assert answer["citations"] == door["citations"] and door["citations"], "the same citations"
    assert answer["citations"][0]["document_id"] == document_id
    assert answer["pending_actions"] == door["pending_actions"] and door["pending_actions"]
    assert answer["pending_actions"][0]["confirm_url"].endswith(
        f"/conversations/{conversation}?action={answer['pending_actions'][0]['id']}"
    )
    assert answer["missing_accounts"] == door["missing_accounts"] == []

    streamed = await client.post(f"{ME}/{conversation}/messages", json={"input": "Again?", "stream": True})
    last = _events(streamed.text)[-1]
    assert last[1] == "response.completed"
    door = await _call(client, installed, "get_conversation", {"conversation_id": conversation})
    assert last[2]["citations"] == door["citations"] and last[2]["pending_actions"] == door["pending_actions"]
    assert last[2]["citations"], "the streamed turn's last event carries them too"


# ─────────────────────────── G2: follow from elsewhere ───────────────────────────


async def test_a_client_attached_mid_turn_gets_the_rest_and_a_resume_repeats_no_delta(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    from tranibor_api.services.turn_follow import run_of, start_turn

    await _agent(client)
    conversation = await _open("northwind", admin)
    upstream.model.say_chunks("one ", "two ", "three ", "four.")
    gate = asyncio.Event()
    _halfway(upstream, "two ", gate)

    run = start_turn(await _principal(admin), conversation, "Count.")
    sender = run.follow()
    assert (await _next(sender))["event"] == "response.created"
    assert json.loads((await _next(sender))["data"])["delta"] == "one "
    assert json.loads((await _next(sender))["data"])["delta"] == "two "

    # A second client attaches now, mid-turn: it replays what it missed and waits for the rest.
    follower = run_of(conversation).follow()  # type: ignore[union-attr]
    late = [await _next(follower) for _ in range(3)]
    assert [e["id"] for e in late] == ["1", "2", "3"]
    gate.set()
    rest = [e async for e in follower]
    deltas = [
        json.loads(e["data"])["delta"] for e in late + rest if e["event"] == "response.output_text.delta"
    ]
    assert deltas == ["one ", "two ", "three ", "four."], "every delta, once, in order"
    assert rest[-1]["event"] == "response.completed"
    assert [e["id"] for e in late + rest] == [str(n) for n in range(1, len(late + rest) + 1)]
    assert [e["event"] async for e in sender][-1] == "response.completed", "the sender is not disturbed"

    # Resuming after position 4 (created, one, two, three) gives only what follows.
    resumed = [e async for e in run.follow(4)]
    assert [json.loads(e["data"]).get("delta") for e in resumed[:-1]] == ["four."]
    assert resumed[-1]["event"] == "response.completed" and resumed[0]["id"] == "5"
    assert [e async for e in run.follow(len(run.events))] == [], "nothing after the last event"


async def test_the_events_route_replays_a_turn_and_resumes_after_last_event_id(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    conversation = await _open("northwind", admin)
    upstream.model.say_chunks("a ", "b ", "c.")
    sent = await client.post(f"{ME}/{conversation}/messages", json={"input": "Go", "stream": True})
    sent_events = _events(sent.text)
    assert sent_events[0][1] == "response.created"

    full = _events((await client.get(f"{ME}/{conversation}/events")).text)
    assert [(e[1]) for e in full] == [e[1] for e in sent_events] and full[0][0] == "1"
    assert [e[2].get("delta") for e in full if e[1] == "response.output_text.delta"] == ["a ", "b ", "c."]

    after_two = _events(
        (await client.get(f"{ME}/{conversation}/events", headers={"Last-Event-ID": "2"})).text
    )
    assert [e[0] for e in after_two] == [str(n) for n in range(3, len(full) + 1)], "no delta repeated"
    by_query = _events((await client.get(f"{ME}/{conversation}/events", params={"after_seq": 2})).text)
    assert by_query == after_two
    header_first = await client.get(
        f"{ME}/{conversation}/events", params={"after_seq": 0}, headers={"Last-Event-ID": "2"}
    )
    assert _events(header_first.text) == after_two, "the header wins over the query"


async def test_the_events_route_is_the_openers_alone(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    from .egress_support import as_person

    await _agent(client)
    conversation = await _open("northwind", admin)
    assert (await client.get(f"{ME}/{conversation}/events")).status_code == 200
    async with as_person(client, "eve@northwind.dev", "project_owner") as eve:
        assert (await eve.get(f"{ME}/{conversation}/events")).status_code == 404
    client.cookies.clear()
    assert (await client.get(f"{ME}/{conversation}/events")).status_code == 401


# ─────────────────────────── G2: detach ───────────────────────────


async def test_a_detached_turn_is_booked_once_when_its_client_left_and_a_plain_one_is_cancelled(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    from tranibor_api.routers.conversations import _sent
    from tranibor_api.services.turn_follow import start_turn

    await _agent(client)
    principal = await _principal(admin)

    # Detached: the client reads the first event and goes away while the model is still streaming.
    kept = await _open("northwind", admin)
    upstream.model.say_chunks("one ", "two ", "three.")
    gate = asyncio.Event()
    _halfway(upstream, "one ", gate)
    run = start_turn(principal, kept, "Go")
    sender = _sent(run, detach=True)
    assert (await _next(sender))["event"] == "response.created"
    await sender.aclose()  # the client left
    assert not run.finished, "the turn is still running"
    gate.set()
    assert run.task is not None
    await asyncio.wait_for(run.task, timeout=10)
    assert run.events[-1][0] == "response.completed", "the turn ended as if nobody had left"
    assert (await _record(kept)).turns == 1
    assert len(upstream.gateway.revoked) == 1 and upstream.gateway.keys == {}, "its key settled once"
    assert len([r for r in await _key_rows() if r.spend_usd > 0]) == 1, "booked once"

    # Not detached: the client leaving cancels the turn, as before.
    plain = await _open("northwind", admin)
    upstream.model.say_chunks("one ", "two ", "three.")
    gate = asyncio.Event()
    _halfway(upstream, "one ", gate)
    run = start_turn(principal, plain, "Go")
    sender = _sent(run, detach=False)
    assert (await _next(sender))["event"] == "response.created"
    await sender.aclose()
    assert run.finished and run.events[-1][0] != "response.completed", "a turn nobody follows is cancelled"
    gate.set()


async def test_a_detached_client_that_stays_gets_the_answer_it_would_get_without_detach(
    client: AsyncClient,
    admin: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    conversation = await _open("northwind", admin)
    upstream.model.say("First.").say("Second.")
    plain = await client.post(f"{ME}/{conversation}/messages", json={"input": "a"})
    detached = await client.post(f"{ME}/{conversation}/messages", json={"input": "b", "detach": True})
    assert plain.status_code == detached.status_code == 200, detached.text
    assert detached.json()["output_text"] == "Second." and set(detached.json()) == set(plain.json())
    busy = await client.post(f"{ME}/{conversation}/messages", json={"input": "c", "detach": True})
    assert busy.status_code in (200, 409)
