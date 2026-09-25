"""Un seul chemin pour un événement : la table `events` et le bus SSE."""

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
from ..events import diffuser


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
    """Persiste dans `events` **et** publie sur le bus SSE : un seul chemin pour tout."""
    row = Event(
        project_id=project_id,
        work_item_id=work_item_id,
        type=str(type_),
        subject=subject,
        payload=payload,
        ts=utcnow(),
    )
    session.add(row)
    # `source` peut venir de l'appelant (« github », « jira ») : c'est la source CloudEvents,
    # pas une donnée — l'ancien `emit()` la prenait de la même façon.
    source = str(payload.get("source") or "/choregos/api")
    donnees = {k: v for k, v in payload.items() if k != "source"}
    await diffuser(
        session,
        ChoregosEvent.emit(type_, source=source, subject=subject, project_slug=project_slug, **donnees),
    )
    return row
