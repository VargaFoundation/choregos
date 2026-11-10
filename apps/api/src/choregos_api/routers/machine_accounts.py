# SPDX-License-Identifier: Apache-2.0
"""Machine accounts (S62-01, ADR 0131): an application calls the A2A door under its own name.

An account belongs to one project, as a `users` row with `is_service` and a `developer` membership.
Its tokens are `a2a` only, bound to the project and expiring; they are read and written at platform
scope because `api_tokens` is filtered on the owner, and here the owner is not the caller.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import APIRouter, Path, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import ApiToken, MachineAccount, Membership, User
from ..db.session import in_platform_scope
from ..deps import Db, ProjectCtx
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission
from ..schemas import (
    ApiTokenCreated,
    ApiTokenDto,
    MachineAccountCreate,
    MachineAccountDto,
    MachineTokenCreate,
)
from ..security import generate_api_token

router = APIRouter(tags=["projects"])

Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
#: `.invalid` is reserved (RFC 2606): no mail reaches it and no identity provider can vouch for it.
ADDRESS_DOMAIN = "machines.choregos.invalid"
#: What it takes to start, follow and stop work items; the door never lets it decide.
ROLE = Role.DEVELOPER
SOURCE = "machine"
SCOPES = ["a2a"]


def _managed(ctx: ProjectCtx) -> None:
    ctx.require(Permission.MEMBER_MANAGE)
    if ctx.principal.kind != "user":
        raise forbidden("a machine account is managed by a person")


async def _account(session: AsyncSession, ctx: ProjectCtx, slug: str) -> MachineAccount:
    account = (
        await session.execute(
            select(MachineAccount).where(
                MachineAccount.project_id == ctx.id,
                MachineAccount.slug == slug,
                MachineAccount.removed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if account is None:
        raise not_found("Machine account", slug)
    return account


def _token_dto(token: ApiToken, ctx: ProjectCtx) -> ApiTokenDto:
    return ApiTokenDto(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        scopes=token.scopes,
        project=f"{ctx.org_slug}:{ctx.slug}",
        last_client=token.last_client,
    )


def _dto(account: MachineAccount, address: str, tokens: list[ApiToken], ctx: ProjectCtx) -> MachineAccountDto:
    return MachineAccountDto(
        slug=account.slug,
        display_name=account.display_name,
        address=address,
        writes_per_day=account.writes_per_day,
        created_by=account.created_by,
        created_at=account.created_at,
        tokens=[_token_dto(token, ctx) for token in tokens],
    )


@router.get(
    "/projects/{id}/machine-accounts",
    response_model=list[MachineAccountDto],
    operation_id="listMachineAccounts",
)
async def list_machine_accounts(ctx: ProjectCtx, session: Db) -> list[MachineAccountDto]:
    ctx.require(Permission.MEMBER_READ)
    accounts = list(
        (
            await session.execute(
                select(MachineAccount)
                .where(MachineAccount.project_id == ctx.id, MachineAccount.removed_at.is_(None))
                .order_by(MachineAccount.slug)
            )
        ).scalars()
    )
    owners = [account.user_id for account in accounts]
    async with in_platform_scope(session):
        tokens = list((await session.execute(select(ApiToken).where(ApiToken.user_id.in_(owners)))).scalars())
        rows = (await session.execute(select(User.id, User.email).where(User.id.in_(owners)))).all()
        addresses = {row.id: row.email for row in rows}
    return [
        _dto(account, addresses[account.user_id], [t for t in tokens if t.user_id == account.user_id], ctx)
        for account in accounts
    ]


@router.post(
    "/projects/{id}/machine-accounts",
    response_model=MachineAccountDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMachineAccount",
)
async def create_machine_account(
    ctx: ProjectCtx, body: MachineAccountCreate, session: Db
) -> MachineAccountDto:
    _managed(ctx)
    taken = (
        await session.execute(
            select(MachineAccount.id).where(
                MachineAccount.project_id == ctx.id, MachineAccount.slug == body.slug
            )
        )
    ).first()
    if taken is not None:
        raise conflict(f"`{body.slug}` is or was a machine account of this project: a slug is not reused")
    address = f"{body.slug}.{ctx.id}@{ADDRESS_DOMAIN}"
    display_name = body.display_name or body.slug
    user = User(email=address, display_name=display_name, is_service=True)
    async with in_platform_scope(session):
        session.add(user)
        await session.flush()
    session.add(
        Membership(
            user_id=user.id, org_id=ctx.project.org_id, project_id=ctx.id, role=str(ROLE), source=SOURCE
        )
    )
    account = MachineAccount(
        org_id=ctx.project.org_id,
        project_id=ctx.id,
        user_id=user.id,
        slug=body.slug,
        display_name=display_name,
        writes_per_day=body.writes_per_day,
        created_by=ctx.principal.email,
    )
    session.add(account)
    await session.flush()
    await record(
        session,
        ctx.principal,
        "machine_account.create",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        slug=body.slug,
        writes_per_day=body.writes_per_day,
    )
    return _dto(account, address, [], ctx)


@router.delete(
    "/projects/{id}/machine-accounts/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="removeMachineAccount",
)
async def remove_machine_account(ctx: ProjectCtx, slug: Slug, session: Db) -> Response:
    _managed(ctx)
    account = await _account(session, ctx, slug)
    async with in_platform_scope(session):
        for token in (
            await session.execute(select(ApiToken).where(ApiToken.user_id == account.user_id))
        ).scalars():
            await session.delete(token)
    for membership in (
        await session.execute(select(Membership).where(Membership.user_id == account.user_id))
    ).scalars():
        await session.delete(membership)
    account.removed_at = utcnow()
    await record(
        session,
        ctx.principal,
        "machine_account.remove",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        slug=slug,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/projects/{id}/machine-accounts/{slug}/tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMachineToken",
)
async def create_machine_token(
    ctx: ProjectCtx, slug: Slug, body: MachineTokenCreate, session: Db
) -> ApiTokenCreated:
    """Issue a token; the plain text is returned HERE only, once. Several may live together: a token
    is rotated by issuing the next one, then revoking the old."""
    _managed(ctx)
    account = await _account(session, ctx, slug)
    raw, digest = generate_api_token()
    token = ApiToken(
        user_id=account.user_id,
        name=body.name,
        hash=digest,
        scopes=list(SCOPES),
        project_id=ctx.id,
        expires_at=utcnow() + timedelta(days=body.expires_in_days),
    )
    async with in_platform_scope(session):
        session.add(token)
        await session.flush()
    await record(
        session,
        ctx.principal,
        "machine_account.token.create",
        org_id=ctx.project.org_id,
        target_type="api_token",
        target_id=token.id,
        slug=slug,
    )
    return ApiTokenCreated(**_token_dto(token, ctx).model_dump(), token=raw)


@router.delete(
    "/projects/{id}/machine-accounts/{slug}/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="revokeMachineToken",
)
async def revoke_machine_token(ctx: ProjectCtx, slug: Slug, token_id: str, session: Db) -> Response:
    _managed(ctx)
    account = await _account(session, ctx, slug)
    async with in_platform_scope(session):
        token = await session.get(ApiToken, token_id)
        if token is None or token.user_id != account.user_id:
            raise not_found("Token", token_id)
        await session.delete(token)
    await record(
        session,
        ctx.principal,
        "machine_account.token.revoke",
        org_id=ctx.project.org_id,
        target_type="api_token",
        target_id=token_id,
        slug=slug,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
