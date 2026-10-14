"""A budget given at launch only tightens the workflow's and the policy's (S27-06, ADR 0047)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

WITH_BUDGET = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: capped, version: 1}
actors:
  hr: {type: human, group: hr}
states:
  asked: {display: Asked, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: asked, to: done, by: hr}
budget: {ticket_usd: 4, on_exceeded: asked}
"""


async def _tracker(client: AsyncClient, project: dict[str, Any]) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert put.status_code == 200, put.text


async def _create(client: AsyncClient, project: dict[str, Any], **body: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Budgeted", "start": False, **body}
    )


async def test_wider_than_the_policy_is_refused(client: AsyncClient, project: dict[str, Any]) -> None:
    await _tracker(client, project)
    refused = await _create(client, project, size="S", budget_usd=10_000)
    assert refused.status_code == 422, refused.text
    assert "policy" in refused.text and "only tighten" in refused.text


async def test_a_tighter_budget_is_kept(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import policy_engine
    from choregos_api.services.budgets import ticket_budget

    await _tracker(client, project)
    created = await _create(client, project, size="M", budget_usd=1.5)
    assert created.status_code == 201, created.text
    assert created.json()["budget_usd"] == 1.5
    async with session_scope() as session:
        item = await session.get(WorkItem, created.json()["id"])
        assert ticket_budget(await policy_engine(session, project["id"]), item) == 1.5


async def test_the_workflow_budget_applies_and_bounds_the_launch(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _tracker(client, project)
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/capped", json={"yaml": WITH_BUDGET}
    )
    assert published.status_code == 200, published.text
    by_workflow = await _create(client, project, workflow="capped")
    assert by_workflow.status_code == 201, by_workflow.text
    assert by_workflow.json()["budget_usd"] == 4
    wider = await _create(client, project, workflow="capped", budget_usd=5)
    assert wider.status_code == 422, wider.text
    assert "workflow `capped`" in wider.text
