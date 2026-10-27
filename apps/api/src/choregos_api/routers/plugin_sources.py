# SPDX-License-Identifier: Apache-2.0
"""The deployment's plugin sources (ADR 0093 §4, S43-03).

Sources are set by the operator (`CHOREGOS_PLUGINS_CONFIG`, `CHOREGOS_PLUGINS_OFFLINE`), never through
the API; any member of an organisation reads them, each with its trust policy and what its index
offers to this core. A source whose index cannot be read is listed with the reason.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from ..db.models import Organization
from ..deps import Db, Me
from ..errors import ApiError, forbidden, not_found
from ..rbac import Permission
from ..schemas.plugin_sources import PluginSourcesDto
from ..services.plugin_sources import SourceError, list_sources, plugins_config

router = APIRouter(tags=["agents"])


@router.get("/orgs/{org}/plugin-sources", response_model=PluginSourcesDto, operation_id="listPluginSources")
async def list_plugin_sources(org: str, session: Db, principal: Me) -> PluginSourcesDto:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"reading the plugin sources of {org} requires being a member of it")
    found = (await session.execute(select(Organization.id).where(Organization.slug == org))).first()
    if found is None:
        raise not_found("Organisation", org)
    try:
        config = plugins_config()
    except SourceError as exc:
        raise ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "The plugins configuration is invalid", exc.message
        ) from exc
    return PluginSourcesDto.model_validate({"sources": await list_sources(config)})
