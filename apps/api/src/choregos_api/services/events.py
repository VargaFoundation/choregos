# SPDX-License-Identifier: Apache-2.0
"""One path for an event: the `events` table and the SSE bus."""

from __future__ import annotations

from typing import Any

from choregos_contracts import ChoregosEvent, EventType
from choregos_core import (
    utcnow,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Event,
)
from ..events import broadcast


async def persist_event(
    session: AsyncSession,
    type_: EventType,
    *,
    project_id: str | None = None,
    work_item_id: str | None = None,
    subject: str | None = None,
    project_slug: str | None = None,
    **payload: Any,
) -> Event:
    """Persist into `events` **and** publish on the SSE bus: one path for everything."""
    row = Event(
        project_id=project_id,
        work_item_id=work_item_id,
        type=str(type_),
        subject=subject,
        payload=payload,
        ts=utcnow(),
    )
    session.add(row)
    # `source` may come from the caller ("github", "jira"): it is the CloudEvents source,
    # not data, and the former `emit()` took it the same way.
    source = str(payload.get("source") or "/choregos/api")
    data = {k: v for k, v in payload.items() if k != "source"}
    await broadcast(
        session,
        ChoregosEvent.emit(type_, source=source, subject=subject, project_slug=project_slug, **data),
    )
    return row
