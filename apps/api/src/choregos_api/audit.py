# SPDX-License-Identifier: Apache-2.0
"""Audit log: every mutation leaves a trace, and that trace belongs to an organisation.

`org_id` is a **mandatory** argument. That is deliberate: until 2026-09-26, `audit_log` had no
organisation column, was not under RLS, and `GET /audit` filtered nothing, so a `project_owner` of
one organisation read the audit of all the others. Making the argument mandatory turns
`mypy --strict` into the guard: a new call that forgets the organisation does not compile. `None`
remains lawful, but it has to be written, and it means "platform event, visible only to a session
of scope `*`".
"""

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
    org_id: str | None,
    target_type: str | None = None,
    target_id: str | None = None,
    **payload: Any,
) -> AuditLog:
    entry = AuditLog(
        actor_id=principal.user_id if principal else None,
        actor_kind=principal.kind if principal else "system",
        org_id=org_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        ts=utcnow(),
    )
    session.add(entry)
    return entry
