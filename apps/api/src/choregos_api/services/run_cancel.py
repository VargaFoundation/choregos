# SPDX-License-Identifier: Apache-2.0
"""Stop one run now, with a reason (ADR 0142 §2, §5-6).

One path for every stop that reaches a run: `POST /runs/{id}/cancel`, a work item's `stop`, and an
agent's stop (S64-04), which calls it for each of the agent's runs in flight.

The run's result is written as stopped by a person, the reason and who asked in its summary, by a
conditional update: whoever writes a result first — this stop or the runner posting its own — keeps
it, and the other changes nothing. The executor is then stopped, the logs archived, the key closed
and the spend booked once by `RunCancellation` (the `cancel_run` activity), started once the
result is committed. The interpreter reads the result at its next poll and takes the stage to a
person; it does not retry it.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import EventType
from choregos_core import utcnow
from choregos_core.run_closure import OPEN_RUN_STATUSES, run_is_closed
from choregos_core.run_stop import stopped_by_a_person, stopped_result
from sqlalchemy import JSON, ColumnElement, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization, Project, Run, WorkItem
from ..db.session import after_commit
from ..errors import ApiError, conflict, forbidden, not_found
from ..logging import get_logger
from ..rbac import Permission, Principal
from .events import persist_event

logger = get_logger("choregos.run_cancel")

#: The run statuses a stop can still interrupt: an open run's (ADR 0143 §1).
IN_FLIGHT = tuple(sorted(OPEN_RUN_STATUSES))


def without_a_result() -> ColumnElement[bool]:
    """A run with no result yet: SQL `NULL`, or the JSON `null` a resumed run's result is reset to
    (`activities/suspension.py`). The condition of every first writer (ADR 0142 §2)."""
    return or_(Run.result.is_(None), Run.result == JSON.NULL)


def how_it_ended(run: Run) -> str:
    """Why a stop changes nothing: the run's own words for how it ended."""
    result: dict[str, Any] = dict(run.result or {})
    if stopped_by_a_person(result):
        return f"the run `{run.id}` is already stopped ({result.get('summary')})"
    if result:
        reason = f", {result['reason']}" if result.get("reason") else ""
        return f"the run `{run.id}` already ended: `{result.get('status')}`{reason}"
    return f"the run `{run.id}` already ended: `{run.status}`"


async def cancel_run_now(
    session: AsyncSession, principal: Principal, run: Run, reason: str, *, channel: str | None = None
) -> Run:
    """Stop `run` for `principal`, with `reason`; the run as it now stands.

    `forbidden` without `item:control` on the run's project; `conflict` (409) when the run has a
    result or no longer runs — first writer wins, and the answer says how it ended. Audited as
    `run.cancel` with the reason; `channel` (`work_item`, `agent_stop`) says which stop reached it."""
    project = await session.get(Project, run.project_id)
    if project is None:
        raise not_found("Project", run.project_id)
    org = await session.get(Organization, project.org_id)
    if not principal.can(Permission.ITEM_CONTROL, org.slug if org else "", project.slug):
        raise forbidden("stopping a run needs `item:control` on its project (developer or above)")
    return await _write_the_stop(session, principal, project, run, reason, channel=channel)


async def cancel_agent_run(session: AsyncSession, principal: Principal, run: Run, reason: str) -> Run:
    """Stop `run` as part of its agent's stop (ADR 0142 §4); the run as it now stands.

    The right is the agent's: whoever may manage the agent (`Action.MANAGE`, decided by the route
    before it calls this) may stop every run of it, in every project of the organisation, without
    holding `item:control` on each of them — the run is the agent's work, not the project's
    decision, and the work item it served is not stopped, it waits for a person. Everything else is
    `cancel_run_now`'s: first writer wins (409 when the run already ended), `run.cancel` audited
    with the reason and `channel: agent_stop`, `RunCancellation` started once committed."""
    project = await session.get(Project, run.project_id)
    if project is None:
        raise not_found("Project", run.project_id)
    return await _write_the_stop(session, principal, project, run, reason, channel="agent_stop")


async def _write_the_stop(
    session: AsyncSession,
    principal: Principal,
    project: Project,
    run: Run,
    reason: str,
    *,
    channel: str | None,
) -> Run:
    """The stop itself, once the right is decided: the conditional write, the audit, the event and
    `RunCancellation`."""
    if run_is_closed(run):  # one definition of an ended run, the orchestrator's too (ADR 0143 §1)
        raise conflict(how_it_ended(run), extra={"code": "run_already_ended"})

    written = stopped_result(principal.email, reason).model_dump(mode="json", by_alias=True)
    claimed = await session.execute(
        update(Run)
        .where(Run.id == run.id, without_a_result(), Run.status.in_(IN_FLIGHT))
        .values(result=written, status="cancelled", ended_at=run.ended_at or utcnow())
        .execution_options(synchronize_session=False)
    )
    await session.refresh(run)
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        # The runner posted its result between the read and the write: it keeps it.
        raise conflict(how_it_ended(run), extra={"code": "run_already_ended"})

    extra = {"channel": channel} if channel else {}
    await record(
        session,
        principal,
        "run.cancel",
        org_id=project.org_id,
        target_type="run",
        target_id=run.id,
        reason=reason,
        work_item_id=run.work_item_id,
        **extra,
    )
    await persist_event(
        session,
        EventType.RUN_FINISHED,
        project_id=project.id,
        work_item_id=run.work_item_id,
        project_slug=project.slug,
        subject=run.id,
        run_id=run.id,
        stage=run.stage_role,
        status="cancelled",
        by=principal.email,
    )
    _cancel_on_the_executor_once_committed(session, project.id, run.id)
    return run


def _cancel_on_the_executor_once_committed(session: AsyncSession, project_id: str, run_id: str) -> None:
    """`RunCancellation` leaves for Temporal once the result is COMMITTED (ADR 0085): its `cancel_run`
    then finds the run stopped. Lost (Temporal down), the interpreter still ends the stage at its
    next poll and its `collect_spend` closes the key; the executor stops the run at its deadline."""
    from ..temporal import get_temporal, run_cancellation_id

    async def start() -> None:
        await get_temporal().start_run_cancellation(
            run_cancellation_id(run_id), {"project_id": project_id, "run_id": run_id}
        )

    after_commit(session, start)


async def cancel_current_runs(
    session: AsyncSession, principal: Principal, item: WorkItem, reason: str, *, channel: str
) -> list[str]:
    """Stop the work item's runs in flight (ADR 0142 §3), normally one: the ids stopped. A run that
    ended meanwhile keeps how it ended and is skipped."""
    runs = (
        (
            await session.execute(
                select(Run)
                .where(Run.work_item_id == item.id, Run.status.in_(IN_FLIGHT), without_a_result())
                .order_by(Run.created_at)
            )
        )
        .scalars()
        .all()
    )
    stopped: list[str] = []
    for run in runs:
        try:
            await cancel_run_now(session, principal, run, reason, channel=channel)
        except ApiError as refused:
            if refused.status_code != 409:
                raise
            logger.info("run already ended", run_id=run.id, work_item=item.id)
            continue
        stopped.append(run.id)
    return stopped
