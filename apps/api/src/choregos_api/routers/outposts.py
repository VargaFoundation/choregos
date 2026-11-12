# SPDX-License-Identifier: Apache-2.0
"""An organisation's outposts (ADR 0147): places a person enrols, which only dial out.

Every route is `outpost:manage`, the organisation administrator's. They keep the platform's records:
an outpost, its instances, its enrolment tokens, the calls routed to it as digests. They are not the
door an outpost dials (`/outposts/v1`, ADR 0148), and nothing here connects to an outpost: the
platform stores no address of one. Creating an outpost or an enrolment token is refused while the
deployment keeps outposts switched off (`CHOREGOS_OUTPOSTS`); everything else still answers, so that
what exists can be read, revoked and removed.
"""

from __future__ import annotations

from typing import Annotated

from choregos_contracts import OutpostCallState
from choregos_contracts.outpost import OUTPOST_ID_PATTERN, OUTPOST_SLUG_PATTERN
from fastapi import APIRouter, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization
from ..deps import Db, Me, Pagination
from ..freshness import authentication_age
from ..rbac import Principal
from ..schemas.base import PageMeta
from ..schemas.outposts import (
    OutpostCallPage,
    OutpostCreate,
    OutpostDto,
    OutpostEnrolmentCreate,
    OutpostEnrolmentCreated,
    OutpostInstanceApprove,
    OutpostInstanceDto,
    OutpostInstanceList,
    OutpostList,
)
from ..services import outposts as service
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["outposts"])

Slug = Annotated[str, Path(pattern=OUTPOST_SLUG_PATTERN)]
InstanceId = Annotated[str, Path(pattern=OUTPOST_ID_PATTERN)]


async def _managed(session: AsyncSession, principal: Principal, org: str) -> Organization:
    """The organisation, for a caller who holds `outpost:manage` in it. The right is checked first:
    row-level security hides an organisation the caller does not belong to."""
    service.require_manage(principal, org)
    return await organisation_by_slug(session, org)


@router.get("/orgs/{org}/outposts", response_model=OutpostList, operation_id="listOutposts")
async def list_outposts(org: str, session: Db, principal: Me) -> OutpostList:
    organisation = await _managed(session, principal, org)
    return OutpostList(items=await service.list_outposts(session, organisation))


@router.post(
    "/orgs/{org}/outposts",
    response_model=OutpostDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOutpost",
)
async def create_outpost(org: str, body: OutpostCreate, session: Db, principal: Me) -> OutpostDto:
    """An outpost with no instance yet: an instance comes from an enrolment token and a person's
    approval (ADR 0147 §4)."""
    organisation = await _managed(session, principal, org)
    outpost = await service.create_outpost(session, organisation, body, principal)
    await record(
        session,
        principal,
        "outpost.create",
        org_id=organisation.id,
        target_type="outpost",
        target_id=outpost.id,
        outpost=outpost.slug,
        capabilities=list(outpost.capabilities),
    )
    return await service.view(session, outpost)


@router.get("/orgs/{org}/outposts/{slug}", response_model=OutpostDto, operation_id="getOutpost")
async def get_outpost(org: str, slug: Slug, session: Db, principal: Me) -> OutpostDto:
    organisation = await _managed(session, principal, org)
    return await service.view(session, await service.outpost_by_slug(session, organisation, slug))


@router.delete(
    "/orgs/{org}/outposts/{slug}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteOutpost"
)
async def delete_outpost(org: str, slug: Slug, session: Db, principal: Me) -> Response:
    """Removes a revoked outpost that no connector routes to any more."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    outpost_id, epoch = outpost.id, outpost.epoch
    await service.delete_outpost(session, outpost)
    await record(
        session,
        principal,
        "outpost.delete",
        org_id=organisation.id,
        target_type="outpost",
        target_id=outpost_id,
        outpost=slug,
        epoch=epoch,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/orgs/{org}/outposts/{slug}/pause", response_model=OutpostDto, operation_id="pauseOutpost")
async def pause_outpost(org: str, slug: Slug, session: Db, principal: Me) -> OutpostDto:
    """Nothing more is handed to it; what its instances hold may finish (ADR 0148 §10)."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    if await service.pause_outpost(session, outpost):
        await record(
            session,
            principal,
            "outpost.pause",
            org_id=organisation.id,
            target_type="outpost",
            target_id=outpost.id,
            outpost=slug,
        )
    return await service.view(session, outpost)


@router.post("/orgs/{org}/outposts/{slug}/resume", response_model=OutpostDto, operation_id="resumeOutpost")
async def resume_outpost(org: str, slug: Slug, session: Db, principal: Me) -> OutpostDto:
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    if await service.resume_outpost(session, outpost):
        await record(
            session,
            principal,
            "outpost.resume",
            org_id=organisation.id,
            target_type="outpost",
            target_id=outpost.id,
            outpost=slug,
        )
    return await service.view(session, outpost)


@router.post("/orgs/{org}/outposts/{slug}/revoke", response_model=OutpostDto, operation_id="revokeOutpost")
async def revoke_outpost(org: str, slug: Slug, session: Db, principal: Me) -> OutpostDto:
    """Revokes the outpost and every instance of it, and moves its epoch (ADR 0147 §5). Final."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    if await service.revoke_outpost(session, outpost, principal):
        await record(
            session,
            principal,
            "outpost.revoke",
            org_id=organisation.id,
            target_type="outpost",
            target_id=outpost.id,
            outpost=slug,
            epoch=outpost.epoch,
        )
    return await service.view(session, outpost)


@router.post(
    "/orgs/{org}/outposts/{slug}/enrolments",
    response_model=OutpostEnrolmentCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOutpostEnrolment",
)
async def create_outpost_enrolment(
    org: str, slug: Slug, session: Db, principal: Me, body: OutpostEnrolmentCreate | None = None
) -> OutpostEnrolmentCreated:
    """An enrolment token for one instance, shown once, with the door's origin and keys to pin."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    created = await service.create_enrolment(session, outpost, body or OutpostEnrolmentCreate(), principal)
    # Who issued a token, for which outpost, until when: never the token.
    await record(
        session,
        principal,
        "outpost.enrol",
        org_id=organisation.id,
        target_type="outpost_enrolment",
        target_id=created.id,
        outpost=slug,
        expires_at=created.expires_at.isoformat(),
    )
    return created


@router.get(
    "/orgs/{org}/outposts/{slug}/instances",
    response_model=OutpostInstanceList,
    operation_id="listOutpostInstances",
)
async def list_outpost_instances(org: str, slug: Slug, session: Db, principal: Me) -> OutpostInstanceList:
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    return OutpostInstanceList(items=await service.list_instances(session, outpost))


@router.post(
    "/orgs/{org}/outposts/{slug}/instances/{instance_id}/approve",
    response_model=OutpostInstanceDto,
    operation_id="approveOutpostInstance",
)
async def approve_outpost_instance(
    org: str, slug: Slug, instance_id: InstanceId, body: OutpostInstanceApprove, session: Db, principal: Me
) -> OutpostInstanceDto:
    """A person approves a pending instance against the fingerprint the daemon printed, in a session
    that carries a recent authentication: a token cannot approve (ADR 0147 §4)."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    approved = await service.approve_instance(session, outpost, instance_id, body.fingerprint, principal)
    await record(
        session,
        principal,
        "outpost.approve",
        org_id=organisation.id,
        target_type="outpost_instance",
        target_id=instance_id,
        outpost=slug,
        fingerprint=approved.fingerprint,
        auth_age_seconds=authentication_age(principal),
    )
    return approved


@router.post(
    "/orgs/{org}/outposts/{slug}/instances/{instance_id}/revoke",
    response_model=OutpostInstanceDto,
    operation_id="revokeOutpostInstance",
)
async def revoke_outpost_instance(
    org: str, slug: Slug, instance_id: InstanceId, session: Db, principal: Me
) -> OutpostInstanceDto:
    """Revokes one instance and moves the outpost's epoch (ADR 0147 §5). Final."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    revoked, changed = await service.revoke_instance(session, outpost, instance_id, principal)
    if changed:
        await record(
            session,
            principal,
            "outpost.revoke",
            org_id=organisation.id,
            target_type="outpost_instance",
            target_id=instance_id,
            outpost=slug,
            fingerprint=revoked.fingerprint,
            epoch=outpost.epoch,
        )
    return revoked


@router.get(
    "/orgs/{org}/outposts/{slug}/calls", response_model=OutpostCallPage, operation_id="listOutpostCalls"
)
async def list_outpost_calls(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    page: Pagination,
    state: Annotated[OutpostCallState | None, Query()] = None,
) -> OutpostCallPage:
    """The recent calls routed to this outpost, newest first: digests, no arguments and no result."""
    organisation = await _managed(session, principal, org)
    outpost = await service.outpost_by_slug(session, organisation, slug)
    window, cursor, has_more = page.slice(await service.list_calls(session, outpost, state))
    return OutpostCallPage(
        items=await service.call_records(session, window),
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )
