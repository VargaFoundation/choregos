# SPDX-License-Identifier: Apache-2.0
"""The webhook events a project received (S42-06f): what each was, and what the platform did with it."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ..db.models import ReceivedEvent
from ..deps import Db, Pagination, ProjectCtx
from ..keyset import keyset_page
from ..schemas import ReceivedEventDto, ReceivedEventPage

router = APIRouter(tags=["work-items"])


@router.get(
    "/projects/{id}/received-events", response_model=ReceivedEventPage, operation_id="listReceivedEvents"
)
async def list_received_events(ctx: ProjectCtx, session: Db, page: Pagination) -> ReceivedEventPage:
    """Newest first; any member of the project reads them."""
    window, meta = await keyset_page(
        session,
        select(ReceivedEvent).where(ReceivedEvent.project_id == ctx.id),
        sort=ReceivedEvent.received_at,
        tiebreak=ReceivedEvent.id,
        page=page,
    )
    return ReceivedEventPage(items=[ReceivedEventDto.model_validate(row) for row in window], meta=meta)
