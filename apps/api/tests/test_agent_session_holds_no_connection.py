"""An agent session holds no pooled connection while its model or a tool answers (#1376).

A prompt session's response used to run its whole loop inside the request's transaction: on
PostgreSQL the request held one pooled connection `idle in transaction` for every model call (up to
300 s each, up to `max_turns` of them) and every tool call; a hosted pod's tool call
(`callAgentSessionTool`) awaited its connector the same way, as `callRunTool` did before #1354. Each
test below looks at the engine's pool at the moment the fake model, the fake connector or the fake
orchestrator is asked, and finds no connection lent out: the request's transaction committed before
the call left, and what the call brought back is booked after it.

These run on SQLite and, in CI's second pass, on PostgreSQL under RLS
(`CHOREGOS_TEST_SUITE_ON_POSTGRES=1`), where the booking after the call also proves that the scope
is set again. The pool itself, exhausted and not, is `test_agent_session_pool_postgres.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient

from .test_agent_hosted_sessions import _ask as _ask_hosted
from .test_agent_hosted_sessions import (  # noqa: F401 - `registry`, `temporal`, `pod_http`, `quick` are fixtures
    _hosted,
    _pod,
    _started_session,
    _token,
    pod_http,
    quick,
    registry,
    temporal,
)
from .test_agent_realtime import GREETING, _socket, voice  # noqa: F401 - `voice` is a fixture
from .test_agent_realtime import _agent as _voice_agent
from .test_agent_realtime import _session as _voice_session
from .test_agent_sessions import (  # noqa: F401 - `supplier` and `upstream` are fixtures
    ORG,
    Upstream,
    _agent,
    _ask,
    _ledger,
    _supplier,
    supplier,
    upstream,
)

pytestmark = pytest.mark.asyncio

READ = "fournisseur__suivi_commande"
GRANT = [{"connector": "fournisseur", "tools": ["suivi_*"]}]


def checked_out() -> int:
    """The connections the API's engine has lent out right now."""
    from choregos_api.db.session import get_engine

    return int(get_engine().pool.checkedout())


def _watch_the_model(behind: Upstream, seen: list[int]) -> None:
    """The model behind the gateway, which notes the connections lent out each time it is asked."""
    from choregos_api.services.agent_sessions import set_model_transport

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(checked_out())
        return behind.handle(request)

    set_model_transport(httpx.MockTransport(answer))


def _watch_the_connector(server: Any, seen: list[int]) -> None:
    """The supplier's read, which notes the connections lent out when it is called."""

    def track(arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append(checked_out())
        return {"numero": arguments["numero"], "state": "shipped"}

    server.gestures["suivi_commande"] = track


@contextmanager
def _a_commit_refused_once() -> Iterator[Callable[[], None]]:
    """`arm()` makes the next commit of any session fail, as a database gone away at that moment."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    armed: list[bool] = []

    def refuse(_session: Any) -> None:
        if armed:
            armed.clear()
            raise ConnectionError("the database went away at the commit")

    event.listen(Session, "before_commit", refuse)
    try:
        yield lambda: armed.append(True)
    finally:
        event.remove(Session, "before_commit", refuse)


async def _tool_events(session_id: str) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts.enums import EventType
    from sqlalchemy import select

    async with session_scope() as db:
        query = select(Event).where(Event.type == EventType.TOOL_CALLED.value, Event.subject == session_id)
        return list((await db.execute(query)).scalars())


# ───────────────────────────── a prompt session's response ─────────────────────────────


async def test_a_response_calls_the_model_and_its_tools_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture imported above
    supplier: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """The first model call also commits the session the request opened, its reservation and its key."""
    await _supplier(client)
    await _agent(client, mcp_servers=GRANT)
    at_the_model: list[int] = []
    at_the_connector: list[int] = []
    _watch_the_model(upstream, at_the_model)
    _watch_the_connector(supplier, at_the_connector)
    upstream.model.call(READ, {"numero": "42"}).say("Order 42 is on its way.")

    answered = await _ask(client, "Where is order 42?")

    assert answered.status_code == 200, answered.text
    answer = answered.json()
    assert answer["status"] == "completed" and answer["output_text"] == "Order 42 is on its way."
    assert at_the_model == [0, 0], "the request held a connection while the model answered"
    assert at_the_connector == [0], "the request held a connection while the connector answered"
    # Booked after the calls, in the scope they left with: under RLS a row written without it is refused.
    session_id = answer["session_id"]
    (line,) = [entry for entry in await _ledger() if entry.kind == "tool"]
    assert (line.session_id, line.model) == (session_id, READ)
    assert [event.payload["status_code"] for event in await _tool_events(session_id)] == [200]
    read = (await client.get(f"{ORG}/agent-sessions/{session_id}")).json()
    assert (read["responses"], read["tool_calls"]) == (1, 1)
    assert [e["type"] for e in read["events"]] == [
        "session.opened",
        "input.text",
        "context.breakdown",  # the first call, at once (ADR 0141 §5)
        "prompt.turn",
        "prompt.tool_call",
        "prompt.turn",
        "context.breakdown",  # the last call, when the response ends
        "response.completed",
    ]
    again = await _ask(client, "And order 43?", session_id=session_id)
    assert again.status_code == 200 and at_the_model[2:] == [0], "a later response holds none either"


@pytest.mark.parametrize("failing", ["line", "commit"])
async def test_a_response_that_cannot_be_booked_is_said_and_keeps_what_its_calls_wrote(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture imported above
    supplier: Any,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """The journal and the tool's booking commit turn by turn; the history and the response's line
    commit together at the end. When that last transaction fails — a statement of it, or its commit —,
    the caller gets a 500, the log says what the response had asked for, and the history the next
    response sends the model is unchanged."""
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from choregos_api.services import agent_sessions
    from choregos_api.services.agent_sessions import set_model_transport
    from sqlalchemy import select

    await _supplier(client)
    await _agent(client, mcp_servers=GRANT)
    upstream.model.call(READ, {"numero": "42"}).say("Order 42 is on its way.")
    journal = agent_sessions.journal

    async def lost(session: Any, record: Any, type_: str, payload: dict[str, Any]) -> None:
        if type_.startswith("response."):
            raise ConnectionError("the database went away after the calls")
        await journal(session, record, type_, payload)

    with _a_commit_refused_once() as arm, structlog.testing.capture_logs() as logged:
        if failing == "line":
            monkeypatch.setattr(agent_sessions, "journal", lost)
        else:
            asked: list[httpx.Request] = []

            def answer(request: httpx.Request) -> httpx.Response:
                # The model's last answer arms it: the next commit is the response's booking.
                asked.append(request)
                if len(asked) == 2:
                    arm()
                return upstream.handle(request)

            set_model_transport(httpx.MockTransport(answer))
        answered = await _ask(client, "Where is order 42?")

    assert answered.status_code == 500, answered.text
    (not_booked,) = [entry for entry in logged if entry["event"] == "agent_session.response_not_booked"]
    assert not_booked["log_level"] == "warning"
    made = (not_booked["model_calls"], not_booked["tool_calls"], not_booked["error"])
    assert made == (2, 1, "ConnectionError")
    async with session_scope() as db:
        (record,) = (await db.execute(select(AgentSessionRecord))).scalars()
    assert not_booked["session_id"] == record.id
    assert (record.responses, record.tool_calls) == (0, 1), "the tool's booking is kept, not the response's"
    assert [m["role"] for m in record.messages] == ["system"], "no half of a turn in the history"
    (line,) = [entry for entry in await _ledger() if entry.kind == "tool"]
    assert line.session_id == record.id


# ───────────────────────────── a hosted session ─────────────────────────────


async def test_a_pods_tool_call_awaits_its_connector_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture imported above
    registry: Any,  # noqa: F811 - the fixture imported above
    temporal: Any,  # noqa: F811 - the fixture imported above
    pod_http: AsyncClient,  # noqa: F811 - the fixture imported above
    supplier: Any,  # noqa: F811 - the fixture imported above
    quick: None,  # noqa: F811 - the fixture imported above
) -> None:
    """`callAgentSessionTool`. The pod calls once its first turn is answered: no response is waiting,
    so the only connection that could be lent out is the call's own."""
    await _supplier(client)
    await _hosted(client, mcp_servers=GRANT)
    asked = asyncio.create_task(_ask_hosted(client, "Where is order 42?"))
    session_id = await _started_session(temporal)
    await _pod(pod_http, session_id, [{"status": "answered", "text": "Looking."}], [])
    assert (await asked).json()["output_text"] == "Looking."
    seen: list[int] = []
    _watch_the_connector(supplier, seen)

    called = await pod_http.post(
        f"/api/v1/internal/agent-sessions/{session_id}/tools/{READ}",
        json={"arguments": {"numero": "42"}},
        headers=_token(session_id),
    )

    assert called.status_code == 200, called.text
    assert seen == [0], "the request held a connection while the connector answered"
    (line,) = [entry for entry in await _ledger() if entry.kind == "tool"]
    assert line.session_id == session_id
    assert [event.payload["status_code"] for event in await _tool_events(session_id)] == [200]
    read = (await client.get(f"{ORG}/agent-sessions/{session_id}")).json()
    assert read["tool_calls"] == 1


@pytest.mark.parametrize("failing", ["event", "commit"])
async def test_a_pods_tool_call_that_cannot_be_booked_is_said(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture imported above
    registry: Any,  # noqa: F811 - the fixture imported above
    temporal: Any,  # noqa: F811 - the fixture imported above
    pod_http: AsyncClient,  # noqa: F811 - the fixture imported above
    supplier: Any,  # noqa: F811 - the fixture imported above
    quick: None,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """The connector answered; its count, its line and its event are one transaction after it. When that
    fails — a statement of it, or its commit —, the pod gets a 500 and the log names what the line
    would have held."""
    from choregos_api.services import agent_sessions

    await _supplier(client)
    await _hosted(client, mcp_servers=GRANT)
    asked = asyncio.create_task(_ask_hosted(client, "Where is order 42?"))
    session_id = await _started_session(temporal)
    await _pod(pod_http, session_id, [{"status": "answered", "text": "Looking."}], [])
    await asked

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("the database went away after the call")

    with _a_commit_refused_once() as arm, structlog.testing.capture_logs() as logged:
        if failing == "event":
            monkeypatch.setattr(agent_sessions, "persist_event", lost)
        else:
            # The connector's answer arms it: the next commit is the call's booking.
            def answering(arguments: dict[str, Any]) -> dict[str, Any]:
                arm()
                return {"numero": arguments["numero"], "state": "shipped"}

            supplier.gestures["suivi_commande"] = answering
        called = await pod_http.post(
            f"/api/v1/internal/agent-sessions/{session_id}/tools/{READ}",
            json={"arguments": {"numero": "42"}},
            headers=_token(session_id),
        )

    assert called.status_code == 500, called.text
    assert [call[0] for call in supplier.calls] == ["suivi_commande"], "the call itself was made"
    (not_booked,) = [entry for entry in logged if entry["event"] == "tool_call.not_booked"]
    assert not_booked["log_level"] == "warning"
    assert (not_booked["session_id"], not_booked["tool"], not_booked["provider"]) == (
        session_id,
        READ,
        "mcp:fournisseur",
    )
    assert (not_booked["status_code"], not_booked["error"]) == (200, "ConnectionError")
    assert [entry for entry in await _ledger() if entry.kind == "tool"] == []
    read = (await client.get(f"{ORG}/agent-sessions/{session_id}")).json()
    assert read["tool_calls"] == 0, "the count is booked with the line, or not at all"


async def test_a_hosted_response_asks_for_its_pod_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811 - the fixture imported above
    registry: Any,  # noqa: F811 - the fixture imported above
    temporal: Any,  # noqa: F811 - the fixture imported above
    pod_http: AsyncClient,  # noqa: F811 - the fixture imported above
    quick: None,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The response commits its turn before the orchestrator is asked and holds nothing until the pod
    answers. On SQLite the scope is a no-op, so this one is red without the fix on PostgreSQL only: the
    scope set again right after the commit began the transaction the whole wait then held."""
    seen: list[int] = []
    start = temporal.start_session_pod

    async def starting(workflow_id: str, payload: dict[str, Any]) -> str:
        seen.append(checked_out())
        return str(await start(workflow_id, payload))

    monkeypatch.setattr(temporal, "start_session_pod", starting)
    await _hosted(client)
    asked = asyncio.create_task(_ask_hosted(client, "Ticket 7 is about a refund."))
    session_id = await _started_session(temporal)
    await _pod(pod_http, session_id, [{"status": "answered", "text": "Billing."}], [])

    assert (await asked).json()["output_text"] == "Billing."
    assert seen == [0], "the response held a connection while the orchestrator was asked"
    read = (await client.get(f"{ORG}/agent-sessions/{session_id}")).json()
    assert read["responses"] == 1 and read["events"][-1]["type"] == "response.completed"


# ───────────────────────────── a voice session ─────────────────────────────


async def test_a_voice_sessions_tool_call_awaits_its_connector_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    voice: Any,  # noqa: F811 - the fixture imported above
    supplier: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """The relay's write for a function call is one lock and one short transaction: the connector is
    now awaited outside it, and the call booked after it."""
    await _supplier(client)
    await _voice_agent(client, mcp_servers=GRANT)
    seen: list[int] = []
    _watch_the_connector(supplier, seen)
    voice.model.say(GREETING)
    voice.model.call(READ, {"numero": "42"}).say("It is on its way.")
    async with _socket(client) as socket:
        await socket.until("response.done")
        await socket.send_json({"type": "response.create"})
        await socket.until("response.function_call_arguments.done")
        await socket.until("response.output_audio_transcript.done")
        await socket.hang_up()

    assert seen == [0], "the relay held a connection while the connector answered"
    read = await _voice_session(client)
    assert read["tool_calls"] == 1
    (line,) = [entry for entry in await _ledger() if entry.kind == "tool"]
    assert line.session_id == read["id"]
