# SPDX-License-Identifier: Apache-2.0
"""Stop an agent: suspend it, then stop what it is doing (ADR 0142 §4-6).

The order is the point. The agent is `suspended` and that is COMMITTED first, so `services/agents.resolve`
refuses any run that would start while the rest is stopped ("agent is suspended: the run does not
start"). Then every run of the agent `queued` or `running`, in every project of the organisation, is
stopped as `POST /runs/{id}/cancel` stops one (`run_cancel.cancel_agent_run`: its work item waits
for a person, it is not stopped), and its open sessions are closed (`close_session`, reason
`agent_stopped`). Conversation turns (`cancel_turn`) are not on this branch yet (S63, then S64-09):
the answer counts none.

Each run and each session is stopped in its own savepoint: one that cannot be stopped now is rolled
back alone and named in `not_stopped`, the others are kept. A run that ended meanwhile keeps how it
ended and is not counted. Stopping an agent already suspended stops what is still in flight and is
audited again: the stop is idempotent, it never fails because it already happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentSessionRecord, Organization, Project, Run
from ..db.session import release_connection
from ..errors import ApiError
from ..logging import get_logger
from ..rbac import Principal
from .agent_sessions import close_session
from .run_cancel import IN_FLIGHT, cancel_agent_run

logger = get_logger("choregos.agent_stop")

#: Why a session closed when its agent was stopped (`AgentSessionRecord.reason`).
SESSION_REASON = "agent_stopped"


@dataclass(frozen=True, slots=True)
class NotStopped:
    """What could not be stopped now, and why; the reconciliation retries a run."""

    kind: Literal["run", "session", "turn"]
    id: str
    detail: str


@dataclass(slots=True)
class AgentStop:
    """What one stop did."""

    runs_cancelled: list[str] = field(default_factory=list)
    sessions_closed: list[str] = field(default_factory=list)
    #: Conversation turns live on the S63 branch: none is cancelled here until S64-09.
    turns_cancelled: int = 0
    not_stopped: list[NotStopped] = field(default_factory=list)


async def suspend_first(session: AsyncSession, agent: Agent) -> None:
    """The agent is `suspended`, COMMITTED: from now on no run of it starts, whatever follows.

    `release_connection`, not a bare `commit`: on PostgreSQL the request's RLS scope
    (`app.current_orgs`, `app.current_user`) is set per transaction, and the transaction after a bare
    commit would start without it — the run updates would see no row and the audit insert would be
    refused by `audit_log`'s policy. The next transaction begins in the same scope."""
    agent.status = "suspended"
    await release_connection(session)


async def stop_agent(
    session: AsyncSession, principal: Principal, organisation: Organization, agent: Agent, reason: str
) -> AgentStop:
    """Suspend `agent` and stop its runs and sessions, for `principal`, with `reason`.

    The caller has decided `Action.MANAGE` on the agent and refused a revoked one."""
    previous = agent.status
    await suspend_first(session, agent)
    stop = AgentStop()
    for run in await _runs_in_flight(session, organisation, agent.slug):
        await _stop_run(session, principal, run, reason, stop)
    for open_session in await _open_sessions(session, organisation, agent.slug):
        await _close(session, open_session, stop)
    await record(
        session,
        principal,
        "agent.stop",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        reason=reason,
        before=previous,
        runs_cancelled=len(stop.runs_cancelled),
        sessions_closed=len(stop.sessions_closed),
        turns_cancelled=stop.turns_cancelled,
        not_stopped=[{"kind": n.kind, "id": n.id, "detail": n.detail} for n in stop.not_stopped],
    )
    return stop


async def _runs_in_flight(session: AsyncSession, organisation: Organization, slug: str) -> list[Run]:
    """The agent's runs `queued` or `running`, in every project of the organisation, oldest first."""
    rows = await session.execute(
        select(Run)
        .join(Project, Project.id == Run.project_id)
        .where(Project.org_id == organisation.id, Run.agent_slug == slug, Run.status.in_(IN_FLIGHT))
        .order_by(Run.created_at, Run.id)
    )
    return list(rows.scalars())


async def _open_sessions(
    session: AsyncSession, organisation: Organization, slug: str
) -> list[AgentSessionRecord]:
    rows = await session.execute(
        select(AgentSessionRecord)
        .where(
            AgentSessionRecord.org_id == organisation.id,
            AgentSessionRecord.agent_slug == slug,
            AgentSessionRecord.status == "open",
        )
        .order_by(AgentSessionRecord.created_at, AgentSessionRecord.id)
    )
    return list(rows.scalars())


async def _stop_run(
    session: AsyncSession, principal: Principal, run: Run, reason: str, stop: AgentStop
) -> None:
    run_id = run.id
    try:
        async with session.begin_nested():
            await cancel_agent_run(session, principal, run, reason)
    except ApiError as refused:
        if refused.status_code == 409:
            # It ended between the list and the stop: it keeps how it ended, nothing to count.
            logger.info("agent_stop.run_already_ended", run_id=run_id)
            return
        stop.not_stopped.append(NotStopped(kind="run", id=run_id, detail=str(refused.detail)[:500]))
        return
    stop.runs_cancelled.append(run_id)


async def _close(session: AsyncSession, record_: AgentSessionRecord, stop: AgentStop) -> None:
    session_id = record_.id
    try:
        async with session.begin_nested():
            await close_session(session, record_, reason=SESSION_REASON)
    except Exception as error:  # the gateway or the pod did not answer: named, the others go on
        logger.warning("agent_stop.session_not_closed", session_id=session_id, error=str(error)[:300])
        await session.refresh(record_)
        stop.not_stopped.append(
            NotStopped(kind="session", id=session_id, detail=f"{type(error).__name__}: {error}"[:500])
        )
        return
    stop.sessions_closed.append(session_id)


__all__ = ["SESSION_REASON", "AgentStop", "NotStopped", "stop_agent", "suspend_first"]
