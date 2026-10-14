"""An agent's page lists its runs (S30-05): newest first, across the organisation's projects."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient


async def test_an_agent_s_runs_are_listed_newest_first(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    cree = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "redacteur", "display_name": "Rédacteur", "spec": {}}
    )
    assert cree.status_code == 201, cree.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-7", title="T", state="ready")
        session.add(item)
        await session.flush()
        maintenant = utcnow()
        for rang, (agent, statut) in enumerate(
            (("redacteur", "succeeded"), ("redacteur", "failed"), ("autre", "succeeded"))
        ):
            session.add(
                Run(
                    id=f"run-a{rang}",
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="refine",
                    status=statut,
                    agent_slug=agent,
                    agent_version=1,
                    created_at=maintenant - timedelta(minutes=10 - rang),
                )
            )
    reponse = await client.get("/api/v1/orgs/varga/agents/redacteur/runs")
    assert reponse.status_code == 200, reponse.text
    assert [(r["id"], r["status"], r["project_slug"]) for r in reponse.json()] == [
        ("run-a1", "failed", "billing-api"),
        ("run-a0", "succeeded", "billing-api"),
    ]
    assert (await client.get("/api/v1/orgs/varga/agents/absent/runs")).status_code == 404
