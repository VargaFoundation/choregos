# SPDX-License-Identifier: Apache-2.0
"""A work item, a run of it, the result it posts and the artifacts it left: the tests' way in."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Credit notes", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    item: dict[str, Any],
    project: dict[str, Any],
    run_id: str,
    role: str,
    *,
    outputs: list[str] | None = None,
    output_types: dict[str, str] | None = None,
    agent: tuple[str, int] | None = None,
    attempt: int = 1,
) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=item["id"],
                project_id=project["id"],
                stage_role=role,
                attempt=attempt,
                status="running",
                agent_slug=agent[0] if agent else None,
                agent_version=agent[1] if agent else None,
                stage_input={"transition": {"outputs": outputs or [], "output_types": output_types or {}}},
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _post(client: AsyncClient, run_id: str, headers: dict[str, str], outputs: dict[str, Any]) -> None:
    result = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "done", "outputs": outputs}
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert posted.status_code == 200, posted.text


async def _rows(item_id: str) -> list[Any]:
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import artifacts_of

    async with session_scope() as session:
        return await artifacts_of(session, item_id)
