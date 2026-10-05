# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents de l'organisation (ADR 0033).

Un agent est un objet de l'organisation, interne ou externe ; ce qu'il EST vit dans ses versions,
qui ne se modifient jamais — un changement publie la suivante. Un projet épingle une version et ne
peut que la resserrer : un budget plus bas, moins d'outils. Une surcharge qui élargit reçoit 422.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated

from choregos_core import utcnow
from fastapi import APIRouter, Path, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Organization, ProjectAgent, User
from ..deps import Db, Me, ProjectCtx
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    AgentCreate,
    AgentDto,
    AgentOverrides,
    AgentPatch,
    AgentSpec,
    AgentVersionDto,
    ProjectAgentDto,
    ProjectAgentPut,
)
from ..services.agents import effective, elargissements, erreurs_d_une_version

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


def empreinte(spec: AgentSpec) -> str:
    """L'empreinte d'une version : ce qu'un run enregistre, et ce qu'on compare."""
    blob = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


async def _agent(session: AsyncSession, organisation: Organization, slug: str) -> Agent:
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == organisation.id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        raise not_found("Agent", f"{organisation.slug}/{slug}")
    return agent


async def _versions(session: AsyncSession, agent: Agent) -> list[AgentVersion]:
    lignes = await session.execute(
        select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc())
    )
    return list(lignes.scalars())


def _version_dto(ligne: AgentVersion) -> AgentVersionDto:
    return AgentVersionDto(
        version=ligne.version,
        spec=AgentSpec.model_validate(ligne.spec),
        checksum=ligne.checksum,
        created_by=ligne.created_by,
        created_at=ligne.created_at,
    )


async def _dto(session: AsyncSession, agent: Agent, *, avec_versions: bool = False) -> AgentDto:
    versions = await _versions(session, agent)
    proprietaire = await session.get(User, agent.owner_id) if agent.owner_id else None
    return AgentDto(
        slug=agent.slug,
        kind=agent.kind,
        display_name=agent.display_name,
        description=agent.description,
        status=agent.status,
        owner=proprietaire.email if proprietaire else None,
        expires_at=agent.expires_at,
        revoked_at=agent.revoked_at,
        latest_version=versions[0].version if versions else 0,
        created_at=agent.created_at,
        versions=[_version_dto(v) for v in versions] if avec_versions else None,
    )


def _lire(principal: Me, org: str) -> None:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"lire les agents de {org} demande d'en être membre")


def _gerer(principal: Me, org: str) -> None:
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("créer, publier ou révoquer un agent demande le rôle org_admin")


@router.get("/orgs/{org}/agents", response_model=list[AgentDto], operation_id="listAgents")
async def list_agents(org: str, session: Db, principal: Me) -> list[AgentDto]:
    _lire(principal, org)
    organisation = await _organisation(session, org)
    agents = (
        await session.execute(select(Agent).where(Agent.org_id == organisation.id).order_by(Agent.slug))
    ).scalars()
    return [await _dto(session, agent) for agent in agents]


@router.post(
    "/orgs/{org}/agents",
    response_model=AgentDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgent",
)
async def create_agent(org: str, body: AgentCreate, session: Db, principal: Me) -> AgentDto:
    _gerer(principal, org)
    erreurs_d_une_version(body.spec)
    organisation = await _organisation(session, org)
    existant = (
        await session.execute(
            select(Agent.id).where(Agent.org_id == organisation.id, Agent.slug == body.slug)
        )
    ).first()
    if existant is not None:
        raise conflict(f"l'agent `{body.slug}` existe déjà dans {org}")
    agent = Agent(
        org_id=organisation.id,
        slug=body.slug,
        kind=body.kind,
        display_name=body.display_name,
        description=body.description,
        owner_id=principal.user_id,
        status="active",
    )
    session.add(agent)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=agent.id,
            org_id=organisation.id,
            version=1,
            spec=body.spec.model_dump(mode="json"),
            checksum=empreinte(body.spec),
            created_by=principal.email,
        )
    )
    await session.flush()
    await record(
        session, principal, "agent.create", org_id=organisation.id, target_type="agent", target_id=agent.id
    )
    return await _dto(session, agent, avec_versions=True)


@router.get("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="getAgent")
async def get_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentDto:
    _lire(principal, org)
    return await _dto(
        session, await _agent(session, await _organisation(session, org), slug), avec_versions=True
    )


@router.post(
    "/orgs/{org}/agents/{slug}/versions",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAgentVersion",
)
async def publish_version(
    org: str, slug: Slug, body: AgentSpec, session: Db, principal: Me
) -> AgentVersionDto:
    """La version suivante ; aucune n'est jamais réécrite."""
    _gerer(principal, org)
    erreurs_d_une_version(body)
    organisation = await _organisation(session, org)
    agent = await _agent(session, organisation, slug)
    if agent.status == "revoked":
        raise conflict(f"l'agent `{slug}` est révoqué : il ne publie plus")
    derniere = (
        await session.execute(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id))
    ).scalar() or 0
    ligne = AgentVersion(
        agent_id=agent.id,
        org_id=organisation.id,
        version=derniere + 1,
        spec=body.model_dump(mode="json"),
        checksum=empreinte(body),
        created_by=principal.email,
    )
    session.add(ligne)
    await session.flush()
    await record(
        session,
        principal,
        "agent.publish",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        version=ligne.version,
    )
    return _version_dto(ligne)


@router.get(
    "/orgs/{org}/agents/{slug}/versions/{version}",
    response_model=AgentVersionDto,
    operation_id="getAgentVersion",
)
async def get_version(org: str, slug: Slug, version: int, session: Db, principal: Me) -> AgentVersionDto:
    _lire(principal, org)
    agent = await _agent(session, await _organisation(session, org), slug)
    ligne = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Version d'agent", f"{slug}@{version}")
    return _version_dto(ligne)


@router.patch("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="updateAgent")
async def update_agent(org: str, slug: Slug, body: AgentPatch, session: Db, principal: Me) -> AgentDto:
    """Son nom, sa description, son expiration, son état. `revoked` est définitif."""
    _gerer(principal, org)
    organisation = await _organisation(session, org)
    agent = await _agent(session, organisation, slug)
    if agent.status == "revoked" and body.status not in {None, "revoked"}:
        raise conflict(f"l'agent `{slug}` est révoqué : la révocation est définitive")
    for champ in ("display_name", "description", "expires_at"):
        if champ in body.model_fields_set:
            setattr(agent, champ, getattr(body, champ))
    if body.status is not None and body.status != agent.status:
        agent.status = body.status
        if body.status == "revoked":
            agent.revoked_at = utcnow()
    await record(
        session,
        principal,
        "agent.update",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        status=agent.status,
    )
    return await _dto(session, agent)


# ───────────────────────────── l'épingle d'un projet ─────────────────────────────


async def _version_de(session: AsyncSession, agent: Agent, version: int) -> AgentVersion:
    ligne = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Version d'agent", f"{agent.slug}@{version}")
    return ligne


async def _projet_dto(session: AsyncSession, epingle: ProjectAgent, agent: Agent) -> ProjectAgentDto:
    spec = AgentSpec.model_validate((await _version_de(session, agent, epingle.version)).spec)
    surcharges = AgentOverrides.model_validate(epingle.overrides or {})
    return ProjectAgentDto(
        agent=agent.slug, version=epingle.version, overrides=surcharges, effective=effective(spec, surcharges)
    )


@router.get("/projects/{id}/agents", response_model=list[ProjectAgentDto], operation_id="listProjectAgents")
async def list_project_agents(ctx: ProjectCtx, session: Db) -> list[ProjectAgentDto]:
    lignes = await session.execute(
        select(ProjectAgent, Agent)
        .join(Agent, Agent.id == ProjectAgent.agent_id)
        .where(ProjectAgent.project_id == ctx.project.id)
        .order_by(Agent.slug)
    )
    return [await _projet_dto(session, epingle, agent) for epingle, agent in lignes.tuples()]


@router.put("/projects/{id}/agents/{slug}", response_model=ProjectAgentDto, operation_id="pinProjectAgent")
async def pin_agent(slug: Slug, body: ProjectAgentPut, ctx: ProjectCtx, session: Db) -> ProjectAgentDto:
    """Épingle une version de l'agent sur le projet ; une surcharge qui élargit reçoit 422."""
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    agent = await _agent(session, organisation, slug)
    if agent.status != "active":
        raise conflict(f"l'agent `{slug}` est {agent.status} : il ne s'épingle pas")
    spec = AgentSpec.model_validate((await _version_de(session, agent, body.version)).spec)
    ecarts = elargissements(spec, body.overrides)
    if ecarts:
        raise unprocessable(
            "un projet ne fait que resserrer une version d'agent",
            [{"loc": ["overrides"], "msg": ecart} for ecart in ecarts],
        )
    epingle = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == ctx.project.id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if epingle is None:
        epingle = ProjectAgent(
            project_id=ctx.project.id, org_id=organisation.id, agent_id=agent.id, version=1
        )
        session.add(epingle)
    epingle.version = body.version
    epingle.overrides = body.overrides.model_dump(mode="json")
    await session.flush()
    await record(
        session,
        ctx.principal,
        "agent.pin",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        project=ctx.project.slug,
        version=body.version,
    )
    return await _projet_dto(session, epingle, agent)


@router.delete(
    "/projects/{id}/agents/{slug}", status_code=status.HTTP_204_NO_CONTENT, operation_id="unpinProjectAgent"
)
async def unpin_agent(slug: Slug, ctx: ProjectCtx, session: Db) -> Response:
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    agent = await _agent(session, organisation, slug)
    epingle = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == ctx.project.id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if epingle is None:
        raise not_found("Épingle", f"{slug} sur {ctx.project.slug}")
    await session.delete(epingle)
    await record(
        session,
        ctx.principal,
        "agent.unpin",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        project=ctx.project.slug,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
