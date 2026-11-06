# SPDX-License-Identifier: Apache-2.0
"""A ticket that comes back restarts its finished work item (C1, D2C plan §4).

A work item that ended — delivered, declined, still observed — used to stay ended whatever its ticket
did next: a trigger delivery for its key answered `already_open`, the tracker catch-up skipped it as
terminal, and a reopened issue only marked its decisions (ADR 0121 §6). The platform's healthcheck
reopens a ticket when a finding comes back (label `recidive`), and that work must start again.

**A comeback is a new pass of the same work item**, not a new work item:

- one ticket, one work item, one interpreter id (`wi-<project>-<key>`, S1-10): every door, signal and
  tracker lookup keeps finding it by its key, and the work item's page tells the whole story — the
  runs, events and decisions of every pass, the pass number (`recurrence`) and when it began;
- the pass is the next Temporal run of that id (`ALLOW_DUPLICATE`), started at the workflow's start
  state; the run numbers go on (`<work item>-<transition>-<attempt>`, ADR 0008), so no run of an earlier
  pass is handed back, while the attempts budget and the ticket spend count from the pass's start;
- twice is once: the restart is claimed by one conditional update — the work item is ended and still
  at the recurrence the door read — so a replayed delivery, a second door or a second catch-up pass
  finds it running and starts nothing more.

A new work item linked to the old one (`recurrence_of`) would need a second work item under the same
tracker key: the key is unique per project, and every webhook, signal and train finds its work item —
and its interpreter — by it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from choregos_contracts import EventType
from choregos_core import utcnow
from choregos_core.domain import aware
from sqlalchemy import update

from ..db.models import Project, WorkItem
from .decision_ledger import record_reopened
from .definitions import work_item_workflow, workflow_model
from .events import persist_event

#: What a pass leaves in the work item's documents that the next pass must not read as its own: the
#: release that delivered it (`close_out` would announce it again), a person's feedback and answer.
PASS_DOCUMENTS = ("deployed_release", "human_feedback", "human_answer")
#: A tracker's clock and the platform's are not the same: a comeback the tracker dates less than this
#: after the work item ended is left to the webhook, never read from the catch-up (a mirror of the
#: platform's own end, seen through a skewed clock, must not restart anything).
CLOCK_MARGIN = timedelta(minutes=5)


#: The status categories a move must say it enters to bring an ended work item back: Jira's To Do and
#: In Progress.
WORK_CATEGORIES = frozenset({"new", "indeterminate"})


def says_back_to_work(event_type: str, payload: dict[str, Any]) -> bool:
    """Whether a tracker event may bring an ended work item back by what it says of the status: any event
    but a status move, and a move into a status its tracker puts in To Do or In Progress. A tracker that
    names no category (a GitHub board column, a plugin tracker) brings a work item back by reopening its
    issue or by its start label, never by a column: archiving a card is not a comeback."""
    if event_type != "tracker.item.moved":
        return True
    return str(payload.get("status_category") or "").casefold() in WORK_CATEGORIES


def terminal_statuses(workflow: Any) -> list[str]:
    """The tracker statuses the workflow's terminal states map to."""
    return [
        s.tracker.status for s in workflow.states.values() if s.terminal and s.tracker and s.tracker.status
    ]


def _folded(statuses: Iterable[str]) -> set[str]:
    return {str(status).strip().casefold() for status in statuses if status}


def came_back_since(item: WorkItem, data: Any, workflow: Any) -> bool:
    """Whether what the tracker says of the ticket now (`WorkItemData`, re-read by the catch-up) shows it
    came back after the work item ended: the tracker dates a return to work (`reopened_at`: GitHub's last
    `reopened`, Jira's move into a To Do or In Progress status) after the end, and the ticket is not in a
    status a terminal state maps to. Always false for a work item still running."""
    ended = aware(item.closed_at)
    back = aware(getattr(data, "reopened_at", None))
    if ended is None or back is None or back <= ended + CLOCK_MARGIN:
        return False
    status = str(getattr(data, "state", None) or "").strip().casefold()
    return not status or status not in _folded(terminal_statuses(workflow))


def restart_payload(project: Project, item: WorkItem) -> dict[str, Any]:
    """What the interpreter of a new pass is started with: where it starts, and the pass number."""
    return {
        "project_id": project.id,
        "project_slug": project.slug,
        "work_item_id": item.id,
        "tracker_key": item.tracker_key,
        "resume_from": item.state,
        "recurrence": item.recurrence,
    }


async def reopen_work_item(
    session: Any, project: Project, item: WorkItem, *, door: str, reason: str
) -> int | None:
    """Claims a new pass of an ended work item and resets it to its workflow's start state.

    Returns the pass number, or `None` when the work item is not ended or another door claimed the
    pass first (the update matched nothing). The caller then starts the interpreter with
    `restart_payload`, in the same transaction, on the same interpreter id."""
    if item.closed_at is None:
        return None
    workflow = workflow_model(await work_item_workflow(session, item))
    initial = workflow.initial_state
    ended = aware(item.closed_at)
    previous = {"from": item.state, "ended_at": ended.isoformat() if ended else None, "pr_url": item.pr_url}
    seen = int(item.recurrence or 0)
    now: datetime = utcnow()
    documents = {k: v for k, v in dict(item.documents or {}).items() if k not in PASS_DOCUMENTS}
    claimed = await session.execute(
        update(WorkItem)
        .where(
            WorkItem.id == item.id,
            WorkItem.closed_at.is_not(None),
            WorkItem.recurrence == seen,
        )
        .values(
            recurrence=seen + 1,
            recurred_at=now,
            closed_at=None,
            state=initial,
            failure=None,
            paused=False,
            pr_url=None,
            documents=documents,
        )
        .execution_options(synchronize_session=False)
    )
    if not claimed.rowcount:
        return None
    await session.refresh(item)
    # The decisions of the pass that ended were not right: the ticket came back (ADR 0121 §6).
    await record_reopened(session, item.id)
    await persist_event(
        session,
        EventType.WORKITEM_STATE_CHANGED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=item.tracker_key,
        to=initial,
        reason="reopened",
        recurrence=seen + 1,
        door=door,
        why=reason,
        **previous,
    )
    return seen + 1


async def restart_work_item(
    session: Any, project: Project, item: WorkItem, *, door: str, reason: str
) -> int | None:
    """`reopen_work_item`, then the new pass's interpreter, from the API (a webhook, a trigger)."""
    from ..temporal import get_temporal, interpreter_id

    recurrence = await reopen_work_item(session, project, item, door=door, reason=reason)
    if recurrence is None:
        return None
    workflow_id = interpreter_id(project.slug, item.tracker_key)
    await get_temporal().restart_interpreter(workflow_id, restart_payload(project, item))
    item.temporal_wf_id = workflow_id
    return recurrence


__all__ = [
    "CLOCK_MARGIN",
    "PASS_DOCUMENTS",
    "WORK_CATEGORIES",
    "came_back_since",
    "reopen_work_item",
    "restart_payload",
    "restart_work_item",
    "says_back_to_work",
    "terminal_statuses",
]
