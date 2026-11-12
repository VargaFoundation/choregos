# SPDX-License-Identifier: Apache-2.0
"""Stopping an assistant stops its conversations' turns, on any replica (S64-09c, ADR 0142 §4 bis).

`POST /orgs/{org}/assistants/{slug}/stop` needs `assistant:manage`. The installation keeps who stopped it,
when and why, and reads `suspended` whatever its readiness says, so no new turn starts and the next
derivation does not undo the stop; every turn in flight of its conversations gets a cancel request on the
conversation's row, which the turn reads between its model and tool calls (a replica with no registry
entry for the turn stops it too); the conversations stay open. `POST .../resume` clears the stop (409 when
there is none). An agent's stop cancels the turns of its conversations the same way, in every person's
space, and counts them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import choregos_contracts as contracts
import httpx
import pytest
from httpx import AsyncClient
from jsonschema import Draft202012Validator
from sqlalchemy import select

from .agent_scopes_kit import as_user
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
from .kits.conversations import _events, _open, _record
from .test_agent_sessions import Upstream, _agent, upstream  # noqa: F401

AUDIENCE = "investors"
ASSISTANT = f"{ORG}/assistants/investment-assistant"
PERSON = "probe@varga.dev"


def _valid(body: Any, schema: str) -> None:
    errors = list(
        Draft202012Validator(
            {"$ref": f"#/components/schemas/{schema}", "components": contracts.load_openapi()["components"]}
        ).iter_errors(body)
    )
    assert errors == [], errors


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


async def _person(client: AsyncClient, app: Any, email: str = PERSON) -> AsyncClient:
    await _member(client, email, [AUDIENCE])
    return await _as(app, email)


async def _opened(person: AsyncClient) -> str:
    opened = await person.post(OPEN, json={})
    assert opened.status_code == 201, opened.text
    return str(opened.json()["id"])


async def _turn(person: AsyncClient, conversation: str, text: str = "Hello") -> Any:
    return await person.post(f"{ME}/conversations/{conversation}/messages", json={"input": text})


async def _audited(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.ts))
        ).scalars()
        return [{"target_id": row.target_id, **dict(row.payload or {})} for row in rows]


async def _ledger_of(conversation: str) -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return list(
            (await session.execute(select(CostLedger).where(CostLedger.session_id == conversation))).scalars()
        )


class Held:
    """The upstream, with the first model call of each turn held until `release` (booked on its key
    first, so a turn cancelled while it waits has spent)."""

    def __init__(self, model: Upstream) -> None:
        self.model = model
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        side = bool((json.loads(request.content).get("metadata") or {}).get("purpose"))
        answer = self.model.handle(request)
        if not side:
            self.calls += 1
            if self.calls == 1:
                self.reached.set()
                await self.release.wait()
        return answer


def _hold(model: Upstream) -> Held:
    from choregos_api.services.agent_sessions import set_model_transport

    held = Held(model)
    set_model_transport(httpx.MockTransport(held))
    return held


async def _in_flight(conversation: str, held: Held) -> None:
    """The turn reached the model and its lease is committed, as another request would see it."""
    await asyncio.wait_for(held.reached.wait(), 10)
    for _ in range(100):
        if (await _record(conversation)).turn_lease_until is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the running turn holds no lease")


def _on_another_replica(monkeypatch: pytest.MonkeyPatch) -> None:
    """The turn runs in another process: this one's registry never holds it. The PostgreSQL poller
    (`watch_cancel`, S63-23) is switched off too, so only the turn's own read of the request can stop it."""
    from choregos_api.services import conversation_turns, conversations

    def nowhere(_conversation_id: str, _task: Any) -> None:
        return None

    async def no_poller(_conversation_id: str, _task: Any) -> None:
        return None

    monkeypatch.setattr(conversations, "register_turn", nowhere)
    monkeypatch.setattr(conversations, "watch_cancel", no_poller)
    monkeypatch.setattr(conversation_turns, "_RUNNING", {})


def _record_tool_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The tools the turn really called (an MCP connector's call), recorded instead of made."""
    from choregos_api.services import agent_sessions

    called: list[str] = []

    async def call_session_tool(_session: Any, _record: Any, _project: Any, tool: Any, *_: Any) -> str:
        called.append(str(tool.name))
        return "61.4"

    monkeypatch.setattr(agent_sessions, "call_session_tool", call_session_tool)
    return called


# ───────────────────────── stop, refuse, resume ─────────────────────────


async def test_a_stopped_assistant_refuses_a_new_turn_until_it_is_resumed(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    person = await _person(client, app)
    await _ready(client)
    upstream.model.say("First.").say("After the resume.")
    conversation = await _opened(person)
    assert (await _turn(person, conversation)).status_code == 200

    stopped = await client.post(f"{ASSISTANT}/stop", json={"reason": "leaking quotes"})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {
        "slug": "investment-assistant",
        "status": "suspended",
        "turns_cancelled": 0,
        "not_stopped": [],
    }
    _valid(stopped.json(), "AssistantStopResult")
    shown = (await client.get(ASSISTANT)).json()
    assert shown["status"] == "suspended"
    assert shown["stopped"]["by"] == "admin@varga.dev" and shown["stopped"]["reason"] == "leaking quotes"
    _valid(shown, "Assistant")

    calls, keys = len(upstream.model.requests), len(upstream.gateway.revoked)
    refused = await _turn(person, conversation, "Again")
    assert refused.status_code == 409 and "suspended" in refused.text, refused.text
    assert len(upstream.model.requests) == calls and len(upstream.gateway.revoked) == keys, "no key, no call"
    kept = await person.get(f"{ME}/conversations/{conversation}")
    assert kept.status_code == 200 and kept.json()["status"] == "open", "the conversation stays open"

    # The next derivations (the readiness, a configuration change) do not undo the stop.
    readiness = await client.get(f"{ASSISTANT}/readiness")
    assert readiness.json()["status"] == "suspended" and readiness.json()["items"] == []
    patched = await client.patch(ASSISTANT, json={"audience_groups": [AUDIENCE]})
    assert patched.status_code == 200 and patched.json()["status"] == "suspended", patched.text

    resumed = await client.post(f"{ASSISTANT}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "ready" and resumed.json()["stopped"] is None
    _valid(resumed.json(), "Assistant")
    assert (await client.post(f"{ASSISTANT}/resume")).status_code == 409, "a resumed assistant is not stopped"
    again = await _turn(person, conversation, "Again")
    assert again.status_code == 200 and again.json()["output_text"] == "After the resume.", again.text

    (stop,) = await _audited("assistant.stop")
    assert stop["target_id"] == "investment-assistant" and stop["reason"] == "leaking quotes"
    assert stop["turns_cancelled"] == 0
    (resume,) = await _audited("assistant.resume")
    assert resume["stopped_reason"] == "leaking quotes" and resume["status"] == "ready"


async def test_a_second_stop_keeps_the_first_and_is_audited_again(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    await _ready(client)
    assert (await client.post(f"{ASSISTANT}/stop", json={"reason": "first"})).status_code == 200
    first = (await client.get(ASSISTANT)).json()["stopped"]
    assert (await client.post(f"{ASSISTANT}/stop", json={"reason": "second"})).status_code == 200
    assert (await client.get(ASSISTANT)).json()["stopped"] == first, "the stop in force is the first"
    assert [a["reason"] for a in await _audited("assistant.stop")] == ["first", "second"]


async def test_a_caller_without_assistant_manage_gets_403_and_nothing_changes(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
) -> None:
    person = await _person(client, app)  # in the audience: `assistant:use`, not `assistant:manage`
    await _ready(client)
    assert (await person.post(f"{ASSISTANT}/stop", json={"reason": "mine"})).status_code == 403
    assert (await client.get(ASSISTANT)).json()["stopped"] is None
    assert (await client.post(f"{ASSISTANT}/stop", json={"reason": "admin"})).status_code == 200
    assert (await person.post(f"{ASSISTANT}/resume")).status_code == 403
    assert (await client.get(ASSISTANT)).json()["status"] == "suspended"
    assert [a["reason"] for a in await _audited("assistant.stop")] == ["admin"]
    assert await _audited("assistant.resume") == []
    assert (await client.post(f"{ASSISTANT}/stop", json={"reason": ""})).status_code == 422
    assert (await client.post(f"{ORG}/assistants/unknown/stop", json={"reason": "x"})).status_code == 404


# ───────────────────────── the turn in flight ─────────────────────────


@pytest.mark.parametrize("where", ["this replica", "another replica"])
async def test_a_turn_in_flight_ends_at_its_next_call_with_its_spend_booked_once(
    where: str,
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person = await _person(client, app)
    await _ready(client)
    # The held call answers with a tool call: the turn reads the request before calling the tool.
    upstream.model.call("market_data__get_quote", {"isin": "VRG"}).say("never said")
    called = _record_tool_calls(monkeypatch)
    conversation = await _opened(person)
    elsewhere = await _conversation(PERSON, None, running=True)  # no assistant's: not this stop's
    if where == "another replica":
        _on_another_replica(monkeypatch)
    held = _hold(upstream)
    turn = asyncio.ensure_future(_turn(person, conversation, "Quote VRG"))
    await _in_flight(conversation, held)

    stopping = asyncio.ensure_future(client.post(f"{ASSISTANT}/stop", json={"reason": "runaway"}))
    if where == "another replica":
        # Nothing here can reach the turn's task: it stops when it reads the request, at its next call.
        stopped = await asyncio.wait_for(stopping, 10)
        assert (await _record(conversation)).turn_cancel_requested_at is not None, "the request is on the row"
        held.release.set()
    else:
        stopped = await asyncio.wait_for(stopping, 10)  # cancelled in this process, the model still held
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["turns_cancelled"] == 1
    assert (await _record(elsewhere)).turn_cancel_requested_at is None, "another assistant's turn is asked"

    answer = await asyncio.wait_for(turn, 10)
    held.release.set()
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "failed" and answer.json()["error"]["code"] == "cancelled"
    assert held.calls == 1, "no model call after the cancel was read"
    assert called == [], "no tool call after the cancel was read"
    assert upstream.gateway.keys == {} and len(upstream.gateway.revoked) == 1, "the turn's key is revoked"
    lines = await _ledger_of(conversation)
    assert len(lines) == 1 and lines[0].cost_usd > 0, "the spend is booked once"
    events = await _events(conversation)
    assert events.count("turn.cancelled") == 1 and events.count("turn.ended") == 1
    record = await _record(conversation)
    assert record.status == "open", "the conversation stays open"
    assert record.turn_lease_until is None and record.turn_cancel_requested_at is None
    (audit,) = await _audited("assistant.stop")
    assert audit["turns_cancelled"] == 1


# ───────────────────────── an agent's stop ─────────────────────────


async def test_an_agents_stop_cancels_the_turns_of_its_conversations_in_every_persons_space(
    client: AsyncClient,
    admin: str,
    app: Any,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _agent(client)
    await _member(client, PERSON, [])
    person = await _as(app, PERSON)
    upstream.model.call("lookup", {"order": 42}).say("never said")
    running = await _open("varga", PERSON)  # the person's space: the admin cannot read it
    idle = await _open("varga", PERSON)
    other_agent = await _conversation(PERSON, None, running=True, agent="writer")
    _on_another_replica(monkeypatch)
    held = _hold(upstream)
    turn = asyncio.ensure_future(_turn(person, running, "Where is order 42?"))
    await _in_flight(running, held)

    stopped = await client.post(f"{ORG}/agents/helper/stop", json={"reason": "runaway"})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["turns_cancelled"] == 1 and stopped.json()["sessions_closed"] == 0
    held.release.set()
    answer = await asyncio.wait_for(turn, 10)
    assert answer.json()["error"]["code"] == "cancelled", answer.text
    assert held.calls == 1 and len(await _ledger_of(running)) == 1
    assert (await _record(other_agent)).turn_cancel_requested_at is None, "another agent's turn is asked"
    for conversation in (running, idle):
        assert (await _record(conversation)).status == "open", "a person's conversation is not closed"
    (audit,) = await _audited("agent.stop")
    assert audit["turns_cancelled"] == 1


# ───────────────────────── the activity list ─────────────────────────


async def _conversation(
    user_email: str,
    installation_id: str | None,
    *,
    running: bool,
    events: tuple[str, ...] = ("turn.started", "model.call"),
    agent: str = "helper",
    ended_minutes_ago: int = 5,
) -> str:
    from datetime import timedelta

    from choregos_api.db.models import AgentSessionEvent, AgentSessionRecord, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.services.personal_spaces import personal_space
    from choregos_core import utcnow

    now = utcnow()
    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == user_email))).scalar_one()
        space = await personal_space(session, organisation, user)
        record = AgentSessionRecord(
            org_id=organisation.id,
            project_id=space.id,
            agent_slug=agent,
            agent_version=1,
            user_id=user.id,
            status="open",
            kind="conversation",
            installation_id=installation_id,
            model="m-3",
            turns=1,
            expires_at=now + timedelta(hours=1),
            last_activity_at=now - timedelta(minutes=0 if running else ended_minutes_ago),
            turn_lease_until=now + timedelta(minutes=30) if running else None,
        )
        session.add(record)
        await session.flush()
        for seq, type_ in enumerate(events, start=1):
            session.add(
                AgentSessionEvent(
                    org_id=organisation.id, session_id=record.id, seq=seq, type=type_, payload={}, ts=now
                )
            )
        return str(record.id)


async def _installation_ids() -> dict[str, str]:
    from choregos_api.db.models import AssistantInstallation, Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        ids = {}
        for slug in ("coach", "tutor"):
            row = AssistantInstallation(
                org_id=org_id, slug=slug, plugin="p", plugin_version="1", manifest_digest="sha256:0",
                assistant_name=slug, owner_groups=[f"{slug}-owners"],
            )  # fmt: skip
            session.add(row)
            await session.flush()
            ids[slug] = row.id
        return ids


@asynccontextmanager
async def _signed_in(app: Any, email: str) -> AsyncIterator[AsyncClient]:
    http = await _as(app, email)
    try:
        yield http
    finally:
        await http.aclose()


async def _plain_session(project_id: str, user_email: str) -> str:
    """A session of the `/responses` kind: no assistant's."""
    from datetime import timedelta

    from choregos_api.db.models import AgentSessionRecord, Project, User
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope(orgs="*") as session:
        project_row = await session.get(Project, project_id)
        user = (await session.execute(select(User).where(User.email == user_email))).scalar_one()
        assert project_row is not None
        record = AgentSessionRecord(
            org_id=project_row.org_id, project_id=project_id, agent_slug="helper", agent_version=1,
            user_id=user.id, status="open", model="m-2", expires_at=utcnow() + timedelta(hours=1),
        )  # fmt: skip
        session.add(record)
        await session.flush()
        return str(record.id)


def _ids(body: dict[str, Any], part: str = "in_flight", kind: str = "turn") -> set[str]:
    return {entry["id"] for entry in body[part] if entry["kind"] == kind}


async def test_the_activity_lists_turns_with_the_assistant_filter_to_whoever_may_see_them(
    client: AsyncClient,
    admin: str,
    app: Any,
    project: dict[str, Any],
) -> None:
    activity = f"{ORG}/activity"
    installations = await _installation_ids()
    await _member(client, PERSON, [])
    await _member(client, "other@varga.dev", [])
    await _member(client, "auditor@varga.dev", ["coach-owners"])  # owns, so audits, `coach` alone
    coach = await _conversation(PERSON, installations["coach"], running=True)
    tutor = await _conversation(PERSON, installations["tutor"], running=True)
    plain = await _conversation(PERSON, None, running=True, agent="writer")
    session_id = await _plain_session(project["id"], PERSON)
    ended = await _conversation(
        PERSON,
        installations["coach"],
        running=False,
        events=("turn.started", "model.call", "turn.ended", "turn.cancelled"),
    )
    old = await _conversation(
        PERSON,
        installations["coach"],
        running=False,
        events=("turn.started", "turn.ended"),
        ended_minutes_ago=120,
    )

    async with _signed_in(app, PERSON) as person:
        listed = await person.get(activity)
        assert listed.status_code == 200, listed.text
        _valid(listed.json(), "ActivityList")
        assert _ids(listed.json()) == {coach, tutor, plain}
        assert _ids(listed.json(), "finished") == {ended}, "the turn ended two hours ago is not shown"
        assert _ids(listed.json(), kind="session") == {session_id}, "a conversation is listed as a session"
        entry = next(e for e in listed.json()["in_flight"] if e["id"] == coach)
        assert (entry["status"], entry["assistant"], entry["agent_slug"], entry["model"]) == (
            "running",
            installations["coach"],
            "helper",
            "m-3",
        )
        assert (entry["steps"], entry["last_event"], entry["can_stop"]) == (2, "model.call", True)
        assert entry["initiated_by"] == f"person:{PERSON}" and entry["project_slug"] is None
        (finished,) = [e for e in listed.json()["finished"] if e["id"] == ended]
        assert finished["status"] == "cancelled" and finished["can_stop"] is False

        narrowed = (await person.get(activity, params={"assistant": installations["coach"]})).json()
        assert _ids(narrowed) == {coach} and _ids(narrowed, "finished") == {ended}
        assert [e["kind"] for e in narrowed["in_flight"] + narrowed["finished"]] == ["turn", "turn"]
        by_kind = (await person.get(activity, params={"kind": "turn", "agent": "writer"})).json()
        assert _ids(by_kind) == {plain}
        assert _ids((await person.get(activity, params={"kind": "run"})).json()) == set()
        assert _ids((await person.get(activity, params={"project": "billing-api"})).json()) == set()

    async with _signed_in(app, "other@varga.dev") as other:
        assert _ids((await other.get(activity)).json()) == set(), "another person's turns are listed"
    async with _signed_in(app, "auditor@varga.dev") as auditor:
        seen = (await auditor.get(activity, params={"kind": "turn"})).json()
        assert _ids(seen) == {coach}, "an auditor sees the turns of the assistants it audits, and only those"
        assert seen["in_flight"][0]["can_stop"] is False, "a turn is stopped by its person"
    assert old not in _ids((await client.get(activity)).json(), "finished")


async def test_the_stream_sends_a_turn_to_whoever_may_see_it(
    client: AsyncClient,
    admin: str,
    app: Any,
    project: dict[str, Any],
) -> None:
    from choregos_api.db.models import Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.activity import ActivityFilters, ActivityReader, entry_of_event
    from choregos_contracts import ChoregosEvent, EventType

    installations = await _installation_ids()
    await _member(client, PERSON, [])
    await _member(client, "other@varga.dev", [])
    coach = await _conversation(PERSON, installations["coach"], running=True)
    tool = ChoregosEvent.emit(EventType.TOOL_CALLED, source="/t", subject=coach, session_id=coach)

    async with session_scope(orgs="*") as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        users = dict((await session.execute(select(User.email, User.id))).all())

    def reader(email: str, **filters: Any) -> ActivityReader:
        principal = Principal(user_id=users[email], email=email, org_roles={"varga": Role.DEVELOPER})
        return ActivityReader(
            principal=principal, org_id=org_id, org_slug="varga", filters=ActivityFilters(**filters)
        )

    seen = entry_of_event
    async with session_scope("varga") as session:
        found = await seen(session, reader(PERSON), tool)
        assert found is not None and found[0] == "activity.upsert" and found[1].kind == "turn"
        assert found[1].id == coach
        assert await seen(session, reader(PERSON, assistant=installations["tutor"]), tool) is None
        assert await seen(session, reader(PERSON, kind="session"), tool) is None
        assert await seen(session, reader("other@varga.dev"), tool) is None
