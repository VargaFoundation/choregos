# SPDX-License-Identifier: Apache-2.0
"""A member's profile page (S66-42, ADR 0162): who they are, what they may do, what waits on them.

One read, shaped by who reads it:

- **any member of the organisation** (`member:read` in it) reads the basic card of another member: names,
  job title, department, e-mail, photo, status, organisation and project roles, groups. The members list
  already tells them the address and the roles; the card adds what the identity provider says of the person;
- **an administrator** (`member:manage` in the organisation) **and the person themself** read the full
  profile: how the account came, the last sign-in, the open sessions, and their work in the organisation's
  team projects the caller reads (a personal space is never shown, ADR 0134);
- **an administrator only** reads the `administration` section (the API tokens count, never a token; the
  organisation's recent audit events where the person is the actor or the target), and the profile of a
  former member the organisation's audit log knows.

Anyone outside the organisation gets 403 before any lookup (as `GET /orgs/{org}/members`); a person who is
not a member, or not any more for a non-administrator, is 404.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final

from choregos_contracts import Role
from choregos_core import utcnow
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    ApiToken,
    AuditLog,
    CostLedger,
    HumanRequest,
    Membership,
    Organization,
    OrgGroup,
    OrgGroupMember,
    Project,
    Run,
    User,
    UserSession,
    WorkItem,
)
from ..db.session import in_platform_scope, session_scope
from ..errors import forbidden, not_found
from ..rbac import Permission, Principal
from ..schemas.member_profiles import (
    AccountOrigin,
    MemberAdministration,
    MemberDecision,
    MemberGroup,
    MemberPendingRequest,
    MemberProfileDto,
    MemberProjectRole,
    MemberRun,
    MemberSessions,
    MemberWork,
)
from ..schemas.platform import AuditEntry
from .avatars import avatar_url
from .budgets import start_of_month
from .human_assignee import StepAssignee, assignee_of_the_request, sla_passed
from .human_groups import groups_for_org
from .org_groups import SCIM, table_groups_by_user
from .organisations import organisation_by_slug
from .personal_spaces import STANDARD

#: How many of each list the page shows: the most recent first.
RECENT: Final = 10
AUDIT_RECENT: Final = 20
#: How many waiting requests of the organisation are examined for the person: the oldest beyond are not.
PENDING_SCAN: Final = 500
#: The prefix of an identity provider's group namespaced for an organisation (`choregos:<org>:<group>`).
_PREFIX = "choregos:"


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive UTC datetimes."""
    return moment.replace(tzinfo=UTC) if moment is not None and moment.tzinfo is None else moment


def is_administrator(principal: Principal, org: str) -> bool:
    return principal.can(Permission.MEMBER_MANAGE, org)


def _member_of(principal: Principal, org: str) -> bool:
    return org in principal.org_roles or any(key.startswith(f"{org}/") for key in principal.project_roles)


async def member_profile(
    session: AsyncSession, principal: Principal, org: str, user_id: str
) -> MemberProfileDto:
    own = principal.user_id == user_id and _member_of(principal, org)
    if not (own or principal.can(Permission.MEMBER_READ, org)):
        raise forbidden(f"reading a member of {org} requires being a member of it")
    organisation = await organisation_by_slug(session, org)
    admin = is_administrator(principal, org)
    full = admin or own
    memberships = (
        await session.execute(
            select(Membership, Project)
            .outerjoin(Project, Membership.project_id == Project.id)
            .where(Membership.org_id == organisation.id, Membership.user_id == user_id)
        )
    ).all()
    status = "active"
    if memberships:
        user = await session.get(User, user_id)
    elif admin and await _known_to_the_audit(session, organisation.id, user_id):
        status = "former"
        # Under the RLS of `users`, a person with no membership in the caller's organisations is invisible:
        # the audit log proved they were here, so the row is read for the whole instance, for them alone.
        async with in_platform_scope(session):
            user = await session.get(User, user_id)
    else:
        user = None
    if user is None or user.is_service:
        raise not_found("Member", user_id)

    org_role = next((Role(m.role) for m, project in memberships if project is None), None)
    profile = MemberProfileDto(
        user_id=user.id,
        org=organisation.slug,
        view="full" if full else "basic",
        status=status,
        display_name=user.display_name,
        given_name=user.given_name,
        family_name=user.family_name,
        job_title=user.job_title,
        department=user.department,
        email=user.email,
        avatar_url=await avatar_url(session, user.id) if status == "active" else None,
        org_role=org_role,
        project_roles=sorted(
            (
                MemberProjectRole(project_slug=project.slug, project_name=project.name, role=Role(m.role))
                for m, project in memberships
                if project is not None and project.kind == STANDARD
            ),
            key=lambda row: row.project_slug,
        ),
        groups=await _groups(session, organisation, user) if status == "active" else [],
    )
    if full and status == "active":
        profile.origin = await _origin(session, organisation.id, user, memberships)
        profile.last_login_at = user.last_login_at
        profile.sessions = await _sessions(session, user.id)
    if full:
        # A former member's account and sessions are not the organisation's any more; their work in it is.
        profile.work = await _work(session, principal, organisation, user, status == "active")
    if admin:
        profile.administration = await _administration(session, organisation.id, user.id)
    return profile


def _names_the_person(user_id: str) -> ColumnElement[bool]:
    """An audit row where the person is the actor or the target."""
    target = and_(AuditLog.target_type == "user", AuditLog.target_id == user_id)
    return or_(AuditLog.actor_id == user_id, target)


async def _known_to_the_audit(session: AsyncSession, org_id: str, user_id: str) -> bool:
    """Whether the organisation's audit log names the person, as actor or as target: they were here."""
    found = await session.execute(
        select(AuditLog.id)
        .where(
            AuditLog.org_id == org_id,
            _names_the_person(user_id),
        )
        .limit(1)
    )
    return found.first() is not None


async def _groups(session: AsyncSession, organisation: Organization, user: User) -> list[MemberGroup]:
    """The organisation's groups the person is in (internal or SCIM, ADR 0160), then the identity provider's
    groups for this organisation as of their last sign-in (a bare one, or one namespaced for it)."""
    rows = (
        await session.execute(
            select(OrgGroup.slug, OrgGroup.name, OrgGroup.source)
            .join(OrgGroupMember, OrgGroupMember.group_id == OrgGroup.id)
            .where(OrgGroup.org_id == organisation.id, OrgGroupMember.user_id == user.id)
            .order_by(OrgGroup.slug)
        )
    ).all()
    groups = [
        MemberGroup(slug=slug, name=name, source="scim" if source == SCIM else "internal")
        for slug, name, source in rows
    ]
    prefix = f"{_PREFIX}{organisation.slug}:"
    claimed: set[str] = set()
    for raw in user.groups or []:
        group = str(raw)
        if group.startswith(prefix) and len(group) > len(prefix):
            claimed.add(group[len(prefix) :])
        elif not group.startswith(_PREFIX):
            claimed.add(group)
    groups += [MemberGroup(slug=g, name=g, source="identity_provider") for g in sorted(claimed)]
    return groups


async def _origin(
    session: AsyncSession, org_id: str, user: User, memberships: Sequence[Any]
) -> AccountOrigin:
    """`identity_provider`: the person signed in through one (a subject is bound, or a sign-in gave them their
    role); `provisioned`: an identity provider pushed them into a group of the organisation (SCIM) before any
    such sign-in; `manual`: an administrator invited them (or a development sign-in)."""
    sub = user.oidc_sub or ""
    if (sub and not sub.startswith("dev|")) or any(m.source == "idp" for m, _ in memberships):
        return "identity_provider"
    pushed = await session.execute(
        select(OrgGroupMember.id)
        .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
        .where(OrgGroup.org_id == org_id, OrgGroup.source == SCIM, OrgGroupMember.user_id == user.id)
        .limit(1)
    )
    return "provisioned" if pushed.first() is not None else "manual"


async def _sessions(session: AsyncSession, user_id: str) -> MemberSessions:
    now = utcnow()
    count, last = (
        await session.execute(
            select(func.count(UserSession.id), func.max(UserSession.last_seen_at)).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > now
            )
        )
    ).one()
    return MemberSessions(open=int(count or 0), last_activity_at=_aware(last))


async def _readable_projects(
    session: AsyncSession, principal: Principal, organisation: Organization
) -> dict[str, Project]:
    """The organisation's team projects the caller reads, by id."""
    projects = (
        await session.execute(
            select(Project).where(Project.org_id == organisation.id, Project.kind == STANDARD)
        )
    ).scalars()
    return {p.id: p for p in projects if principal.can(Permission.PROJECT_READ, organisation.slug, p.slug)}


async def _work(
    session: AsyncSession, principal: Principal, organisation: Organization, user: User, active: bool
) -> MemberWork:
    projects = await _readable_projects(session, principal, organisation)
    email = user.email.strip().lower()
    return MemberWork(
        pending_requests=await _pending(session, organisation, user, projects) if active else [],
        decisions=await _decisions(session, email, projects),
        runs=await _runs(session, email, projects),
        spend_month_usd=await _spend_this_month(organisation.id, user.id),
    )


def _addressed(assignee: StepAssignee | None, email: str, groups: set[str], row: HumanRequest) -> str | None:
    """`person`, `group` or `None`: whether the request waits on the person, by the rules a decision checks
    (ADR 0153): the person named; a group they are in when no person is named, or once the SLA passed."""
    if assignee is None:
        return None
    if assignee.user and assignee.user == email:
        return "person"
    if assignee.group and assignee.group in groups and (assignee.user is None or sla_passed(row)):
        return "group"
    return None


async def _pending(
    session: AsyncSession, organisation: Organization, user: User, projects: dict[str, Project]
) -> list[MemberPendingRequest]:
    if not projects:
        return []
    rows = (
        await session.execute(
            select(HumanRequest, WorkItem)
            .join(WorkItem, WorkItem.id == HumanRequest.work_item_id)
            .where(
                HumanRequest.project_id.in_(list(projects)),
                HumanRequest.decided_at.is_(None),
                WorkItem.closed_at.is_(None),
            )
            .order_by(HumanRequest.requested_at.desc())
            .limit(PENDING_SCAN)
        )
    ).all()
    email = user.email.strip().lower()
    groups = groups_for_org(user.groups, organisation.slug) | (
        (await table_groups_by_user(session, organisation.id)).get(user.id, set())
    )
    found: list[MemberPendingRequest] = []
    for row, item in rows:
        assignee = await assignee_of_the_request(session, item, row)
        via = _addressed(assignee, email, groups, row)
        if via is None or assignee is None:
            continue
        found.append(
            MemberPendingRequest(
                id=row.id,
                work_item_id=item.id,
                work_item_key=item.tracker_key,
                work_item_title=item.title,
                project_slug=projects[row.project_id].slug,
                kind=row.kind,
                requested_at=_aware(row.requested_at) or row.requested_at,
                due_at=_aware(row.due_at),
                via=via,
                group=assignee.group if via == "group" else None,
            )
        )
    return found


async def _decisions(session: AsyncSession, email: str, projects: dict[str, Project]) -> list[MemberDecision]:
    if not projects:
        return []
    rows = (
        await session.execute(
            select(HumanRequest, WorkItem)
            .join(WorkItem, WorkItem.id == HumanRequest.work_item_id)
            .where(
                HumanRequest.project_id.in_(list(projects)),
                HumanRequest.decided_at.is_not(None),
                func.lower(HumanRequest.decided_by) == email,
            )
            .order_by(HumanRequest.decided_at.desc())
            .limit(RECENT)
        )
    ).all()
    return [
        MemberDecision(
            request_id=row.id,
            work_item_id=item.id,
            work_item_key=item.tracker_key,
            work_item_title=item.title,
            project_slug=projects[row.project_id].slug,
            kind=row.kind,
            approved=(row.decision or {}).get("approved") if isinstance(row.decision, dict) else None,
            decided_at=_aware(row.decided_at) or utcnow(),
        )
        for row, item in rows
    ]


async def _runs(session: AsyncSession, email: str, projects: dict[str, Project]) -> list[MemberRun]:
    """The runs of the work items the person started, through the console, the API or the MCP door
    (`services/initiator.py`): what `initiated_by` names, never guessed from anything else."""
    if not projects:
        return []
    started = [f"person:{email}", f"mcp:{email}"]
    rows = (
        await session.execute(
            select(Run, WorkItem)
            .join(WorkItem, WorkItem.id == Run.work_item_id)
            .where(Run.project_id.in_(list(projects)), func.lower(Run.initiated_by).in_(started))
            .order_by(Run.created_at.desc())
            .limit(RECENT)
        )
    ).all()
    return [
        MemberRun(
            id=run.id,
            work_item_id=item.id,
            work_item_key=item.tracker_key,
            project_slug=projects[run.project_id].slug,
            stage_role=run.stage_role,
            status=run.status,
            started_at=_aware(run.started_at),
            cost_usd=float(run.cost_usd or 0.0),
        )
        for run, item in rows
    ]


async def _spend_this_month(org_id: str, user_id: str) -> float:
    """What the person's assistant conversations and tasks cost in the organisation since the first of the
    month: the ledger lines that name them (S63-15). Those lines are booked in the person's personal space,
    which nobody else reads (ADR 0134): the sum is made under the platform scope, which returns the total and
    never a row, as the month caps are (`assistant_access.month_spend`)."""
    in_the_organisation = or_(
        CostLedger.org_id == org_id,
        CostLedger.project_id.in_(select(Project.id).where(Project.org_id == org_id)),
    )
    query = select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
        CostLedger.user_id == user_id, CostLedger.ts >= start_of_month(), in_the_organisation
    )
    async with session_scope(orgs="*") as platform:
        return round(float((await platform.execute(query)).scalar_one()), 6)


async def _administration(session: AsyncSession, org_id: str, user_id: str) -> MemberAdministration:
    now = utcnow()
    tokens = (
        await session.execute(
            select(func.count(ApiToken.id)).where(
                ApiToken.user_id == user_id, or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now)
            )
        )
    ).scalar_one()
    audit = (
        (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.org_id == org_id,
                    _names_the_person(user_id),
                )
                .order_by(AuditLog.ts.desc())
                .limit(AUDIT_RECENT)
            )
        )
        .scalars()
        .all()
    )
    return MemberAdministration(
        api_tokens=int(tokens or 0), audit=[AuditEntry.model_validate(row) for row in audit]
    )
