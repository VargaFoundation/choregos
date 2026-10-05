# SPDX-License-Identifier: Apache-2.0
"""Ce que la console rend sans le connaître : les sections d'administration des greffons (ADR 0032).

Un greffon déclare ses sections comme des données ; la console les rend avec ses propres blocs. La
permission et la portée de chaque section se filtrent ICI, comme pour toute route : la console ne
cache pas ce que l'API servirait, elle ne reçoit pas ce qu'elle ne doit pas montrer.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ..deps import Db, Me, exiger_admin_de_plateforme
from ..errors import ApiError
from ..greffons import sections_declarees
from ..rbac import Permission

router = APIRouter(tags=["ui"])


@router.get("/ui/admin-sections", response_model=list[dict[str, Any]], operation_id="listAdminSections")
async def admin_sections(
    session: Db, principal: Me, org: Annotated[str | None, Query()] = None
) -> list[dict[str, Any]]:
    """Les sections que l'appelant peut voir ; `org` restreint celles d'organisation à celle-là."""
    visibles: list[dict[str, Any]] = []
    plateforme: bool | None = None
    for section in sections_declarees():
        if section["scope"] == "platform":
            if plateforme is None:
                plateforme = await _admin_de_plateforme(session, principal)
            if plateforme:
                visibles.append(section)
            continue
        permission = Permission(section["permission"])
        organisations = [org] if org else list(principal.org_roles)
        if any(principal.can(permission, organisation) for organisation in organisations):
            visibles.append(section)
    return visibles


async def _admin_de_plateforme(session: Any, principal: Any) -> bool:
    try:
        await exiger_admin_de_plateforme(session, principal)
    except ApiError:
        return False
    return True
