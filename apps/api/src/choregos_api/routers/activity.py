# SPDX-License-Identifier: Apache-2.0
"""What is in flight in an organisation (ADR 0142 §1): one list, and the same entries as a stream.

Any member of the organisation may ask; each entry is then listed only to whoever may read it
(`services/activity.py`). The stream follows the events the platform already emits (`run.queued`,
`run.started`, `run.progress`, `run.finished`, a session's tool calls) and sends each entry they
change as `activity.upsert` or, once it has ended, `activity.ended`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from ..deps import Db, Me
from ..errors import forbidden
from ..events import LIVE_PING_S, get_bus
from ..schemas.activity import ActivityKind, ActivityList
from ..services.activity import ActivityFilters, ActivityReader, activity_messages, list_activity
from ..services.agent_governance import is_member
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["runs"])

AGENT = "^[a-z0-9][a-z0-9-]{0,63}$"


def _filters(
    project: str | None,
    workflow: str | None,
    agent: str | None,
    assistant: str | None,
    kind: ActivityKind | None,
) -> ActivityFilters:
    return ActivityFilters(project=project, workflow=workflow, agent=agent, assistant=assistant, kind=kind)


async def _reader(session: Any, principal: Any, org: str, filters: ActivityFilters) -> ActivityReader:
    if not is_member(principal, org):
        raise forbidden(f"reading what is in flight in {org} requires being a member of it")
    organisation = await organisation_by_slug(session, org)
    return ActivityReader(principal=principal, org_id=organisation.id, org_slug=org, filters=filters)


@router.get("/orgs/{org}/activity", response_model=ActivityList, operation_id="listOrgActivity")
async def list_org_activity(
    org: str,
    session: Db,
    principal: Me,
    project: Annotated[str | None, Query()] = None,
    workflow: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    agent: Annotated[str | None, Query(pattern=AGENT)] = None,
    assistant: Annotated[str | None, Query(max_length=64)] = None,
    kind: Annotated[ActivityKind | None, Query()] = None,
) -> ActivityList:
    reader = await _reader(session, principal, org, _filters(project, workflow, agent, assistant, kind))
    return await list_activity(session, reader)


@router.get(
    "/orgs/{org}/activity/stream",
    operation_id="streamOrgActivity",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_org_activity(
    org: str,
    session: Db,
    principal: Me,
    project: Annotated[str | None, Query()] = None,
    workflow: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    agent: Annotated[str | None, Query(pattern=AGENT)] = None,
    assistant: Annotated[str | None, Query(max_length=64)] = None,
    kind: Annotated[ActivityKind | None, Query()] = None,
) -> Any:
    reader = await _reader(session, principal, org, _filters(project, workflow, agent, assistant, kind))
    from ..db.session import session_scope

    async def stream() -> AsyncIterator[dict[str, str]]:
        # Every event of the platform: those of another organisation are dropped by the entry's own
        # check (its project must be one of this organisation's, readable by this reader).
        with get_bus().listen("*", size=1000) as queue:
            async for message in activity_messages(reader, queue, lambda: session_scope(org)):
                yield message

    return EventSourceResponse(stream(), ping=LIVE_PING_S)
