# SPDX-License-Identifier: Apache-2.0
"""A person talking to a hosted agent directly (S41-11, ADR 0080 §10): one pod per session.

A hosted agent's container keeps its own conversation, so a session is ONE pod that lives across its
responses: the runner beside the container (`choregos-runner serve-session`) and the container, the
same loopback governance as a workflow step (S41-04). The first response asks the orchestrator for
it (`AgentSessionPod`, one workflow per session: a second response starts nothing); the pod stops
when its session closes, expires, or goes `hosted_session_idle_s` without a response.

A response is a TURN. The API journals it (`turn.queued`), the pod's runner takes it from the
internal API (`GET /internal/agent-sessions/{id}/turns/next`), sends it to the container and posts
what came back (`turn.answered`); the request waiting on `POST …/responses` reads that answer from
the journal. The pod holds no key of its own and no person's token: its runner reads the session's
gateway key from its Secret, and its tool calls come back here, through the courtier, as a prompt
session's do.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AgentSessionEvent, AgentSessionRecord, Organization, Project
from ..db.session import limiter_aux_organisations, session_scope
from ..errors import not_found
from ..logging import get_logger
from ..schemas.agent_sessions import (
    AgentOutputMessage,
    AgentOutputText,
    AgentResponse,
    AgentResponseError,
    AgentResponseUsage,
    AgentSessionTurn,
    AgentSessionTurnResult,
)
from ..schemas.network import RunNetwork
from ..temporal import get_temporal, session_pod_id

if TYPE_CHECKING:
    from ..schemas import AgentSpec

log = get_logger(__name__)

#: How long a response waits for the pod once it is ready (a test shortens it).
TURN_TIMEOUT_S = 300.0
#: How often a waiting response reads the journal.
POLL_S = 0.2
#: The pod's status for a turn → the response's error code.
_ERRORS = {
    "failed": "hosted_failed",
    "not_ready": "hosted_not_ready",
    "timeout": "hosted_timeout",
    "max_turns": "max_turns",
    "budget_exceeded": "budget_exceeded",
}
#: What a turn's events keep of a payload's text: the transcript is not a dump of the container's.
_EVENT_TEXT = 2000


def session_run_id(session_id: str) -> str:
    """The `run_id` a session's pod speaks as: its token's subject, its proxies' journal key."""
    return f"session-{session_id}"


def hosted_protocol(spec: AgentSpec) -> str | None:
    """How a turn reaches the container: `responses` when it serves it, else `invocations`; `None`
    for a container that only speaks `invocations_ws` (relayed in S41-21)."""
    protocols = list(getattr(spec.runtime, "protocols", []) or [])
    for protocol in ("responses", "invocations"):
        if protocol in protocols:
            return protocol
    return None


async def respond_hosted(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    spec: AgentSpec,
    inputs: list[str],
) -> AgentResponse:
    """One turn: queued in the journal, the pod asked for (once), its answer awaited."""
    from .agent_sessions import close_session, journal

    response_id = f"resp_{uuid4().hex}"
    text_in = "\n\n".join(inputs)
    for text in inputs:
        await journal(session, record, "input.text", {"text": text[:_EVENT_TEXT]})
    await journal(
        session,
        record,
        "turn.queued",
        {"turn_id": response_id, "input": text_in, "protocol": hosted_protocol(spec) or "responses"},
    )
    first = record.pod is None
    if first:
        record.pod = {"state": "requested"}
    organisation = await session.get(Organization, record.org_id)
    org_slug = organisation.slug if organisation else ""
    # The pod reads the turn from its own transaction: this one must be committed, and the waiting
    # below must hold no lock (SQLite's SHARED lock would block the pod's answer).
    await session.commit()
    await limiter_aux_organisations(session, [org_slug])

    error: AgentResponseError | None = None
    result: AgentSessionTurnResult | None = None
    runtime = spec.runtime
    readiness = float(getattr(runtime, "readiness_timeout_s", 120))
    try:
        if first:
            await get_temporal().start_session_pod(
                session_pod_id(record.id),
                {
                    "session_id": record.id,
                    "org": org_slug,
                    "idle_s": get_settings().hosted_session_idle_s,
                    "expires_at": record.expires_at.isoformat(),
                },
            )
        else:
            with contextlib.suppress(Exception):  # a missed touch only shortens the pod's idle wait
                await get_temporal().signal(session_pod_id(record.id), "touch", {})
    except Exception as refused:
        log.warning("agent_session.pod_not_started", session_id=record.id, error=str(refused)[:300])
        error = AgentResponseError(code="hosted_unavailable", message="the session's pod could not start")
    if error is None:
        result = await _await_answer(record.id, org_slug, response_id, readiness + TURN_TIMEOUT_S)
    await session.refresh(record)  # the pod's tool calls moved `tool_calls`, its workflow `pod`
    if error is not None and first:
        record.pod = None  # the next response asks again
    if error is None and result is None:
        if record.status != "open":
            error = AgentResponseError(code="session_closed", message=f"the session ended: {record.reason}")
        else:
            error = AgentResponseError(code="hosted_timeout", message="the session's pod did not answer")
            await journal(session, record, "turn.abandoned", {"turn_id": response_id})
    if result is not None and result.status != "answered":
        error = AgentResponseError(
            code=_ERRORS.get(result.status, "hosted_failed"),
            message=(result.error or result.status)[:500],
        )
    record.responses += 1
    text = result.text if result is not None and error is None else ""
    status = "failed" if error is not None else "completed"
    await journal(
        session,
        record,
        f"response.{status}",
        {
            "response_id": response_id,
            "text": text[:_EVENT_TEXT],
            **({"error": error.model_dump()} if error else {}),
        },
    )
    if error is not None and error.code == "budget_exceeded":
        await close_session(session, record, reason="budget_exceeded")
    return AgentResponse(
        id=response_id,
        session_id=record.id,
        status=status,
        output=[AgentOutputMessage(content=[AgentOutputText(text=text)])] if text else [],
        output_text=text,
        usage=AgentResponseUsage(
            turns=result.turns if result else 0, tool_calls=result.tool_calls if result else 0
        ),
        error=error,
    )


async def _await_answer(
    session_id: str, org_slug: str, turn_id: str, timeout_s: float
) -> AgentSessionTurnResult | None:
    """The pod's answer to the turn, read from the journal in short transactions of their own;
    `None` at the deadline, or as soon as the session is no longer open."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        async with session_scope(orgs=[org_slug]) as db:
            record = await db.get(AgentSessionRecord, session_id)
            if record is None or record.status != "open":
                return None
            answered = await _events(db, session_id, "turn.answered")
            for event in answered:
                if (event.payload or {}).get("turn_id") == turn_id:
                    return AgentSessionTurnResult.model_validate((event.payload or {}).get("result") or {})
        await asyncio.sleep(POLL_S)
    return None


async def _events(session: AsyncSession, session_id: str, *types: str) -> list[AgentSessionEvent]:
    rows = await session.execute(
        select(AgentSessionEvent)
        .where(AgentSessionEvent.session_id == session_id, AgentSessionEvent.type.in_(types))
        .order_by(AgentSessionEvent.seq)
    )
    return list(rows.scalars())


async def next_turn(session: AsyncSession, record: AgentSessionRecord) -> AgentSessionTurn | None:
    """The oldest turn neither answered nor abandoned."""
    events = await _events(session, record.id, "turn.queued", "turn.answered", "turn.abandoned")
    done = {(e.payload or {}).get("turn_id") for e in events if e.type != "turn.queued"}
    for event in events:
        payload = event.payload or {}
        if event.type == "turn.queued" and payload.get("turn_id") not in done:
            return AgentSessionTurn(
                turn_id=str(payload["turn_id"]),
                input=str(payload.get("input") or ""),
                protocol=payload.get("protocol") or "responses",
            )
    return None


async def answer_turn(
    session: AsyncSession, record: AgentSessionRecord, turn_id: str, result: AgentSessionTurnResult
) -> bool:
    """The pod's answer to a turn, journaled once: `False` when the turn already had one."""
    from .agent_sessions import journal

    events = await _events(session, record.id, "turn.queued", "turn.answered", "turn.abandoned")
    if not any(e.type == "turn.queued" and (e.payload or {}).get("turn_id") == turn_id for e in events):
        raise not_found("Agent session turn", turn_id)
    if any(e.type != "turn.queued" and (e.payload or {}).get("turn_id") == turn_id for e in events):
        return False
    for event in result.events:
        await journal(session, record, f"pod.{event.type}", {"turn_id": turn_id, **_short(event.payload)})
    await journal(
        session, record, "turn.answered", {"turn_id": turn_id, "result": result.model_dump(mode="json")}
    )
    return True


def _short(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v[:_EVENT_TEXT] if isinstance(v, str) else v for k, v in payload.items()}


async def fail_open_turns(session: AsyncSession, record: AgentSessionRecord, reason: str) -> int:
    """Every turn still waiting when the pod stops: answered `failed`, so no response waits on it."""
    failed = 0
    while (turn := await next_turn(session, record)) is not None:
        await answer_turn(
            session,
            record,
            turn.turn_id,
            AgentSessionTurnResult(status="failed", error=f"the session's pod stopped: {reason}"),
        )
        failed += 1
    return failed


async def session_network(session: AsyncSession, record: AgentSessionRecord) -> RunNetwork:
    """The network the session's pod may use, as a run of the same agent version gets it."""
    from .agents import resoudre_l_agent
    from .network import project_effective_network

    project = await session.get(Project, record.project_id)
    if project is None:
        raise not_found("Project of the session", record.id)
    organisation = await session.get(Organization, project.org_id)
    served = f"{record.agent_slug}@{record.agent_version}"
    spec = (await resoudre_l_agent(session, record.org_id, project.id, served)).spec
    effective, _ = await project_effective_network(session, project, spec.network)
    return RunNetwork(
        run_id=session_run_id(record.id),
        allow=effective.allow,
        deny=effective.deny,
        implied=effective.implied,
        per_host_per_minute=effective.per_host_per_minute,
        per_run_per_minute=effective.per_run_per_minute,
        org_per_host_per_minute=effective.org_per_host_per_minute,
        builtin_web_tools=effective.builtin_web_tools,
        listed=True,
        org=organisation.slug if organisation else "",
        project=project.slug,
        agent=record.agent_slug,
    )


async def stop_pod(record: AgentSessionRecord, reason: str) -> None:
    """Asks the session's pod workflow to stop; a pod already gone answers nothing."""
    if record.pod is None or record.pod.get("state") == "stopped":
        return
    with contextlib.suppress(Exception):
        await get_temporal().signal(session_pod_id(record.id), "close", {"reason": reason})
    log.info("agent_session.pod_close_asked", session_id=record.id, reason=reason)


__all__ = [
    "POLL_S",
    "TURN_TIMEOUT_S",
    "answer_turn",
    "fail_open_turns",
    "hosted_protocol",
    "next_turn",
    "respond_hosted",
    "session_network",
    "session_run_id",
    "stop_pod",
]
