"""A voice session outlives its caller's socket (S41-21, ADR 0080 §10).

The socket's first event names the session; every event carries `seq`. A socket that drops (1006)
leaves the model's socket open for `RESUME_GRACE_S`; the caller that comes back in time with
`session` and `after` gets what it missed, then the live events, on the same key, each response
priced once. Past the grace the session closes, `caller_gone`; a hang-up (1000) still closes it at
once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from choregos_adapters.fakes.realtime import AUDIO_B64
from httpx import AsyncClient

from .egress_support import as_person
from .test_agent_realtime import (
    GREETING,
    REALTIME,
    RESPONSE_USD,
    Voice,
    _agent,
    _events,
    _session,
    _socket,
)
from .test_agent_realtime import voice as voice  # noqa: PLC0414 - the fixture
from .test_agent_sessions import _key_rows, _ledger
from .ws_support import AsgiWebSocket, Closed


def _resume(session_id: str, after: int) -> str:
    return f"{REALTIME}&session={session_id}&after={after}"


async def _greeted(socket: AsgiWebSocket) -> tuple[str, int]:
    """The session's id from the first event, and the `seq` of the greeting's `response.done`."""
    first = await socket.receive_json()
    assert first["type"] == "choregos.session" and first["seq"] == 1
    done = await socket.until("response.done")
    return str(first["session_id"]), int(done["seq"])


async def _until(check: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await check():
        assert asyncio.get_running_loop().time() < deadline, "the condition never held"
        await asyncio.sleep(0.02)


async def _closed_reason(client: AsyncClient) -> str | None:
    read = await _session(client)
    return str(read["reason"]) if read["status"] != "open" else None


async def test_a_caller_back_in_time_misses_nothing_and_nothing_is_counted_twice(
    client: AsyncClient, project: dict[str, Any], voice: Voice
) -> None:
    from choregos_api.services.agent_realtime import held_relay

    await _agent(client)
    voice.model.say(GREETING).hear("Where is order 42?").say("It shipped yesterday.")
    socket = _socket(client)
    await socket.__aenter__()
    session_id, read_up_to = await _greeted(socket)
    await socket.send_json({"type": "input_audio_buffer.append", "audio": AUDIO_B64})
    await socket.send_json({"type": "input_audio_buffer.commit"})
    await socket.drop()  # the model answers while the caller is away
    relay = held_relay(session_id)
    assert relay is not None, "the model's socket stays open through the grace"
    await _until(lambda: _answered(relay))

    async with _socket(client, _resume(session_id, read_up_to)) as back:
        missed = [await back.receive_json()]
        while missed[-1]["type"] != "response.done":
            missed.append(await back.receive_json())
        await back.hang_up()

    assert [event["seq"] for event in missed] == list(range(read_up_to + 1, read_up_to + 1 + len(missed)))
    types = [event["type"] for event in missed]
    assert "conversation.item.input_audio_transcription.completed" in types
    assert any(e.get("transcript") == "It shipped yesterday." for e in missed)
    assert voice.model.connections == 1 and len(voice.minted) == 1, "one model socket, one key"
    (line,) = await _ledger()
    assert line.cost_usd == pytest.approx(2 * RESPONSE_USD), "two responses, each priced once"
    read = await _session(client)
    assert read["status"] == "closed" and read["reason"] == "closed" and read["responses"] == 2
    assert voice.gateway.revoked == [voice.key_id]
    journal = [event["type"] for event in await _events(session_id)]
    assert journal.index("caller.dropped") < journal.index("caller.resumed")


async def _answered(relay: Any) -> bool:
    return any('"response.done"' in text for _, text in list(relay._held)[-3:])


async def test_past_the_grace_the_session_closes_caller_gone_and_its_key_is_revoked(
    client: AsyncClient, project: dict[str, Any], voice: Voice, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import agent_realtime

    monkeypatch.setattr(agent_realtime, "RESUME_GRACE_S", 0.2)
    await _agent(client)
    voice.model.say(GREETING)
    socket = _socket(client)
    await socket.__aenter__()
    session_id, read_up_to = await _greeted(socket)
    await socket.drop()

    await _until(lambda: _closed_reason(client))
    assert await _closed_reason(client) == "caller_gone"
    assert voice.gateway.revoked == [voice.key_id] and voice.model.closed
    (row,) = await _key_rows()
    assert row.revoked
    with pytest.raises(Closed) as late:
        async with _socket(client, _resume(session_id, read_up_to)):
            pass
    assert late.value.code == 1008 and "caller_gone" in late.value.reason


async def test_a_hang_up_closes_the_session_at_once(
    client: AsyncClient, project: dict[str, Any], voice: Voice
) -> None:
    from choregos_api.services.agent_realtime import held_relay

    await _agent(client)
    voice.model.say(GREETING)
    async with _socket(client) as socket:
        session_id, _ = await _greeted(socket)
        await socket.hang_up()
    assert held_relay(session_id) is None
    assert await _closed_reason(client) == "closed", "a close frame is not a drop: no grace"
    assert voice.gateway.revoked == [voice.key_id]


async def test_another_person_cannot_take_the_session(
    client: AsyncClient, project: dict[str, Any], voice: Voice
) -> None:
    await _agent(client)
    voice.model.say(GREETING)
    async with _socket(client) as socket:
        session_id, read_up_to = await _greeted(socket)
        async with as_person(client, "maintainer@varga.dev", "org_admin") as other:
            with pytest.raises(Closed) as refused:
                async with _socket(other, _resume(session_id, read_up_to)):
                    pass
        assert refused.value.code == 1008 and "Agent session" in refused.value.reason
        await socket.send_json({"type": "response.create"})
        assert (await socket.until("response.done"))["seq"] > read_up_to, "the owner's socket is untouched"
        await socket.hang_up()


async def test_a_newer_socket_takes_over_the_older_one(
    client: AsyncClient, project: dict[str, Any], voice: Voice
) -> None:
    await _agent(client)
    voice.model.say(GREETING).say("Still here.")
    old = _socket(client)
    await old.__aenter__()
    session_id, read_up_to = await _greeted(old)
    async with _socket(client, _resume(session_id, read_up_to)) as new:
        replaced = await old.closed()
        assert replaced.code == 1000 and "another socket" in replaced.reason
        await new.send_json({"type": "response.create"})
        done = await new.until("response.done")
        assert done["seq"] > read_up_to
        await new.hang_up()
    journal = [event["type"] for event in await _events(session_id)]
    assert "caller.replaced" in journal


async def test_events_no_longer_held_are_refused_not_skipped(
    client: AsyncClient, project: dict[str, Any], voice: Voice, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import agent_realtime

    monkeypatch.setattr(agent_realtime, "RESUME_BUFFER_BYTES", 1)  # only the last event is held
    await _agent(client)
    voice.model.say(GREETING)
    async with _socket(client) as socket:
        session_id, read_up_to = await _greeted(socket)
        for after in (0, read_up_to + 5):
            with pytest.raises(Closed) as refused:
                async with _socket(client, _resume(session_id, after)):
                    pass
            assert refused.value.code == 1008 and f"after {after}" in refused.value.reason
        await socket.hang_up()


async def test_a_session_this_process_does_not_hold_is_answered_1013(
    client: AsyncClient, project: dict[str, Any], voice: Voice, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.routers import agent_realtime as router

    await _agent(client)
    voice.model.say(GREETING)
    async with _socket(client) as socket:
        session_id, read_up_to = await _greeted(socket)
        monkeypatch.setattr(router, "held_relay", lambda _: None)  # another API replica
        with pytest.raises(Closed) as elsewhere:
            async with _socket(client, _resume(session_id, read_up_to)):
                pass
        assert elsewhere.value.code == 1013
        await socket.hang_up()


async def test_a_caller_who_lost_the_project_cannot_resume(
    client: AsyncClient, project: dict[str, Any], voice: Voice
) -> None:
    await _agent(client)
    voice.model.say(GREETING)
    async with as_person(client, "dev@varga.dev", "developer") as person:
        socket = _socket(person)
        await socket.__aenter__()
        session_id, read_up_to = await _greeted(socket)
        await socket.drop()
        await _demote("dev@varga.dev", "viewer")
        with pytest.raises(Closed) as refused:
            async with _socket(person, _resume(session_id, read_up_to)):
                pass
    assert refused.value.code == 1008 and "requires working on that project" in refused.value.reason


async def _demote(email: str, role: str) -> None:
    from choregos_api.db.models import Membership, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        membership = (await db.execute(select(Membership).where(Membership.user_id == user.id))).scalar_one()
        membership.role = role
