# SPDX-License-Identifier: Apache-2.0
"""The webhook events a project received (S42-06f): what each was, and what the platform did with it."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ..db.models import ReceivedEvent
from ..deps import Db, Pagination, ProjectCtx
from ..schemas import PageMeta, ReceivedEventDto, ReceivedEventPage

router = APIRouter(tags=["work-items"])

#: How far back the list reads: the purge keeps 90 days (`docs/runbooks/purge-runs.md`).
MAX_ROWS = 2000


@router.get(
    "/projects/{id}/received-events", response_model=ReceivedEventPage, operation_id="listReceivedEvents"
)
async def list_received_events(ctx: ProjectCtx, session: Db, page: Pagination) -> ReceivedEventPage:
    """Newest first; any member of the project reads them."""
    rows = (
        (
            await session.execute(
                select(ReceivedEvent)
                .where(ReceivedEvent.project_id == ctx.id)
                .order_by(ReceivedEvent.received_at.desc(), ReceivedEvent.id.desc())
                .limit(MAX_ROWS)
            )
        )
        .scalars()
        .all()
    )
    window, cursor, has_more = page.slice(list(rows))
    return ReceivedEventPage(
        items=[ReceivedEventDto.model_validate(row) for row in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )
