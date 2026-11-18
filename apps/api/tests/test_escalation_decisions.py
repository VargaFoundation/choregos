"""An escalated work item holds two open requests; an approval never abandons it by default (FON-319).

On prod, 2026-11-18: a budget escalation parked FON-319 in `needs_human`, which opened the
escalation's request and, a moment later, the abandon step's (`needs_human -> abandoned`, by a
person). The board showed, and a decision without `request_id` decided, the NEWEST request: the
"Approve" a person gave the budget message abandoned the work item.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: escalating
  version: 1
initial: implementing
actors:
  dev: {type: agent, role: implementer}
  maintainers: {type: human, group: maintainers}
states:
  implementing: {display: Implementing}
  needs_human: {display: Needs a person}
  done: {display: Done, terminal: true}
  abandoned: {display: Abandoned, terminal: true}
transitions:
  - id: t-implement
    from: implementing
    to: done
    by: dev
  - id: t-abandon
    from: needs_human
    to: abandoned
    by: maintainers
defaults:
  from_any_agent_state:
    on_question: needs_human
    on_budget_exceeded: needs_human
  needs_human:
    on_answer: resume
    on_abandon: abandoned
"""


async def _parked(client: AsyncClient, project: dict[str, Any], *, spent_usd: float = 0.0) -> tuple[str, str]:
    """A work item parked by a budget escalation: its request, then the abandon step's, both open."""
    from tranibor_api.db.models import CostLedger, HumanRequest, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_core import utcnow

    pid = project["id"]
    saved = await client.put(f"/api/v1/projects/{pid}/workflows/escalating", json={"yaml": WORKFLOW})
    assert saved.status_code == 200, saved.text
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Make Iris reachable", "workflow": "escalating"}
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    now = utcnow()
    async with session_scope() as session:
        row = await session.get(WorkItem, item_id)
        assert row is not None
        row.state = "needs_human"
        escalation = HumanRequest(
            work_item_id=item_id,
            project_id=pid,
            transition_id="t-implement",
            kind="approval",
            payload={"summary": "the gateway refused the run's key (work item budget exceeded)"},
            requested_at=now,
        )
        session.add(escalation)
        session.add(
            HumanRequest(
                work_item_id=item_id,
                project_id=pid,
                transition_id="t-abandon",
                kind="approval",
                payload={"summary": "Approval needed to move to `abandoned`"},
                requested_at=now + timedelta(seconds=2),
            )
        )
        if spent_usd:
            session.add(
                CostLedger(project_id=pid, work_item_id=item_id, kind="model", cost_usd=spent_usd, ts=now)
            )
        await session.flush()
        return item_id, str(escalation.id)


def _signals() -> list[tuple[str, Any]]:
    from tranibor_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    return [(name, payload) for _, name, payload in fake.signals]


async def _open(item_id: str) -> list[str]:
    from sqlalchemy import select
    from tranibor_api.db.models import HumanRequest
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(HumanRequest.transition_id).where(
                HumanRequest.work_item_id == item_id, HumanRequest.decided_at.is_(None)
            )
        )
        return sorted(str(t) for t in rows.scalars())


async def test_the_board_shows_the_escalation_not_the_abandon_step(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, escalation = await _parked(client, project)

    shown = await client.get(f"/api/v1/work-items/{item_id}")

    assert shown.status_code == 200, shown.text
    assert shown.json()["pending_request"]["id"] == escalation


async def test_approving_without_a_request_id_replays_the_stage_and_never_abandons(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, _ = await _parked(client, project)

    approved = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})

    assert approved.status_code == 202, approved.text
    sent = _signals()
    assert [name for name, _ in sent] == ["control"], "no decision reached the abandon step"
    assert sent[0][1]["action"] == "rerun_stage"
    # The abandon step stays open until the interpreter resumes and closes it as superseded.
    assert await _open(item_id) == ["t-abandon"]


async def test_approving_the_escalation_by_its_id_replays_the_stage(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, escalation = await _parked(client, project)

    approved = await client.post(
        f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve", "request_id": escalation}
    )

    assert approved.status_code == 202, approved.text
    assert approved.json()["decision"]["approved"] is True
    assert [p["action"] for name, p in _signals() if name == "control"] == ["rerun_stage"]


async def test_a_spent_budget_refuses_the_approval_and_says_what_to_raise(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, _ = await _parked(client, project, spent_usd=500.0)

    refused = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})

    assert refused.status_code == 409, refused.text
    assert "budget is spent" in refused.json()["detail"]
    assert _signals() == [], "a replay refused its key at once would only park it again"
    assert await _open(item_id) == ["t-abandon", "t-implement"]


async def test_the_abandon_step_is_still_decided_when_named(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import HumanRequest
    from tranibor_api.db.session import session_scope

    item_id, _ = await _parked(client, project)
    async with session_scope() as session:
        abandon = (
            await session.execute(
                select(HumanRequest.id).where(
                    HumanRequest.work_item_id == item_id, HumanRequest.transition_id == "t-abandon"
                )
            )
        ).scalar_one()

    approved = await client.post(
        f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve", "request_id": str(abandon)}
    )

    assert approved.status_code == 202, approved.text
    sent = _signals()
    assert [name for name, _ in sent] == ["human_decision"]
    assert sent[0][1]["request_id"] == str(abandon)


async def test_a_request_of_another_work_item_is_not_found(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    first, escalation = await _parked(client, project)
    other = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Other", "workflow": "escalating"}
    )
    assert other.status_code == 201, other.text

    refused = await client.post(
        f"/api/v1/work-items/{other.json()['id']}/decisions",
        json={"kind": "approve", "request_id": escalation},
    )

    assert refused.status_code == 409, refused.text
    assert await _open(first) == ["t-abandon", "t-implement"]
