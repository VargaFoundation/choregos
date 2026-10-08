# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents de l'organisation (ADR 0033).

Un agent est un objet de l'organisation, interne ou externe ; ce qu'il EST vit dans ses versions,
qui ne se modifient jamais — un changement publie la suivante. Un projet épingle une version et ne
peut que la resserrer : un budget plus bas, moins d'outils. Une surcharge qui élargit reçoit 422.
"""

from __future__ import annotations

from typing import Annotated

from choregos_core import utcnow
from fastapi import APIRouter, Path, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentCredential, AgentVersion, ApiToken, Organization, ProjectAgent, User
from ..deps import Config, Db, Me, ProjectCtx
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    AgentCatalogueConnect,
    AgentCatalogueConnection,
    AgentCatalogueEntry,
    AgentCatalogueInstall,
    AgentCreate,
    AgentCredentialCreate,
    AgentCredentialDto,
    AgentDto,
    AgentMetrics,
    AgentOverrides,
    AgentPatch,
    AgentProjectMetrics,
    AgentSpec,
    AgentVersionDto,
    ApiTokenCreated,
    ProjectAgentDto,
    ProjectAgentPut,
)
from ..services.agents import depense_du_jour, effective, elargissements, empreinte, erreurs_d_une_version

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


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


FINIS = {"succeeded", "failed", "timed_out", "cancelled"}


@router.get("/orgs/{org}/agents/{slug}/metrics", response_model=AgentMetrics, operation_id="getAgentMetrics")
async def agent_metrics(
    org: str, slug: Slug, session: Db, principal: Me, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> AgentMetrics:
    """Les runs de l'agent sur la période, leur issue, leur coût — modèles et outils —, par projet."""
    from datetime import timedelta

    from ..db.models import CostLedger, Project, Run

    _lire(principal, org)
    organisation = await _organisation(session, org)
    agent = await _agent(session, organisation, slug)
    depuis = utcnow() - timedelta(days=days)
    runs = (
        await session.execute(
            select(Run.id, Run.status, Project.slug)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, Run.created_at >= depuis)
        )
    ).all()
    couts = (
        await session.execute(
            select(CostLedger.kind, Project.slug, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, CostLedger.ts >= depuis)
            .group_by(CostLedger.kind, Project.slug)
        )
    ).all()
    reussis = sum(1 for _, statut, _ in runs if statut == "succeeded")
    termines = sum(1 for _, statut, _ in runs if statut in FINIS)
    par_sorte: dict[str, float] = {}
    par_projet: dict[str, list[float]] = {}
    for sorte, projet, montant in couts:
        par_sorte[sorte] = par_sorte.get(sorte, 0.0) + float(montant or 0.0)
        par_projet.setdefault(projet, [0, 0.0])[1] += float(montant or 0.0)
    for _, _, projet in runs:
        par_projet.setdefault(projet, [0, 0.0])[0] += 1
    derniere = (
        await session.execute(
            select(AgentVersion.spec)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return AgentMetrics(
        agent=slug,
        days=days,
        runs=len(runs),
        succeeded=reussis,
        failed=termines - reussis,
        success_rate=round(reussis / termines, 4) if termines else None,
        cost_usd=round(sum(par_sorte.values()), 6),
        cost_by_kind={k: round(v, 6) for k, v in par_sorte.items()},
        by_project=[
            AgentProjectMetrics(project=projet, runs=int(n), cost_usd=round(c, 6))
            for projet, (n, c) in sorted(par_projet.items())
        ],
        spent_today_usd=round(await depense_du_jour(session, organisation.id, slug), 6),
        daily_budget_usd=AgentSpec.model_validate(derniere or {}).budget.daily_usd,
    )


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


# ───────────────────────────── les agents externes ─────────────────────────────


def _credential_dto(ligne: AgentCredential, jeton: ApiToken | None = None) -> AgentCredentialDto:
    return AgentCredentialDto(
        id=ligne.id,
        kind=ligne.kind,
        token_id=ligne.api_token_id,
        client_id=ligne.client_id,
        created_by=ligne.created_by,
        created_at=ligne.created_at,
        revoked_at=ligne.revoked_at,
        token_name=jeton.name if jeton else None,
        last_used_at=jeton.last_used_at if jeton else None,
        last_client=jeton.last_client if jeton else None,
    )


@router.get(
    "/orgs/{org}/agents/{slug}/credentials",
    response_model=list[AgentCredentialDto],
    operation_id="listAgentCredentials",
)
async def list_credentials(org: str, slug: Slug, session: Db, principal: Me) -> list[AgentCredentialDto]:
    from ..db.session import en_portee_de_plateforme

    _gerer(principal, org)
    agent = await _agent(session, await _organisation(session, org), slug)
    lignes = list(
        (await session.execute(select(AgentCredential).where(AgentCredential.agent_id == agent.id))).scalars()
    )
    # Les jetons sont ceux de leurs humains, hors de la portée de l'organisation : lus comme au
    # rattachement, pour dire quand et avec quel client chacun a appelé la porte pour la dernière fois.
    ids = [ligne.api_token_id for ligne in lignes if ligne.api_token_id]
    jetons: dict[str, ApiToken] = {}
    if ids:
        async with en_portee_de_plateforme(session):
            trouves = await session.execute(select(ApiToken).where(ApiToken.id.in_(ids)))
            jetons = {jeton.id: jeton for jeton in trouves.scalars()}
    return [_credential_dto(ligne, jetons.get(ligne.api_token_id or "")) for ligne in lignes]


@router.post(
    "/orgs/{org}/agents/{slug}/credentials",
    response_model=AgentCredentialDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="attachAgentCredential",
)
async def attach_credential(
    org: str, slug: Slug, body: AgentCredentialCreate, session: Db, principal: Me
) -> AgentCredentialDto:
    """Rattache un client de la porte MCP à un agent `external` : ses appels portent alors l'agent,
    et ses droits sont ceux de l'humain intersectés avec ceux de la version de l'agent."""
    from ..db.session import en_portee_de_plateforme

    _gerer(principal, org)
    organisation = await _organisation(session, org)
    agent = await _agent(session, organisation, slug)
    if agent.kind != "external":
        raise unprocessable(f"`{slug}` est un agent interne : seul un agent `external` porte un client MCP")
    if body.kind == "token":
        if not body.token_id:
            raise unprocessable("`token_id` manque")
        async with en_portee_de_plateforme(session):
            jeton = await session.get(ApiToken, body.token_id)
        if jeton is None or not set(jeton.scopes or []) & {"mcp:read", "mcp:write"}:
            raise unprocessable("un jeton `mcp:read` ou `mcp:write` existant est attendu")
        existant = (
            await session.execute(select(AgentCredential.id).where(AgentCredential.api_token_id == jeton.id))
        ).first()
    else:
        if not body.client_id:
            raise unprocessable("`client_id` manque")
        existant = (
            await session.execute(
                select(AgentCredential.id).where(
                    AgentCredential.org_id == organisation.id, AgentCredential.client_id == body.client_id
                )
            )
        ).first()
    if existant is not None:
        raise conflict("ce client est déjà rattaché à un agent")
    ligne = AgentCredential(
        org_id=organisation.id,
        agent_id=agent.id,
        kind=body.kind,
        api_token_id=body.token_id if body.kind == "token" else None,
        client_id=body.client_id if body.kind == "oauth_client" else None,
        created_by=principal.email,
    )
    session.add(ligne)
    await session.flush()
    await record(
        session,
        principal,
        "agent.credential.attach",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
    )
    return _credential_dto(ligne)


@router.delete(
    "/orgs/{org}/agents/{slug}/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="detachAgentCredential",
)
async def detach_credential(org: str, slug: Slug, credential_id: str, session: Db, principal: Me) -> Response:
    _gerer(principal, org)
    agent = await _agent(session, await _organisation(session, org), slug)
    ligne = await session.get(AgentCredential, credential_id)
    if ligne is None or ligne.agent_id != agent.id:
        raise not_found("Client de l'agent", credential_id)
    await session.delete(ligne)
    await record(
        session,
        principal,
        "agent.credential.detach",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ───────────────────────────── le catalogue (ADR 0040) ─────────────────────────────


@router.get(
    "/orgs/{org}/agent-catalogue",
    response_model=list[AgentCatalogueEntry],
    operation_id="listAgentCatalogue",
)
async def list_agent_catalogue(
    org: str, session: Db, principal: Me, settings: Config
) -> list[AgentCatalogueEntry]:
    """Les agents que la plateforme propose, et ce qu'il en est dans l'organisation : installé, en
    quelle version, une mise à jour disponible — ou un agent de l'organisation qui porte ce nom."""
    from ..services import catalogue_d_agents as catalogue

    _lire(principal, org)
    organisation = await _organisation(session, org)
    rendu = []
    for proposition in catalogue.catalogue():
        etat = await catalogue.etat(session, organisation.id, proposition)
        offert, raison = catalogue.offre(proposition, settings)
        rendu.append(
            AgentCatalogueEntry(
                slug=proposition.slug,
                kind=proposition.genre,
                display_name=proposition.document["display_name"],
                description=proposition.document.get("description"),
                role=proposition.role,
                summary=proposition.resume,
                version=proposition.version,
                skills=list(proposition.skills),
                client=proposition.client,
                reach=proposition.portee,
                installed=etat.installe,
                installed_version=etat.version_installee,
                own_agent=etat.installe and not etat.du_catalogue,
                update_available=etat.mise_a_jour,
                offered=offert,
                unavailable_reason=raison,
            )
        )
    return rendu


@router.post(
    "/orgs/{org}/agent-catalogue/{slug}/install",
    response_model=AgentDto,
    operation_id="installCatalogueAgent",
)
async def install_catalogue_agent(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    response: Response,
    body: AgentCatalogueInstall | None = None,
) -> AgentDto:
    """Installe l'agent en version 1 (201) ; déjà installé, `upgrade` publie la version suivante si le
    catalogue a changé (200). Un agent de l'organisation du même nom n'est jamais touché (409)."""
    from choregos_core.catalogue_d_agents import entree

    from ..services import catalogue_d_agents as catalogue

    _gerer(principal, org)
    organisation = await _organisation(session, org)
    proposition = entree(slug)
    if proposition is None:
        raise not_found("Catalogue agent", slug)
    etat = await catalogue.etat(session, organisation.id, proposition)
    if not etat.installe:
        await catalogue.installer(session, organisation.id, proposition, principal)
        response.status_code = status.HTTP_201_CREATED
    elif not etat.du_catalogue:
        raise conflict(
            f"`{slug}` is an agent of {org} that does not come from the catalogue: it is left untouched"
        )
    elif not (body and body.upgrade):
        raise conflict(f"`{slug}` is already installed: ask for `upgrade` to publish the catalogue's version")
    else:
        await catalogue.mettre_a_jour(session, organisation.id, proposition, principal)
    return await _dto(session, await _agent(session, organisation, slug), avec_versions=True)


@router.post(
    "/orgs/{org}/agent-catalogue/{slug}/connect",
    response_model=AgentCatalogueConnection,
    status_code=status.HTTP_201_CREATED,
    operation_id="connectCatalogueClient",
)
async def connect_catalogue_client(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    settings: Config,
    body: AgentCatalogueConnect | None = None,
) -> AgentCatalogueConnection:
    """Connecte un client de la porte MCP en un clic (ADR 0040) : son agent externe — créé par un
    administrateur la première fois, rejoint ensuite par chaque membre —, puis un jeton `mcp:*`
    frappé pour la personne et rattaché à l'agent, rendu UNE fois. Pour un client qui appelle depuis
    le cloud de son éditeur, pas de jeton : le client OAuth que l'IdP a enregistré est rattaché."""
    from datetime import timedelta

    from choregos_core.catalogue_d_agents import entree

    from ..security import generate_api_token
    from ..services import catalogue_d_agents as catalogue
    from .auth import _token_dto

    corps = body or AgentCatalogueConnect()
    _lire(principal, org)
    if principal.kind != "user":
        raise forbidden("a client is connected by a person, from a session")
    proposition = entree(slug)
    if proposition is None or proposition.genre != "external":
        raise not_found("Catalogue client", slug)
    offert, raison = catalogue.offre(proposition, settings)
    if not offert:
        raise conflict(f"`{slug}` is not offered here: {raison}")
    organisation = await _organisation(session, org)
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == organisation.id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        _gerer(principal, org)
        await catalogue.installer(session, organisation.id, proposition, principal)
        agent = await _agent(session, organisation, slug)
    elif agent.kind != "external":
        raise conflict(f"`{slug}` is an internal agent of {org}: it cannot carry a client")
    jeton = None
    client_oauth = None
    if proposition.portee == "cloud":
        client_oauth = settings.mcp_oauth_clients[str(proposition.client)].client_id
        deja = (
            await session.execute(
                select(AgentCredential.id).where(
                    AgentCredential.org_id == organisation.id, AgentCredential.client_id == client_oauth
                )
            )
        ).first()
        if deja is None:
            session.add(AgentCredential(org_id=organisation.id, agent_id=agent.id, kind="oauth_client",
                                        client_id=client_oauth, created_by=principal.email))  # fmt: skip
    else:
        brut, empreinte_du_jeton = generate_api_token()
        ligne = ApiToken(
            user_id=principal.user_id,
            name=str(proposition.document["display_name"]),
            hash=empreinte_du_jeton,
            scopes=["mcp:read"] if corps.read_only else ["mcp:write"],
            expires_at=utcnow() + timedelta(days=corps.expires_in_days) if corps.expires_in_days else None,
        )
        session.add(ligne)
        await session.flush()
        session.add(AgentCredential(org_id=organisation.id, agent_id=agent.id, kind="token",
                                    api_token_id=ligne.id, created_by=principal.email))  # fmt: skip
        await record(session, principal, "token.create", org_id=None, target_type="api_token",
                     target_id=ligne.id, scopes=list(ligne.scopes or []))  # fmt: skip
        jeton = ApiTokenCreated(**_token_dto(ligne).model_dump(), token=brut)
    await session.flush()
    await record(session, principal, "agent.credential.attach", org_id=organisation.id, target_type="agent",
                 target_id=agent.id, via="catalogue")  # fmt: skip
    return AgentCatalogueConnection(
        agent=await _dto(session, agent, avec_versions=True), token=jeton, oauth_client_id=client_oauth
    )
