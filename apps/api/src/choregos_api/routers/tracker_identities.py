# SPDX-License-Identifier: Apache-2.0
"""Link a member to their GitHub user id (S47-14, ADR 0102).

The id is asserted by an organisation administrator: nothing here proves the member owns the account."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from ..audit import record
from ..db.models import Membership, TrackerIdentity
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission
from ..schemas import TrackerIdentityDto, TrackerIdentityPut
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["admin"])

Tracker = Literal["github"]


async def _member_of(session: Db, principal: Me, org: str, user_id: str) -> str:
    """The organisation's id, after the rights check and the check that the user is a member of it."""
    if not principal.can(Permission.MEMBER_MANAGE, org):
        raise forbidden("linking a tracker account needs the org_admin role")
    organisation = await organisation_by_slug(session, org)
    member = (
        await session.execute(
            select(Membership.id).where(
                Membership.org_id == organisation.id,
                Membership.user_id == user_id,
                Membership.project_id.is_(None),
            )
        )
    ).first()
    if member is None:
        raise not_found("Member", user_id)
    return organisation.id


@router.get(
    "/orgs/{org}/members/{user_id}/tracker-identities",
    response_model=list[TrackerIdentityDto],
    operation_id="listTrackerIdentities",
)
async def list_tracker_identities(
    org: str, user_id: str, session: Db, principal: Me
) -> list[TrackerIdentityDto]:
    org_id = await _member_of(session, principal, org, user_id)
    rows = (
        (
            await session.execute(
                select(TrackerIdentity)
                .where(TrackerIdentity.org_id == org_id, TrackerIdentity.user_id == user_id)
                .order_by(TrackerIdentity.tracker)
            )
        )
        .scalars()
        .all()
    )
    return [TrackerIdentityDto.model_validate(row) for row in rows]


@router.put(
    "/orgs/{org}/members/{user_id}/tracker-identities/{tracker}",
    response_model=TrackerIdentityDto,
    operation_id="setTrackerIdentity",
)
async def set_tracker_identity(
    org: str, user_id: str, tracker: Tracker, body: TrackerIdentityPut, session: Db, principal: Me
) -> TrackerIdentityDto:
    org_id = await _member_of(session, principal, org, user_id)
    holder = (
        await session.execute(
            select(TrackerIdentity).where(
                TrackerIdentity.org_id == org_id,
                TrackerIdentity.tracker == tracker,
                TrackerIdentity.external_id == body.external_id,
            )
        )
    ).scalar_one_or_none()
    if holder is not None and holder.user_id != user_id:
        raise conflict(f"this {tracker} user id is already linked to another member")
    row = (
        await session.execute(
            select(TrackerIdentity).where(
                TrackerIdentity.org_id == org_id,
                TrackerIdentity.user_id == user_id,
                TrackerIdentity.tracker == tracker,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TrackerIdentity(org_id=org_id, user_id=user_id, tracker=tracker)
        session.add(row)
    row.external_id = body.external_id
    row.login = body.login
    row.created_by = principal.email
    await session.flush()
    await session.refresh(row)
    await record(
        session,
        principal,
        "tracker_identity.set",
        org_id=org_id,
        target_type="user",
        target_id=user_id,
        tracker=tracker,
        external_id=body.external_id,
        login=body.login,
    )
    return TrackerIdentityDto.model_validate(row)


@router.delete(
    "/orgs/{org}/members/{user_id}/tracker-identities/{tracker}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteTrackerIdentity",
)
async def delete_tracker_identity(
    org: str, user_id: str, tracker: Tracker, session: Db, principal: Me
) -> Response:
    org_id = await _member_of(session, principal, org, user_id)
    row = (
        await session.execute(
            select(TrackerIdentity).where(
                TrackerIdentity.org_id == org_id,
                TrackerIdentity.user_id == user_id,
                TrackerIdentity.tracker == tracker,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Tracker identity", tracker)
    external_id = row.external_id
    await session.delete(row)
    await record(
        session,
        principal,
        "tracker_identity.delete",
        org_id=org_id,
        target_type="user",
        target_id=user_id,
        tracker=tracker,
        external_id=external_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
