"""`migrate`: the API reads and checks the target definition, the interpreter receives it (ADR 0031, S16-05).

The API passed `workflow_def_id` alone, and the interpreter, which does no input/output, validated
the control message itself as a workflow definition: the work item died. A migration is now
checked here, before any signal: the definition belongs to the project, the work item's current
state exists in it or is mapped, and the work item is not busy.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .kits.work_item_migration import make_work_item, signals_of

ARRIVAL = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: onboarding, version: 1}
actors:
  hr: {type: human, group: hr}
states:
  request: {display: Request, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - {id: t-do, from: request, to: done, by: hr}
"""


async def _target(client: AsyncClient, project: dict[str, Any]) -> str:
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": ARRIVAL}
    )
    assert published.status_code == 200, published.text
    return str(published.json()["id"])


async def _migrate(client: AsyncClient, item: dict[str, Any], **body: Any) -> Any:
    return await client.post(f"/api/v1/work-items/{item['id']}/actions", json={"action": "migrate", **body})


async def test_the_definition_leaves_with_the_signal(client: AsyncClient, project: dict[str, Any]) -> None:
    item, target = await make_work_item(client, project), await _target(client, project)
    response = await _migrate(client, item, workflow_def_id=target, state_mapping={item["state"]: "request"})
    assert response.status_code == 202, response.text
    (_, name, control), *_ = [s for s in signals_of() if s[1] == "control"]
    assert name == "control"
    assert control["workflow"]["metadata"] == {"name": "onboarding", "version": 1}
    assert control["workflow_def_id"] == target
    assert control["state_mapping"] == {item["state"]: "request"}


async def test_an_impossible_migration_does_not_leave(client: AsyncClient, project: dict[str, Any]) -> None:
    item, target = await make_work_item(client, project), await _target(client, project)
    without_mapping = await _migrate(client, item, workflow_def_id=target)
    assert without_mapping.status_code == 422, without_mapping.text
    assert item["state"] in without_mapping.text, "the refusal names the missing state"
    without_target = await _migrate(client, item, state_mapping={item["state"]: "request"})
    assert without_target.status_code == 422, without_target.text
    unknown = await _migrate(client, item, workflow_def_id="not-a-definition")
    assert unknown.status_code == 404, unknown.text
    assert not [s for s in signals_of() if s[1] == "control"], "nothing is passed on"


async def test_the_definition_of_another_project_is_not_found(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    other = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "hr", "name": "HR", "config": {"slug": "hr", "org": "varga"}},
    )
    assert other.status_code == 201, other.text
    foreign = await _target(client, other.json())
    item = await make_work_item(client, project)
    response = await _migrate(client, item, workflow_def_id=foreign, state_mapping={item["state"]: "request"})
    assert response.status_code == 404, response.text


async def test_a_busy_work_item_does_not_migrate(client: AsyncClient, project: dict[str, Any]) -> None:
    """A pending decision, a run in progress: the state would move before the migration, and the
    mapping checked here would no longer hold."""
    from choregos_api.db.models import HumanRequest, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    item, target = await make_work_item(client, project), await _target(client, project)
    mapping = {item["state"]: "request"}
    async with session_scope() as session:
        request = HumanRequest(
            project_id=project["id"], work_item_id=item["id"], kind="approval", requested_at=utcnow()
        )
        session.add(request)
    waiting = await _migrate(client, item, workflow_def_id=target, state_mapping=mapping)
    assert waiting.status_code == 409 and "decision" in waiting.text, waiting.text

    async with session_scope() as session:
        read = await session.get(HumanRequest, request.id)
        assert read is not None
        read.decided_at = utcnow()
        session.add(
            Run(project_id=project["id"], work_item_id=item["id"], status="running", stage_role="build")
        )
    running = await _migrate(client, item, workflow_def_id=target, state_mapping=mapping)
    assert running.status_code == 409 and "run" in running.text, running.text

    async with session_scope() as session:
        for run in (await session.execute(select(Run))).scalars():
            run.status = "succeeded"
    free = await _migrate(client, item, workflow_def_id=target, state_mapping=mapping)
    assert free.status_code == 202, free.text
