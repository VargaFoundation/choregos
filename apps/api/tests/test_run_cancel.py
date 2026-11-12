"""A run stopped alone, and a work item's stop stops its run (S64-03, ADR 0142 §2-3, §5-6).

`POST /runs/{id}/cancel` writes the run's result as stopped by the caller — first writer wins —
starts `RunCancellation` once that is committed, and audits `run.cancel` with the reason. A run
that already ended answers 409 saying how, and changes nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
DONE = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "implemented"}


async def _item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Fix the login"}
    )
    assert created.status_code == 201, created.text
    return {**created.json(), "project_id": project["id"]}


async def _run(item: dict[str, Any], run_id: str, *, status: str = "running", result: Any = None) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = Run(
            id=run_id,
            work_item_id=item["id"],
            project_id=item["project_id"],
            stage_role="implement",
            status=status,
        )
        if result is not None:
            run.result = result
        session.add(run)
    return run_id


async def _stored(run_id: str) -> Any:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run.status, run.result


async def _audited(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return [{"target_id": row.target_id, **dict(row.payload or {})} for row in rows]


def _cancellations() -> dict[str, dict[str, Any]]:
    from choregos_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    return {key: value for key, value in fake.started.items() if key.startswith("run-cancel-")}


async def test_a_running_run_is_stopped_with_its_reason_and_who_asked(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _item(client, project)
    run_id = await _run(item, "run-stop-1")

    stopped = await client.post(
        f"/api/v1/runs/{run_id}/cancel", json={"reason": "it is editing the wrong repo"}
    )

    assert stopped.status_code == 202, stopped.text
    run = stopped.json()
    assert run["status"] == "cancelled"
    assert run["result"]["status"] == "failed" and run["result"]["reason"] == "stopped_by_person"
    assert run["result"]["summary"] == "run stopped by admin@varga.dev: it is editing the wrong repo"
    assert run["result"]["diagnostics"]["agent_exit"] == "cancelled"
    assert await _stored(run_id) == ("cancelled", run["result"]), "written, not only answered"
    assert _cancellations() == {
        f"run-cancel-{run_id}": {"project_id": item["project_id"], "run_id": run_id}
    }, "the executor is stopped by `RunCancellation`, once the result is committed"
    audited = await _audited("run.cancel")
    assert [(a["target_id"], a["reason"]) for a in audited] == [(run_id, "it is editing the wrong repo")]


async def test_a_viewer_cannot_stop_a_run(app: Any, client: AsyncClient, project: dict[str, Any]) -> None:
    item = await _item(client, project)
    run_id = await _run(item, "run-stop-viewer")
    await client.post(f"{ORG}/members", json={"email": "reader@varga.dev", "role": "viewer"})
    reader = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(reader, "reader@varga.dev")
        assert (await reader.get(f"/api/v1/runs/{run_id}")).status_code == 200, "the reader sees it"
        refused = await reader.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "curious"})
    finally:
        await reader.aclose()

    assert refused.status_code == 403, refused.text
    assert await _stored(run_id) == ("running", None)
    assert _cancellations() == {} and await _audited("run.cancel") == []


async def test_a_run_that_ended_keeps_how_it_ended(client: AsyncClient, project: dict[str, Any]) -> None:
    item = await _item(client, project)
    run_id = await _run(item, "run-stop-done", status="succeeded", result=DONE)

    refused = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "too late"})

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "run_already_ended"
    assert "already ended: `done`" in refused.json()["detail"]
    assert await _stored(run_id) == ("succeeded", DONE)
    assert _cancellations() == {} and await _audited("run.cancel") == []


async def test_a_second_stop_says_the_run_is_already_stopped(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _item(client, project)
    run_id = await _run(item, "run-stop-twice")
    assert (await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "first"})).status_code == 202

    again = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "second"})

    assert again.status_code == 409, again.text
    assert "already stopped (run stopped by admin@varga.dev: first)" in again.json()["detail"]
    assert [a["reason"] for a in await _audited("run.cancel")] == ["first"]


async def test_a_reason_is_required(client: AsyncClient, project: dict[str, Any]) -> None:
    item = await _item(client, project)
    run_id = await _run(item, "run-stop-no-reason")
    assert (await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": ""})).status_code == 422
    assert (await client.post(f"/api/v1/runs/{run_id}/cancel", json={})).status_code == 422
    assert (await client.post("/api/v1/runs/nope/cancel", json={"reason": "x"})).status_code == 404
    assert await _stored(run_id) == ("running", None)


async def test_the_runner_posting_after_the_stop_changes_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.security import mint_run_token

    item = await _item(client, project)
    run_id = await _run(item, "run-stop-then-post")
    stopped = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "stop it"})
    assert stopped.status_code == 202

    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    posted = await client.post(
        f"/api/v1/internal/runs/{run_id}/result", json=DONE, headers={"Authorization": f"Bearer {token}"}
    )

    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "already_recorded"
    assert await _stored(run_id) == ("cancelled", stopped.json()["result"])


async def test_a_stop_landing_while_the_runner_posts_keeps_the_stop(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """The stop commits after the runner's post read the run and before it writes: the post finds a
    result in its conditional update and changes nothing, instead of overwriting the stop."""
    from choregos_api.routers import internal
    from choregos_api.security import mint_run_token

    item = await _item(client, project)
    run_id = await _run(item, "run-post-race")
    read_the_run = internal._run_and_item

    async def read_then_stopped(session: Any, run: str) -> Any:
        read = await read_the_run(session, run)
        stopped = await client.post(f"/api/v1/runs/{run}/cancel", json={"reason": "stopped meanwhile"})
        assert stopped.status_code == 202, stopped.text
        return read

    monkeypatch.setattr(internal, "_run_and_item", read_then_stopped)
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    posted = await client.post(
        f"/api/v1/internal/runs/{run_id}/result", json=DONE, headers={"Authorization": f"Bearer {token}"}
    )

    assert posted.status_code == 200, posted.text
    assert posted.json()["status"] == "already_recorded"
    status, result = await _stored(run_id)
    assert status == "cancelled" and result["summary"].endswith("stopped meanwhile")


async def test_a_result_posted_between_the_read_and_the_write_wins(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The runner writes first while the stop holds a stale copy of the run: the conditional update
    finds a result and the stop changes nothing (first writer wins), instead of overwriting it."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.rbac import Principal, Role
    from choregos_api.services.run_cancel import cancel_run_now

    item = await _item(client, project)
    run_id = await _run(item, "run-stop-race")
    principal = Principal(user_id="u1", email="ops@varga.dev", org_roles={"varga": Role.ORG_ADMIN})

    async with session_scope() as session:
        stale = await session.get(Run, run_id)
        assert stale is not None and stale.result is None
        async with session_scope() as runner:  # the runner's post, committed first
            posted = await runner.get(Run, run_id)
            assert posted is not None
            posted.result, posted.status = DONE, "succeeded"
        try:
            await cancel_run_now(session, principal, stale, "too slow")
        except ApiError as refused:
            assert refused.status_code == 409
            assert "already ended: `done`" in refused.detail, "the answer says how it ended"
        else:
            raise AssertionError("the stop overwrote a result written before it")

    assert await _stored(run_id) == ("succeeded", DONE)
    assert _cancellations() == {}


@pytest.mark.parametrize(
    ("stored", "sql_null"),
    [pytest.param("NULL", True, id="sql-null"), pytest.param("'null'", False, id="json-null")],
)
async def test_a_resumed_run_whose_result_was_reset_can_be_stopped(
    client: AsyncClient, project: dict[str, Any], stored: str, sql_null: bool
) -> None:
    """A run resumed after a person's answer has its result reset (`activities/suspension.py`). The
    ORM writes that `None` as SQL `NULL` or as the JSON `null`, depending on the database: both mean
    "no result", and the stop's condition reads both so. Each is written here in SQL, so each case
    exists on SQLite and on PostgreSQL alike (`'null'` is a `jsonb` null there, a text one here)."""
    from choregos_api.db.session import session_scope
    from sqlalchemy import text

    item = await _item(client, project)
    run_id = await _run(item, f"run-stop-reset-{'sql' if sql_null else 'json'}", result=DONE)
    async with session_scope() as session:
        await session.execute(text(f"UPDATE runs SET result = {stored} WHERE id = :id"), {"id": run_id})
    async with session_scope() as session:
        is_null = (
            await session.execute(text("SELECT result IS NULL FROM runs WHERE id = :id"), {"id": run_id})
        ).scalar()
    assert bool(is_null) is sql_null, f"the case this proves: SQL NULL {sql_null}, read {is_null!r}"

    stopped = await client.post(f"/api/v1/runs/{run_id}/cancel", json={"reason": "enough"})

    assert stopped.status_code == 202, stopped.text
    assert stopped.json()["result"]["reason"] == "stopped_by_person"


async def test_stopping_a_work_item_stops_its_run_with_the_same_reason(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    item = await _item(client, project)
    over = await _run(item, "run-item-1", status="failed", result={**DONE, "status": "failed"})
    current = await _run(item, "run-item-2")

    paused = await client.post(f"/api/v1/work-items/{item['id']}/actions", json={"action": "pause"})
    assert paused.status_code == 202, paused.text
    assert await _stored(current) == ("running", None), "a pause holds the next stage, not this run"

    stopped = await client.post(
        f"/api/v1/work-items/{item['id']}/actions", json={"action": "stop", "reason": "wrong ticket"}
    )

    assert stopped.status_code == 202, stopped.text
    status, result = await _stored(current)
    assert status == "cancelled"
    assert result["summary"] == "run stopped by admin@varga.dev: wrong ticket"
    assert (await _stored(over))[0] == "failed", "a run that ended keeps how it ended"
    assert set(_cancellations()) == {f"run-cancel-{current}"}
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert [s[2]["action"] for s in fake.signals if s[1] == "control"] == ["pause", "stop"], "the flag too"
    audited = await _audited("run.cancel")
    assert [(a["target_id"], a["reason"], a["channel"]) for a in audited] == [
        (current, "wrong ticket", "work_item")
    ]
    assert [a["reason"] for a in await _audited("workitem.stop")] == ["wrong ticket"]


async def test_a_stop_without_a_reason_still_says_why_the_run_stopped(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _item(client, project)
    current = await _run(item, "run-item-no-reason")

    stopped = await client.post(f"/api/v1/work-items/{item['id']}/actions", json={"action": "stop"})

    assert stopped.status_code == 202, stopped.text
    _, result = await _stored(current)
    assert result["summary"] == "run stopped by admin@varga.dev: the work item was stopped"
