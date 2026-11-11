# SPDX-License-Identifier: Apache-2.0
"""The assistants an installed plugin ships (S63-18): what the owners' installation form is built from."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from ..deps import Db, Me
from ..errors import forbidden, not_found
from ..schemas.assistant_catalogue import PluginAssistantList
from ..schemas.assistants import SLUG
from ..services import assistant_catalogue as service
from ..services.agent_governance import is_member
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["agents"])


@router.get(
    "/orgs/{org}/plugins/{name}/assistants",
    response_model=PluginAssistantList,
    operation_id="listPluginAssistants",
)
async def list_plugin_assistants(
    org: str, name: Annotated[str, Path(pattern=SLUG)], session: Db, principal: Me
) -> PluginAssistantList:
    if not is_member(principal, org):
        raise forbidden(f"the plugins of {org} are visible to its members")
    organisation = await organisation_by_slug(session, org)
    found = await service.plugin_assistants(session, organisation.id, name)
    if found is None:
        raise not_found("Plugin", name)
    return PluginAssistantList(items=found)
