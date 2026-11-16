# SPDX-License-Identifier: Apache-2.0
"""An organisation's sign-in security (ADR 0170): how recent an approval's authentication must be, and
an administrator ending a member's sessions. Both belong to the organisation's administrator."""

from __future__ import annotations

from choregos_contracts import Role
from fastapi import APIRouter
from sqlalchemy import func, select

from ..approval_freshness import SETTING, required_seconds
from ..audit import record
from ..browser_sessions import revoke_user_sessions
from ..db.models import Membership, User
from ..db.session import in_platform_scope
from ..deps import Db, Me
from ..errors import forbidden, not_found
from ..schemas.org_security import OrgSecurity, SessionRevocation, SessionsRevoked
from ..services.organisations import organisation_by_slug

router = APIRouter(tags=["security"])


def _require_admin(principal: Me, org: str, gesture: str) -> None:
    if principal.org_roles.get(org) != Role.ORG_ADMIN:
        raise forbidden(f"{gesture} the security of {org} takes being its org_admin")


@router.get("/orgs/{org}/security", response_model=OrgSecurity, operation_id="getOrgSecurity")
async def get_security(org: str, session: Db, principal: Me) -> OrgSecurity:
    _require_admin(principal, org, "reading")
    organisation = await organisation_by_slug(session, org)
    return OrgSecurity(approval_freshness_s=required_seconds(organisation))


@router.put("/orgs/{org}/security", response_model=OrgSecurity, operation_id="setOrgSecurity")
async def set_security(org: str, body: OrgSecurity, session: Db, principal: Me) -> OrgSecurity:
    _require_admin(principal, org, "setting")
    organisation = await organisation_by_slug(session, org)
    settings = dict(organisation.settings or {})
    if body.approval_freshness_s is None:
        settings.pop(SETTING, None)
    else:
        settings[SETTING] = body.approval_freshness_s
    organisation.settings = settings  # reassigned: a mutated JSON column is not tracked
    await record(
        session,
        principal,
        "org.security",
        org_id=organisation.id,
        target_type="org",
        target_id=organisation.id,
        **body.model_dump(),
    )
    return body


@router.post(
    "/orgs/{org}/members/{email}/sessions/revocation",
    response_model=SessionsRevoked,
    operation_id="revokeMemberSessions",
)
async def revoke_member_sessions(
    org: str, email: str, body: SessionRevocation, session: Db, principal: Me
) -> SessionsRevoked:
    """End every session of a member of this organisation, from their next request."""
    _require_admin(principal, org, "revoking sessions in")
    organisation = await organisation_by_slug(session, org)
    user = (
        (
            await session.execute(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.org_id == organisation.id, func.lower(User.email) == email.lower())
            )
        )
        .scalars()
        .first()
    )
    if user is None:
        raise not_found("Member", email)
    # Membership was checked above; the write itself is on ANOTHER person's rows, which the identity
    # RLS lets only the owner or the platform scope write (ADR 0130, 6).
    async with in_platform_scope(session):
        ended = await revoke_user_sessions(session, user)
    await record(
        session,
        principal,
        "user.sessions.revoke",
        org_id=organisation.id,
        target_type="user",
        target_id=user.id,
        reason=body.reason,
        ended=ended,
    )
    return SessionsRevoked(revoked=user.email, ended=ended)
