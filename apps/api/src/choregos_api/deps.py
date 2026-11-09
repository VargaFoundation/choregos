# SPDX-License-Identifier: Apache-2.0
"""FastAPI dependencies: session, identity, RBAC, pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import jwt
from choregos_contracts import Role
from choregos_core import utcnow
from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from .config import Settings, get_settings
from .db.models import ApiToken, Membership, Organization, Project, User
from .db.org_aliases import current_slug
from .db.session import (
    ALL_ORGS,
    get_sessionmaker,
    in_platform_scope,
    limit_to_organisations,
    name_the_user,
    run_after_commit,
)
from .errors import ApiError, forbidden, not_found, unauthorized
from .logging import bind
from .rbac import Permission, Principal
from .security import RunClaims, hash_api_token, read_session, verify_egress_token, verify_run_token


async def get_db() -> AsyncIterator[AsyncSession]:
    """Transactional session per request: commit on exit, rollback on exception."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


#: `scope="function"`: the session closes, and the transaction commits, BEFORE the response is sent.
#: With "request" scope (FastAPI's default), the `commit` came AFTER: a fast client read a state not
#: yet written (the user that the sign-in callback had just created: "expired session"), and a
#: `commit` that failed left the client a 201 on a write that had not happened. `ASGITransport`
#: waits for the application to finish, so no test saw it. Found on the integrated stack of the
#: foundation trial, served by uvicorn.
Db = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def get_platform_db() -> AsyncIterator[AsyncSession]:
    """A session that acts FOR the platform: it sees every organisation (`*`).

    For the routes without a principal, authenticated by a signature or a shared secret: the
    webhooks. They used `get_db`, without a scope: on PostgreSQL fail-closed RLS hid every project
    from them, and every CI, CD or tracker event was dropped as "no known project". The API tests
    run on SQLite, where RLS does not exist: nobody saw it. A route that takes this session must
    have checked who is calling BEFORE reading.
    """
    async with get_sessionmaker()() as session:
        await limit_to_organisations(session, ALL_ORGS)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await run_after_commit(session)


PlatformDb = Annotated[AsyncSession, Depends(get_platform_db, scope="function")]
Config = Annotated[Settings, Depends(get_settings)]


def _aware(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; PostgreSQL returns UTC ones. We compare in UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _principal_from_user(session: AsyncSession, user: User, *, narrow: bool = True) -> Principal:
    # The identity is resolved in PLATFORM scope, then the session is narrowed below. Without this
    # line, the join on `projects` (under RLS) saw no project on PostgreSQL: a PROJECT membership
    # arrived with `project_slug = None`, and became a role on the WHOLE organisation, so a developer
    # invited to one project read all the others. Invisible on SQLite, where RLS does not exist;
    # found by running the whole suite on PostgreSQL.
    await limit_to_organisations(session, ALL_ORGS)
    rows = (
        await session.execute(
            select(Membership, Organization.slug, Project.slug)
            .join(Organization, Membership.org_id == Organization.id)
            .outerjoin(Project, Membership.project_id == Project.id)
            .where(Membership.user_id == user.id)
        )
    ).all()
    org_roles: dict[str, Role] = {}
    project_roles: dict[str, Role] = {}
    for membership, org_slug, project_slug in rows:
        role = Role(membership.role)
        if project_slug:
            project_roles[f"{org_slug}/{project_slug}"] = role
        else:
            org_roles[org_slug] = role
    principal = Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        kind="user",
        org_roles=org_roles,
        project_roles=project_roles,
        groups=[str(g) for g in (user.groups or [])],
    )
    # THIS request's session sees only the principal's organisations: this is where RLS arms,
    # because this is where we know who is speaking.
    orgs = set(org_roles) | {qualified.split("/", 1)[0] for qualified in project_roles}
    if narrow:
        await limit_to_organisations(session, sorted(orgs))
        await name_the_user(session, user.id)
    return principal


async def principal_for_user(session: AsyncSession, user: User, *, narrow: bool = True) -> Principal:
    """The principal of a known user, for code that acts on their behalf outside a sign-in.

    `narrow=False` leaves the session's scope and attribution untouched: a webhook that handles several
    events must not end up scoped to, or attributed to, the first member it resolved (S47-14)."""
    return await _principal_from_user(session, user, narrow=narrow)


def is_rest_token(token: ApiToken) -> bool:
    """Does this token open the REST API? A token older than scopes (empty list) counts as `*`."""
    return "*" in (token.scopes or ["*"])


def record_usage(token: ApiToken, request: HTTPConnection) -> None:
    """The last use and the client that made it: the Integrations page says "connected"."""
    token.last_used_at = utcnow()
    client = request.headers.get("user-agent", "").strip()
    token.last_client = client[:200] or None


async def current_principal(
    # A connection, not a request: the realtime WebSocket (S41-10) authenticates the same way.
    request: HTTPConnection,
    session: Db,
    settings: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Identity of the caller: OIDC session cookie, or bearer API token.

    The token and the user are read in PLATFORM scope: `api_tokens` and `users` are under RLS since
    0.11, and we do not yet know who is speaking. `_principal_from_user` narrows afterwards.
    """
    await limit_to_organisations(session, ALL_ORGS)
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
        token = (
            await session.execute(select(ApiToken).where(ApiToken.hash == hash_api_token(raw)))
        ).scalar_one_or_none()
        if token is None:
            raise unauthorized("unknown or revoked API token")
        # `expires_at` existed in the database without anyone reading it: an expired token stayed
        # valid for life (inventory of 2026-09-24).
        if token.expires_at is not None and _aware(token.expires_at) <= utcnow():
            raise unauthorized("expired API token")
        if not is_rest_token(token):
            # ADR 0030: a token with MCP scope serves ONLY the MCP door. Stolen from a client's
            # configuration, it must neither mint another token, nor decide, nor read anything
            # outside the door.
            raise forbidden(
                "this token is reserved for the MCP door or the A2A door (scope mcp:* or a2a); "
                "the REST API needs the `*` scope"
            )
        user = await session.get(User, token.user_id)
        if user is None:
            raise unauthorized("orphaned API token: its user no longer exists")
        record_usage(token, request)
        principal = await _principal_from_user(session, user)
        principal.kind = "user"
        return principal

    payload = read_session(request.cookies.get(settings.session_cookie, ""), settings)
    if payload is None:
        raise unauthorized()
    user = await session.get(User, str(payload.get("sub", "")))
    if user is None:
        raise unauthorized("expired session")
    from .browser_sessions import check_session

    sid = await check_session(session, user, payload, request, settings)
    await _validate_session(session, user, payload)
    principal = await _principal_from_user(session, user)
    principal.session_id = sid
    # The authentication time at the IdP (`auth_time`) when the session carries it; otherwise the
    # time the session was opened (`iat`), which overestimates it after a silent SSO reconnection.
    when = payload.get("auth_time", payload.get("iat"))
    principal.authenticated_at = int(when) if isinstance(when, int | float) else None
    return principal


async def _validate_session(session: AsyncSession, user: User, payload: dict[str, Any]) -> None:
    """The validations declared by the extensions (`edition.declare_session_validation`)."""
    import inspect

    from .edition import SessionRefused, session_validations

    for validation in session_validations():
        try:
            result = validation(session, user, payload)
            if inspect.isawaitable(result):
                await result
        except SessionRefused as refusal:
            raise unauthorized(str(refusal)) from refusal


Me = Annotated[Principal, Depends(current_principal)]


async def require_platform_admin(session: AsyncSession, principal: Principal) -> None:
    """Administering the PLATFORM is a right on the instance, not on an organisation.

    `Principal.is_platform_admin()` is true as soon as one is `org_admin` of ANY organisation. On an
    installation with a single organisation, the community edition (ADR 0024), that is exact: the
    administrator of that organisation IS the administrator of the instance. As soon as there are
    two, it no longer is, and the predicate then granted the `/platform/*` routes (backend
    catalogue, executors, model profiles, gateway keys) to the administrator of any tenant.

    So we demand to be `org_admin` of ALL the organisations of the instance. In single-org, nothing
    changes. In multi-org, it is true of nobody until the enterprise edition has defined a real
    platform role: a refusal, and that is the right default: better a route nobody can reach than a
    route open to the wrong tenant.
    """
    from .db.models import Organization

    # On the WHOLE instance: under the RLS of `organizations`, the count made from the scope of the
    # administrator of a single organisation would be 1, and they would pass for administrator of all.
    async with in_platform_scope(session):
        slugs = set((await session.execute(select(Organization.slug))).scalars().all())
    administered = {org for org, role in principal.org_roles.items() if role == Role.ORG_ADMIN}
    if not slugs or not slugs <= administered:
        if await _granted_by_an_extension(session, principal):
            return
        missing = sorted(slugs - administered)
        raise forbidden(
            "reserved for platform administrators: this right covers the whole instance, and you "
            f"do not administer {missing or 'any organisation'}."
        )


async def is_instance_admin(session: AsyncSession, principal: Principal) -> bool:
    """`require_platform_admin` as a predicate, for a right that changes what a route accepts
    rather than whether it opens."""
    try:
        await require_platform_admin(session, principal)
    except ApiError:
        return False
    return True


async def _granted_by_an_extension(session: AsyncSession, principal: Principal) -> bool:
    """`edition.declare_platform_administrator`: additive, it takes nothing away."""
    import inspect

    from .edition import platform_administrators

    for authority in platform_administrators():
        verdict = authority(session, principal)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if verdict is True:
            return True
    return False


@dataclass(slots=True)
class ProjectContext:
    """A resolved project, with the organisation and the caller's role."""

    project: Project
    org_slug: str
    principal: Principal

    def require(self, permission: Permission) -> None:
        if not self.principal.can(permission, self.org_slug, self.project.slug):
            raise forbidden(
                f"insufficient rights: `{permission}` needs a higher role on "
                f"{self.org_slug}/{self.project.slug}"
            )

    @property
    def slug(self) -> str:
        return self.project.slug

    @property
    def id(self) -> str:
        return self.project.id


async def resolve_project(session: AsyncSession, identifier: str) -> tuple[Project, str]:
    """Accepts a UUID, `org/slug` (or `org:slug` in a URL segment), or a bare slug if it exists in
    only ONE organisation.

    The bare slug used to drop the `org/` part and take the first project of that name, across all
    organisations: a reader of `a/billing` could land on `b/billing`. A slug present in two
    organisations is now ambiguous, and says so. The `org:slug` form exists because a `/` does not
    fit in `/projects/{id}`.
    """
    project = await session.get(Project, identifier)
    if project is not None:
        org = await session.get(Organization, project.org_id)
        return project, org.slug if org else ""
    separator = "/" if "/" in identifier else ":" if ":" in identifier else ""
    if separator:
        org_slug, slug = identifier.split(separator, 1)
        # A former slug of the organisation resolves to it: an MCP address or a qualified id copied
        # before a rename keeps working, and the caller gets the CURRENT slug (ADR 0090).
        org_slug = await current_slug(session, org_slug)
        project = (
            await session.execute(
                select(Project)
                .join(Organization, Project.org_id == Organization.id)
                .where(Organization.slug == org_slug, Project.slug == slug)
            )
        ).scalar_one_or_none()
        if project is None:
            raise not_found("Project", identifier)
        return project, org_slug
    candidates = (await session.execute(select(Project).where(Project.slug == identifier))).scalars().all()
    if not candidates:
        raise not_found("Project", identifier)
    if len(candidates) > 1:
        raise not_found("Project", f"{identifier} (ambiguous: specify `org/{identifier}`)")
    org = await session.get(Organization, candidates[0].org_id)
    return candidates[0], org.slug if org else ""


async def project_context(id: str, session: Db, principal: Me) -> ProjectContext:
    project, org_slug = await resolve_project(session, id)
    context = ProjectContext(project=project, org_slug=org_slug, principal=principal)
    context.require(Permission.PROJECT_READ)
    bind(project=f"{org_slug}/{project.slug}")
    return context


ProjectCtx = Annotated[ProjectContext, Depends(project_context)]


async def run_claims(
    id: str,
    session: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> RunClaims:
    """Authenticates a runner: the JWT must carry **this** run and not be expired."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing run token")
    try:
        claims = verify_run_token(authorization.split(" ", 1)[1].strip())
    except jwt.PyJWTError as exc:
        raise forbidden(f"invalid run token: {exc}") from exc
    if claims.run_id != id:
        raise forbidden("this token belongs to another run")
    bind(run_id=claims.run_id, project=claims.project_slug, work_item=claims.work_item_key)
    # A run token is minted by the platform for ONE run: the scope is already in the token
    # (`claims.run_id`, checked by every route), not in an organisation.
    await limit_to_organisations(session, ALL_ORGS)
    return claims


RunAuth = Annotated[RunClaims, Depends(run_claims)]


async def network_claims(
    id: str,
    session: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> RunClaims:
    """A run's network routes (ADR 0115): its run token, or its egress token — the one the proxy
    variables carry, which the agent inherits. Every other internal route takes the run token only."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing run token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_run_token(token)
    except jwt.PyJWTError:
        try:
            claims = verify_egress_token(token)
        except jwt.PyJWTError as exc:
            raise forbidden(f"invalid run or egress token: {exc}") from exc
    if claims.run_id != id:
        raise forbidden("this token belongs to another run")
    bind(run_id=claims.run_id, project=claims.project_slug, work_item=claims.work_item_key)
    await limit_to_organisations(session, ALL_ORGS)
    return claims


NetworkAuth = Annotated[RunClaims, Depends(network_claims)]


@dataclass(slots=True)
class Page:
    cursor: str | None = None
    limit: int = 50

    def slice(self, rows: list[Any]) -> tuple[list[Any], str | None, bool]:
        """Pagination by opaque cursor: the identifier of the last row returned."""
        start = 0
        if self.cursor:
            for index, row in enumerate(rows):
                if getattr(row, "id", None) == self.cursor:
                    start = index + 1
                    break
        window = rows[start : start + self.limit]
        has_more = start + self.limit < len(rows)
        next_cursor = getattr(window[-1], "id", None) if window and has_more else None
        return window, next_cursor, has_more


async def pagination(
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    return Page(cursor=cursor, limit=limit)


Pagination = Annotated[Page, Depends(pagination)]
