"""Stopping an agent suspends it and stops its runs and sessions (S64-04, ADR 0142 §4-6).

`POST /orgs/{org}/agents/{slug}/stop` needs the right that guards a status change (`Action.MANAGE`).
The agent is `suspended` and committed first, so nothing new starts; then every run of it `queued` or
`running`, in every project of the organisation, is stopped as `POST /runs/{id}/cancel` stops one,
and its open sessions are closed. `agent.stop` is audited with the reason and the counts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
AGENTS = f"{ORG}/agents"
SLUG = "onboarding-coordinator"
DONE = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "implemented"}


async def _agent(client: AsyncClient, slug: str = SLUG) -> dict[str, Any]:
    created = await client.post(AGENTS, json={"slug": slug, "display_name": slug})
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _second_project(client: AsyncClient) -> dict[str, Any]:
    payload = {
        "slug": "billing-web",
        "name": "Billing web",
        "config": {
            "slug": "billing-web",
            "org": "varga",
            "repo": {
                "url": "https://github.com/varga/billing-web.git",
                "default_branch": "main",
                "language": "python",
                "test_command": "make test",
            },
        },
    }
    created = await client.post(f"{ORG}/projects", json=payload)
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    project_id: str, run_id: str, *, slug: str = SLUG, status: str = "running", result: Any = None
) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key=run_id.upper(), title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id=run_id,
            work_item_id=item.id,
            project_id=project_id,
            stage_role="implement",
            status=status,
            agent_slug=slug,
            agent_version=1,
        )
        if result is not None:
            run.result = result
        session.add(run)
    return run_id


async def _open_session(project_id: str, *, slug: str = SLUG) -> str:
    from choregos_api.db.models import AgentSessionRecord, Project
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        project_row = await session.get(Project, project_id)
        assert project_row is not None
        record = AgentSessionRecord(
            org_id=project_row.org_id,
            project_id=project_id,
            agent_slug=slug,
            agent_version=1,
            user_id="someone",
            status="open",
            expires_at=utcnow() + timedelta(hours=1),
        )
        session.add(record)
        await session.flush()
        return str(record.id)


async def _runs() -> dict[str, tuple[str, Any]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(Run))).scalars()
        return {row.id: (row.status, row.result) for row in rows}


async def _sessions() -> dict[str, tuple[str, str | None]]:
    from choregos_api.db.models import AgentSessionRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AgentSessionRecord))).scalars()
        return {row.id: (row.status, row.reason) for row in rows}


async def _status(slug: str = SLUG) -> str:
    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return str((await session.execute(select(Agent.status).where(Agent.slug == slug))).scalar_one())


async def _audited(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.ts))
        ).scalars()
        return [{"target_id": row.target_id, **dict(row.payload or {})} for row in rows]


def _cancellations() -> set[str]:
    from choregos_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    return {key for key in fake.started if key.startswith("run-cancel-")}


async def test_the_agent_is_suspended_before_any_run_is_stopped(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """Each cancel sees the agent `suspended` from ANOTHER session: committed, not only flushed, so
    `resolve` refuses a new run while the others are being stopped."""
    from choregos_api.services import agent_stop

    await _agent(client)
    await _run(project["id"], "run-a")
    await _run(project["id"], "run-b", status="queued")
    seen: list[tuple[str, str]] = []
    cancel = agent_stop.cancel_agent_run

    async def observed(session: Any, principal: Any, run: Any, reason: str) -> Any:
        seen.append((run.id, await _status()))
        return await cancel(session, principal, run, reason)

    monkeypatch.setattr(agent_stop, "cancel_agent_run", observed)

    stopped = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "it deletes branches"})

    assert stopped.status_code == 200, stopped.text
    assert seen == [("run-a", "suspended"), ("run-b", "suspended")]


async def test_a_stop_cancels_every_run_of_the_agent_in_every_project_and_closes_its_sessions(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.services.agents import AgentUnavailable, resolve_agent

    await _agent(client)
    await _agent(client, "other-agent")
    second = await _second_project(client)
    await _run(project["id"], "run-1")
    await _run(second["id"], "run-2", status="queued")
    await _run(project["id"], "run-other", slug="other-agent")
    await _run(project["id"], "run-done", status="succeeded", result=DONE)
    mine = [await _open_session(project["id"]), await _open_session(second["id"])]
    theirs = await _open_session(project["id"], slug="other-agent")

    stopped = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "it deletes branches"})

    assert stopped.status_code == 200, stopped.text
    assert stopped.json() == {
        "slug": SLUG,
        "status": "suspended",
        "runs_cancelled": 2,
        "sessions_closed": 2,
        "turns_cancelled": 0,
        "not_stopped": [],
    }
    runs = await _runs()
    for run_id in ("run-1", "run-2"):
        status, result = runs[run_id]
        assert status == "cancelled", run_id
        assert result["summary"] == "run stopped by admin@varga.dev: it deletes branches"
    assert runs["run-other"] == ("running", None), "another agent's run is not touched"
    assert runs["run-done"] == ("succeeded", DONE), "an ended run keeps how it ended"
    assert _cancellations() == {"run-cancel-run-1", "run-cancel-run-2"}
    sessions = await _sessions()
    assert [sessions[s] for s in mine] == [("closed", "agent_stopped")] * 2
    assert sessions[theirs] == ("open", None), "another agent's session stays open"
    assert await _status() == "suspended" and await _status("other-agent") == "active"
    cancelled = await _audited("run.cancel")
    assert sorted((a["target_id"], a["channel"], a["reason"]) for a in cancelled) == [
        ("run-1", "agent_stop", "it deletes branches"),
        ("run-2", "agent_stop", "it deletes branches"),
    ]
    audited = await _audited("agent.stop")
    assert len(audited) == 1
    assert audited[0]["reason"] == "it deletes branches"
    assert (audited[0]["runs_cancelled"], audited[0]["sessions_closed"]) == (2, 2)
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project_row = await session.get(Project, project["id"])
        assert project_row is not None
        try:
            await resolve_agent(session, project_row.org_id, project_row.id, SLUG)
        except AgentUnavailable as refused:
            assert "suspended" in str(refused)
        else:
            raise AssertionError("a stopped agent still starts runs")


async def _member(email: str, role: str, project_id: str | None = None) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
            session.add(user)
            await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))


async def test_the_agent_managers_right_stops_runs_in_projects_where_they_cannot_control_items(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    """ADR 0142 §4: the right is `Action.MANAGE` on the agent, not `item:control` on each project.
    The owner of a personal agent (developer in another project, viewer of the organisation) stops
    its run in `billing-api`, where stopping that run alone is refused."""
    second = await _second_project(client)
    await _member("owner@varga.dev", "viewer")
    await _member("owner@varga.dev", "developer", second["id"])
    owner = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(owner, "owner@varga.dev")
        created = await owner.post(AGENTS, json={"slug": "mine", "display_name": "mine", "scope": "personal"})
        assert created.status_code == 201, created.text
        await _run(project["id"], "run-personal", slug="mine")
        cannot_alone = await owner.post("/api/v1/runs/run-personal/cancel", json={"reason": "x"})
        stopped = await owner.post(f"{AGENTS}/mine/stop", json={"reason": "mine to stop"})
    finally:
        await owner.aclose()

    assert cannot_alone.status_code == 403, "the run alone needs `item:control` on its project"
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["runs_cancelled"] == 1
    assert (await _runs())["run-personal"][0] == "cancelled"


async def test_a_member_who_cannot_manage_the_agent_gets_403(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client)
    await _run(project["id"], "run-keep")
    session_id = await _open_session(project["id"])
    await client.post(f"{ORG}/members", json={"email": "dev@varga.dev", "role": "developer"})
    developer = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(developer, "dev@varga.dev")
        assert (await developer.get(f"{AGENTS}/{SLUG}")).status_code == 200, "the developer sees it"
        refused = await developer.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "curious"})
    finally:
        await developer.aclose()

    assert refused.status_code == 403, refused.text
    assert await _status() == "active"
    assert (await _runs())["run-keep"] == ("running", None)
    assert (await _sessions())[session_id] == ("open", None)
    assert await _audited("agent.stop") == [] and _cancellations() == set()


async def test_a_revoked_agent_is_not_stopped_and_an_unknown_one_is_404(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client)
    assert (await client.patch(f"{AGENTS}/{SLUG}", json={"status": "revoked"})).status_code == 200
    await _run(project["id"], "run-revoked")

    refused = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "too late"})

    assert refused.status_code == 409, refused.text
    assert await _status() == "revoked"
    assert (await _runs())["run-revoked"] == ("running", None)
    assert await _audited("agent.stop") == []
    assert (await client.post(f"{AGENTS}/nobody/stop", json={"reason": "x"})).status_code == 404
    assert (await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": ""})).status_code == 422


async def test_stopping_a_suspended_agent_stops_what_is_still_in_flight(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client)
    await _run(project["id"], "run-first")
    first = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "first"})
    assert first.status_code == 200 and first.json()["runs_cancelled"] == 1
    await _run(project["id"], "run-late", status="queued")  # queued before the suspension landed

    again = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "second"})

    assert again.status_code == 200, again.text
    assert (again.json()["runs_cancelled"], again.json()["sessions_closed"]) == (1, 0)
    runs = await _runs()
    assert runs["run-late"][0] == "cancelled"
    assert runs["run-first"][1]["summary"].endswith(": first"), "the first stop keeps its reason"
    assert [a["reason"] for a in await _audited("agent.stop")] == ["first", "second"]
    assert await _status() == "suspended"


async def test_a_run_that_cannot_be_stopped_is_named_and_the_others_go_on(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """A run that ended between the list and the stop (409) is skipped silently; any other refusal
    is named in `not_stopped`, and the other runs are still stopped."""
    from choregos_api.errors import conflict, not_found
    from choregos_api.services import agent_stop

    await _agent(client)
    for run_id in ("run-ended", "run-lost", "run-ok"):
        await _run(project["id"], run_id)
    cancel = agent_stop.cancel_agent_run

    async def flaky(session: Any, principal: Any, run: Any, reason: str) -> Any:
        if run.id == "run-ended":
            raise conflict("the run `run-ended` already ended: `done`")
        if run.id == "run-lost":
            raise not_found("Project", run.project_id)
        return await cancel(session, principal, run, reason)

    monkeypatch.setattr(agent_stop, "cancel_agent_run", flaky)

    stopped = await client.post(f"{AGENTS}/{SLUG}/stop", json={"reason": "stop"})

    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert body["runs_cancelled"] == 1
    assert [(n["kind"], n["id"]) for n in body["not_stopped"]] == [("run", "run-lost")]
    assert "does not exist" in body["not_stopped"][0]["detail"]
    assert (await _runs())["run-ok"][0] == "cancelled"
