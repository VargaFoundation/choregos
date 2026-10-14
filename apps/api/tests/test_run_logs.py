"""`GET /runs/{id}/logs`: the runner pod's log tail, live while it runs, archived after (S36-03).

The API never reads the cluster: it serves what the orchestrator wrote in `runs.log_archive`
(S36-02) — a live snapshot refreshed at each poll, then the final archive.
"""

from __future__ import annotations

import itertools
from typing import Any

from httpx import ASGITransport, AsyncClient

from .conftest import login, sans_postgres

_KEYS = itertools.count(1)


async def _run(project_id: str, **fields: Any) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project_id, tracker_key=f"LOGS-{next(_KEYS)}", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project_id,
            stage_role="implement",
            attempt=1,
            status=fields.pop("status", "running"),
            **fields,
        )
        session.add(run)
        await session.flush()
        return str(run.id)


def _archive(**fields: Any) -> dict[str, Any]:
    return {
        "state": "failed",
        "exit_code": 137,
        "reason": "OOMKilled",
        "message": "runner container OOMKilled",
        "lines": ["agent: loading embeddings", "env: GITHUB_TOKEN=[REDACTED]", "Killed"],
        "truncated": False,
        "error": None,
        "final": True,
        "archived_at": "2026-10-12T10:00:00+00:00",
        **fields,
    }


async def test_an_ended_run_serves_its_archive(client: AsyncClient, project: dict[str, Any]) -> None:
    run_id = await _run(project["id"], status="failed", log_archive=_archive())
    response = await client.get(f"/api/v1/runs/{run_id}/logs")
    assert response.status_code == 200, response.text
    logs = response.json()
    assert (logs["source"], logs["state"], logs["exit_code"], logs["reason"]) == (
        "archive",
        "failed",
        137,
        "OOMKilled",
    )
    assert logs["lines"][-1] == "Killed"
    assert logs["captured_at"].startswith("2026-10-12T10:00:00")


async def test_a_running_run_serves_its_live_tail(client: AsyncClient, project: dict[str, Any]) -> None:
    live = _archive(state="running", exit_code=None, reason=None, lines=["agent: turn 3"], final=False)
    run_id = await _run(project["id"], log_archive=live)
    logs = (await client.get(f"/api/v1/runs/{run_id}/logs")).json()
    assert (logs["source"], logs["state"], logs["lines"]) == ("live", "running", ["agent: turn 3"])


async def test_a_run_without_capture_says_so(client: AsyncClient, project: dict[str, Any]) -> None:
    run_id = await _run(project["id"], status="queued")
    logs = (await client.get(f"/api/v1/runs/{run_id}/logs")).json()
    assert (logs["source"], logs["state"], logs["lines"]) == ("none", "queued", [])
    assert (await client.get("/api/v1/runs/absent/logs")).status_code == 404


# ───────────────────────── another organisation's run, under real RLS ─────────────────────────


@sans_postgres
async def test_another_organisations_run_logs_answer_404(pg_app: Any) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults

    projects: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            project = Project(
                org_id=org.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(project)
            await session.flush()
            await ensure_defaults(session, project)
            projects[slug] = project.id
    own = await _run(projects["a"], status="failed", log_archive=_archive())
    other = await _run(projects["b"], status="failed", log_archive=_archive(lines=["b's secret work"]))

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")  # a developer of `a` only (OIDC_DEFAULT_ORG=a)
        assert (await client.get(f"/api/v1/runs/{own}/logs")).status_code == 200
        response = await client.get(f"/api/v1/runs/{other}/logs")
        assert response.status_code == 404, "another organisation's run does not exist for this caller"
        assert "secret work" not in response.text
