"""Journal d'audit : toute mutation laisse une trace, et cette trace appartient à une organisation.

`org_id` est un argument **obligatoire**. C'est voulu : jusqu'au 2026-09-26, `audit_log` n'avait
aucune colonne d'organisation, n'était pas sous RLS, et `GET /audit` ne filtrait rien — un
`project_owner` d'une organisation lisait l'audit de toutes les autres. Rendre l'argument
obligatoire fait de `mypy --strict` la garde : un nouvel appel qui oublie l'organisation ne compile
pas. `None` reste licite, mais il faut l'écrire, et il signifie « événement de plateforme, visible
d'une session de portée `*` seulement ».
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
