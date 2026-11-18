# SPDX-License-Identifier: Apache-2.0
"""Assistant installations of an organisation (ADR 0133, S63-02): install, configure, bind, readiness, and
stop and resume (S64-09c, ADR 0142 §4 bis).

Installing, uninstalling and changing the owners are the organisation administrator's; configuring and
binding, stopping and resuming are the administrator's and the owner groups' (`assistant:manage`, decided by
`services.assistant_access`); binding a launch also needs the right to create work items in its project
(ADR 0158). The manifest is read from the installed plugin, never sent by the client.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from ..deps import Db, Me
from ..errors import forbidden
from ..schemas.assistants import (
    NEED,
    SLUG,
    Assistant,
    AssistantBind,
    AssistantInstall,
    AssistantKnowledgeBind,
    AssistantLaunchBind,
    AssistantList,
    AssistantReadiness,
    AssistantStopResult,
    AssistantUpdate,
)
from ..schemas.tickets import StopRequest
from ..services import assistants as service
from ..services.agent_governance import is_member
from ..services.assistant_access import may_manage_groups
from ..services.assistant_stop import resume_assistant as resume_the_assistant
from ..services.assistant_stop import stop_assistant as stop_the_assistant
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=SLUG)]
Need = Annotated[str, Path(pattern=NEED)]


@router.get("/orgs/{org}/assistants", response_model=AssistantList, operation_id="listAssistants")
async def list_assistants(org: str, session: Db, principal: Me) -> AssistantList:
    if not is_member(principal, org):
        raise forbidden(f"the assistants of {org} are visible to its members")
    organisation = await organisation_by_slug(session, org)
    rows = [
        r
        for r in await service.installations(session, organisation.id)
        if may_manage_groups(principal, org, list(r.owner_groups))
    ]
    return AssistantList(items=[await service.to_dto(session, r) for r in rows])


@router.post(
    "/orgs/{org}/assistants",
    response_model=Assistant,
    status_code=status.HTTP_201_CREATED,
    operation_id="installAssistant",
)
async def install_assistant(org: str, body: AssistantInstall, session: Db, principal: Me) -> Assistant:
    service.require_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    row = await service.install(session, organisation.id, org, body, principal)
    return await service.to_dto(session, row)


@router.get("/orgs/{org}/assistants/{slug}", response_model=Assistant, operation_id="getAssistant")
async def get_assistant(org: str, slug: Slug, session: Db, principal: Me) -> Assistant:
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    return await service.to_dto(session, row)


@router.patch("/orgs/{org}/assistants/{slug}", response_model=Assistant, operation_id="updateAssistant")
async def update_assistant(
    org: str, slug: Slug, body: AssistantUpdate, session: Db, principal: Me
) -> Assistant:
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    row = await service.update(session, organisation.id, org, row, body, principal)
    return await service.to_dto(session, row)


@router.delete(
    "/orgs/{org}/assistants/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="uninstallAssistant",
)
async def uninstall_assistant(org: str, slug: Slug, session: Db, principal: Me) -> Response:
    service.require_admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    await service.uninstall(session, organisation.id, row, principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/orgs/{org}/assistants/{slug}/readiness",
    response_model=AssistantReadiness,
    operation_id="getAssistantReadiness",
)
async def assistant_readiness(org: str, slug: Slug, session: Db, principal: Me) -> AssistantReadiness:
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    return await service.readiness(session, row)


@router.put(
    "/orgs/{org}/assistants/{slug}/bindings/{need}",
    response_model=Assistant,
    operation_id="bindAssistantNeed",
)
async def bind_need(
    org: str, slug: Slug, need: Need, body: AssistantBind, session: Db, principal: Me
) -> Assistant:
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    row = await service.bind(session, organisation.id, org, row, need, body, principal)
    return await service.to_dto(session, row)


@router.post(
    "/orgs/{org}/assistants/{slug}/stop", response_model=AssistantStopResult, operation_id="stopAssistant"
)
async def stop_assistant(
    org: str, slug: Slug, body: StopRequest, session: Db, principal: Me
) -> AssistantStopResult:
    """Mark the assistant stopped, then cancel the turns its conversations are taking, on any replica
    (ADR 0142 §4 bis). The conversations stay open to their people."""
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    cancelled = await stop_the_assistant(session, principal, row, body.reason)
    return AssistantStopResult(slug=row.slug, turns_cancelled=len(cancelled))


@router.post("/orgs/{org}/assistants/{slug}/resume", response_model=Assistant, operation_id="resumeAssistant")
async def resume_assistant(org: str, slug: Slug, session: Db, principal: Me) -> Assistant:
    """Clear the stop: the status is derived again, and nothing stopped restarts on its own."""
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    service.require_manager(principal, org, row)
    await resume_the_assistant(session, principal, row)
    return await service.to_dto(session, row)


@router.put(
    "/orgs/{org}/assistants/{slug}/launches/{launch}",
    response_model=Assistant,
    operation_id="bindAssistantLaunch",
)
async def bind_launch(
    org: str, slug: Slug, launch: Need, body: AssistantLaunchBind, session: Db, principal: Me
) -> Assistant:
    """Binds a launch to a project's workflow (ADR 0158): `assistant:manage` on the installation and
    `item:control` in the project."""
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    row = await service.bind_launch(session, organisation.id, org, row, launch, body, principal)
    return await service.to_dto(session, row)


@router.put(
    "/orgs/{org}/assistants/{slug}/knowledge-bindings/{name}",
    response_model=Assistant,
    operation_id="bindAssistantKnowledge",
)
async def bind_knowledge(
    org: str, slug: Slug, name: Need, body: AssistantKnowledgeBind, session: Db, principal: Me
) -> Assistant:
    """A knowledge need bound to existing bases of the organisation, by reference (ADR 0159)."""
    organisation = await organisation_by_slug(session, org)
    row = await service.installation(session, organisation.id, slug)
    row = await service.bind_knowledge(session, org, row, name, body, principal)
    return await service.to_dto(session, row)
