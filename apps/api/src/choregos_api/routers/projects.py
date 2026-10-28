# SPDX-License-Identifier: Apache-2.0
"""Projets : création, configuration, provisioning (SSE), suspension."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import EventType, ProjectConfig, Role
from choregos_core import utcnow
from choregos_core.memory_scope import MEMORY_KEY_SETTING, memory_key
from fastapi import APIRouter, Path, Request, status
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from ..audit import record
from ..config import get_settings
from ..db.models import Membership, Organization, Project
from ..db.org_aliases import aliases_by_org, aliases_of, holder_of_slug, keep_old_slug, release
from ..db.session import TOUT, limiter_aux_organisations
from ..deps import Db, Me, Pagination, ProjectCtx, exiger_admin_de_plateforme
from ..edition import est_entreprise
from ..errors import conflict, forbidden, not_found
from ..greffons import DemandeDeGeste, controler
from ..rbac import Permission
from ..schemas import (
    OrgCreate,
    OrgDto,
    OrgUpdate,
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
from ..services.plugin_tracker import wire_template_tracker
from ..services.projets import config_revision
from ..temporal import get_temporal, provisioning_id

router = APIRouter(tags=["projects"])


@router.get("/orgs", response_model=list[OrgDto], operation_id="listOrgs")
async def list_orgs(session: Db, principal: Me) -> list[OrgDto]:
    """Les organisations de l'appelant, avec son rôle — ce que le front affiche dans son sélecteur."""
    slugs = set(principal.org_roles) | {q.split("/", 1)[0] for q in principal.project_roles}
    rows = list((await session.execute(select(Organization).where(Organization.slug.in_(slugs)))).scalars())
    aliases = await aliases_by_org(session, [o.id for o in rows])
    return sorted(
        (
            OrgDto(
                slug=o.slug,
                name=o.name,
                role=principal.org_roles.get(o.slug),
                currency=moneta(o),
                aliases=aliases[o.id],
            )
            for o in rows
        ),
        key=lambda o: o.slug,
    )


@router.post("/orgs", response_model=OrgDto, status_code=status.HTTP_201_CREATED, operation_id="createOrg")
async def create_org(body: OrgCreate, session: Db, principal: Me) -> OrgDto:
    """Crée une organisation ; l'appelant en devient administrateur.

    Réservé à qui administre déjà une organisation. La toute première vient de l'amorçage
    (`CHOREGOS_BOOTSTRAP_ORG`) : avant le 2026-09-24, aucune route, aucune commande ni
    aucun écran ne savait en créer une.
    """
    # Créer une organisation est un geste d'INSTANCE : il faut administrer l'instance, pas une
    # organisation parmi d'autres.
    await exiger_admin_de_plateforme(session, principal)
    # Créer une organisation est un geste d'INSTANCE, et le droit vient d'être établi : la suite de la
    # requête voit l'instance. Bornée aux organisations existantes de l'appelant, la session ne
    # voyait ni le compte réel des organisations, ni un slug déjà pris ailleurs (doublon → 500), et
    # la trace de la nouvelle organisation était refusée par la RLS d'`audit_log` (500 en 0.10.0).
    await limiter_aux_organisations(session, TOUT)
    if not est_entreprise():
        # L'édition communautaire est mono-organisation (ADR 0024). Ce n'est pas un plafond
        # arbitraire : le multi-locataire n'est pas fini ici — treize tables restent hors RLS —
        # et une installation à une seule organisation n'est exposée à aucune de ces fuites,
        # parce qu'il n'y a rien à franchir. L'édition entreprise le déverrouille ET le termine.
        deja = (await session.execute(select(func.count()).select_from(Organization))).scalar_one()
        if deja:
            raise conflict(
                "the community edition holds a single organisation. Several organisations, and the "
                "isolation between them, are the enterprise edition (docs/adr/0024-two-editions.md)."
            )
    if await holder_of_slug(session, body.slug) is not None:  # a current slug, or a former one (ADR 0090)
        raise conflict(f"organisation `{body.slug}` already exists")
    if any(
        memory_key(other) == body.slug for other in (await session.execute(select(Organization))).scalars()
    ):
        raise conflict(f"organisation `{body.slug}` already exists")  # a renamed organisation's memory key
    org = Organization(slug=body.slug, name=body.name)
    session.add(org)
    await session.flush()
    session.add(Membership(user_id=principal.user_id, org_id=org.id, role=str(Role.ORG_ADMIN)))
    await record(
        session, principal, "org.create", org_id=org.id, target_type="org", target_id=org.id, slug=body.slug
    )
    # The deployment's default plugins (ADR 0093 §5): a new organisation gets them as the others did at
    # start-up; one that brings a server waits for an administrator's approval.
    from ..services.plugin_defaults import reconcile_org

    await reconcile_org(session, org.id)
    return OrgDto(slug=org.slug, name=org.name, role=Role.ORG_ADMIN, currency=moneta(org))


def moneta(organisation: Organization) -> Any:
    """The organisation's currency (S29-03): its `currency` setting, EUR when it has none."""
    return "USD" if (organisation.settings or {}).get("currency") == "USD" else "EUR"


@router.patch("/orgs/{org}", response_model=OrgDto, operation_id="updateOrg")
async def update_org(org: str, body: OrgUpdate, session: Db, principal: Me) -> OrgDto:
    """Renames an organisation. Its NAME, its administrator changes. Its SLUG, only the platform
    administrator: it is in every console URL, every MCP door address (`/mcp/projects/<org>:<project>`),
    every qualified id (`<org>:<project>`) and the IdP's group names (`choregos:<org>:<group>`), so
    changing it is an instance gesture.

    Global memories (S40-11) follow: their tenant is `org:<memory key>`, and the key is the slug the
    organisation had when its slug first changed (`settings["memory_key"]`, set once, never
    overwritten), so a rename never orphans them.

    Projects, memberships, tokens and everything that depends on them follow: they hold the
    organisation by its id, not its slug (ADR 0090). Addresses already copied elsewhere and IdP groups
    that name the old slug keep working: the old slug becomes an ALIAS of the organisation
    (`Org.aliases`), resolved by URLs (`/orgs/<alias>/…`, `/a2a/orgs/<alias>`), MCP addresses and
    qualified ids (`resolve_project`), the IdP group mapping, the bootstrap and the payloads waiting in
    a workflow (`session_scope`). A slug that is taken, or an alias of ANOTHER organisation, is refused
    (409); the platform administrator releases an alias with `DELETE /orgs/{org}/aliases/{alias}`.
    """
    est_admin = principal.org_roles.get(org) == Role.ORG_ADMIN
    if not est_admin:
        # A platform administrator (granted by an edition plugin) renames an organisation they are
        # not a member of. The instance right is checked BEFORE the lookup: under PostgreSQL RLS, a
        # lookup bounded to the caller's organisations would not see the row (404).
        await exiger_admin_de_plateforme(session, principal)
        await limiter_aux_organisations(session, TOUT)
    organisation = await _org(session, org)
    avant = {"slug": organisation.slug, "name": organisation.name, "currency": moneta(organisation)}
    if body.slug is not None and body.slug != organisation.slug:
        await exiger_admin_de_plateforme(session, principal)
        # La RLS compare les SLUGS (`slug = ANY(choregos_current_orgs())`) : bornée à l'ancien, la
        # transaction ne verrait plus la ligne qu'elle renomme, et la mise à jour serait refusée. Le
        # droit d'instance vient d'être établi : la suite de la requête voit l'instance.
        await limiter_aux_organisations(session, TOUT)
        # A slug is unique across current slugs AND aliases: another organisation's old address is
        # not free (ADR 0090). An organisation may take back one of its own former slugs.
        holder = await holder_of_slug(session, body.slug)
        if holder is not None and holder != organisation.id:
            raise conflict(f"`{body.slug}` is already taken, or is a former slug of another organisation")
        # A slug another organisation keeps as its memory key is not free either: two organisations
        # would share one memory tenant.
        for other in (await session.execute(select(Organization))).scalars():
            if other.id != organisation.id and memory_key(other) == body.slug:
                raise conflict(f"organisation `{body.slug}` already exists")
        # The first rename pins the memory key to the old slug; a later rename keeps the first.
        if not (organisation.settings or {}).get(MEMORY_KEY_SETTING):
            organisation.settings = {
                **(organisation.settings or {}),
                MEMORY_KEY_SETTING: organisation.slug,
            }
        ancien = organisation.slug
        organisation.slug = body.slug
        await session.flush()
        # The old slug stays an address of the organisation (ADR 0090), and the copy of the slug the
        # project configuration holds (`ProjectConfig.org`) is rewritten with it.
        await keep_old_slug(session, organisation, ancien, principal.email)
        for projet in (
            await session.execute(select(Project).where(Project.org_id == organisation.id))
        ).scalars():
            if (projet.config or {}).get("org") == ancien:
                projet.config = {**projet.config, "org": body.slug}
    if body.name is not None:
        organisation.name = body.name
    if body.currency is not None:
        organisation.settings = {**(organisation.settings or {}), "currency": body.currency}
    await session.flush()
    apres = {"slug": organisation.slug, "name": organisation.name, "currency": moneta(organisation)}
    await record(session, principal, "org.update", org_id=organisation.id, target_type="org",
                 target_id=organisation.id, avant=avant, apres=apres)  # fmt: skip
    return OrgDto(
        slug=organisation.slug,
        name=organisation.name,
        role=Role.ORG_ADMIN if est_admin else None,  # a non-member platform admin has no role here
        currency=moneta(organisation),
        aliases=await aliases_of(session, organisation.id),
    )


@router.delete(
    "/orgs/{org}/aliases/{alias}", status_code=status.HTTP_204_NO_CONTENT, operation_id="releaseOrgAlias"
)
async def release_org_alias(org: str, alias: str, session: Db, principal: Me) -> None:
    """Free a former slug of the organisation, so another organisation may take it (ADR 0090).

    The platform administrator's gesture, like the rename: it is the one that breaks the old addresses
    and the IdP group names that still name the alias.
    """
    await exiger_admin_de_plateforme(session, principal)
    await limiter_aux_organisations(session, TOUT)
    organisation = await _org(session, org)
    if not await release(session, organisation.id, alias):
        raise not_found("Alias", alias)
    await record(
        session, principal, "org.alias.release", org_id=organisation.id, target_type="org",
        target_id=organisation.id, alias=alias,
    )  # fmt: skip


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


def _avec_les_defauts_du_deploiement(config: ProjectConfig) -> dict[str, Any]:
    """La configuration d'un projet neuf : ce que la demande dit, et — pour ce qu'elle tait — le
    backend et les profils de modèle que le déploiement sait servir (#245). Une demande qui nomme
    `agent` ou `models` garde les siens : le déploiement propose, le projet dispose."""
    reglages = get_settings()
    donnees = config.model_dump(mode="json", exclude_none=True)
    if "agent" not in config.model_fields_set and reglages.default_agent_backend:
        permis = list(reglages.allowed_agent_backends) or [reglages.default_agent_backend]
        donnees["agent"] = {"default_backend": reglages.default_agent_backend, "allowed_backends": permis}
    if "models" not in config.model_fields_set and reglages.default_model_profiles:
        donnees["models"] = {**donnees.get("models", {}), "profiles": dict(reglages.default_model_profiles)}
    # Repassée par le contrat : un profil s'écrit en raccourci (`standard: platform/standard`).
    return ProjectConfig.model_validate(donnees).model_dump(mode="json", exclude_none=True)


@router.post(
    "/orgs/{org}/projects",
    response_model=ProjectDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
async def create_project(org: str, body: ProjectCreate, session: Db, principal: Me) -> ProjectDto:
    organization = await _org(session, org)
    if not principal.can(Permission.PROJECT_CREATE, org):
        raise forbidden("creating a project needs the org_admin role")
    existing = (
        await session.execute(
            select(Project).where(Project.org_id == organization.id, Project.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(f"project `{body.slug}` already exists in {org}")
    await controler(session, DemandeDeGeste("project.create", org, principal, {"slug": body.slug}))
    project = Project(
        org_id=organization.id,
        slug=body.slug,
        name=body.name,
        template_ref=body.template_ref,
        config=_avec_les_defauts_du_deploiement(body.config),
        status="draft",
        provision_state={"status": "pending", "steps": [], "inputs": body.inputs},
    )
    session.add(project)
    await session.flush()
    await ensure_defaults(session, project)
    # A template whose tracker is a plugin's server wires it, or refuses the project (S43-07).
    await wire_template_tracker(session, project)
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
    # A row lock on PostgreSQL (a no-op on SQLite) that also reloads the instance the request resolved,
    # without expiring it: two editors of the same configuration are serialised, and the second compares
    # its `base_revision` with what the first wrote (S47-06).
    await session.execute(
        select(Project)
        .where(Project.id == ctx.project.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = config_revision(ctx.project.name, ctx.project.config)
    if body.base_revision is not None and body.base_revision != current:
        raise conflict(
            "the project changed since you read it: reload, then apply your change again",
            extra={"code": "stale_base", "latest_revision": current},
        )
    config_before = dict(ctx.project.config or {})
    name_before = ctx.project.name
    if body.name is not None:
        ctx.project.name = body.name
    if body.config is not None:
        avant = sorted({str(g) for g in ((ctx.project.config or {}).get("groups") or [])})
        apres = sorted(set(body.config.groups))
        if apres != avant:
            # Le second verrou de l'ADR 0014 : les groupes décident de ce que le DÉPLOIEMENT
            # ouvre au projet. Les laisser à l'équipe du projet ferait de sa propre liste le seul
            # contrôle — un propriétaire de projet s'ouvrait ainsi les outils d'un autre métier.
            ctx.require(Permission.TOOLS_GRANT)
        ctx.project.config = body.config.model_dump(mode="json", exclude_none=True)
    config_after = ctx.project.config or {}
    await record(
        session,
        ctx.principal,
        "project.update",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        changes={
            key: {"before": config_before.get(key), "after": config_after.get(key)}
            for key in sorted(config_before.keys() | config_after.keys())
            if config_before.get(key) != config_after.get(key)
        },
        name_changed=ctx.project.name != name_before,
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
