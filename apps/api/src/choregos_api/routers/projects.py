"""Projets : création, configuration, provisioning (SSE), suspension."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import EventType, Role
from choregos_core import utcnow
from fastapi import APIRouter, Path, Request, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from ..audit import record
from ..db.models import Membership, Organization, Project
from ..deps import Db, Me, Pagination, ProjectCtx
from ..errors import conflict, forbidden, not_found
from ..rbac import Permission
from ..schemas import (
    OrgCreate,
    OrgDto,
    PageMeta,
    ProjectCreate,
    ProjectDto,
    ProjectPage,
    ProjectUpdate,
    ProvisionRequest,
    ProvisionStatus,
    ProvisionStep,
)
from ..services import ensure_defaults, persist_event, project_dto
from ..temporal import get_temporal, provisioning_id

router = APIRouter(tags=["projects"])


@router.get("/orgs", response_model=list[OrgDto], operation_id="listOrgs")
async def list_orgs(session: Db, principal: Me) -> list[OrgDto]:
    """Les organisations de l'appelant, avec son rôle — ce que le front affiche dans son sélecteur."""
    slugs = set(principal.org_roles) | {q.split("/", 1)[0] for q in principal.project_roles}
    rows = (await session.execute(select(Organization).where(Organization.slug.in_(slugs)))).scalars()
    return sorted(
        (OrgDto(slug=o.slug, name=o.name, role=principal.org_roles.get(o.slug)) for o in rows),
        key=lambda o: o.slug,
    )


@router.post("/orgs", response_model=OrgDto, status_code=status.HTTP_201_CREATED, operation_id="createOrg")
async def create_org(body: OrgCreate, session: Db, principal: Me) -> OrgDto:
    """Crée une organisation ; l'appelant en devient administrateur.

    Réservé à qui administre déjà une organisation. La toute première vient de l'amorçage
    (`CHOREGOS_BOOTSTRAP_ORG`) : avant le 2026-09-24, aucune route, aucune commande ni
    aucun écran ne savait en créer une.
    """
    if not principal.is_platform_admin():
        raise forbidden("créer une organisation demande le rôle org_admin sur une organisation")
    if (
        await session.execute(select(Organization).where(Organization.slug == body.slug))
    ).scalar_one_or_none():
        raise conflict(f"l'organisation `{body.slug}` existe déjà")
    org = Organization(slug=body.slug, name=body.name)
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=principal.user_id, org_id=org.id, role=str(Role.ORG_ADMIN)))
    await record(
        session, principal, "org.create", org_id=org.id, target_type="org", target_id=org.id, slug=body.slug
    )
    return OrgDto(slug=org.slug, name=org.name, role=Role.ORG_ADMIN)


async def _org(session: Any, slug: str) -> Organization:
    row = (await session.execute(select(Organization).where(Organization.slug == slug))).scalar_one_or_none()
    if not isinstance(row, Organization):
        raise not_found("Organisation", slug)
    return row


@router.get("/orgs/{org}/projects", response_model=ProjectPage, operation_id="listProjects")
async def list_projects(org: str, session: Db, principal: Me, page: Pagination) -> ProjectPage:
    organization = await _org(session, org)
    rows = (
        (
            await session.execute(
                select(Project).where(Project.org_id == organization.id).order_by(Project.slug)
            )
        )
        .scalars()
        .all()
    )
    visible = [p for p in rows if principal.can(Permission.PROJECT_READ, org, p.slug)]
    window, cursor, has_more = page.slice(list(visible))
    return ProjectPage(
        items=[await project_dto(session, p, org) for p in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


@router.post(
    "/orgs/{org}/projects",
    response_model=ProjectDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
async def create_project(org: str, body: ProjectCreate, session: Db, principal: Me) -> ProjectDto:
    organization = await _org(session, org)
    if not principal.can(Permission.PROJECT_CREATE, org):
        raise forbidden("créer un projet demande le rôle org_admin")
    existing = (
        await session.execute(
            select(Project).where(Project.org_id == organization.id, Project.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(f"le projet `{body.slug}` existe déjà dans {org}")
    project = Project(
        org_id=organization.id,
        slug=body.slug,
        name=body.name,
        template_ref=body.template_ref,
        config=body.config.model_dump(mode="json", exclude_none=True),
        status="draft",
        provision_state={"status": "pending", "steps": [], "inputs": body.inputs},
    )
    session.add(project)
    await session.flush()
    await ensure_defaults(session, project)
    session.add(
        Membership(
            user_id=principal.user_id,
            org_id=organization.id,
            project_id=project.id,
            role=str(Role.PROJECT_OWNER),
        )
    )
    await record(
        session,
        principal,
        "project.create",
        org_id=project.org_id,
        target_type="project",
        target_id=project.id,
        slug=body.slug,
    )
    return await project_dto(session, project, org)


@router.get("/projects/{id}", response_model=ProjectDto, operation_id="getProject")
async def get_project(ctx: ProjectCtx, session: Db) -> ProjectDto:
    return await project_dto(session, ctx.project, ctx.org_slug)


@router.get("/projects/{id}/tools", operation_id="getProjectTools")
async def get_project_tools(ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    """Le catalogue du déploiement, et ce que CE projet a le droit d'appeler.

    Les deux ensembles, pas seulement le second : sans la liste complète, on ne peut ni
    savoir ce qu'on pourrait autoriser, ni comprendre pourquoi un outil manque.
    """
    from ..catalogue import catalogue

    config = ctx.project.config or {}
    autorises = {str(t) for t in (config.get("tools") or [])}
    groupes = [str(g) for g in (config.get("groups") or [])]
    tout = "*" in autorises
    return {
        "tools": [
            {
                "name": outil.name,
                "description": outil.description,
                "provider": outil.provider,
                "categories": outil.categories,
                "price_eur": outil.price_eur,
                # La clé n'est jamais rendue : seulement le fait qu'il en faille une.
                "needs_credential": outil.credential_env is not None,
                # Un outil distant : la plateforme appelle le serveur MCP, et le jeton du
                # run ne sort jamais. L'écran le dit, parce que c'est ce qu'on vient y
                # vérifier.
                "source": "mcp" if outil.mcp is not None else "http",
                "groups": outil.groups,
                "allowed": (tout or outil.name in autorises) and outil.ouvert_a(groupes),
            }
            for outil in catalogue().outils
        ],
        "allows_all": tout,
    }


@router.patch("/projects/{id}", response_model=ProjectDto, operation_id="updateProject")
async def update_project(ctx: ProjectCtx, body: ProjectUpdate, session: Db) -> ProjectDto:
    ctx.require(Permission.PROJECT_WRITE)
    if body.name is not None:
        ctx.project.name = body.name
    if body.config is not None:
        ctx.project.config = body.config.model_dump(mode="json", exclude_none=True)
    await record(
        session,
        ctx.principal,
        "project.update",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )
    return await project_dto(session, ctx.project, ctx.org_slug)


@router.delete("/projects/{id}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteProject")
async def delete_project(ctx: ProjectCtx, session: Db) -> None:
    ctx.require(Permission.PROJECT_DELETE)
    if ctx.project.status == "active":
        ctx.project.status = "archived"
    await record(
        session,
        ctx.principal,
        "project.archive",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )


@router.post("/projects/{id}/suspend", response_model=ProjectDto, operation_id="suspendProject")
async def suspend_project(ctx: ProjectCtx, session: Db) -> ProjectDto:
    ctx.require(Permission.PROJECT_WRITE)
    ctx.project.status = "suspended"
    await record(
        session,
        ctx.principal,
        "project.suspend",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )
    return await project_dto(session, ctx.project, ctx.org_slug)


def _provision_status(project: Project) -> ProvisionStatus:
    state = project.provision_state or {}
    return ProvisionStatus(
        project_id=project.id,
        workflow_id=state.get("workflow_id"),
        status=state.get("status", "pending"),
        current_step=state.get("current_step"),
        steps=[ProvisionStep.model_validate(s) for s in state.get("steps", [])],
    )


@router.post(
    "/projects/{id}/provision",
    response_model=ProvisionStatus,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="provisionProject",
)
async def provision(ctx: ProjectCtx, body: ProvisionRequest, session: Db) -> ProvisionStatus:
    ctx.require(Permission.PROJECT_WRITE)
    workflow_id = provisioning_id(ctx.slug)
    payload = {
        "project_id": ctx.id,
        "project_slug": ctx.slug,
        "org": ctx.org_slug,
        "template_ref": ctx.project.template_ref,
        "inputs": {**(ctx.project.provision_state or {}).get("inputs", {}), **body.inputs},
        "dry_run": body.dry_run,
        "resume": body.resume,
    }
    if not body.dry_run:
        await get_temporal().start_provisioning(workflow_id, payload)
        ctx.project.status = "provisioning"
    ctx.project.provision_state = {
        **(ctx.project.provision_state or {}),
        "status": "running",
        "workflow_id": workflow_id,
        "started_at": utcnow().isoformat(),
        "inputs": payload["inputs"],
    }
    await persist_event(
        session,
        EventType.PROVISIONING_STEP,
        project_id=ctx.id,
        project_slug=ctx.slug,
        subject=ctx.slug,
        step="start",
        status="running",
    )
    await record(
        session,
        ctx.principal,
        "project.provision",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )
    return _provision_status(ctx.project)


@router.get("/projects/{id}/provision", response_model=ProvisionStatus, operation_id="getProvisionStatus")
async def provision_status(
    request: Request,
    ctx: ProjectCtx,
    session: Db,
    id: Annotated[str, Path()],
) -> Any:
    """Statut du provisioning. Avec `Accept: text/event-stream`, suit les étapes en direct."""
    if "text/event-stream" not in request.headers.get("accept", ""):
        return _provision_status(ctx.project)

    from ..events import get_bus, sse_format

    bus = get_bus()
    topic = f"project:{ctx.slug}"

    async def stream() -> Any:
        yield {"event": "snapshot", "data": _provision_status(ctx.project).model_dump_json()}
        async for event in bus.subscribe(topic, request.headers.get("last-event-id")):
            if not str(event.type).startswith("choregos.project.provisioning"):
                continue
            yield sse_format(event)
            if str(event.type) in {
                str(EventType.PROVISIONING_COMPLETED),
                str(EventType.PROVISIONING_FAILED),
            }:
                break

    return EventSourceResponse(stream())
