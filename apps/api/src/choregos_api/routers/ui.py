# SPDX-License-Identifier: Apache-2.0
"""What the console renders without knowing it: the administration sections of extensions (ADR 0032).

An extension declares its sections as data; the console renders them with its own blocks. The
permission and the scope of each section are filtered HERE, as for any route: the console does not
hide what the API would serve, it does not receive what it must not show.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ..deps import Db, Me, require_platform_admin
from ..errors import ApiError
from ..extensions import declared_sections
from ..rbac import Permission

router = APIRouter(tags=["ui"])


@router.get("/ui/admin-sections", response_model=list[dict[str, Any]], operation_id="listAdminSections")
async def admin_sections(
    session: Db, principal: Me, org: Annotated[str | None, Query()] = None
) -> list[dict[str, Any]]:
    """The sections the caller may see; `org` restricts organisation sections to that one."""
    visible: list[dict[str, Any]] = []
    platform: bool | None = None

    async def holds(permission: str) -> bool:
        """Whether the caller holds `permission`: on the instance for `platform:admin`, in any
        organisation in scope otherwise."""
        nonlocal platform
        if permission == Permission.PLATFORM_ADMIN.value:
            if platform is None:
                platform = await _is_platform_admin(session, principal)
            return platform
        organisations = [org] if org else list(principal.org_roles)
        return any(principal.can(Permission(permission), organisation) for organisation in organisations)

    for section in declared_sections():
        # `platform:admin` is a right on the INSTANCE, whatever the scope of the section: an
        # `org_admin` holds every permission in THEIR organisation, that one included, and that is
        # not administering the platform as soon as there are two organisations.
        if section["scope"] == "platform":
            if not await holds(Permission.PLATFORM_ADMIN.value):
                continue
        elif not await holds(section["permission"]):
            continue
        # A block may carry its own permission (S42-02): the section's permission still applies, then
        # each block the caller lacks is left out, and a section with no block left is not listed.
        blocks = [b for b in section["blocks"] if "permission" not in b or await holds(b["permission"])]
        if blocks:
            visible.append({**section, "blocks": blocks})
    return visible


async def _is_platform_admin(session: Any, principal: Any) -> bool:
    try:
        await require_platform_admin(session, principal)
    except ApiError:
        return False
    return True
