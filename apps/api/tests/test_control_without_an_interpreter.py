"""An action on a work item whose interpreter does not run is a 409 that says why, not a 500 (#715).

An eval item is run by `EvalMatrix` itself and never gets an interpreter: `stop` on it named nothing
before, and answered 500. Any other item whose interpreter ended gets `interpreter_not_running`.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _item(client: AsyncClient, project: dict[str, Any], *, created_by: str | None) -> tuple[str, str]:
    """A work item whose interpreter Temporal no longer knows, and that interpreter's id."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import FakeTemporal, get_temporal, interpreter_id

    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Fix the login"}
    )
    assert created.status_code == 201, created.text
    item = created.json()
    async with session_scope() as session:
        row = await session.get(WorkItem, item["id"])
        assert row is not None
        row.created_by = created_by
    workflow_id = interpreter_id(project["slug"], item["tracker_key"])
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    fake.gone.add(workflow_id)
    return str(item["id"]), workflow_id


async def _paused(item_id: str) -> bool:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = await session.get(WorkItem, item_id)
        assert row is not None
        return row.paused


async def test_stopping_an_eval_item_names_the_eval_matrix(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, _ = await _item(client, project, created_by="eval")

    refused = await client.post(f"/api/v1/work-items/{item_id}/actions", json={"action": "stop"})

    assert refused.status_code == 409, refused.text
    problem = refused.json()
    assert problem["errors"][0]["code"] == "driven_by_eval_matrix"
    assert "EvalMatrix" in problem["detail"] and "cancel" in problem["detail"]
    assert "mark_agent_ready" not in problem["detail"], (
        "starting an interpreter on an eval item is no way out"
    )


async def test_pausing_an_item_whose_interpreter_ended_is_a_409_and_nothing_is_paused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, _ = await _item(client, project, created_by=None)

    refused = await client.post(f"/api/v1/work-items/{item_id}/actions", json={"action": "pause"})

    assert refused.status_code == 409, refused.text
    problem = refused.json()
    assert problem["errors"][0]["code"] == "interpreter_not_running"
    assert "`pause` action" in problem["detail"] and "mark_agent_ready" in problem["detail"]
    assert not await _paused(item_id), "nothing received the pause: the item is not marked paused"

    restarted = await client.post(
        f"/api/v1/work-items/{item_id}/actions", json={"action": "mark_agent_ready"}
    )
    assert restarted.status_code == 202, restarted.text
    paused = await client.post(f"/api/v1/work-items/{item_id}/actions", json={"action": "pause"})
    assert paused.status_code == 202, paused.text
    assert await _paused(item_id)
