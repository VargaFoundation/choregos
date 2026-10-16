# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents de l'organisation (ADR 0033).

Un agent est un objet de l'organisation, interne ou externe ; ce qu'il EST vit dans ses versions,
qui ne se modifient jamais — un changement publie la suivante. Un projet épingle une version et ne
peut que la resserrer : un budget plus bas, moins d'outils. Une surcharge qui élargit reçoit 422.
"""

from __future__ import annotations

from typing import Annotated, Literal

from choregos_core import utcnow
from fastapi import APIRouter, Path, Query, Response, status
from pydantic import StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    Agent,
    AgentCredential,
    AgentVersion,
    ApiToken,
    Membership,
    Organization,
    Project,
    ProjectAgent,
    User,
    WorkflowDef,
)
from ..deps import Config, Db, Me, ProjectCtx
from ..errors import ApiError, conflict, forbidden, not_found, unprocessable, upstream
from ..rbac import Permission
from ..schemas import (
    AgentCatalogueConnect,
    AgentCatalogueConnection,
    AgentCatalogueEntry,
    AgentCatalogueInstall,
    AgentCreate,
    AgentCredentialCreate,
    AgentCredentialDto,
    AgentDraft,
    AgentDraftRequest,
    AgentDto,
    AgentFork,
    AgentLifecycleChange,
    AgentLifecycleResult,
    AgentMetrics,
    AgentMetricsSummary,
    AgentOverrides,
    AgentOwnerChange,
    AgentPatch,
    AgentProjectMetrics,
    AgentResolution,
    AgentScopeChange,
    AgentSpec,
    AgentTrial,
    AgentTrialCreate,
    AgentUpstream,
    AgentVersionDto,
    AgentVersionMetrics,
    AgentVersionPublish,
    AgentVersionRestore,
    ApiTokenCreated,
    ImplicitAgent,
    ProjectAgentDto,
    ProjectAgentPut,
    RunDto,
)
from ..schemas.agents import check_remote_kind
from ..services.agent_evals import evaluate, gate, suite_for
from ..services.agent_governance import (
    Action,
    Decision,
    Subject,
    decide,
    is_admin,
    is_member,
    permissions_of,
    subject_of,
    visible_clause,
)
from ..services.agent_lifecycle import (
    TransitionRefused,
    check_transition,
    last_run_at,
    retire,
    running_work_items,
    unused,
)
from ..services.agent_releases import after_publish, policy_of, production_spec, served
from ..services.agents import (
    AgentIndisponible,
    depense_du_jour,
    effective,
    elargissements,
    empreinte,
    erreurs_d_une_version,
    resoudre_l_agent,
)
from ..services.memory_access import check_bindings_at_publish
from ..services.network import check_agent_network
from ..services.remote_agents import pin_remote_runtime
from ..services.routage import nouveau_ticket_d_essai
from ..services.skill_review import unusable_skills

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
AgentRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,62}(@[1-9][0-9]*)?$")]


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


def _version_dto(
    ligne: AgentVersion, agent: Agent | None = None, pins: dict[int, list[str]] | None = None
) -> AgentVersionDto:
    channels: list[Literal["stable", "candidate"]] = []
    if agent is not None:
        if agent.stable_version == ligne.version:
            channels.append("stable")
        if agent.candidate_version == ligne.version:
            channels.append("candidate")
    return AgentVersionDto(
        version=ligne.version,
        spec=AgentSpec.model_validate(ligne.spec),
        checksum=ligne.checksum,
        created_by=ligne.created_by,
        created_at=ligne.created_at,
        message=ligne.message,
        restored_from=ligne.restored_from,
        pinned_by=sorted((pins or {}).get(ligne.version, [])),
        channels=channels,
    )


async def _pins(session: AsyncSession, principal: Me, org: str, agent: Agent) -> dict[int, list[str]]:
    """Version -> the slugs of the projects pinning it that the caller may see: an organisation admin
    sees them all, anyone else the projects they belong to (S40-12)."""
    rows = await session.execute(
        select(ProjectAgent.version, Project.slug)
        .join(Project, Project.id == ProjectAgent.project_id)
        .where(ProjectAgent.agent_id == agent.id)
    )
    pins: dict[int, list[str]] = {}
    for version, slug in rows:
        if is_admin(principal, org) or principal.role_for(org, slug) is not None:
            pins.setdefault(version, []).append(slug)
    return pins


async def _upstream(session: AsyncSession, agent: Agent, principal: Me, org: str) -> AgentUpstream | None:
    """What the fork was copied from; the source's state only when the caller may see it (ADR 0061)."""
    if agent.forked_from_slug is None or agent.forked_from_version is None:
        return None
    source = await session.get(Agent, agent.forked_from_agent_id) if agent.forked_from_agent_id else None
    if source is None or not decide(principal, org, await subject_of(session, source), Action.SEE).allowed:
        return AgentUpstream(slug=agent.forked_from_slug, version=agent.forked_from_version, available=False)
    derniere = (
        await session.execute(
            select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == source.id)
        )
    ).scalar() or 0
    return AgentUpstream(
        slug=source.slug,
        version=agent.forked_from_version,
        scope=source.scope,
        latest_version=derniere,
        behind=derniere > agent.forked_from_version,
    )


async def _dto(
    session: AsyncSession, agent: Agent, principal: Me, org: str, *, avec_versions: bool = False
) -> AgentDto:
    versions = await _versions(session, agent)
    proprietaire = await session.get(User, agent.owner_id) if agent.owner_id else None
    dernier = await last_run_at(session, agent.org_id, agent.slug)
    sujet = await subject_of(session, agent)
    pins = await _pins(session, principal, org, agent) if avec_versions else None
    droits, raison = permissions_of(principal, org, sujet)
    return AgentDto(
        scope=agent.scope,
        project=sujet.project_slug,
        forked_from=await _upstream(session, agent, principal, org),
        can=droits,
        cannot_reason=raison,
        slug=agent.slug,
        kind=agent.kind,
        display_name=agent.display_name,
        description=agent.description,
        tags=list(agent.tags or []),
        status=agent.status,
        owner=proprietaire.email if proprietaire else None,
        expires_at=agent.expires_at,
        revoked_at=agent.revoked_at,
        latest_version=versions[0].version if versions else 0,
        created_at=agent.created_at,
        versions=[_version_dto(v, agent, pins) for v in versions] if pins is not None else None,
        lifecycle=agent.lifecycle or "active",
        retired_at=agent.retired_at,
        last_run_at=dernier,
        unused=unused(agent, dernier),
        stable_version=agent.stable_version,
        candidate_version=agent.candidate_version,
        candidate_percent=agent.candidate_percent or 0,
        candidate_since=agent.candidate_since,
        release_policy=policy_of(agent),
    )


async def _skills_usable(session: AsyncSession, organisation: Organization, spec: AgentSpec) -> None:
    """A version may not carry a skill version that waits for its second reviewer (S32-06)."""
    problemes = await unusable_skills(session, organisation.id, list(spec.skills))
    if problemes:
        raise unprocessable("; ".join(problemes))


async def _model_allowed(session: AsyncSession, organisation: Organization, spec: AgentSpec) -> None:
    """A version may not name a model the organisation's catalogue does not allow (ADR 0060)."""
    from ..services.model_catalogue import load_policy, refusal_for_spec

    refusal = refusal_for_spec(await load_policy(session, organisation.id), spec)
    if refusal is not None:
        raise unprocessable(refusal, [{"loc": ["spec", "model"], "msg": refusal}])


def _lire(principal: Me, org: str) -> None:
    if not is_member(principal, org):
        raise forbidden(f"reading the agents of {org} requires being a member of it")


def _admin(principal: Me, org: str) -> None:
    if not is_admin(principal, org):
        raise forbidden("this needs the org_admin role")


def _exiger(decision: Decision) -> None:
    """A refusal says why: 403 for the caller's rights, 422 for a request that cannot be."""
    if decision.allowed:
        return
    reason = decision.reason or "insufficient rights"
    raise unprocessable(reason) if decision.unprocessable else forbidden(reason)


async def _charger(
    session: AsyncSession, principal: Me, org: str, slug: str
) -> tuple[Organization, Agent, Subject]:
    """The agent, for a caller who may see it: a personal agent of someone else, or a project's agent
    the caller does not belong to, is a 404 — never a 403 that would reveal it exists."""
    _lire(principal, org)
    organisation = await _organisation(session, org)
    agent = await _agent(session, organisation, slug)
    sujet = await subject_of(session, agent)
    if not decide(principal, org, sujet, Action.SEE).allowed:
        raise not_found("Agent", f"{org}/{slug}")
    return organisation, agent, sujet


async def _projet(session: AsyncSession, organisation: Organization, slug: str) -> Project:
    projet = (
        await session.execute(select(Project).where(Project.org_id == organisation.id, Project.slug == slug))
    ).scalar_one_or_none()
    if projet is None:
        raise not_found("Project", f"{organisation.slug}/{slug}")
    return projet


@router.get("/orgs/{org}/agents", response_model=list[AgentDto], operation_id="listAgents")
async def list_agents(
    org: str,
    session: Db,
    principal: Me,
    scope: Annotated[Literal["platform", "org", "project", "personal"] | None, Query()] = None,
    project: Annotated[str | None, Query()] = None,
    tag: Annotated[list[str] | None, Query(description="every tag given (AND)")] = None,
) -> list[AgentDto]:
    """The agents the caller may see (ADR 0061), optionally of one scope or one project, and carrying
    every tag given (S40-10; applied after visibility, so a tag never reveals an invisible agent)."""
    _lire(principal, org)
    organisation = await _organisation(session, org)
    requete = select(Agent).where(
        Agent.org_id == organisation.id, await visible_clause(session, principal, organisation)
    )
    if scope is not None:
        requete = requete.where(Agent.scope == scope)
    if project is not None:
        requete = requete.where(Agent.project_id == (await _projet(session, organisation, project)).id)
    agents = (await session.execute(requete.order_by(Agent.slug))).scalars()
    wanted = set(tag or [])
    return [await _dto(session, agent, principal, org) for agent in agents if wanted <= set(agent.tags or [])]


@router.post(
    "/orgs/{org}/agents",
    response_model=AgentDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgent",
)
async def create_agent(org: str, body: AgentCreate, session: Db, principal: Me) -> AgentDto:
    _exiger(decide(principal, org, None, Action.CREATE, project_slug=body.project, target_scope=body.scope))
    if body.kind == "external" and body.scope != "org":
        raise unprocessable("an external agent carries an organisation credential: it is an `org` agent")
    erreurs_d_une_version(body.spec)
    # Its own eval suite gates its version 1 (S32-03).
    gate(body.spec, None)
    organisation = await _organisation(session, org)
    await check_agent_network(session, organisation.id, body.spec)
    projet = await _projet(session, organisation, body.project) if body.project else None
    # A remote agent's card is read and pinned now (ADR 0062); any other spec comes back as it is.
    body.spec = await pin_remote_runtime(session, organisation.id, body.spec)
    await _skills_usable(session, organisation, body.spec)
    await _model_allowed(session, organisation, body.spec)
    await check_bindings_at_publish(
        session,
        principal,
        organisation,
        body.spec,
        project_slug=body.project if body.scope == "project" else None,
    )
    existant = (
        await session.execute(
            select(Agent.id).where(Agent.org_id == organisation.id, Agent.slug == body.slug)
        )
    ).first()
    if existant is not None:
        raise conflict(f"agent `{body.slug}` already exists in {org}")
    agent = Agent(
        org_id=organisation.id,
        slug=body.slug,
        kind=body.kind,
        display_name=body.display_name,
        description=body.description,
        tags=list(body.tags),
        owner_id=principal.user_id,
        status="active",
        lifecycle=body.lifecycle,
        scope=body.scope,
        project_id=projet.id if projet else None,
        release_policy=body.release_policy.model_dump(mode="json") if body.release_policy else None,
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
            message=(body.message or "").strip() or "Created",
        )
    )
    await session.flush()
    await after_publish(session, principal, agent, 1, principal.email)
    await record(
        session,
        principal,
        "agent.create",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        scope=agent.scope,
        project=body.project,
        tags=agent.tags,
    )
    return await _dto(session, agent, principal, org, avec_versions=True)


@router.post("/orgs/{org}/agent-drafts", response_model=AgentDraft, operation_id="draftAgent")
async def draft_agent(org: str, body: AgentDraftRequest, session: Db, principal: Me) -> AgentDraft:
    """One capped model call proposes an agent from its description (S39-05). Nothing is saved: the
    console fills the builder with the draft, and a person creates the agent."""
    from ..services import agent_drafts

    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("drafting an agent needs the agent:manage permission")
    admitted, wait = agent_drafts.limiter.admet(f"{org}:{principal.user_id}")
    if not admitted:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many drafts",
            f"more than {agent_drafts.PER_MINUTE} drafts per minute",
            retry_after=wait,
        )
    organisation = await _organisation(session, org)
    try:
        proposal = await agent_drafts.draft(session, organisation, body)
    except agent_drafts.DraftingError as exc:
        raise upstream("gateway", str(exc)) from exc
    await record(
        session,
        principal,
        "agent.draft",
        org_id=organisation.id,
        model=proposal.model,
        description_chars=len(body.description),
        notes=len(proposal.notes),
    )
    return proposal


@router.get("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="getAgent")
async def get_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentDto:
    _, agent, _ = await _charger(session, principal, org, slug)
    return await _dto(session, agent, principal, org, avec_versions=True)


async def _append_version(  # noqa: PLR0913 - the actors and the three ways a version is made
    session: AsyncSession,
    principal: Me,
    org: str,
    organisation: Organization,
    agent: Agent,
    sujet: Subject,
    spec: AgentSpec,
    *,
    message: str | None,
    restored_from: AgentVersion | None = None,
    base_version: int | None = None,
) -> AgentVersion:
    """The one way a version is appended (S40-12, ADR 0076): who may publish, the spec's own checks,
    the eval gate — and, when `base_version` is given, the two refusals that make a publication
    deliberate: the latest moved since the author read it (`stale_base`), or nothing changed
    (`no_change`). A restore carries the source's exact spec and checksum. Nothing is ever rewritten."""
    _exiger(decide(principal, org, sujet, Action.PUBLISH))
    erreurs_d_une_version(spec)
    await check_agent_network(session, organisation.id, spec)
    await _skills_usable(session, organisation, spec)
    await _model_allowed(session, organisation, spec)
    try:
        check_remote_kind(agent.kind, spec)
    except ValueError as erreur:
        raise unprocessable(str(erreur)) from erreur
    await check_bindings_at_publish(session, principal, organisation, spec, project_slug=sujet.project_slug)
    spec = await pin_remote_runtime(session, organisation.id, spec)
    if agent.status == "revoked":
        raise conflict(f"agent `{agent.slug}` is revoked: it can no longer publish")
    if agent.lifecycle == "retired":
        raise conflict(f"agent `{agent.slug}` is retired: it can no longer publish")
    versions = await _versions(session, agent)
    derniere = versions[0] if versions else None
    numero = derniere.version if derniere else 0
    checksum = restored_from.checksum if restored_from is not None else empreinte(spec)
    if base_version is not None:
        if base_version != numero:
            author = derniere.created_by if derniere and derniere.created_by else "someone"
            raise conflict(
                f"v{numero} of `{agent.slug}` was published by {author} "
                f"since you started from v{base_version}",
                extra={
                    "code": "stale_base",
                    "latest_version": numero,
                    "latest_author": derniere.created_by if derniere else None,
                },
            )
        if derniere is not None and checksum == derniere.checksum:
            raise conflict(
                f"nothing changed since v{numero}", extra={"code": "no_change", "latest_version": numero}
            )
    # The published version's eval suite, and the new one's, gate the publication (S32-03).
    gate(spec, AgentSpec.model_validate(derniere.spec) if derniere else None)
    ligne = AgentVersion(
        agent_id=agent.id,
        org_id=organisation.id,
        version=numero + 1,
        spec=restored_from.spec if restored_from is not None else spec.model_dump(mode="json"),
        checksum=checksum,
        created_by=principal.email,
        message=message,
        restored_from=restored_from.version if restored_from is not None else None,
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
        **({"restored_from": ligne.restored_from} if ligne.restored_from else {}),
        **({"message_chars": len(message)} if message else {}),
    )
    # What saving a revision does to the channels is the agent's policy (ADR 0071).
    await after_publish(session, principal, agent, ligne.version, principal.email)
    return ligne


@router.post(
    "/orgs/{org}/agents/{slug}/versions",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAgentVersion",
)
async def publish_version(
    org: str, slug: Slug, body: AgentSpec, session: Db, principal: Me
) -> AgentVersionDto:
    """The next version; none is ever rewritten. Superseded by `publishAgentVersionWithMessage`
    (a message, a base): kept as is for the clients written before, which publish with no message."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    ligne = await _append_version(session, principal, org, organisation, agent, sujet, body, message=None)
    return _version_dto(ligne, agent)


@router.post(
    "/orgs/{org}/agents/{slug}/versions/publish",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishAgentVersionWithMessage",
)
async def publish_version_with_message(
    org: str, slug: Slug, body: AgentVersionPublish, session: Db, principal: Me
) -> AgentVersionDto:
    """The next version, with the author's message, from the version they read: 409 `stale_base` when
    the latest moved meanwhile, 409 `no_change` when the spec equals the latest's (S40-12)."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    ligne = await _append_version(
        session,
        principal,
        org,
        organisation,
        agent,
        sujet,
        body.spec,
        message=body.message,
        base_version=body.base_version,
    )
    return _version_dto(ligne, agent)


@router.post(
    "/orgs/{org}/agents/{slug}/versions/{version}/restore",
    response_model=AgentVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="restoreAgentVersion",
)
async def restore_version(
    org: str, slug: Slug, version: int, body: AgentVersionRestore, session: Db, principal: Me
) -> AgentVersionDto:
    """Copy version N, byte for byte (same spec, same checksum), as the next version. It is a new
    version that remembers where it came from; nothing is rewritten or deleted (S40-12)."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    source = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if source is None:
        raise not_found("Agent version", f"{slug}@{version}")
    ligne = await _append_version(
        session,
        principal,
        org,
        organisation,
        agent,
        sujet,
        AgentSpec.model_validate(source.spec),
        message=body.message or f"Restore v{version}",
        restored_from=source,
        base_version=body.base_version,
    )
    return _version_dto(ligne, agent)


@router.get(
    "/orgs/{org}/agents/{slug}/versions/{version}",
    response_model=AgentVersionDto,
    operation_id="getAgentVersion",
)
async def get_version(org: str, slug: Slug, version: int, session: Db, principal: Me) -> AgentVersionDto:
    _, agent, _ = await _charger(session, principal, org, slug)
    ligne = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Agent version", f"{slug}@{version}")
    return _version_dto(ligne, agent, await _pins(session, principal, org, agent))


FINIS = {"succeeded", "failed", "timed_out", "cancelled"}


@router.get("/orgs/{org}/agents/{slug}/metrics", response_model=AgentMetrics, operation_id="getAgentMetrics")
async def agent_metrics(
    org: str, slug: Slug, session: Db, principal: Me, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> AgentMetrics:
    """Les runs de l'agent sur la période, leur issue, leur coût — modèles et outils —, par projet."""
    from datetime import timedelta

    from ..db.models import CostLedger, Project, Run

    organisation, agent, _ = await _charger(session, principal, org, slug)
    depuis = utcnow() - timedelta(days=days)
    runs = (
        await session.execute(
            select(Run.id, Run.status, Project.slug, Run.agent_version)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, Run.created_at >= depuis)
        )
    ).all()
    couts_par_version = (
        await session.execute(
            select(Run.agent_version, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id, CostLedger.ts >= depuis)
            .group_by(Run.agent_version)
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
    reussis = sum(1 for _, statut, _, _ in runs if statut == "succeeded")
    termines = sum(1 for _, statut, _, _ in runs if statut in FINIS)
    par_sorte: dict[str, float] = {}
    par_projet: dict[str, list[float]] = {}
    for sorte, projet, montant in couts:
        par_sorte[sorte] = par_sorte.get(sorte, 0.0) + float(montant or 0.0)
        par_projet.setdefault(projet, [0, 0.0])[1] += float(montant or 0.0)
    for _, _, projet, _ in runs:
        par_projet.setdefault(projet, [0, 0.0])[0] += 1
    par_version: dict[int, list[int]] = {}
    for _, statut, _, numero in runs:
        if numero is None:
            continue
        ligne_v = par_version.setdefault(int(numero), [0, 0, 0])
        if statut in FINIS:
            ligne_v[0] += 1
            ligne_v[1] += 1 if statut == "succeeded" else 0
        ligne_v[2] += 1
    cout_de_version = {int(v): float(c or 0.0) for v, c in couts_par_version if v is not None}
    # The budget shown is production's: it is the revision most runs use (ADR 0071).
    en_production = await production_spec(session, agent)
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
        daily_budget_usd=AgentSpec.model_validate(en_production or {}).budget.daily_usd,
        by_version=[
            AgentVersionMetrics(
                version=numero,
                runs=total_v,
                succeeded=reussis_v,
                failed=finis_v - reussis_v,
                success_rate=round(reussis_v / finis_v, 4) if finis_v else None,
                cost_usd=round(cout_de_version.get(numero, 0.0), 6),
            )
            for numero, (finis_v, reussis_v, total_v) in sorted(par_version.items())
        ],
    )


@router.get(
    "/orgs/{org}/agent-metrics", response_model=list[AgentMetricsSummary], operation_id="listAgentMetrics"
)
async def list_agent_metrics(
    org: str, session: Db, principal: Me, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> list[AgentMetricsSummary]:
    """The registry's columns for every agent at once (S30-04): runs, success rate, cost — two
    grouped queries instead of one call per agent."""
    from datetime import timedelta

    from ..db.models import CostLedger, Project, Run

    _lire(principal, org)
    organisation = await _organisation(session, org)
    depuis = utcnow() - timedelta(days=days)
    slugs = list(
        (
            await session.execute(
                select(Agent.slug)
                .where(
                    Agent.org_id == organisation.id, await visible_clause(session, principal, organisation)
                )
                .order_by(Agent.slug)
            )
        ).scalars()
    )
    runs = (
        await session.execute(
            select(Run.agent_slug, Run.status, func.count())
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug.in_(slugs), Project.org_id == organisation.id, Run.created_at >= depuis)
            .group_by(Run.agent_slug, Run.status)
        )
    ).all()
    couts = (
        await session.execute(
            select(Run.agent_slug, func.sum(CostLedger.cost_usd))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug.in_(slugs), Project.org_id == organisation.id, CostLedger.ts >= depuis)
            .group_by(Run.agent_slug)
        )
    ).all()
    total: dict[str, int] = {}
    reussis: dict[str, int] = {}
    termines: dict[str, int] = {}
    for slug_lu, statut, nombre in runs:
        cle = str(slug_lu)
        total[cle] = total.get(cle, 0) + int(nombre)
        if statut in FINIS:
            termines[cle] = termines.get(cle, 0) + int(nombre)
        if statut == "succeeded":
            reussis[cle] = reussis.get(cle, 0) + int(nombre)
    cout = {str(slug_lu): float(montant or 0.0) for slug_lu, montant in couts}
    return [
        AgentMetricsSummary(
            agent=slug,
            days=days,
            runs=total.get(slug, 0),
            success_rate=round(reussis.get(slug, 0) / termines[slug], 4) if termines.get(slug) else None,
            cost_usd=round(cout.get(slug, 0.0), 6),
        )
        for slug in slugs
    ]


@router.get("/orgs/{org}/agents/{slug}/runs", response_model=list[RunDto], operation_id="listAgentRuns")
async def list_agent_runs(
    org: str, slug: Slug, session: Db, principal: Me, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[RunDto]:
    """The agent's latest runs (S30-05): its page lists them in a tab, newest first, from the
    projects the caller may read only."""
    from ..db.models import Project, Run
    from ..services.tickets import run_dto

    organisation, _, _ = await _charger(session, principal, org, slug)
    lignes = (
        await session.execute(
            select(Run, Project.slug)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == slug, Project.org_id == organisation.id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        run_dto(run, projet) for run, projet in lignes if principal.can(Permission.PROJECT_READ, org, projet)
    ]


@router.patch("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="updateAgent")
async def update_agent(org: str, slug: Slug, body: AgentPatch, session: Db, principal: Me) -> AgentDto:
    """Son nom, sa description, son expiration, son état. `revoked` est définitif."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.PUBLISH))
    if body.status is not None:
        _exiger(decide(principal, org, sujet, Action.MANAGE))
    if agent.status == "revoked" and body.status not in {None, "revoked"}:
        raise conflict(f"agent `{slug}` is revoked: revocation is final")
    for champ in ("display_name", "description", "expires_at"):
        if champ in body.model_fields_set:
            setattr(agent, champ, getattr(body, champ))
    if body.tags is not None:
        agent.tags = list(body.tags)
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
        tags=agent.tags,
    )
    return await _dto(session, agent, principal, org)


# ───────────────────────────── its life (S32-04) ─────────────────────────────


@router.post(
    "/orgs/{org}/agents/{slug}/lifecycle",
    response_model=AgentLifecycleResult,
    operation_id="changeAgentLifecycle",
)
async def change_agent_lifecycle(
    org: str, slug: Slug, body: AgentLifecycleChange, session: Db, principal: Me
) -> AgentLifecycleResult:
    """Activate a draft, deprecate, reactivate, or retire. Retiring runs its checklist — credentials
    revoked, project pins removed, new runs refused — and is refused while a run of the agent is in
    flight, unless `force` (audited)."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.PUBLISH))
    avant = agent.lifecycle or "active"
    try:
        check_transition(avant, body.to)
    except TransitionRefused as refus:
        raise conflict(str(refus)) from refus
    resultat = AgentLifecycleResult(agent=await _dto(session, agent, principal, org), forced=body.force)
    if body.to == "retired":
        en_vol = await running_work_items(session, organisation.id, slug)
        if en_vol and not body.force:
            raise conflict(
                f"agent `{slug}` has runs in flight on {', '.join(en_vol)}: let them finish, or force "
                "the retirement"
            )
        fait = await retire(session, agent)
        resultat.revoked_credentials = fait["revoked_credentials"]
        resultat.unpinned_projects = fait["unpinned_projects"]
        resultat.running_work_items = en_vol
    else:
        agent.lifecycle = body.to
        await session.flush()
    await record(
        session,
        principal,
        f"agent.{'retire' if body.to == 'retired' else 'lifecycle'}",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=avant,
        after=agent.lifecycle,
        reason=body.reason,
        forced=body.force if body.to == "retired" else None,
        revoked_credentials=resultat.revoked_credentials,
        unpinned_projects=resultat.unpinned_projects,
        running_work_items=resultat.running_work_items,
    )
    resultat.agent = await _dto(session, agent, principal, org)
    return resultat


@router.post(
    "/orgs/{org}/agents/{slug}/owner", response_model=AgentDto, operation_id="transferAgentOwnership"
)
async def transfer_agent_ownership(
    org: str, slug: Slug, body: AgentOwnerChange, session: Db, principal: Me
) -> AgentDto:
    """A new owner, a member of the organisation. The agent keeps its credentials: they belong to
    the agent, not to the person who owned it."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.MANAGE))
    if agent.lifecycle == "retired":
        raise conflict(f"agent `{slug}` is retired: it has no owner to change")
    nouveau = (
        await session.execute(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    ).scalar_one_or_none()
    membre = (
        nouveau is not None
        and (
            await session.execute(
                select(Membership.id).where(
                    Membership.user_id == nouveau.id, Membership.org_id == organisation.id
                )
            )
        ).first()
        is not None
    )
    if nouveau is None or not membre:
        raise unprocessable(f"`{body.email}` is not a member of {org}: the owner must be one")
    ancien = await session.get(User, agent.owner_id) if agent.owner_id else None
    agent.owner_id = nouveau.id
    await session.flush()
    accreditations = (
        await session.execute(
            select(func.count(AgentCredential.id)).where(
                AgentCredential.agent_id == agent.id, AgentCredential.revoked_at.is_(None)
            )
        )
    ).scalar_one()
    await record(
        session,
        principal,
        "agent.transfer",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=ancien.email if ancien else None,
        after=nouveau.email,
        credentials_kept=int(accreditations),
    )
    return await _dto(session, agent, principal, org)


# ───────────────────────────── forks and promotion (ADR 0061) ─────────────────────────────


@router.post(
    "/orgs/{org}/agents/{slug}/fork",
    response_model=AgentDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="forkAgent",
)
async def fork_agent(org: str, slug: Slug, body: AgentFork, session: Db, principal: Me) -> AgentDto:
    """A new agent, in the scope asked, whose version 1 is a byte-for-byte copy of one of the source's
    versions (its latest by default) — same spec, same checksum. It records where it came from and
    inherits nothing else: no pin, no client, no metrics. The source is never touched."""
    organisation, source, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.FORK, project_slug=body.project, target_scope=body.scope))
    projet = await _projet(session, organisation, body.project) if body.project else None
    versions = await _versions(session, source)
    if body.version is None:
        copiee = versions[0] if versions else None
    else:
        copiee = next((v for v in versions if v.version == body.version), None)
    if copiee is None:
        raise not_found("Agent version", f"{slug}@{body.version or 'latest'}")
    # The copy binds the same memories: the forker must see the global ones (S40-11).
    await check_bindings_at_publish(
        session,
        principal,
        organisation,
        AgentSpec.model_validate(copiee.spec),
        project_slug=body.project if body.scope == "project" else None,
    )
    if (
        await session.execute(
            select(Agent.id).where(Agent.org_id == organisation.id, Agent.slug == body.slug)
        )
    ).first() is not None:
        raise conflict(f"agent `{body.slug}` already exists in {org}")
    fork = Agent(
        org_id=organisation.id,
        slug=body.slug,
        kind=source.kind,
        display_name=body.display_name or f"{source.display_name} (fork)",
        description=source.description,
        tags=list(source.tags or []),
        owner_id=principal.user_id,
        status="active",
        scope=body.scope,
        project_id=projet.id if projet else None,
        forked_from_agent_id=source.id,
        forked_from_slug=source.slug,
        forked_from_version=copiee.version,
    )
    session.add(fork)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=fork.id,
            org_id=organisation.id,
            version=1,
            spec=copiee.spec,
            checksum=copiee.checksum,
            created_by=principal.email,
            message=(body.message or "").strip() or f"Forked from {source.slug}@{copiee.version}",
        )
    )
    await session.flush()
    await after_publish(session, principal, fork, 1, principal.email)
    await record(
        session,
        principal,
        "agent.fork",
        org_id=organisation.id,
        target_type="agent",
        target_id=fork.id,
        source=source.slug,
        source_version=copiee.version,
        scope=fork.scope,
        project=body.project,
    )
    return await _dto(session, fork, principal, org, avec_versions=True)


async def _named_outside(session: AsyncSession, agent: Agent, project: Project) -> list[str]:
    """The other projects of the organisation that pin the agent or name it in an active workflow."""
    dehors: set[str] = set()
    epingles = await session.execute(
        select(Project.slug)
        .join(ProjectAgent, ProjectAgent.project_id == Project.id)
        .where(ProjectAgent.agent_id == agent.id, Project.id != project.id)
    )
    dehors.update(epingles.scalars())
    workflows = await session.execute(
        select(Project.slug, WorkflowDef.json_doc)
        .join(Project, Project.id == WorkflowDef.project_id)
        .where(Project.org_id == agent.org_id, Project.id != project.id, WorkflowDef.is_active.is_(True))
    )
    for slug_du_projet, document in workflows.all():
        for acteur in ((document or {}).get("actors") or {}).values():
            nomme = str(acteur.get("agent") or "") if isinstance(acteur, dict) else ""
            if nomme.split("@")[0] == agent.slug:
                dehors.add(slug_du_projet)
    return sorted(dehors)


@router.post("/orgs/{org}/agents/{slug}/scope", response_model=AgentDto, operation_id="changeAgentScope")
async def change_agent_scope(
    org: str, slug: Slug, body: AgentScopeChange, session: Db, principal: Me
) -> AgentDto:
    """Promote an agent (ADR 0061): a personal agent to a project (its owner AND that project's owner,
    or an org_admin) or to the organisation (an org_admin); a project's agent to the organisation; an
    organisation agent into one project (an org_admin; refused while another project pins or names
    it). A common agent never moves."""
    organisation, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.MOVE, project_slug=body.project, target_scope=body.scope))
    projet = await _projet(session, organisation, body.project) if body.project else None
    if projet is not None:
        dehors = await _named_outside(session, agent, projet)
        if dehors:
            raise conflict(
                f"`{slug}` is pinned or named by {', '.join(dehors)}: it would stop running there. Fork it "
                "into those projects, or unpin it, first"
            )
    avant = agent.scope
    agent.scope = body.scope
    agent.project_id = projet.id if projet else None
    await session.flush()
    await record(
        session,
        principal,
        "agent.scope",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=avant,
        after=agent.scope,
        project=body.project,
    )
    return await _dto(session, agent, principal, org)


# ───────────────────────────── l'épingle d'un projet ─────────────────────────────


async def _version_de(session: AsyncSession, agent: Agent, version: int) -> AgentVersion:
    ligne = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Agent version", f"{agent.slug}@{version}")
    return ligne


async def _latest(session: AsyncSession, agent: Agent) -> int:
    return (
        await session.execute(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id))
    ).scalar() or 1


async def _served_version(session: AsyncSession, agent: Agent, epingle: ProjectAgent | None) -> int:
    """The revision a pin serves now: its fixed one, or the one of the channel it follows."""
    version, _, _ = served(agent, pin=epingle, project_channel="stable", work_item_id=None)
    return version if version is not None else await _latest(session, agent)


async def _projet_dto(session: AsyncSession, epingle: ProjectAgent, agent: Agent) -> ProjectAgentDto:
    version = await _served_version(session, agent, epingle)
    spec = AgentSpec.model_validate((await _version_de(session, agent, version)).spec)
    surcharges = AgentOverrides.model_validate(epingle.overrides or {})
    return ProjectAgentDto(
        agent=agent.slug,
        version=version,
        channel=epingle.channel,
        overrides=surcharges,
        effective=effective(spec, surcharges),
        warning=(
            f"agent `{agent.slug}` is deprecated: it still runs, but plan to replace it"
            if agent.lifecycle == "deprecated"
            else None
        ),
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


@router.get(
    "/projects/{id}/implicit-agents", response_model=list[ImplicitAgent], operation_id="listImplicitAgents"
)
async def list_implicit_agents(ctx: ProjectCtx, session: Db) -> list[ImplicitAgent]:
    """The `agent` actors of the project's active workflows that name no agent of the registry
    (S30-04): the console used to read every workflow, one request each, to find them."""
    registre = set(
        (await session.execute(select(Agent.slug).where(Agent.org_id == ctx.project.org_id))).scalars()
    )
    workflows = (
        await session.execute(
            select(WorkflowDef.name, WorkflowDef.json_doc)
            .where(WorkflowDef.project_id == ctx.project.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.name)
        )
    ).all()
    trouves: list[ImplicitAgent] = []
    for nom, document in workflows:
        acteurs = (document or {}).get("actors") or {}
        for acteur, definition in acteurs.items():
            if not isinstance(definition, dict) or definition.get("type") != "agent":
                continue
            nomme = str(definition.get("agent") or "").split("@")[0]
            if nomme and nomme in registre:
                continue
            trouves.append(
                ImplicitAgent(
                    workflow=nom,
                    actor=acteur,
                    role=definition.get("role"),
                    model=definition.get("model"),
                    backend=definition.get("backend"),
                    max_turns=definition.get("max_turns"),
                    max_minutes=definition.get("max_minutes"),
                )
            )
    return trouves


@router.get(
    "/projects/{id}/agent-resolution",
    response_model=list[AgentResolution],
    operation_id="resolveProjectAgents",
)
async def resolve_project_agents(
    ctx: ProjectCtx, session: Db, ref: Annotated[list[AgentRef], Query(min_length=1, max_length=64)]
) -> list[AgentResolution]:
    """Which agent version a run of this project would load for each reference (S39-13).

    It delegates to the runtime's own resolver, so the console shows exactly what a run loads: an
    explicit `@N`, else the project's pin, else the project's channel, else production (ADR 0071).
    A canary share depends on the work item, so it is reported (`candidate_percent`), not applied.
    Read only, no audit record."""
    from choregos_core.catalogue_d_agents import entree

    reponses: list[AgentResolution] = []
    for reference in ref:
        slug = reference.partition("@")[0]
        try:
            resolu = await resoudre_l_agent(session, ctx.project.org_id, ctx.project.id, reference)
        except AgentIndisponible as erreur:
            agent = (
                await session.execute(
                    select(Agent).where(Agent.org_id == ctx.project.org_id, Agent.slug == slug)
                )
            ).scalar_one_or_none()
            if agent is None:
                reponses.append(
                    AgentResolution(
                        ref=reference,
                        slug=slug,
                        status="not_installed",
                        in_catalogue=entree(slug) is not None,
                        reason=str(erreur),
                    )
                )
            else:
                reponses.append(
                    AgentResolution(
                        ref=reference,
                        slug=slug,
                        status="unavailable",
                        display_name=agent.display_name,
                        lifecycle=agent.lifecycle,
                        reason=str(erreur),
                    )
                )
            continue
        reponses.append(
            AgentResolution(
                ref=reference,
                slug=slug,
                status="resolved",
                version=resolu.version,
                pinned=resolu.pinned,
                source=resolu.source,
                channel=resolu.channel,
                candidate_version=resolu.agent.candidate_version,
                candidate_percent=(
                    resolu.agent.candidate_percent if resolu.agent.candidate_version is not None else None
                ),
                display_name=resolu.agent.display_name,
                lifecycle=resolu.agent.lifecycle,
            )
        )
    return reponses


@router.put("/projects/{id}/agents/{slug}", response_model=ProjectAgentDto, operation_id="pinProjectAgent")
async def pin_agent(slug: Slug, body: ProjectAgentPut, ctx: ProjectCtx, session: Db) -> ProjectAgentDto:
    """Épingle une version de l'agent sur le projet ; une surcharge qui élargit reçoit 422."""
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    agent = await _agent(session, organisation, slug)
    sujet = await subject_of(session, agent)
    if not decide(ctx.principal, organisation.slug, sujet, Action.SEE).allowed:
        raise not_found("Agent", f"{organisation.slug}/{slug}")
    _exiger(decide(ctx.principal, organisation.slug, sujet, Action.PIN, project_slug=ctx.project.slug))
    if agent.status != "active":
        raise conflict(f"agent `{slug}` is {agent.status}: it cannot be pinned")
    if agent.lifecycle in {"draft", "retired"}:
        raise conflict(f"agent `{slug}` is {agent.lifecycle}: it cannot be pinned")
    if body.version is not None:
        pinned_version = body.version
    elif body.channel == "candidate" and agent.candidate_version is not None:
        pinned_version = agent.candidate_version
    else:
        pinned_version = agent.stable_version or await _latest(session, agent)
    spec = AgentSpec.model_validate((await _version_de(session, agent, pinned_version)).spec)
    ecarts = elargissements(spec, body.overrides)
    if ecarts:
        raise unprocessable(
            "a project can only narrow an agent version, never widen it",
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
    epingle.channel = body.channel
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
        channel=body.channel,
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
        raise not_found("Pin", f"{slug} on {ctx.project.slug}")
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


#: The most a trial may spend, whatever is asked (S32-03).
TRIAL_CAP_USD = 0.50
TRIAL_DEFAULT_USD = 0.25


@router.post(
    "/orgs/{org}/agent-trials",
    response_model=AgentTrial,
    status_code=status.HTTP_201_CREATED,
    operation_id="startAgentTrial",
)
async def start_agent_trial(org: str, body: AgentTrialCreate, session: Db, principal: Me) -> AgentTrial:
    """Run a draft version once on a sample input, without publishing it (S32-03).

    The run machinery is the usual one — the orchestrator's `AgentTrial` prepares, starts and awaits
    one stage — on a trial work item that no workflow, tracker or board sees. The budget is capped at
    0.50 $ and at the draft's budget per run; the orchestrator tightens it further to the project's
    budget for the role (policy). The gateway key is minted at that amount: the trial cannot spend more.
    """
    from ..temporal import get_temporal, trial_run_id, trial_workflow_id

    _lire(principal, org)
    if not principal.can(Permission.PROJECT_READ, org, body.project):
        raise forbidden(f"trying an agent in {org}/{body.project} requires being a member of it")
    organisation = await _organisation(session, org)
    sujet = None
    if body.agent:
        _, _, sujet = await _charger(session, principal, org, body.agent)
    _exiger(decide(principal, org, sujet, Action.TRIAL, project_slug=body.project))
    erreurs_d_une_version(body.spec)
    await _model_allowed(session, organisation, body.spec)
    await check_bindings_at_publish(session, principal, organisation, body.spec)
    await check_agent_network(session, organisation.id, body.spec)
    projet = await _projet(session, organisation, body.project)
    publiee = None
    if body.agent:
        agent = await _agent(session, organisation, body.agent)
        en_production = await production_spec(session, agent)
        publiee = AgentSpec.model_validate(en_production) if en_production is not None else None
    plafonds = [TRIAL_CAP_USD, body.budget_usd or TRIAL_DEFAULT_USD]
    if body.spec.budget.run_usd is not None:
        plafonds.append(body.spec.budget.run_usd)
    budget = round(min(plafonds), 4)
    item = await nouveau_ticket_d_essai(
        session,
        projet,
        title=body.input.title or "Trial",
        body=body.input.body,
        created_by=principal.email,
    )
    await get_temporal().start_trial(
        trial_workflow_id(item.id),
        {
            "project_id": projet.id,
            "work_item_id": item.id,
            "spec": body.spec.model_dump(mode="json"),
            "budget_usd": budget,
        },
    )
    item.temporal_wf_id = trial_workflow_id(item.id)
    await record(
        session,
        principal,
        "agent.trial",
        org_id=organisation.id,
        target_type="work_item",
        target_id=item.id,
        project=body.project,
        budget_usd=budget,
    )
    return AgentTrial(
        run_id=trial_run_id(item.id),
        work_item_id=item.id,
        project=body.project,
        budget_usd=budget,
        status="queued",
        evals=evaluate(body.spec, suite_for(body.spec, publiee)),
    )


@router.get(
    "/orgs/{org}/agent-credentials",
    response_model=list[AgentCredentialDto],
    operation_id="listOrgAgentCredentials",
)
async def list_org_credentials(org: str, session: Db, principal: Me) -> list[AgentCredentialDto]:
    """Every client attached to an agent of the organisation, with its agent (S30-04): the AI
    clients' page read one list per external agent."""
    from ..db.session import en_portee_de_plateforme

    _admin(principal, org)
    organisation = await _organisation(session, org)
    lignes = (
        await session.execute(
            select(AgentCredential, Agent.slug)
            .join(Agent, Agent.id == AgentCredential.agent_id)
            .where(AgentCredential.org_id == organisation.id)
            .order_by(Agent.slug)
        )
    ).all()
    ids = [ligne.api_token_id for ligne, _ in lignes if ligne.api_token_id]
    jetons: dict[str, ApiToken] = {}
    if ids:
        async with en_portee_de_plateforme(session):
            trouves = await session.execute(select(ApiToken).where(ApiToken.id.in_(ids)))
            jetons = {jeton.id: jeton for jeton in trouves.scalars()}
    return [
        _credential_dto(ligne, jetons.get(ligne.api_token_id or "")).model_copy(update={"agent": slug})
        for ligne, slug in lignes
    ]


@router.get(
    "/orgs/{org}/agents/{slug}/credentials",
    response_model=list[AgentCredentialDto],
    operation_id="listAgentCredentials",
)
async def list_credentials(org: str, slug: Slug, session: Db, principal: Me) -> list[AgentCredentialDto]:
    from ..db.session import en_portee_de_plateforme

    _, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.MANAGE))
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

    organisation, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.MANAGE))
    if agent.kind != "external":
        raise unprocessable(f"`{slug}` is an internal agent: only an `external` agent carries an MCP client")
    if body.kind == "token":
        if not body.token_id:
            raise unprocessable("`token_id` is missing")
        async with en_portee_de_plateforme(session):
            jeton = await session.get(ApiToken, body.token_id)
        if jeton is None or not set(jeton.scopes or []) & {"mcp:read", "mcp:write"}:
            raise unprocessable("an existing `mcp:read` or `mcp:write` token is expected")
        existant = (
            await session.execute(select(AgentCredential.id).where(AgentCredential.api_token_id == jeton.id))
        ).first()
    else:
        if not body.client_id:
            raise unprocessable("`client_id` is missing")
        existant = (
            await session.execute(
                select(AgentCredential.id).where(
                    AgentCredential.org_id == organisation.id, AgentCredential.client_id == body.client_id
                )
            )
        ).first()
    if existant is not None:
        raise conflict("this client is already attached to an agent")
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
    _, agent, sujet = await _charger(session, principal, org, slug)
    _exiger(decide(principal, org, sujet, Action.MANAGE))
    ligne = await session.get(AgentCredential, credential_id)
    if ligne is None or ligne.agent_id != agent.id:
        raise not_found("Agent client", credential_id)
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
                tags=list(proposition.tags),
                client=proposition.client,
                reach=proposition.portee,
                installed=etat.installe,
                installed_version=etat.version_installee,
                own_agent=etat.installe and not etat.du_catalogue,
                scope=etat.scope,
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

    _admin(principal, org)
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
    return await _dto(session, await _agent(session, organisation, slug), principal, org, avec_versions=True)


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
        _admin(principal, org)
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
        agent=await _dto(session, agent, principal, org, avec_versions=True),
        token=jeton,
        oauth_client_id=client_oauth,
    )
