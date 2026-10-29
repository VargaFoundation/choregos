# SPDX-License-Identifier: Apache-2.0
"""The workflow catalogue (ADR 0108): list it, adopt an entry into a project, share a project's workflow.

Listing is any member's; adopting is a project gesture (`workflow:write`); sharing to the organisation's
catalogue is an organisation administrator's. Adopting installs the catalogue agents a workflow names
and never a plugin: an agent that resolves nowhere refuses the adoption and says so.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization, OrgWorkflow, Project, WorkflowDef
from ..deps import Db, Me, ProjectCtx, resolve_project
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas.definitions import WorkflowAdopt, WorkflowCatalogueEntryDto, WorkflowDefDto, WorkflowShare
from ..services.agent_governance import is_admin, is_member
from ..services.definitions import publier_workflow, workflow_actif
from ..services.workflow_catalogue import (
    ORG,
    entries,
    entry_dto,
    find,
    named_agents,
    offered,
    parse_origin,
    renamed,
    resolutions,
)
from .workflows import workflow_dto

router = APIRouter(tags=["workflows"])


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


@router.get(
    "/orgs/{org}/workflow-catalogue",
    response_model=list[WorkflowCatalogueEntryDto],
    operation_id="listWorkflowCatalogue",
)
async def list_workflow_catalogue(org: str, session: Db, principal: Me) -> list[WorkflowCatalogueEntryDto]:
    # The right first: under RLS a non-member does not see the organisation, and would get a 404.
    if not is_member(principal, org):
        raise forbidden(f"reading the workflow catalogue of {org} requires being a member of it")
    organisation = await _organisation(session, org)
    return await entries(session, organisation.id)


async def _adopted_from_same_entry(
    session: AsyncSession, project: Project, name: str, source: str, entry: str
) -> bool:
    """The project's active workflow of that name was adopted from this very entry: adopting again
    publishes its next version (an update, ADR 0108 §5) instead of refusing a taken name."""
    active = await workflow_actif(session, project.id, name)
    parts = parse_origin(active.origin or "") if active is not None and active.source == "catalogue" else None
    return parts is not None and parts[0] == source and parts[1] == entry


@router.post(
    "/projects/{id}/workflow-adoptions",
    response_model=WorkflowDefDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="adoptWorkflow",
)
async def adopt_workflow(ctx: ProjectCtx, body: WorkflowAdopt, session: Db) -> WorkflowDefDto:
    ctx.require(Permission.WORKFLOW_WRITE)
    project = ctx.project
    entry = await find(session, project.org_id, body.source, body.name)
    if entry is None:
        raise not_found("Catalogue workflow", f"{body.source}/{body.name}")
    if body.version is not None and body.version != entry.version:
        raise conflict(
            f"`{body.source}/{body.name}` is now version {entry.version}, not {body.version}: read it again"
        )
    resolved = await resolutions(
        session, project.org_id, {slug for _, slug, _ in named_agents(entry.workflow)}
    )
    missing = sorted(slug for slug, r in resolved.items() if r.resolution == "unresolved")
    if missing:
        names = ", ".join(
            f"`{slug}`" + (f" (the plugin {resolved[slug].plugin} ships it)" if resolved[slug].plugin else "")
            for slug in missing
        )
        raise unprocessable(
            f"`{entry.name}` names agents this organisation does not have and no catalogue provides: "
            f"{names}. An organisation administrator installs the plugin that ships them first; "
            "adopting never installs one.",
            [{"loc": ["actors"], "msg": f"`{slug}` resolves nowhere"} for slug in missing],
        )
    name = body.as_ or entry.name
    text = renamed(entry.yaml, name) if name != entry.name else entry.yaml
    update = await _adopted_from_same_entry(session, project, name, entry.source, entry.name)
    row = await publier_workflow(
        session,
        ctx.principal,
        project,
        text,
        source="catalogue",
        creation_seule=not update,
        devient_le_defaut=body.make_default,
        origin=entry.origin,
    )
    await record(
        session,
        ctx.principal,
        "workflow.adopt",
        org_id=project.org_id,
        target_type="workflow",
        target_id=row.id,
        name=row.name,
        version=row.version,
        origin=entry.origin,
        update=update,
    )
    return workflow_dto(row, project)


async def _project_of(session: AsyncSession, organisation: Organization, identifier: str) -> Project:
    found = await session.get(Project, identifier)
    if found is None:
        found, _ = await resolve_project(session, f"{organisation.slug}/{identifier}")
    if found.org_id != organisation.id:
        raise not_found("Project", identifier)
    return found


@router.post(
    "/orgs/{org}/workflow-catalogue",
    response_model=WorkflowCatalogueEntryDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="shareWorkflow",
)
async def share_workflow(
    org: str, body: WorkflowShare, session: Db, principal: Me
) -> WorkflowCatalogueEntryDto:
    if not is_admin(principal, org):
        raise forbidden(f"sharing a workflow to the catalogue of {org} needs the org_admin role")
    organisation = await _organisation(session, org)
    project = await _project_of(session, organisation, body.project)
    query = select(WorkflowDef).where(WorkflowDef.project_id == project.id, WorkflowDef.name == body.name)
    query = (
        query.where(WorkflowDef.version == body.version)
        if body.version is not None
        else query.where(WorkflowDef.is_active.is_(True))
    )
    row = (await session.execute(query.limit(1))).scalar_one_or_none()
    if row is None:
        raise not_found(
            "Workflow", f"{project.slug}/{body.name}" + (f"@{body.version}" if body.version else "")
        )
    last = (
        await session.execute(
            select(func.max(OrgWorkflow.version)).where(
                OrgWorkflow.org_id == organisation.id, OrgWorkflow.name == row.name
            )
        )
    ).scalar() or 0
    shared = OrgWorkflow(
        org_id=organisation.id,
        name=row.name,
        version=last + 1,
        yaml=row.yaml,
        checksum=row.checksum,
        shared_from=f"{project.slug}/{row.name}@{row.version}",
        shared_by=principal.email,
    )
    session.add(shared)
    await session.flush()
    await record(
        session,
        principal,
        "workflow.share",
        org_id=organisation.id,
        target_type="workflow",
        target_id=row.id,
        name=row.name,
        version=shared.version,
        shared_from=shared.shared_from,
    )
    entry = next(o for o in await offered(session, organisation.id) if o.source == ORG and o.name == row.name)
    resolved = await resolutions(
        session, organisation.id, {slug for _, slug, _ in named_agents(entry.workflow)}
    )
    return entry_dto(entry, resolved)
