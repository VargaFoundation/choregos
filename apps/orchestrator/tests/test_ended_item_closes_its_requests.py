"""An ended work item leaves no request open (FON-319, 2026-11-18).

FON-319 was abandoned while its budget escalation's request stayed open: the board kept, in the
*Abandoned* column, an "Approve" that could only answer 409, its interpreter having completed.
"""

from __future__ import annotations

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def test_close_out_closes_the_open_requests_as_superseded(setup: Fixture) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import HumanRequest, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_core import utcnow
    from tranibor_orchestrator.activities.tracker import close_out

    async with session_scope() as session:
        row = await session.get(WorkItem, setup.work_item_id)
        assert row is not None
        row.state = "abandoned"
        session.add(
            HumanRequest(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-implement",
                kind="approval",
                payload={"summary": "work item budget exceeded"},
                requested_at=utcnow(),
            )
        )
        session.add(
            HumanRequest(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                transition_id="t-abandon",
                kind="approval",
                payload={"summary": "Approval needed to move to `abandoned`"},
                requested_at=utcnow(),
                decided_at=utcnow(),
                decided_by="marie",
                decision={"approved": True},
            )
        )

    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})
    # Replayed (an activity retry): nothing changes twice.
    await close_out({"project_id": setup.project_id, "work_item_id": setup.work_item_id})

    async with session_scope() as session:
        rows = {
            r.transition_id: r
            for r in (
                await session.execute(
                    select(HumanRequest).where(HumanRequest.work_item_id == setup.work_item_id)
                )
            ).scalars()
        }
    budget, abandon = rows["t-implement"], rows["t-abandon"]
    assert budget.decided_at is not None, "an ended work item keeps a request nobody can answer"
    assert budget.decided_by == "the platform"
    assert budget.decision == {"approved": False, "superseded": "the work item ended in `abandoned`"}
    assert abandon.decided_by == "marie" and abandon.decision == {"approved": True}, "a decision is kept"
