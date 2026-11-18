# SPDX-License-Identifier: Apache-2.0
"""Which open request of a work item a person sees and decides (FON-319, 2026-11-18).

A work item that escalates to the question state holds TWO open requests: the escalation's own,
which says why (the budget is spent, the run was stopped…), and, a moment later, the request of the
transition that waits in the question state — in every template, `needs_human -> abandoned`, by a
person. The board showed, and a decision without `request_id` decided, the NEWEST: an "Approve"
under a budget message abandoned the work item. Two rules here:

- the abandon step is never the default while another request is open (`pending_request`);
- approving the escalation's request replays the stage that escalated (`is_parking_request`): it is
  what the person asked for, and the interpreter ignored that approval as stale (#1795).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tranibor_contracts import HumanRequestKind, Transition, Workflow
from tranibor_core import WorkflowEngine

from ..db.models import HumanRequest, WorkItem


def _transition(workflow: Workflow, transition_id: str | None) -> Transition | None:
    return next((t for t in workflow.transitions if t.key == transition_id), None)


def leads_to_abandon(workflow: Workflow, row: HumanRequest) -> bool:
    """The request is the step that abandons the work item."""
    abandon = WorkflowEngine(workflow).abandon_state()
    transition = _transition(workflow, row.transition_id)
    return abandon is not None and transition is not None and transition.to == abandon


def is_parking_request(workflow: Workflow, item: WorkItem, row: HumanRequest) -> bool:
    """The approval an escalation asked for: the work item is parked in the question state, and the
    request belongs to an agent's or a job's stage it left — not to a transition that waits where it
    now stands, nor to a person's step left open elsewhere (the `pr_open` approval of #1795)."""
    if row.kind != HumanRequestKind.APPROVAL.value or row.decided_at is not None:
        return False
    engine = WorkflowEngine(workflow)
    question = engine.question_state()
    transition = _transition(workflow, row.transition_id)
    return (
        question is not None
        and item.state == question
        and transition is not None
        and transition.from_ != item.state
        and engine.actor_kind(transition) in ("agent", "job")
    )


async def open_requests(session: AsyncSession, work_item_id: str) -> list[HumanRequest]:
    """The work item's undecided requests, newest first."""
    query = (
        select(HumanRequest)
        .where(HumanRequest.work_item_id == work_item_id, HumanRequest.decided_at.is_(None))
        .order_by(HumanRequest.requested_at.desc())
    )
    return list((await session.execute(query)).scalars())


async def pending_request(session: AsyncSession, item: WorkItem, workflow: Workflow) -> HumanRequest | None:
    """The request a person sees and decides by default: the newest open one, the abandon step last."""
    rows = await open_requests(session, item.id)
    # `sorted` is stable: newest first among the requests that do not abandon, then the abandon.
    rows.sort(key=lambda row: leads_to_abandon(workflow, row))
    return rows[0] if rows else None
