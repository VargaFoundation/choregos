"""A work item's fields, declared by its workflow (ADR 0031).

A work item had only a title, a body, a size and a risk: an onboarding had nowhere to put the
arrival date or the position, and an agent had to guess them in free text.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ONBOARDING = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    required: [start_date, position]
    additionalProperties: false
    properties:
      start_date: {type: string, format: date}
      position: {type: string, minLength: 1}
actors:
  hr: {type: human, group: hr}
states:
  arrival: {display: Arrival, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-welcome, from: arrival, to: done, by: hr}
"""


async def _hr_project(client: AsyncClient, project: dict[str, Any]) -> str:
    pid = project["id"]
    response = await client.put(f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": ONBOARDING})
    assert response.status_code == 200, response.text
    return str(pid)


async def test_the_fields_are_validated_at_birth(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = await _hr_project(client, project)
    body = {"title": "Arrival of Jeanne", "start": False, "workflow": "onboarding"}
    missing = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={**body, "fields": {"position": "data"}}
    )
    assert missing.status_code == 422, missing.text
    foreign = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={**body, "fields": {"start_date": "2026-11-03", "position": "data", "salary": 1}},
    )
    assert foreign.status_code == 422, foreign.text
    good = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={**body, "fields": {"start_date": "2026-11-03", "position": "data consultant"}},
    )
    assert good.status_code == 201, good.text
    assert good.json()["fields"] == {"start_date": "2026-11-03", "position": "data consultant"}


async def test_a_workflow_without_fields_refuses_fields(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "x", "start": False, "fields": {"a": 1}},
    )
    assert response.status_code == 422, response.text


async def test_an_invalid_fields_schema_is_refused_at_publication(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    wrong = ONBOARDING.replace("type: object\n    required", "type: array\n    required")
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": wrong}
    )
    assert response.status_code == 422, response.text


async def test_the_fields_arrive_in_the_runs_context(client: AsyncClient, project: dict[str, Any]) -> None:
    """The agent reads the work item through `get_ticket`: the fields are there, no guessing."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    pid = await _hr_project(client, project)
    fields = {"start_date": "2026-11-03", "position": "data consultant"}
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrival of Jeanne", "start": False, "workflow": "onboarding", "fields": fields},
    )
    work_item = created.json()
    async with session_scope() as session:
        session.add(
            Run(
                id="run-fields",
                work_item_id=work_item["id"],
                project_id=pid,
                stage_role="custom",
                status="running",
            )
        )
    token = mint_run_token(
        "run-fields", project_slug="billing-api", work_item_key=work_item["tracker_key"], ttl_minutes=5
    )
    read_back = await client.get(
        "/api/v1/internal/runs/run-fields/ticket", headers={"Authorization": f"Bearer {token}"}
    )
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["fields"] == fields
