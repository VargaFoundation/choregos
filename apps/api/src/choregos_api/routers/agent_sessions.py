# SPDX-License-Identifier: Apache-2.0
"""A person talking to a prompt agent directly (S41-09, ADR 0080 §10): `POST …/responses`, the
Responses API's shape, and the session it opens, read and closed.

The caller needs the agent's `SEE` and `CONVERSE` (`item.control` in the project, ADR 0061): what
the agent may do in a session is what its spec grants in the project, and the caller must be one who
may drive that project's work. A session is its opener's: anyone else is answered 404.

With `stream`, the turn is computed first, its rows committed, and the answer sent as server-sent
events (`response.created`, `response.output_text.delta`, `response.completed|failed`): the text
arrives in one delta, not token by token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..db.models import AgentSessionRecord, Organization, Project
from ..deps import Db, Me
from ..errors import not_found, unprocessable
from ..schemas.agent_sessions import AgentDelta, AgentResponse, AgentResponseCreate, AgentSession
from ..services.agent_governance import Action, decide
from ..services.agent_sessions import close_session, open_session, respond, session_dto
from .agents import _charger, _exiger, _lire, _organisation, _projet

router = APIRouter(tags=["agents"])


async def _own_session(
    session: AsyncSession, principal: Me, organisation: Organization, session_id: str
) -> AgentSessionRecord:
    record = await session.get(AgentSessionRecord, session_id)
    if record is None or record.org_id != organisation.id or record.user_id != principal.user_id:
        raise not_found("Agent session", session_id)
    return record


@router.post(
    "/orgs/{org}/agents/{slug}/responses",
    response_model=AgentResponse,
    operation_id="createAgentResponse",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def create_agent_response(
    org: str, slug: str, body: AgentResponseCreate, session: Db, principal: Me
) -> Any:
    organisation, agent, subject = await _charger(session, principal, org, slug)
    project = await _projet(session, organisation, body.project)
    _exiger(decide(principal, org, subject, Action.CONVERSE, project_slug=project.slug))
    if body.session_id is None:
        record = await open_session(
            session, principal, organisation, agent.slug, project, max_usd=body.max_usd
        )
    else:
        record = await _own_session(session, principal, organisation, body.session_id)
        if record.agent_slug != agent.slug or record.project_id != project.id:
            raise unprocessable(
                f"session {record.id} is not a session of `{agent.slug}` in `{project.slug}`",
                [{"loc": ["body", "session_id"], "msg": "another agent's or another project's session"}],
            )
    inputs = [body.input] if isinstance(body.input, str) else [m.content for m in body.input]
    answer = await respond(session, record, project, inputs)
    if not body.stream:
        return answer
    return EventSourceResponse(_events(answer))


async def _events(answer: AgentResponse) -> AsyncIterator[dict[str, str]]:
    created = answer.model_copy(update={"output": [], "output_text": ""})
    yield {"event": "response.created", "data": created.model_dump_json()}
    if answer.output_text:
        yield {
            "event": "response.output_text.delta",
            "data": AgentDelta(response_id=answer.id, delta=answer.output_text).model_dump_json(),
        }
    yield {"event": f"response.{answer.status}", "data": answer.model_dump_json()}


@router.get(
    "/orgs/{org}/agent-sessions/{session_id}",
    response_model=AgentSession,
    operation_id="getAgentSession",
)
async def get_agent_session(org: str, session_id: str, session: Db, principal: Me) -> AgentSession:
    _lire(principal, org)
    organisation = await _organisation(session, org)
    record = await _own_session(session, principal, organisation, session_id)
    project = await session.get(Project, record.project_id)
    return await session_dto(session, record, project.slug if project else "")


@router.delete(
    "/orgs/{org}/agent-sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="closeAgentSession",
)
async def close_agent_session(org: str, session_id: str, session: Db, principal: Me) -> Response:
    _lire(principal, org)
    organisation = await _organisation(session, org)
    record = await _own_session(session, principal, organisation, session_id)
    await close_session(session, record, reason="closed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
