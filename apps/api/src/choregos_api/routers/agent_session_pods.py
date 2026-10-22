# SPDX-License-Identifier: Apache-2.0
"""What a hosted session's pod reads and writes (S41-11, ADR 0080 §10): `/internal/agent-sessions/{id}`.

The pod's runner speaks with the token its workflow minted for it, whose subject is
`session-{id}`: a run's token, or another session's, is refused here. The runner takes its stage
input (no key, no token in it), the next turn, posts the container's answer, and relays the
container's tool calls, which go through the courtier as a prompt session's do.

The long poll of `turns/next` holds no transaction while it waits: each look is a short one of its
own, so a response committing its turn is never blocked by the pod waiting for it.

A container that serves `invocations_ws` (S41-21) is reached through the same journal: the pod reads
the caller's frames from `socket/next`, the same way, and posts the container's to `socket/out`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, Project
from ..db.session import TOUT, limiter_aux_organisations, session_scope
from ..deps import Db
from ..errors import ApiError, forbidden, not_found, unauthorized
from ..schemas.agent_sessions import (
    AgentSessionSocketNext,
    AgentSessionSocketOut,
    AgentSessionSocketRecorded,
    AgentSessionToolAnswer,
    AgentSessionToolCall,
    AgentSessionTools,
    AgentSessionTurn,
    AgentSessionTurnResult,
)
from ..security import verify_run_token
from ..services.agent_hosted_sessions import POLL_S, answer_turn, next_turn, session_run_id
from ..services.agent_session_sockets import record_out, socket_frames, write
from ..services.agent_sessions import call_session_tool, session_tools

router = APIRouter(tags=["internal"], prefix="/internal")

#: The longest a runner's `turns/next` may wait, in seconds.
MAX_WAIT_S = 30.0


async def session_pod_record(
    id: str, session: Db, authorization: Annotated[str | None, Header()] = None
) -> AgentSessionRecord:
    """The session THIS token was minted for; any other token is refused."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing session token")
    try:
        claims = verify_run_token(authorization.split(" ", 1)[1].strip())
    except jwt.PyJWTError as exc:
        raise forbidden(f"invalid session token: {exc}") from exc
    if claims.run_id != session_run_id(id):
        raise forbidden("this token belongs to another run or another session")
    # The token's scope is the session it names, not an organisation (as a run's, `run_claims`).
    await limiter_aux_organisations(session, TOUT)
    record = await session.get(AgentSessionRecord, id)
    if record is None:
        raise not_found("Agent session", id)
    return record


SessionPod = Annotated[AgentSessionRecord, Depends(session_pod_record)]


def _gone(record: AgentSessionRecord) -> ApiError:
    return ApiError(410, "Session ended", f"session {record.id} is {record.status} ({record.reason})")


async def _project(session: AsyncSession, record: AgentSessionRecord) -> Project:
    project = await session.get(Project, record.project_id)
    if project is None:
        raise not_found("Project of the session", record.id)
    return project


async def _spec(session: AsyncSession, record: AgentSessionRecord, project: Project) -> Any:
    from ..services.agents import resoudre_l_agent

    served = f"{record.agent_slug}@{record.agent_version}"
    return (await resoudre_l_agent(session, record.org_id, project.id, served)).spec


@router.get("/agent-sessions/{id}/input", operation_id="getAgentSessionInput")
async def get_session_input(id: str, session: Db, record: SessionPod) -> dict[str, Any]:
    """The stage input the pod's workflow wrote: what the runner and the container are told."""
    if record.stage_input is None:
        raise not_found("Stage input of the session", id)
    return dict(record.stage_input)


@router.get(
    "/agent-sessions/{id}/turns/next",
    response_model=AgentSessionTurn,
    operation_id="nextAgentSessionTurn",
    responses={204: {"description": "no turn waits"}, 410: {"description": "the session ended"}},
)
async def get_next_turn(
    id: str,
    session: Db,
    record: SessionPod,
    wait: Annotated[float, Query(ge=0, le=MAX_WAIT_S)] = 0.0,
) -> Any:
    """The oldest turn waiting for the pod; with `wait`, up to that many seconds for one to come."""
    if record.status != "open":
        raise _gone(record)
    org_id = record.org_id
    await session.commit()  # the request's own transaction holds nothing while it waits
    deadline = time.monotonic() + wait
    while True:
        async with session_scope(orgs=TOUT) as db:
            current = await db.get(AgentSessionRecord, id)
            if current is None or current.org_id != org_id:
                raise not_found("Agent session", id)
            if current.status != "open":
                raise _gone(current)
            turn = await next_turn(db, current)
        if turn is not None:
            return turn
        if time.monotonic() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await asyncio.sleep(POLL_S)


@router.post(
    "/agent-sessions/{id}/turns/{turn_id}",
    operation_id="postAgentSessionTurnResult",
)
async def post_turn_result(
    id: str, turn_id: str, body: AgentSessionTurnResult, session: Db, record: SessionPod
) -> dict[str, str]:
    """The container's answer to a turn. A second post of the same turn changes nothing."""
    if record.status != "open":
        raise _gone(record)
    recorded = await answer_turn(session, record, turn_id, body)
    return {"status": "recorded" if recorded else "already_recorded", "turn_id": turn_id}


@router.get(
    "/agent-sessions/{id}/socket/next",
    response_model=AgentSessionSocketNext,
    operation_id="nextAgentSessionSocketFrames",
    responses={410: {"description": "the session ended"}},
)
async def get_socket_frames(
    id: str,
    session: Db,
    record: SessionPod,
    after: Annotated[int, Query(ge=0)] = 0,
    wait: Annotated[float, Query(ge=0, le=MAX_WAIT_S)] = 0.0,
) -> AgentSessionSocketNext:
    """The caller's frames after the journal's `after`; with `wait`, up to that many seconds for one."""
    if record.status != "open":
        raise _gone(record)
    await session.commit()  # as `turns/next`: nothing is held while it waits
    deadline = time.monotonic() + wait
    while True:
        async with session_scope(orgs=TOUT) as db:
            current = await db.get(AgentSessionRecord, id)
            if current is None:
                raise not_found("Agent session", id)
            if current.status != "open":
                raise _gone(current)
            found = await socket_frames(db, current, after)
        if found.frames or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(POLL_S)


@router.post(
    "/agent-sessions/{id}/socket/out",
    response_model=AgentSessionSocketRecorded,
    operation_id="postAgentSessionSocketFrames",
    responses={409: {"description": "a frame out of order"}, 410: {"description": "the session ended"}},
)
async def post_socket_frames(
    id: str, body: AgentSessionSocketOut, session: Db, record: SessionPod
) -> AgentSessionSocketRecorded:
    """The container's frames, numbered by the pod: each number is journaled once."""
    if record.status != "open":
        raise _gone(record)
    await session.commit()  # the caller's handler journals too: a short transaction, tried again

    async def act(db: AsyncSession, current: AgentSessionRecord) -> int:
        return await record_out(db, current, body)

    return AgentSessionSocketRecorded(out_n=int(await write(id, act, orgs=TOUT) or 0))


@router.get(
    "/agent-sessions/{id}/tools",
    response_model=AgentSessionTools,
    operation_id="listAgentSessionTools",
)
async def list_session_tools(id: str, session: Db, record: SessionPod) -> AgentSessionTools:
    """The read tools the agent's `mcp_servers` grant in the project, as MCP announces them."""
    project = await _project(session, record)
    tools = await session_tools(session, await _spec(session, record, project), project)
    return AgentSessionTools(tools=[tool.annonce() for tool in tools.values()])


@router.post(
    "/agent-sessions/{id}/tools/{name}",
    response_model=AgentSessionToolAnswer,
    operation_id="callAgentSessionTool",
)
async def call_tool(
    id: str, name: str, body: AgentSessionToolCall, session: Db, record: SessionPod
) -> AgentSessionToolAnswer:
    """One of the container's tool calls, through the courtier: a tool the session is not granted
    is answered 404, as one that does not exist."""
    if record.status != "open":
        raise _gone(record)
    project = await _project(session, record)
    tools = await session_tools(session, await _spec(session, record, project), project)
    if name not in tools:
        raise not_found("Tool of the session", name)
    return AgentSessionToolAnswer(
        text=await call_session_tool(session, record, project, tools[name], body.arguments)
    )
