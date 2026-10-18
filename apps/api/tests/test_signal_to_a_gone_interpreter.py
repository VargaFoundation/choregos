"""A decision for an interpreter that no longer runs is a 409 that says why and what to do, not a 500
(#679). Temporal answers NOT_FOUND to a signal for a workflow that ended; the request stays pending, so
the same request is answered once the interpreter runs again.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: release
  version: 1
initial: waiting
actors:
  captain: {type: human, group: release-captains}
states:
  waiting: {display: Waiting for the captain, kind: wait}
  done: {display: Done, terminal: true}
transitions:
  - id: t-ship
    from: waiting
    to: done
    by: captain
"""


async def _waiting_ticket(client: AsyncClient, project: dict[str, Any]) -> tuple[str, str]:
    """A work item with a pending approval, and the id of its interpreter."""
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import interpreter_id
    from choregos_core import utcnow

    pid = project["id"]
    saved = await client.put(f"/api/v1/projects/{pid}/workflows/release", json={"yaml": WORKFLOW})
    assert saved.status_code == 200, saved.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Ship 1.2", "workflow": "release"}
    )
    assert created.status_code == 201, created.text
    item = created.json()
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=pid,
                transition_id="t-ship",
                kind="approval",
                payload={"summary": "Ship?"},
                requested_at=utcnow(),
            )
        )
    return str(item["id"]), interpreter_id(project["slug"], item["tracker_key"])


async def _pending(item_id: str) -> bool:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == item_id))
        ).scalar_one()
        return row.decided_at is None


def _fake() -> Any:
    from choregos_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    return fake


async def test_a_decision_for_a_stopped_interpreter_is_a_409_and_the_request_stays_pending(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import WorkflowState

    item_id, workflow_id = await _waiting_ticket(client, project)
    fake = _fake()
    fake.gone.add(workflow_id)
    fake.described[workflow_id] = WorkflowState(status="TERMINATED")

    refused = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})

    assert refused.status_code == 409, refused.text
    problem = refused.json()
    assert problem["title"] == "Interpreter not running"
    assert problem["errors"][0]["code"] == "interpreter_not_running"
    assert "TERMINATED" in problem["detail"] and "mark_agent_ready" in problem["detail"]
    assert await _pending(item_id), "nothing received the decision: the request is still to answer"
    assert workflow_id in fake.gone, "the API does not restart an interpreter a person may have stopped"

    # What the problem says to do works: restart it, send the same decision again.
    restarted = await client.post(
        f"/api/v1/work-items/{item_id}/actions", json={"action": "mark_agent_ready"}
    )
    assert restarted.status_code == 202, restarted.text
    answered = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
    assert answered.status_code == 202, answered.text
    assert not await _pending(item_id)
    assert [name for wid, name, _ in fake.signals if wid == workflow_id] == ["human_decision"]


async def test_a_decision_for_a_completed_interpreter_says_nothing_will_restart_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import WorkflowState

    item_id, workflow_id = await _waiting_ticket(client, project)
    fake = _fake()
    fake.gone.add(workflow_id)
    fake.described[workflow_id] = WorkflowState(status="COMPLETED")

    refused = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "completed" in detail and "no longer waits" in detail
    assert "mark_agent_ready" not in detail, "a completed interpreter is not started again: do not promise it"
    assert await _pending(item_id)


async def test_a_signal_failure_other_than_not_found_is_not_reworded(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Temporal unreachable is not "the interpreter ended": it stays a server error."""
    from temporalio.service import RPCError, RPCStatusCode

    item_id, _ = await _waiting_ticket(client, project)
    fake = _fake()

    async def unavailable(workflow_id: str, name: str, payload: Any) -> None:
        raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")

    fake.signal = unavailable
    failed = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
    assert failed.status_code == 500, failed.text
    assert await _pending(item_id)
