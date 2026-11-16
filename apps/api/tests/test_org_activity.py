"""What is in flight, as one list and a stream (S64-05, ADR 0142 §1, ADR 0141 §2).

A run is listed only to whoever may read its project; each filter keeps only its entries; a run's spend
so far is the gateway's count, asked at most every 30 s, and unknown (null) when the gateway fails;
what ended is shown for an hour, at most 50; the stream sends an upsert, then an ended, for a run.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_core import Spend, utcnow
from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator

from .conftest import login

ACTIVITY = "/api/v1/orgs/varga/activity"


@pytest.fixture(autouse=True)
def _no_spend_remembered() -> Iterator[None]:
    from choregos_api.services import activity

    activity.forget_spends()
    yield
    activity.forget_spends()


async def _project(client: AsyncClient, slug: str) -> dict[str, Any]:
    config = {"slug": slug, "org": "varga", "repo": {"url": f"https://github.com/varga/{slug}.git"}}
    response = await client.post(
        "/api/v1/orgs/varga/projects", json={"slug": slug, "name": slug, "config": config}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _only(email: str, role: str | None = None, project_slug: str | None = None) -> None:
    """The person holds this one role on this one project, or nothing in the organisation (a development
    sign-in makes everyone a developer of it)."""
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == user.id))
        if role is not None:
            project_id = (
                await session.execute(
                    select(Project.id).where(Project.org_id == org.id, Project.slug == project_slug)
                )
            ).scalar_one()
            session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))


async def _org_id() -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str(
            (await session.execute(select(Organization.id).where(Organization.slug == "varga"))).scalar_one()
        )


async def _user_id(email: str) -> str:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str((await session.execute(select(User.id).where(User.email == email))).scalar_one())


@asynccontextmanager
async def _as(
    client: AsyncClient, email: str, role: str | None = None, project_slug: str | None = None
) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        await _only(email, role, project_slug)
        yield c


async def _workflow(project_id: str, name: str) -> str:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = WorkflowDef(project_id=project_id, name=name, version=1, yaml="", json_doc={}, checksum=name)
        session.add(row)
        await session.flush()
        return str(row.id)


async def _run(
    project_id: str,
    run_id: str,
    *,
    status: str = "running",
    agent: str = "writer",
    workflow_def_id: str | None = None,
    started_ago: timedelta = timedelta(minutes=5),
    ended_ago: timedelta | None = None,
    gateway_key_id: str | None = None,
    events: tuple[str, ...] = (),
    **fields: Any,
) -> None:
    from choregos_api.db.models import Run, RunEvent, WorkItem
    from choregos_api.db.session import session_scope

    now = utcnow()
    async with session_scope() as session:
        item = WorkItem(
            project_id=project_id,
            tracker_key=f"KEY-{run_id}",
            title=run_id,
            state="doing",
            workflow_def_id=workflow_def_id,
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project_id,
                stage_role="implement",
                status=status,
                agent_slug=agent,
                model="m-1",
                started_at=now - started_ago,
                ended_at=now - ended_ago if ended_ago is not None else None,
                gateway_key_id=gateway_key_id,
                **fields,
            )
        )
        await session.flush()
        for seq, type_ in enumerate(events, start=1):
            session.add(RunEvent(run_id=run_id, seq=seq, type=type_, payload={}, ts=now))


async def _session(project: dict[str, Any], user_id: str, session_id: str, *, agent: str = "helper") -> None:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            AgentSessionRecord(
                id=session_id,
                org_id=await _org_id(),
                project_id=project["id"],
                agent_slug=agent,
                agent_version=1,
                user_id=user_id,
                status="open",
                model="m-2",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )


def _ids(body: dict[str, Any], part: str = "in_flight") -> set[str]:
    return {entry["id"] for entry in body[part]}


class _Gateway:
    """A gateway whose spend is counted, or raises."""

    def __init__(self, spend: Spend | None = None) -> None:
        self.calls: list[str] = []
        self._spend = spend

    async def spend(self, key_id: str) -> Spend:
        self.calls.append(key_id)
        if self._spend is None:
            raise RuntimeError("gateway down")
        return self._spend


def _use_gateway(monkeypatch: pytest.MonkeyPatch, gateway: _Gateway) -> None:
    from choregos_api.services import gateway as gateway_service

    async def project_gateway(_session: Any, _project: Any) -> _Gateway:
        return gateway

    monkeypatch.setattr(gateway_service, "project_gateway", project_gateway)


# ───────────────────────────── who sees what ─────────────────────────────


async def test_a_person_who_cannot_read_a_project_sees_none_of_its_runs(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    vault = await _project(client, "vault")
    await _run(project["id"], "run-billing", events=("tool_call", "message"))
    await _run(vault["id"], "run-vault")

    everything = await client.get(ACTIVITY)
    assert everything.status_code == 200, everything.text
    assert _ids(everything.json()) == {"run-billing", "run-vault"}
    errors = list(
        Draft202012Validator(
            {
                "$ref": "#/components/schemas/ActivityList",
                "components": contracts.load_openapi()["components"],
            }
        ).iter_errors(everything.json())
    )
    assert errors == []

    async with _as(client, "viewer@varga.dev", "viewer", "billing-api") as viewer:
        seen = await viewer.get(ACTIVITY)
        assert seen.status_code == 200, seen.text
        assert _ids(seen.json()) == {"run-billing"}, "a run of a project the reader cannot read is listed"
        entry = seen.json()["in_flight"][0]
        assert entry["work_item_key"] == "KEY-run-billing"
        assert (entry["steps"], entry["last_event"]) == (2, "message")
        assert entry["can_stop"] is False, "a viewer holds no item:control"
    admin_entry = next(e for e in everything.json()["in_flight"] if e["id"] == "run-billing")
    assert admin_entry["can_stop"] is True

    async with _as(client, "stranger@elsewhere.dev") as stranger:
        assert (await stranger.get(ACTIVITY)).status_code == 403
        assert (await stranger.get(f"{ACTIVITY}/stream")).status_code == 403


async def test_a_session_is_listed_to_its_opener_only(client: AsyncClient, project: dict[str, Any]) -> None:
    async with _as(client, "dev@varga.dev", "developer", "billing-api") as dev:
        await _session(project, await _user_id("admin@varga.dev"), "s-admin")
        await _session(project, await _user_id("dev@varga.dev"), "s-dev")
        assert _ids((await client.get(ACTIVITY)).json()) == {"s-admin"}
        body = (await dev.get(ACTIVITY)).json()
    assert _ids(body) == {"s-dev"}
    assert body["in_flight"][0]["can_stop"] is True


# ───────────────────────────── filters ─────────────────────────────


async def test_each_filter_keeps_only_its_entries(client: AsyncClient, project: dict[str, Any]) -> None:
    vault = await _project(client, "vault")
    deliver = await _workflow(project["id"], "deliver")
    review = await _workflow(project["id"], "review")
    await _run(project["id"], "r-deliver-a", agent="alpha", workflow_def_id=deliver)
    await _run(project["id"], "r-review-b", agent="beta", workflow_def_id=review)
    await _run(vault["id"], "r-vault-a", agent="alpha")
    await _session(project, await _user_id("admin@varga.dev"), "s-helper", agent="helper")

    async def listed(**params: str) -> set[str]:
        response = await client.get(ACTIVITY, params=params)
        assert response.status_code == 200, response.text
        return _ids(response.json())

    assert await listed() == {"r-deliver-a", "r-review-b", "r-vault-a", "s-helper"}
    assert await listed(project="vault") == {"r-vault-a"}
    assert await listed(workflow="review") == {"r-review-b"}
    assert await listed(agent="alpha") == {"r-deliver-a", "r-vault-a"}
    assert await listed(agent="helper") == {"s-helper"}
    assert await listed(kind="run") == {"r-deliver-a", "r-review-b", "r-vault-a"}
    assert await listed(kind="session") == {"s-helper"}
    # Conversation turns are S64-09's: accepted, and nothing until then.
    assert await listed(kind="turn") == set()
    assert await listed(assistant="inst-1") == set()


# ───────────────────────────── spend so far ─────────────────────────────


async def test_spend_so_far_is_null_when_the_gateway_raises(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _Gateway(spend=None)
    _use_gateway(monkeypatch, gateway)
    await _run(project["id"], "r-keyed", gateway_key_id="key-1")
    await _run(project["id"], "r-keyless")
    await _run(
        project["id"], "r-booked", status="succeeded", ended_ago=timedelta(minutes=1), spend_collected=True,
        tokens={"tokens_in": 10, "tokens_out": 4, "tokens_cached": 2}, cost_usd=0.5,
    )  # fmt: skip
    body = (await client.get(ACTIVITY)).json()
    spend = {e["id"]: e["spend"] for e in body["in_flight"] + body["finished"]}
    assert gateway.calls == ["key-1"]
    assert spend["r-keyed"] is None, "a gateway that fails says nothing, never zeros"
    assert spend["r-keyless"] is None
    booked = spend["r-booked"]
    assert (booked["tokens_in"], booked["tokens_out"], booked["tokens_cached"]) == (10, 4, 2)
    assert (booked["cost_usd"], booked["booked"]) == (0.5, True)


async def test_spend_so_far_is_asked_once_in_30_seconds(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import activity

    now = [1000.0]
    monkeypatch.setattr(activity, "clock", lambda: now[0])
    gateway = _Gateway(spend=Spend(tokens_in=120, tokens_out=30, tokens_cached=7, cost_usd=0.02))
    _use_gateway(monkeypatch, gateway)
    await _run(project["id"], "r-live", gateway_key_id="key-live")

    first = (await client.get(ACTIVITY)).json()["in_flight"][0]["spend"]
    assert (first["tokens_in"], first["tokens_out"], first["tokens_cached"]) == (120, 30, 7)
    assert (first["cost_usd"], first["booked"]) == (0.02, False)
    now[0] += 29
    await client.get(ACTIVITY)
    assert gateway.calls == ["key-live"], "the gateway was asked twice within 30 s"
    now[0] += 2
    await client.get(ACTIVITY)
    assert gateway.calls == ["key-live", "key-live"]


# ───────────────────────────── what has ended ─────────────────────────────


async def test_what_ended_is_shown_for_an_hour_and_at_most_50(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _run(
        project["id"],
        "done-long-ago",
        status="failed",
        started_ago=timedelta(hours=3),
        ended_ago=timedelta(hours=2),
    )
    await _run(project["id"], "done-recently", status="succeeded", ended_ago=timedelta(minutes=59))
    await _run(project["id"], "still-running")
    body = (await client.get(ACTIVITY)).json()
    assert _ids(body) == {"still-running"}
    assert _ids(body, "finished") == {"done-recently"}, "what ended two hours ago is still shown"

    for rank in range(55):
        await _run(
            project["id"], f"done-{rank:02d}", status="succeeded",
            started_ago=timedelta(minutes=58), ended_ago=timedelta(minutes=55 - rank),
        )  # fmt: skip
    finished = [e["id"] for e in (await client.get(ACTIVITY)).json()["finished"]]
    assert len(finished) == 50
    assert finished[0] == "done-54", "the newest ended first"
    assert finished == sorted(finished, reverse=True)


# ───────────────────────────── the stream ─────────────────────────────


async def test_the_stream_sends_an_upsert_then_an_ended_for_a_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.events import get_bus
    from choregos_api.rbac import Principal, Role
    from choregos_api.routers.activity import stream_org_activity
    from choregos_contracts import ChoregosEvent, EventType

    vault = await _project(client, "vault")
    await _run(vault["id"], "r-hidden")
    await _run(project["id"], "r-live", status="queued")
    viewer = Principal(user_id="u-v", email="v@varga.dev", project_roles={"varga/billing-api": Role.VIEWER})

    def event(type_: EventType, run_id: str, **data: Any) -> ChoregosEvent:
        return ChoregosEvent.emit(type_, source="/t", subject=run_id, run_id=run_id, **data)

    # The route itself, its response's body read as the server would: ASGITransport would wait for the
    # end of a stream that never ends.
    async with session_scope() as session:
        response = await stream_org_activity("varga", session, viewer)
    messages = response.body_iterator
    first = asyncio.ensure_future(anext(messages))
    while get_bus().subscriber_count("*") == 0:
        await asyncio.sleep(0.01)
    get_bus().publish(event(EventType.RUN_STARTED, "r-hidden"))
    get_bus().publish(event(EventType.RUN_STARTED, "r-live"))
    started = await asyncio.wait_for(first, timeout=5)
    async with session_scope() as session:
        run = await session.get(Run, "r-live")
        assert run is not None
        run.status, run.ended_at = "succeeded", utcnow()
    get_bus().publish(event(EventType.RUN_FINISHED, "r-live", status="succeeded"))
    ended = await asyncio.wait_for(anext(messages), timeout=5)
    await messages.aclose()  # type: ignore[attr-defined]
    assert get_bus().subscriber_count("*") == 0, "a closed stream still listens"

    assert started["event"] == "activity.upsert"
    upsert = json.loads(started["data"])
    assert (upsert["id"], upsert["status"]) == ("r-live", "queued"), "the hidden project's run came first"
    assert ended["event"] == "activity.ended"
    finished = json.loads(ended["data"])
    assert (finished["id"], finished["status"], finished["can_stop"]) == ("r-live", "succeeded", False)


async def test_the_stream_says_a_run_ended_from_its_event_before_the_row_is_read(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.activity import ActivityFilters, ActivityReader, entry_of_event
    from choregos_contracts import ChoregosEvent, EventType

    await _run(project["id"], "r-racing", agent="alpha")
    admin = Principal(user_id="u-a", email="a@varga.dev", org_roles={"varga": Role.ORG_ADMIN})
    finished = ChoregosEvent.emit(EventType.RUN_FINISHED, source="/t", run_id="r-racing", status="failed")
    org_id = await _org_id()
    async with session_scope() as session:
        reader = ActivityReader(principal=admin, org_id=org_id, org_slug="varga", filters=ActivityFilters())
        found = await entry_of_event(session, reader, finished)
        assert found is not None
        assert (found[0], found[1].status) == ("activity.ended", "failed")
        other_agent = ActivityReader(admin, org_id, "varga", ActivityFilters(agent="beta"))
        assert await entry_of_event(session, other_agent, finished) is None, "the stream ignores its filters"


async def test_a_late_event_of_an_ended_run_and_a_session_tool_call_reach_the_stream(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.activity import ActivityFilters, ActivityReader, entry_of_event
    from choregos_contracts import ChoregosEvent, EventType

    await _run(project["id"], "r-done", status="succeeded", ended_ago=timedelta(minutes=1))
    admin_id = await _user_id("admin@varga.dev")
    await _session(project, admin_id, "s-live")
    admin = Principal(user_id=admin_id, email="admin@varga.dev", org_roles={"varga": Role.ORG_ADMIN})
    stranger = Principal(user_id="u-x", email="x@varga.dev", org_roles={"varga": Role.ORG_ADMIN})
    late = ChoregosEvent.emit(EventType.RUN_PROGRESS, source="/t", run_id="r-done", events=1)
    tool = ChoregosEvent.emit(EventType.TOOL_CALLED, source="/t", subject="s-live", session_id="s-live")
    org_id = await _org_id()
    async with session_scope() as session:
        reader = ActivityReader(admin, org_id, "varga", ActivityFilters())
        ended = await entry_of_event(session, reader, late)
        assert ended is not None
        assert (ended[0], ended[1].status) == ("activity.ended", "succeeded"), (
            "an ended run is upserted again"
        )
        upsert = await entry_of_event(session, reader, tool)
        assert upsert is not None
        assert (upsert[0], upsert[1].kind, upsert[1].id) == ("activity.upsert", "session", "s-live")
        someone_else = ActivityReader(stranger, org_id, "varga", ActivityFilters())
        assert await entry_of_event(session, someone_else, tool) is None, (
            "another person's session is streamed"
        )
