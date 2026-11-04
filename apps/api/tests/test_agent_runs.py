"""An agent's page lists its runs (S30-05): newest first, across the organisation's projects."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient


async def test_an_agent_s_runs_are_listed_newest_first(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "writer", "display_name": "Writer", "spec": {}}
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-7", title="T", state="ready")
        session.add(item)
        await session.flush()
        now = utcnow()
        for rank, (agent, status) in enumerate(
            (("writer", "succeeded"), ("writer", "failed"), ("other", "succeeded"))
        ):
            session.add(
                Run(
                    id=f"run-a{rank}",
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="refine",
                    status=status,
                    agent_slug=agent,
                    agent_version=1,
                    created_at=now - timedelta(minutes=10 - rank),
                )
            )
    response = await client.get("/api/v1/orgs/varga/agents/writer/runs")
    assert response.status_code == 200, response.text
    assert [(r["id"], r["status"], r["project_slug"]) for r in response.json()] == [
        ("run-a1", "failed", "billing-api"),
        ("run-a0", "succeeded", "billing-api"),
    ]
    assert (await client.get("/api/v1/orgs/varga/agents/absent/runs")).status_code == 404
