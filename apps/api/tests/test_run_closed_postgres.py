"""A close and a late result made at the same moment cannot both win (S65-01, ADR 0143), on a real
PostgreSQL.

`POST …/result` read the run, saw no result, and wrote its own a few statements later. Between the
two, the orchestrator could close the run — its budget was just spent — and commit: the route's
update then overwrote the close, the run came back `succeeded`, and its work item took the late
result's pull request and outputs. The route now reads the row again under the lock an update takes
(`FOR NO KEY UPDATE`) before it decides: it waits for the close, then sees it.

SQLite has one writer and no row lock: only a real PostgreSQL shows this
(`CHOREGOS_TEST_DATABASE_URL`), as the application role, so under RLS.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import APP_ROLE, PG_URL, without_postgres
from .kits.closed_runs import ALLOWED_PATHS, DOCUMENTS, LATE_RESULT, PR_URL, a_run, kept, token_of

pytestmark = [without_postgres, pytest.mark.asyncio]


async def _a_project() -> dict[str, Any]:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults

    async with session_scope(orgs="*") as session:
        organisation = Organization(slug="a", name="A")
        session.add(organisation)
        await session.flush()
        project = Project(
            org_id=organisation.id,
            slug="billing-api",
            name="Billing",
            status="active",
            config={"slug": "billing-api", "org": "a"},
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        return {"id": project.id}


async def _waiting_for_a_lock() -> int:
    """The application role's sessions that wait for a lock, seen as a superuser."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = $1 AND wait_event_type = 'Lock'"
                " AND datname = current_database()",
                APP_ROLE,
            )
        )
    finally:
        await connection.close()


async def test_a_result_posted_while_the_run_is_being_closed_waits_and_changes_nothing(pg_app: Any) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult
    from choregos_core.run_closure import close_run

    project = await _a_project()
    run_id = await a_run(project)
    loop = asyncio.get_running_loop()

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        # The orchestrator's close, written and not committed yet: it holds the row.
        async with session_scope(orgs="*") as closing:
            run = (
                await closing.execute(select(Run).where(Run.id == run_id).with_for_update(key_share=True))
            ).scalar_one()
            assert close_run(run, StageResult.failure("over 30 min", "timeout"), status="failed")
            await closing.flush()

            late = asyncio.create_task(
                client.post(
                    f"/api/v1/internal/runs/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT
                )
            )
            deadline = loop.time() + 20
            while not await _waiting_for_a_lock():
                assert not late.done(), f"the late result did not wait for the close: {late.result().text}"
                assert loop.time() < deadline, "the late result never reached the row"
                await asyncio.sleep(0.02)
        # The close is committed: the late result goes on, and reads it.
        answer = await asyncio.wait_for(late, timeout=20)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"status": "already_recorded", "run_id": run_id}
    after = await kept(run_id)
    assert (after.status, (after.result or {})["reason"]) == ("failed", "timeout")
    assert (after.pr_url, after.size, after.risk) == (PR_URL, "M", "low")
    assert after.allowed_paths == ALLOWED_PATHS and after.documents == DOCUMENTS
    assert (after.finished_events, after.artifacts, after.receipts) == (0, 0, 0)
