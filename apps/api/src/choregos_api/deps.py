"""Dépendances FastAPI : session, identité, RBAC, pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import jwt
from choregos_contracts import Role
from choregos_core import utcnow
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


def _aware(moment: datetime) -> datetime:
    """SQLite rend des datetimes naïfs ; PostgreSQL des datetimes en UTC. On compare en UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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
            project_roles[f"{org_slug}/{project_slug}"] = role
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
        # `expires_at` existait en base sans que personne ne le lise : un jeton expiré
        # restait valable à vie (état des lieux du 2026-09-24).
        if token.expires_at is not None and _aware(token.expires_at) <= utcnow():
            raise unauthorized("jeton d'API expiré")
        user = await session.get(User, token.user_id)
        if user is None:
            raise unauthorized("jeton d'API orphelin")
        token.last_used_at = utcnow()
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
    """Accepte un UUID, `org/slug` (ou `org:slug` dans un segment d'URL), ou un slug seul
    s'il n'existe que dans UNE organisation.

    Le slug seul jetait la partie `org/` et prenait le premier projet de ce nom, toutes
    organisations confondues : un lecteur de `a/billing` pouvait tomber sur `b/billing`.
    Un slug présent dans deux organisations est désormais ambigu, et le dit. La forme
    `org:slug` existe parce qu'un `/` ne passe pas dans `/projects/{id}`.
    """
    project = await session.get(Project, identifier)
    if project is not None:
        org = await session.get(Organization, project.org_id)
        return project, org.slug if org else ""
    separateur = "/" if "/" in identifier else ":" if ":" in identifier else ""
    if separateur:
        org_slug, slug = identifier.split(separateur, 1)
        project = (
            await session.execute(
                select(Project)
                .join(Organization, Project.org_id == Organization.id)
                .where(Organization.slug == org_slug, Project.slug == slug)
            )
        ).scalar_one_or_none()
        if project is None:
            raise not_found("Projet", identifier)
        return project, org_slug
    candidats = (await session.execute(select(Project).where(Project.slug == identifier))).scalars().all()
    if not candidats:
        raise not_found("Projet", identifier)
    if len(candidats) > 1:
        raise not_found("Projet", f"{identifier} (ambigu : préciser `org/{identifier}`)")
    org = await session.get(Organization, candidats[0].org_id)
    return candidats[0], org.slug if org else ""


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
