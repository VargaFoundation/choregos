# SPDX-License-Identifier: Apache-2.0
"""The one case where a second approval may come from the requester (ADR 0050, ADR 0093 §4).

A high-risk skill and a plugin that brings a server wait for "a second person" (S32-06, S43-05). An
organisation with ONE administrator has no second person: the request could never be approved, and the
product could not be tried. The requester may then approve their own request, and the approval says so
(`sole_admin=True` in the audit event).

Who counts: the human members of the organisation holding the `org_admin` role at organisation scope
(the rule `egress.approvers` and the sign-in use), service accounts excluded. A platform administrator
who is not a member of the organisation with that role is NOT a second person: they cannot approve in it
at all (the rights are read from the membership), so they do not make the organisation "have two".
"""

from __future__ import annotations

from choregos_contracts import Role
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Membership, User
from ..rbac import Principal


async def is_sole_admin(session: AsyncSession, org_id: str, principal: Principal) -> bool:
    """True when `principal` is the only administrator of the organisation."""
    emails = (
        (
            await session.execute(
                select(func.lower(User.email))
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.org_id == org_id,
                    Membership.project_id.is_(None),
                    Membership.role == Role.ORG_ADMIN.value,
                    User.is_service.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(emails) == 1 and emails[0] == principal.email.lower()
