# SPDX-License-Identifier: Apache-2.0
"""An organisation's groups (ADR 0152): read by every member, written by the administrator.

A group is `internal` (created and edited here) or `scim` (pushed by an identity provider through
`services.org_groups.upsert_external_group`, which no route of the core serves): every write on a
`scim` group answers 409 `group_read_only`. Each write is audited (`group.*`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from ..audit import record
from ..deps import Db, Me
from ..schemas.org_groups import (
    GROUP_SLUG_PATTERN,
    OrgGroupCreate,
    OrgGroupDto,
    OrgGroupList,
    OrgGroupMemberAdd,
    OrgGroupMemberDto,
    OrgGroupMemberList,
    OrgGroupUpdate,
)
from ..services import org_groups as service
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["groups"])

Slug = Annotated[str, Path(pattern=GROUP_SLUG_PATTERN)]


@router.get("/orgs/{org}/groups", response_model=OrgGroupList, operation_id="listOrgGroups")
async def list_groups(org: str, session: Db, principal: Me) -> OrgGroupList:
    service.require_read(principal, org)
    organisation = await organisation_by_slug(session, org)
    return OrgGroupList(items=await service.list_groups(session, organisation))


@router.post(
    "/orgs/{org}/groups",
    response_model=OrgGroupDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOrgGroup",
)
async def create_group(org: str, body: OrgGroupCreate, session: Db, principal: Me) -> OrgGroupDto:
    service.require_manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.create_group(session, organisation, body, principal)
    await record(
        session,
        principal,
        "group.create",
        org_id=organisation.id,
        target_type="group",
        target_id=group.id,
        group=group.slug,
    )
    return await service.view(session, group)


@router.get("/orgs/{org}/groups/{slug}", response_model=OrgGroupDto, operation_id="getOrgGroup")
async def get_group(org: str, slug: Slug, session: Db, principal: Me) -> OrgGroupDto:
    service.require_read(principal, org)
    organisation = await organisation_by_slug(session, org)
    return await service.view(session, await service.group_by_slug(session, organisation, slug))


@router.patch("/orgs/{org}/groups/{slug}", response_model=OrgGroupDto, operation_id="updateOrgGroup")
async def update_group(org: str, slug: Slug, body: OrgGroupUpdate, session: Db, principal: Me) -> OrgGroupDto:
    service.require_manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.group_by_slug(session, organisation, slug)
    changed = await service.update_group(session, group, body)
    if changed:
        await record(
            session,
            principal,
            "group.update",
            org_id=organisation.id,
            target_type="group",
            target_id=group.id,
            group=group.slug,
            fields=changed,
        )
    return await service.view(session, group)


@router.delete(
    "/orgs/{org}/groups/{slug}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteOrgGroup"
)
async def delete_group(org: str, slug: Slug, session: Db, principal: Me) -> Response:
    """Removes an `internal` group and its members' rows. A workflow that names it warns again
    (`actor.group_unknown`) unless the identity provider still sends the group."""
    service.require_manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.group_by_slug(session, organisation, slug)
    group_id = group.id
    await service.delete_group(session, group)
    await record(
        session,
        principal,
        "group.delete",
        org_id=organisation.id,
        target_type="group",
        target_id=group_id,
        group=slug,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/orgs/{org}/groups/{slug}/members", response_model=OrgGroupMemberList, operation_id="listOrgGroupMembers"
)
async def list_members(org: str, slug: Slug, session: Db, principal: Me) -> OrgGroupMemberList:
    service.require_read(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.group_by_slug(session, organisation, slug)
    return OrgGroupMemberList(items=await service.list_members(session, group))


@router.post(
    "/orgs/{org}/groups/{slug}/members",
    response_model=OrgGroupMemberDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="addOrgGroupMember",
)
async def add_member(
    org: str, slug: Slug, body: OrgGroupMemberAdd, session: Db, principal: Me
) -> OrgGroupMemberDto:
    service.require_manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.group_by_slug(session, organisation, slug)
    member, added = await service.add_member(session, organisation, group, body, principal)
    if added:
        await record(
            session,
            principal,
            "group.member.add",
            org_id=organisation.id,
            target_type="group",
            target_id=group.id,
            group=group.slug,
            user_id=member.user_id,
        )
    return member


@router.delete(
    "/orgs/{org}/groups/{slug}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeOrgGroupMember",
)
async def remove_member(org: str, slug: Slug, user_id: str, session: Db, principal: Me) -> Response:
    service.require_manage(principal, org)
    organisation = await organisation_by_slug(session, org)
    group = await service.group_by_slug(session, organisation, slug)
    await service.remove_member(session, group, user_id)
    await record(
        session,
        principal,
        "group.member.remove",
        org_id=organisation.id,
        target_type="group",
        target_id=group.id,
        group=group.slug,
        user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
