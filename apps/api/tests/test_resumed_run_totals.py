"""A run resumed after a person's answer is still served (#1348).

`resume_suspended_run` keeps its bookkeeping (`resumed`, `resumed_for`) in `run.tokens`, and
`book_spend` starts the resumed run's tokens from that dict. `Totals` forbids unknown keys, so the
run, its work item's run list and its agent's run list answered 500 for it — and for every run
listed beside it. `run.tokens` is a JSON column several writers add to; the DTO keeps what it
declares.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def test_a_resumed_run_is_served_with_its_totals(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    created = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "writer", "display_name": "Writer", "spec": {}}
    )
    assert created.status_code == 201, created.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-8", title="T", state="ready")
        session.add(item)
        await session.flush()
        item_id = item.id
        session.add(
            Run(
                id="run-resumed",
                work_item_id=item_id,
                project_id=project["id"],
                stage_role="refine",
                status="succeeded",
                agent_slug="writer",
                agent_version=1,
                tokens={
                    "tokens_in": 120,
                    "tokens_out": 30,
                    "runs": 1,
                    "resumed": 1,
                    "resumed_for": "answer-1",
                },
            )
        )

    run = await client.get("/api/v1/runs/run-resumed")
    assert run.status_code == 200, run.text
    assert run.json()["tokens"]["tokens_in"] == 120
    assert "resumed_for" not in run.json()["tokens"]

    listed = await client.get(f"/api/v1/work-items/{item_id}/runs")
    assert listed.status_code == 200, listed.text
    assert [r["id"] for r in listed.json()] == ["run-resumed"]

    by_agent = await client.get("/api/v1/orgs/varga/agents/writer/runs")
    assert by_agent.status_code == 200, by_agent.text
