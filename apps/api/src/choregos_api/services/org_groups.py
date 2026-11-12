# SPDX-License-Identifier: Apache-2.0
"""An organisation's groups (ADR 0152): first-class groups, each with exactly one source.

- `internal`: created and edited through the API by an organisation administrator (`group:manage`);
- `scim`: pushed by an identity provider through `upsert_external_group`, which the core serves on no
  route of its own; such a group is read-only through the core's API (409 `group_read_only`).

The identity provider's `groups` claim (`User.groups`) stays a third source, read as before. A person
carries a group of the table as `choregos:<org>:<group>`, the namespaced form `groups_for_org`
already reads as the bare name for its organisation, so every check that reads `Principal.groups`
(a human step's group, an action's approvers, knowledge access, an assistant's audience) sees it with
no change of its own.

A membership row counts only while its person is a member of the organisation (a role in it or on one
of its projects): removing someone from the organisation takes their groups away with no second
gesture, and an identity-provider sign-in that revokes a membership does too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from ..db.models import Membership, Organization, OrgGroup, OrgGroupMember, User
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable
from ..rbac import Permission, Principal
from ..schemas.org_groups import (
    GROUP_SLUG_PATTERN,
    OrgGroupCreate,
    OrgGroupDto,
    OrgGroupMemberAdd,
    OrgGroupMemberDto,
    OrgGroupUpdate,
)

#: A column, mapped or not, that a condition compares.
_Column = ColumnElement[str] | InstrumentedAttribute[str]

INTERNAL: Final = "internal"
SCIM: Final = "scim"
#: The code of the 409 a write on a group the identity provider owns answers.
READ_ONLY: Final = "group_read_only"


def require_read(principal: Principal, org: str) -> None:
    """Every member of the organisation reads its groups: a role in it, or on one of its projects."""
    if principal.can(Permission.MEMBER_READ, org) or any(
        key.startswith(f"{org}/") for key in principal.project_roles
    ):
        return
    raise forbidden(f"reading the groups of {org} requires being a member of it")


def require_manage(principal: Principal, org: str) -> None:
    if not principal.can(Permission.GROUP_MANAGE, org):
        raise forbidden(f"the groups of {org} are managed by its administrator (`group:manage`)")


def _writable(group: OrgGroup) -> None:
    if group.source != INTERNAL:
        raise conflict(
            f"the group `{group.slug}` comes from the identity provider ({group.source}): it is changed "
            "there, and is read-only here",
            extra={"code": READ_ONLY, "source": group.source},
        )


def _in_the_organisation(user_id: _Column, org_id: _Column | str) -> ColumnElement[bool]:
    """The person still has a role in the organisation or on one of its projects."""
    return exists().where(Membership.user_id == user_id, Membership.org_id == org_id)


def _counted(org_id: _Column | str) -> ColumnElement[bool]:
    return and_(OrgGroupMember.org_id == org_id, _in_the_organisation(OrgGroupMember.user_id, org_id))


# ── what a person carries, and what the organisation knows ──


async def principal_groups(session: AsyncSession, user_id: str) -> list[str]:
    """The table's groups of one person, in every organisation they belong to, as
    `choregos:<org slug>:<group slug>`: one query. The slug is read now (it can change)."""
    rows = (
        await session.execute(
            select(Organization.slug, OrgGroup.slug)
            .select_from(OrgGroupMember)
            .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
            .join(Organization, Organization.id == OrgGroupMember.org_id)
            .where(
                OrgGroupMember.user_id == user_id,
                _in_the_organisation(OrgGroupMember.user_id, OrgGroupMember.org_id),
            )
            .order_by(Organization.slug, OrgGroup.slug)
        )
    ).all()
    return [f"choregos:{org_slug}:{group_slug}" for org_slug, group_slug in rows]


async def groups_of(session: AsyncSession, user: User) -> list[str]:
    """Everything a person carries: the identity provider's claim as sent, then the table's groups."""
    claimed = [str(g) for g in (user.groups or [])]
    return claimed + [g for g in await principal_groups(session, user.id) if g not in claimed]


async def groups_with_members(session: AsyncSession, org_id: str) -> set[str]:
    """The slugs of the organisation's groups that have at least one member."""
    rows = (
        await session.execute(
            select(OrgGroup.slug)
            .join(OrgGroupMember, OrgGroupMember.group_id == OrgGroup.id)
            .where(OrgGroup.org_id == org_id, _counted(org_id))
            .distinct()
        )
    ).scalars()
    return set(rows)


async def table_groups_by_user(session: AsyncSession, org_id: str) -> dict[str, set[str]]:
    """For each member of the organisation in some group of it, the slugs of those groups."""
    rows = (
        await session.execute(
            select(OrgGroupMember.user_id, OrgGroup.slug)
            .join(OrgGroup, OrgGroup.id == OrgGroupMember.group_id)
            .where(OrgGroup.org_id == org_id, _counted(org_id))
        )
    ).all()
    found: dict[str, set[str]] = {}
    for user_id, slug in rows:
        found.setdefault(user_id, set()).add(slug)
    return found


# ── the groups ──


async def _member_counts(session: AsyncSession, org_id: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(OrgGroupMember.group_id, func.count())
            .where(_counted(org_id))
            .group_by(OrgGroupMember.group_id)
        )
    ).all()
    return {group_id: int(count) for group_id, count in rows}


def _view(group: OrgGroup, member_count: int) -> OrgGroupDto:
    return OrgGroupDto(
        slug=group.slug,
        name=group.name,
        description=group.description,
        source=INTERNAL if group.source == INTERNAL else SCIM,
        member_count=member_count,
        created_by=group.created_by,
        created_at=group.created_at,
    )


async def view(session: AsyncSession, group: OrgGroup) -> OrgGroupDto:
    return _view(group, (await _member_counts(session, group.org_id)).get(group.id, 0))


async def list_groups(session: AsyncSession, organisation: Organization) -> list[OrgGroupDto]:
    groups = (
        (
            await session.execute(
                select(OrgGroup).where(OrgGroup.org_id == organisation.id).order_by(OrgGroup.slug)
            )
        )
        .scalars()
        .all()
    )
    counts = await _member_counts(session, organisation.id)
    return [_view(group, counts.get(group.id, 0)) for group in groups]


async def group_by_slug(session: AsyncSession, organisation: Organization, slug: str) -> OrgGroup:
    group = (
        await session.execute(
            select(OrgGroup).where(OrgGroup.org_id == organisation.id, OrgGroup.slug == slug)
        )
    ).scalar_one_or_none()
    if group is None:
        raise not_found("Group", f"{organisation.slug}/{slug}")
    return group


async def _slug_taken(session: AsyncSession, org_id: str, slug: str) -> bool:
    return (
        await session.execute(select(OrgGroup.id).where(OrgGroup.org_id == org_id, OrgGroup.slug == slug))
    ).scalar_one_or_none() is not None


async def create_group(
    session: AsyncSession, organisation: Organization, body: OrgGroupCreate, principal: Principal
) -> OrgGroup:
    """An `internal` group. 409 `group_exists` when the slug is taken in the organisation."""
    if await _slug_taken(session, organisation.id, body.slug):
        raise conflict(
            f"the group `{body.slug}` already exists in {organisation.slug}", extra={"code": "group_exists"}
        )
    group = OrgGroup(
        org_id=organisation.id,
        slug=body.slug,
        name=body.name.strip() or body.slug,
        description=body.description,
        source=INTERNAL,
        created_by=principal.email or principal.user_id,
    )
    session.add(group)
    await session.flush()
    return group


async def update_group(session: AsyncSession, group: OrgGroup, body: OrgGroupUpdate) -> list[str]:
    """Changes what was sent; answers the names of the fields that changed."""
    _writable(group)
    changed: list[str] = []
    if body.name is not None and body.name.strip() and body.name.strip() != group.name:
        group.name = body.name.strip()
        changed.append("name")
    if "description" in body.model_fields_set and body.description != group.description:
        group.description = body.description
        changed.append("description")
    await session.flush()
    return changed


async def delete_group(session: AsyncSession, group: OrgGroup) -> None:
    _writable(group)
    await session.delete(group)
    await session.flush()


# ── who is in a group ──


async def list_members(session: AsyncSession, group: OrgGroup) -> list[OrgGroupMemberDto]:
    rows = (
        await session.execute(
            select(OrgGroupMember, User)
            .join(User, User.id == OrgGroupMember.user_id)
            .where(OrgGroupMember.group_id == group.id, _counted(group.org_id))
            .order_by(User.email)
        )
    ).all()
    return [
        OrgGroupMemberDto(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name or "",
            added_by=row.added_by,
            added_at=row.created_at,
        )
        for row, user in rows
    ]


async def _organisation_member(
    session: AsyncSession, organisation: Organization, body: OrgGroupMemberAdd
) -> User:
    """The person the body names, who must be a member of the organisation (a person, not a machine
    account): 422 otherwise, with the same sentence whether the person exists elsewhere or not."""
    who = body.email or body.user_id or ""
    criterion = User.email == body.email if body.email is not None else User.id == body.user_id
    user = (
        await session.execute(
            select(User).where(
                criterion, _in_the_organisation(User.id, organisation.id), User.is_service.is_(False)
            )
        )
    ).scalar_one_or_none()
    if user is None:
        raise unprocessable(
            f"`{who}` is not a member of {organisation.slug}: only its members join its groups"
        )
    return user


async def add_member(
    session: AsyncSession,
    organisation: Organization,
    group: OrgGroup,
    body: OrgGroupMemberAdd,
    principal: Principal,
) -> tuple[OrgGroupMemberDto, bool]:
    """Puts a member of the organisation in an `internal` group; answers whether a row was added
    (adding someone already in it changes nothing)."""
    _writable(group)
    user = await _organisation_member(session, organisation, body)
    row = (
        await session.execute(
            select(OrgGroupMember).where(
                OrgGroupMember.group_id == group.id, OrgGroupMember.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    added = row is None
    if row is None:
        row = OrgGroupMember(
            org_id=organisation.id,
            group_id=group.id,
            user_id=user.id,
            added_by=principal.email or principal.user_id,
        )
        session.add(row)
        await session.flush()
    return (
        OrgGroupMemberDto(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name or "",
            added_by=row.added_by,
            added_at=row.created_at,
        ),
        added,
    )


async def remove_member(session: AsyncSession, group: OrgGroup, user_id: str) -> None:
    _writable(group)
    row = (
        await session.execute(
            select(OrgGroupMember).where(
                OrgGroupMember.group_id == group.id, OrgGroupMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Group member", f"{user_id} in {group.slug}")
    await session.delete(row)
    await session.flush()


# ── a group the identity provider pushes ──


async def upsert_external_group(
    session: AsyncSession,
    org_id: str,
    *,
    external_id: str,
    slug: str,
    name: str,
    members_user_ids: Iterable[str],
    actor: str,
) -> OrgGroup:
    """Creates or updates the `scim` group the identity provider knows as `external_id`, and replaces
    its members with `members_user_ids` (ADR 0152).

    The core serves this on no route: an edition that speaks SCIM calls it. The group is found by its
    `external_id` in the organisation, so a renamed group keeps its row and its members' history.
    Refused (`ApiError`): a slug that does not match the pattern, a slug taken by another group of the
    organisation (an `internal` group is never taken over), a person who is not a member of the
    organisation (or a machine account). Nothing is written when it refuses."""
    if not re.fullmatch(GROUP_SLUG_PATTERN, slug):
        raise unprocessable(f"`{slug}` is not a group slug ({GROUP_SLUG_PATTERN})")
    if not external_id.strip():
        raise unprocessable("a group pushed by the identity provider has an `external_id`")
    wanted = list(dict.fromkeys(members_user_ids))
    members = set(
        (
            await session.execute(
                select(User.id).where(
                    User.id.in_(wanted), _in_the_organisation(User.id, org_id), User.is_service.is_(False)
                )
            )
        ).scalars()
    )
    strangers = [user_id for user_id in wanted if user_id not in members]
    if strangers:
        raise unprocessable(f"not members of the organisation: {', '.join(strangers)}")
    group = (
        await session.execute(
            select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.external_id == external_id)
        )
    ).scalar_one_or_none()
    holder = (
        await session.execute(select(OrgGroup).where(OrgGroup.org_id == org_id, OrgGroup.slug == slug))
    ).scalar_one_or_none()
    if holder is not None and (group is None or holder.id != group.id):
        raise _slug_conflict(slug, holder)
    if group is None:
        group = OrgGroup(
            org_id=org_id,
            slug=slug,
            name=name.strip() or slug,
            source=SCIM,
            external_id=external_id,
            created_by=actor,
        )
        session.add(group)
        await session.flush()
    else:
        group.slug = slug
        group.name = name.strip() or slug
    current = set(
        (
            await session.execute(select(OrgGroupMember.user_id).where(OrgGroupMember.group_id == group.id))
        ).scalars()
    )
    gone = current - members
    if gone:
        await session.execute(
            delete(OrgGroupMember).where(
                OrgGroupMember.group_id == group.id, OrgGroupMember.user_id.in_(gone)
            )
        )
    for user_id in wanted:
        if user_id not in current:
            session.add(OrgGroupMember(org_id=org_id, group_id=group.id, user_id=user_id, added_by=actor))
    await session.flush()
    return group


def _slug_conflict(slug: str, holder: OrgGroup) -> ApiError:
    return conflict(
        f"the slug `{slug}` is taken by another group of the organisation ({holder.source})",
        extra={"code": "group_exists", "source": holder.source},
    )
