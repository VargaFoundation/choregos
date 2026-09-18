"""Dépendances FastAPI : session, identité, RBAC, pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from choregos_contracts import Role
from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db.models import ApiToken, Membership, Organization, Project, User
from .db.session import get_sessionmaker
from .errors import forbidden, not_found, unauthorized
from .rbac import Permission, Principal
from .security import RunClaims, hash_api_token, read_session, verify_run_token


async def get_db() -> AsyncIterator[AsyncSession]:
    """Session transactionnelle par requête : commit à la sortie, rollback sur exception."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


Db = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


async def _principal_from_user(session: AsyncSession, user: User) -> Principal:
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
            project_roles[project_slug] = role
        else:
            org_roles[org_slug] = role
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        kind="user",
        org_roles=org_roles,
        project_roles=project_roles,
    )


async def current_principal(
    request: Request,
    session: Db,
    settings: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Identité de l'appelant : cookie de session OIDC, ou jeton d'API porteur."""
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1].strip()
        token = (
            await session.execute(select(ApiToken).where(ApiToken.hash == hash_api_token(raw)))
        ).scalar_one_or_none()
        if token is None:
            raise unauthorized("jeton d'API inconnu ou révoqué")
        user = await session.get(User, token.user_id)
        if user is None:
            raise unauthorized("jeton d'API orphelin")
        principal = await _principal_from_user(session, user)
        principal.kind = "user"
        return principal

    payload = read_session(request.cookies.get(settings.session_cookie, ""), settings)
    if payload is None:
        raise unauthorized()
    user = await session.get(User, str(payload.get("sub", "")))
    if user is None:
        raise unauthorized("session périmée")
    return await _principal_from_user(session, user)


Me = Annotated[Principal, Depends(current_principal)]


@dataclass(slots=True)
class ProjectContext:
    """Un projet résolu, avec l'organisation et le rôle de l'appelant."""

    project: Project
    org_slug: str
    principal: Principal

    def require(self, permission: Permission) -> None:
        if not self.principal.can(permission, self.org_slug, self.project.slug):
            raise forbidden(
                f"`{permission}` requiert un rôle supérieur sur {self.org_slug}/{self.project.slug}"
            )

    @property
    def slug(self) -> str:
        return self.project.slug

    @property
    def id(self) -> str:
        return self.project.id


async def resolve_project(session: AsyncSession, identifier: str) -> tuple[Project, str]:
    """Accepte un UUID ou un slug (`org/slug` ou `slug`)."""
    project = await session.get(Project, identifier)
    if project is None:
        slug = identifier.rsplit("/", maxsplit=1)[-1]
        project = (
            await session.execute(select(Project).where(Project.slug == slug).limit(1))
        ).scalar_one_or_none()
    if project is None:
        raise not_found("Projet", identifier)
    org = await session.get(Organization, project.org_id)
    return project, org.slug if org else ""


async def project_context(id: str, session: Db, principal: Me) -> ProjectContext:
    project, org_slug = await resolve_project(session, id)
    context = ProjectContext(project=project, org_slug=org_slug, principal=principal)
    context.require(Permission.PROJECT_READ)
    return context


ProjectCtx = Annotated[ProjectContext, Depends(project_context)]


async def run_claims(
    id: str,
    authorization: Annotated[str | None, Header()] = None,
) -> RunClaims:
    """Authentifie un runner : le JWT doit porter **ce** run et ne pas être expiré."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("jeton de run manquant")
    try:
        claims = verify_run_token(authorization.split(" ", 1)[1].strip())
    except jwt.PyJWTError as exc:
        raise forbidden(f"jeton de run invalide : {exc}") from exc
    if claims.run_id != id:
        raise forbidden("ce jeton n'est pas celui de ce run")
    return claims


RunAuth = Annotated[RunClaims, Depends(run_claims)]


@dataclass(slots=True)
class Page:
    cursor: str | None = None
    limit: int = 50

    def slice(self, rows: list[Any]) -> tuple[list[Any], str | None, bool]:
        """Pagination par curseur opaque : l'identifiant de la dernière ligne rendue."""
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
