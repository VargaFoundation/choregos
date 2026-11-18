# SPDX-License-Identifier: Apache-2.0
"""How an API token is shown, shared by the sign-in and agent routers (ADR 0099)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiToken, Organization, Project
from ..schemas import ApiTokenDto


def api_token_dto(row: ApiToken, project: str | None = None) -> ApiTokenDto:
    """A token as the API shows it; `project` is the `org:slug` of the project it is bound to."""
    return ApiTokenDto(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        scopes=list(row.scopes or ["*"]),
        project=project,
        last_client=row.last_client,
    )


async def linked_projects(session: AsyncSession, ids: set[str]) -> dict[str, str]:
    """The projects that tokens are bound to, as `{id: "org:slug"}`."""
    if not ids:
        return {}
    rows = await session.execute(
        select(Project.id, Organization.slug, Project.slug)
        .join(Organization, Project.org_id == Organization.id)
        .where(Project.id.in_(ids))
    )
    return {pid: f"{org}:{slug}" for pid, org, slug in rows}
