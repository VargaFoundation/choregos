"""Journal d'audit : toute mutation laisse une trace."""

from __future__ import annotations

from typing import Any

from choregos_core import utcnow
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditLog
from .rbac import Principal


async def record(
    session: AsyncSession,
    principal: Principal | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    **payload: Any,
) -> AuditLog:
    entry = AuditLog(
        actor_id=principal.user_id if principal else None,
        actor_kind=principal.kind if principal else "system",
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        ts=utcnow(),
    )
    session.add(entry)
    return entry
