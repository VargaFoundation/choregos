# SPDX-License-Identifier: Apache-2.0
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
from .db.session import (
    TOUT,
    en_portee_de_plateforme,
    get_sessionmaker,
    limiter_aux_organisations,
    nommer_l_utilisateur,
)
from .errors import forbidden, not_found, unauthorized
from .logging import bind
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


async def get_db_plateforme() -> AsyncIterator[AsyncSession]:
    """Session qui agit POUR la plateforme : elle voit toutes les organisations (`*`).

    Pour les routes sans principal, authentifiées par une signature ou un secret partagé — les
    webhooks. Elles utilisaient `get_db`, sans portée : sur PostgreSQL la RLS fail-closed leur
    cachait tous les projets, et chaque événement de CI, de CD ou de tracker était jeté comme « sans
    projet connu ». Les tests d'API tournent sur SQLite, où la RLS n'existe pas : personne ne l'a vu.
    Une route qui prend cette session doit avoir vérifié qui l'appelle AVANT de lire.
    """
    async with get_sessionmaker()() as session:
        await limiter_aux_organisations(session, TOUT)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbPlateforme = Annotated[AsyncSession, Depends(get_db_plateforme)]
Config = Annotated[Settings, Depends(get_settings)]


def _aware(moment: datetime) -> datetime:
    """SQLite rend des datetimes naïfs ; PostgreSQL des datetimes en UTC. On compare en UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _principal_from_user(session: AsyncSession, user: User) -> Principal:
    # L'identité se résout en portée de PLATEFORME, puis la session est bornée plus bas. Sans cette
    # ligne, la jointure sur `projects` (sous RLS) ne voyait aucun projet sur PostgreSQL : une
    # appartenance DE PROJET arrivait avec `project_slug = None`, et devenait un rôle sur TOUTE
    # l'organisation — un développeur invité sur un projet lisait tous les autres. Invisible sur
    # SQLite, où la RLS n'existe pas ; trouvé en faisant tourner la suite entière sur PostgreSQL.
    await limiter_aux_organisations(session, TOUT)
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
    )
    # La session de CETTE requête ne voit que les organisations du principal : c'est ici
    # que la RLS s'arme, parce que c'est ici qu'on sait qui parle.
    orgs = set(org_roles) | {qualified.split("/", 1)[0] for qualified in project_roles}
    await limiter_aux_organisations(session, sorted(orgs))
    await nommer_l_utilisateur(session, user.id)
    return principal


async def current_principal(
    request: Request,
    session: Db,
    settings: Config,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Identité de l'appelant : cookie de session OIDC, ou jeton d'API porteur.

    Le jeton et l'utilisateur se lisent en portée de PLATEFORME : `api_tokens` et `users` sont sous
    RLS depuis la 0.11, et on ne sait pas encore qui parle. `_principal_from_user` borne ensuite.
    """
    await limiter_aux_organisations(session, TOUT)
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
    await _valider_la_session(session, user, payload)
    principal = await _principal_from_user(session, user)
    # L'heure d'authentification chez l'IdP (`auth_time`) quand la session la porte ; sinon l'heure
    # d'ouverture de la session (`iat`), qui la surestime après une reconnexion SSO silencieuse.
    quand = payload.get("auth_time", payload.get("iat"))
    principal.authentifie_le = int(quand) if isinstance(quand, int | float) else None
    return principal


async def _valider_la_session(session: AsyncSession, user: User, payload: dict[str, Any]) -> None:
    """Les validations déclarées par les greffons (`edition.declarer_une_validation_de_session`)."""
    import inspect

    from .edition import SessionRefusee, validations_de_session

    for validation in validations_de_session():
        try:
            resultat = validation(session, user, payload)
            if inspect.isawaitable(resultat):
                await resultat
        except SessionRefusee as refus:
            raise unauthorized(str(refus)) from refus


Me = Annotated[Principal, Depends(current_principal)]


async def exiger_admin_de_plateforme(session: AsyncSession, principal: Principal) -> None:
    """Administrer la PLATEFORME est un droit sur l'instance, pas sur une organisation.

    `Principal.is_platform_admin()` rend vrai dès qu'on est `org_admin` de N'IMPORTE QUELLE
    organisation. Sur une installation à une seule organisation — l'édition communautaire
    (ADR 0024) — c'est exact : l'administrateur de cette organisation EST l'administrateur de
    l'instance. Dès qu'il y en a deux, ça ne l'est plus, et le prédicat accordait alors les
    routes `/platform/*` — catalogue de backends, exécuteurs, profils de modèles, clés de
    passerelle — à l'administrateur de n'importe quel locataire.

    On exige donc d'être `org_admin` de TOUTES les organisations de l'instance. En mono-org, rien
    ne change. En multi-org, ce n'est vrai de personne tant que l'édition entreprise n'a pas
    défini un vrai rôle de plateforme : un refus, et c'est le bon défaut — mieux vaut une route
    inaccessible qu'une route ouverte au mauvais locataire.
    """
    from .db.models import Organization

    # Sur l'instance ENTIÈRE : sous la RLS de `organizations`, le compte fait depuis la portée d'un
    # administrateur d'une seule organisation vaudrait 1, et il passerait pour administrateur de tout.
    async with en_portee_de_plateforme(session):
        slugs = set((await session.execute(select(Organization.slug))).scalars().all())
    administrees = {org for org, role in principal.org_roles.items() if role == Role.ORG_ADMIN}
    if not slugs or not slugs <= administrees:
        if await _accorde_par_un_greffon(session, principal):
            return
        manquantes = sorted(slugs - administrees)
        raise forbidden(
            "réservé aux administrateurs de la plateforme : ce droit porte sur l'instance "
            f"entière, et il manque {manquantes or 'toute organisation'}."
        )


async def _accorde_par_un_greffon(session: AsyncSession, principal: Principal) -> bool:
    """`edition.declarer_un_administrateur_de_plateforme` : additive, elle ne retire rien."""
    import inspect

    from .edition import administrateurs_de_plateforme

    for autorite in administrateurs_de_plateforme():
        verdict = autorite(session, principal)
        if inspect.isawaitable(verdict):
            verdict = await verdict
        if verdict is True:
            return True
    return False


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
    bind(project=f"{org_slug}/{project.slug}")
    return context


ProjectCtx = Annotated[ProjectContext, Depends(project_context)]


async def run_claims(
    id: str,
    session: Db,
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
    bind(run_id=claims.run_id, project=claims.project_slug, work_item=claims.work_item_key)
    # Un jeton de run est frappé par la plateforme pour UN run : la portée est déjà dans
    # le jeton (`claims.run_id`, vérifié par chaque route), pas dans une organisation.
    await limiter_aux_organisations(session, TOUT)
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
