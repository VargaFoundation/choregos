# SPDX-License-Identifier: Apache-2.0
"""Projects: creation, configuration, provisioning (SSE), suspension."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import EventType, ProjectConfig, Role
from choregos_core import utcnow
from choregos_core.agent_completeness import ORG_SETTING as COMPLETE_AGENTS_SETTING
from choregos_core.agent_completeness import org_requires_complete
from choregos_core.memory_scope import MEMORY_KEY_SETTING, memory_key
from choregos_core.tool_scripts_switch import ORG_SETTING as TOOL_SCRIPTS_SETTING
from choregos_core.tool_scripts_switch import org_tool_scripts
from fastapi import APIRouter, Path, Request, status
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from ..audit import record
from ..config import get_settings
from ..db.models import Membership, Organization, Project
from ..db.org_aliases import aliases_by_org, aliases_of, holder_of_slug, keep_old_slug, release
from ..db.session import ALL_ORGS, limit_to_organisations
from ..deps import Db, Me, Pagination, ProjectCtx, require_platform_admin
from ..edition import is_enterprise
from ..errors import conflict, forbidden, not_found
from ..extensions import GestureRequest, check_gesture
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
from ..services.organisations import organisation_by_slug
from ..services.plugin_tracker import wire_template_tracker
from ..services.projects import config_revision
from ..temporal import get_temporal, provisioning_id

router = APIRouter(tags=["projects"])


@router.get("/orgs", response_model=list[OrgDto], operation_id="listOrgs")
async def list_orgs(session: Db, principal: Me) -> list[OrgDto]:
    """The caller's organisations, with their role — what the front end shows in its selector."""
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
                tool_scripts=org_tool_scripts(o.settings),
                complete_agents="required" if org_requires_complete(o.settings) else None,
            )
            for o in rows
        ),
        key=lambda o: o.slug,
    )


@router.post("/orgs", response_model=OrgDto, status_code=status.HTTP_201_CREATED, operation_id="createOrg")
async def create_org(body: OrgCreate, session: Db, principal: Me) -> OrgDto:
    """Create an organisation; the caller becomes its administrator.

    Reserved to whoever already administers an organisation. The very first one comes from the bootstrap
    (`CHOREGOS_BOOTSTRAP_ORG`): before 2026-09-24, no route, no command and
    no screen could create one.
    """
    # Creating an organisation is an INSTANCE gesture: it takes administering the instance, not one
    # organisation among others.
    await require_platform_admin(session, principal)
    # Creating an organisation is an INSTANCE gesture, and the right has just been established: the
    # rest of the request sees the instance. Bounded to the caller's existing organisations, the session saw
    # neither the real count of organisations, nor a slug already taken elsewhere (duplicate → 500), and
    # the trace of the new organisation was refused by the RLS of `audit_log` (500 in 0.10.0).
    await limit_to_organisations(session, ALL_ORGS)
    if not is_enterprise():
        # The community edition is single-organisation (ADR 0024). This is not an arbitrary
        # cap: multi-tenancy is not finished here — thirteen tables remain outside RLS —
        # and an installation with a single organisation is exposed to none of those leaks,
        # because there is nothing to cross. The enterprise edition unlocks it AND finishes it.
        already = (await session.execute(select(func.count()).select_from(Organization))).scalar_one()
        if already:
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


def _audited(organisation: Organization) -> dict[str, Any]:
    """What `org.update` records before and after the change."""
    return {
        "slug": organisation.slug,
        "name": organisation.name,
        "currency": moneta(organisation),
        "tool_scripts": org_tool_scripts(organisation.settings),
        "complete_agents": "required" if org_requires_complete(organisation.settings) else None,
    }


def _refuse_admin_switches(org: str, body: OrgUpdate) -> None:
    """The organisation's switches are its org_admin's alone, like its policy ceiling: tool scripts
    (S57-05b) and complete agents (S60-05, ADR 0127)."""
    if "tool_scripts" in body.model_fields_set:
        raise forbidden(f"the tool scripts of {org} are switched by its org_admin")
    if "complete_agents" in body.model_fields_set:
        raise forbidden(f"whether {org} requires complete agents is its org_admin's choice")


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

    `tool_scripts` (S57-05b, ADR 0122 §6) switches tool scripts on (`enabled`) or off (`disabled`) for
    every run of the organisation, or back to the deployment's default (`null`); its administrator only.
    The ceiling and each project's policy still decide past it, and the deployment's brake wins over it.
    """
    is_admin = principal.org_roles.get(org) == Role.ORG_ADMIN
    # Whether the organisation's runs may be offered tool scripts (S57-05b) is its administrator's
    # choice alone, like its policy ceiling: a platform administrator who is not one is refused, before
    # anything else of the request is applied.
    if not is_admin:
        _refuse_admin_switches(org, body)
    if not is_admin:
        # A platform administrator (granted by an edition plugin) renames an organisation they are
        # not a member of. The instance right is checked BEFORE the lookup: under PostgreSQL RLS, a
        # lookup bounded to the caller's organisations would not see the row (404).
        await require_platform_admin(session, principal)
        await limit_to_organisations(session, ALL_ORGS)
    organisation = await organisation_by_slug(session, org)
    org_before = _audited(organisation)
    if body.slug is not None and body.slug != organisation.slug:
        await require_platform_admin(session, principal)
        # The RLS compares SLUGS (`slug = ANY(choregos_current_orgs())`): bounded to the old one, the
        # transaction would no longer see the row it renames, and the update would be refused. The
        # instance right has just been established: the rest of the request sees the instance.
        await limit_to_organisations(session, ALL_ORGS)
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
        former_slug = organisation.slug
        organisation.slug = body.slug
        await session.flush()
        # The old slug stays an address of the organisation (ADR 0090), and the copy of the slug the
        # project configuration holds (`ProjectConfig.org`) is rewritten with it.
        await keep_old_slug(session, organisation, former_slug, principal.email)
        for project_row in (
            await session.execute(select(Project).where(Project.org_id == organisation.id))
        ).scalars():
            if (project_row.config or {}).get("org") == former_slug:
                project_row.config = {**project_row.config, "org": body.slug}
    if body.name is not None:
        organisation.name = body.name
    if body.currency is not None:
        organisation.settings = {**(organisation.settings or {}), "currency": body.currency}
    # `null` takes the organisation back to no choice: the key is removed, so the orchestrator reads none
    # (`choregos_core.tool_scripts_switch`), and nor does publication (`choregos_core.agent_completeness`).
    for field, key in (("tool_scripts", TOOL_SCRIPTS_SETTING), ("complete_agents", COMPLETE_AGENTS_SETTING)):
        if field in body.model_fields_set:
            settings = {k: v for k, v in (organisation.settings or {}).items() if k != key}
            if (choice := getattr(body, field)) is not None:
                settings[key] = choice
            organisation.settings = settings
    await session.flush()
    org_after = _audited(organisation)
    await record(
        session,
        principal,
        "org.update",
        org_id=organisation.id,
        target_type="org",
        target_id=organisation.id,
        before=org_before,
        after=org_after,
    )
    return OrgDto(
        slug=organisation.slug,
        name=organisation.name,
        role=Role.ORG_ADMIN if is_admin else None,  # a non-member platform admin has no role here
        currency=moneta(organisation),
        aliases=await aliases_of(session, organisation.id),
        tool_scripts=org_tool_scripts(organisation.settings),
        complete_agents="required" if org_requires_complete(organisation.settings) else None,
    )


@router.delete(
    "/orgs/{org}/aliases/{alias}", status_code=status.HTTP_204_NO_CONTENT, operation_id="releaseOrgAlias"
)
async def release_org_alias(org: str, alias: str, session: Db, principal: Me) -> None:
    """Free a former slug of the organisation, so another organisation may take it (ADR 0090).

    The platform administrator's gesture, like the rename: it is the one that breaks the old addresses
    and the IdP group names that still name the alias.
    """
    await require_platform_admin(session, principal)
    await limit_to_organisations(session, ALL_ORGS)
    organisation = await organisation_by_slug(session, org)
    if not await release(session, organisation.id, alias):
        raise not_found("Alias", alias)
    await record(
        session, principal, "org.alias.release", org_id=organisation.id, target_type="org",
        target_id=organisation.id, alias=alias,
    )  # fmt: skip


@router.get("/orgs/{org}/projects", response_model=ProjectPage, operation_id="listProjects")
async def list_projects(org: str, session: Db, principal: Me, page: Pagination) -> ProjectPage:
    organization = await organisation_by_slug(session, org)
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


def _with_deployment_defaults(config: ProjectConfig) -> dict[str, Any]:
    """The configuration of a new project: what the request says, and — for what it leaves out — the
    backend and the model profiles the deployment can serve (#245). A request that names
    `agent` or `models` keeps its own: the deployment proposes, the project disposes."""
    deployment_settings = get_settings()
    payload = config.model_dump(mode="json", exclude_none=True)
    if "agent" not in config.model_fields_set and deployment_settings.default_agent_backend:
        permitted = list(deployment_settings.allowed_agent_backends) or [
            deployment_settings.default_agent_backend
        ]
        payload["agent"] = {
            "default_backend": deployment_settings.default_agent_backend,
            "allowed_backends": permitted,
        }
    if "models" not in config.model_fields_set and deployment_settings.default_model_profiles:
        payload["models"] = {
            **payload.get("models", {}),
            "profiles": dict(deployment_settings.default_model_profiles),
        }
    # Passed back through the contract: a profile is written in shorthand (`standard: platform/standard`).
    return ProjectConfig.model_validate(payload).model_dump(mode="json", exclude_none=True)


@router.post(
    "/orgs/{org}/projects",
    response_model=ProjectDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
async def create_project(org: str, body: ProjectCreate, session: Db, principal: Me) -> ProjectDto:
    organization = await organisation_by_slug(session, org)
    if not principal.can(Permission.PROJECT_CREATE, org):
        raise forbidden("creating a project needs the org_admin role")
    existing = (
        await session.execute(
            select(Project).where(Project.org_id == organization.id, Project.slug == body.slug)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(f"project `{body.slug}` already exists in {org}")
    await check_gesture(session, GestureRequest("project.create", org, principal, {"slug": body.slug}))
    project = Project(
        org_id=organization.id,
        slug=body.slug,
        name=body.name,
        template_ref=body.template_ref,
        config=_with_deployment_defaults(body.config),
        status="draft",
        provision_state={"status": "pending", "steps": [], "inputs": body.inputs},
    )
    session.add(project)
    await session.flush()
    await ensure_defaults(session, project, refuse_unenforced=True)
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


@router.get(
    "/projects/{id}/events/stream",
    operation_id="streamProjectEvents",
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_project_events(ctx: ProjectCtx) -> Any:
    """What happens in the project from now on, as unnamed SSE messages (`type`, `subject`, `run_id`):
    its board, inbox and overview refetch on each one (S59-08)."""
    from ..events import live_signals

    return live_signals(f"project-id:{ctx.project.id}")


@router.get("/projects/{id}/tools", operation_id="getProjectTools")
async def get_project_tools(ctx: ProjectCtx, session: Db) -> dict[str, Any]:
    """The deployment catalogue, and what THIS project may call.

    Both sets, not only the second: without the complete list, one can neither
    know what could be authorised, nor understand why a tool is missing.
    """
    from ..catalogue import catalogue

    config = ctx.project.config or {}
    authorised = {str(t) for t in (config.get("tools") or [])}
    groups = [str(g) for g in (config.get("groups") or [])]
    everything = "*" in authorised
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "provider": tool.provider,
                "categories": tool.categories,
                "price_eur": tool.price_eur,
                # The key is never returned: only the fact that one is needed.
                "needs_credential": tool.credential_env is not None,
                # A remote tool: the platform calls the MCP server, and the run's token
                # never leaves. The screen says so, because that is what people come
                # to check.
                "source": "mcp" if tool.mcp is not None else "http",
                "groups": tool.groups,
                "allowed": (everything or tool.name in authorised)
                and tool.open_to(groups)
                and not tool.may_write,
            }
            for tool in catalogue().tools
        ],
        "allows_all": everything,
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
        groups_before = sorted({str(g) for g in ((ctx.project.config or {}).get("groups") or [])})
        groups_after = sorted(set(body.config.groups))
        if groups_after != groups_before:
            # The second lock of ADR 0014: groups decide what the DEPLOYMENT
            # opens to the project. Leaving them to the project team would make its own list the only
            # control — a project owner could thus open the tools of another trade.
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
    """Archive the project, whatever its status: a draft, a project left provisioning, a suspended one.

    Only an `active` project used to be archived: on any other the console's Archive answered 204,
    the audit said `project.archive`, and nothing changed (a draft could never be retired).
    """
    ctx.require(Permission.PROJECT_DELETE)
    if ctx.project.status == "archived":
        return
    previous = ctx.project.status
    ctx.project.status = "archived"
    await record(
        session,
        ctx.principal,
        "project.archive",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        previous_status=previous,
    )
    from ..temporal import stop_memory_ingestion, stop_tracker_reconciliation

    await stop_tracker_reconciliation(ctx.slug)
    await stop_memory_ingestion(ctx.slug)


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


def _nothing_to_provision(project: Project) -> bool:
    """No template and no repository: there is no repository to wire, no pipeline to create.

    The orchestrator falls back to the GitHub · Tekton · Argo steps when a project names no template;
    a project that has no repository would only fail them, and stayed a draft nobody could activate."""
    repo = (project.config or {}).get("repo") or {}
    return not project.template_ref and not repo.get("url")


async def _activate_without_steps(ctx: ProjectCtx, session: Db, *, dry_run: bool) -> ProvisionStatus:
    if dry_run:
        return ProvisionStatus(project_id=ctx.id, status="pending")
    now = utcnow().isoformat()
    ctx.project.status = "active"
    ctx.project.provision_state = {
        **(ctx.project.provision_state or {}),
        "status": "succeeded",
        "workflow_id": None,
        "current_step": None,
        "steps": [],
        "started_at": now,
    }
    await persist_event(
        session,
        EventType.PROVISIONING_COMPLETED,
        project_id=ctx.id,
        project_slug=ctx.slug,
        subject=ctx.slug,
        failed=[],
    )
    await record(
        session,
        ctx.principal,
        "project.provision",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        steps=0,
    )
    from ..services import policy_engine
    from ..temporal import start_memory_ingestion, start_tracker_reconciliation

    await start_tracker_reconciliation(ctx.slug)
    # The memory is fed from activation on (S51-02), unless the project's policy disables it.
    if (await policy_engine(session, ctx.id)).memory_enabled():
        await start_memory_ingestion(ctx.slug)
    return _provision_status(ctx.project)


@router.post(
    "/projects/{id}/provision",
    response_model=ProvisionStatus,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="provisionProject",
)
async def provision(ctx: ProjectCtx, body: ProvisionRequest, session: Db) -> ProvisionStatus:
    ctx.require(Permission.PROJECT_WRITE)
    if _nothing_to_provision(ctx.project):
        return await _activate_without_steps(ctx, session, dry_run=body.dry_run)
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
    """Provisioning status. With `Accept: text/event-stream`, follows the steps live."""
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
