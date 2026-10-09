# SPDX-License-Identifier: Apache-2.0
"""Workflow et politique d'un projet : lecture, validation localisée, activation."""

from __future__ import annotations

from typing import Any

from choregos_core import parse_policy, parse_workflow, to_graph, to_process
from choregos_core.dsl import TEMPLATE_NAMES, load_template, template_yaml
from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from ..audit import record
from ..db.models import PolicyDef, Project, WorkflowDef, WorkItem
from ..deps import Db, ProjectCtx
from ..errors import conflict, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    PolicyDto,
    PolicyPut,
    WorkflowDefDto,
    WorkflowEditRequest,
    WorkflowEditResult,
    WorkflowIssue,
    WorkflowPut,
    WorkflowRoutingDto,
    WorkflowSummaryDto,
    WorkflowTemplateDto,
    WorkflowValidateRequest,
    WorkflowValidation,
)
from ..services import active_policy, default_workflow, ensure_defaults, publier_workflow, workflow_actif

router = APIRouter(tags=["workflows"])


@router.get(
    "/workflows/templates", response_model=list[WorkflowTemplateDto], operation_id="listWorkflowTemplates"
)
async def list_templates() -> list[WorkflowTemplateDto]:
    out: list[WorkflowTemplateDto] = []
    for name in TEMPLATE_NAMES:
        workflow = load_template(name)
        out.append(
            WorkflowTemplateDto(
                name=name,
                version=workflow.metadata.version,
                display=workflow.metadata.description or name,
                description=workflow.metadata.description or "",
                yaml=template_yaml(name),
            )
        )
    return out


@router.post("/workflows/validate", response_model=WorkflowValidation, operation_id="validateWorkflow")
async def validate(body: WorkflowValidateRequest) -> WorkflowValidation:
    """Validation sans effet de bord : c'est ce que l'éditeur du front appelle à chaque frappe."""
    import yaml as yaml_lib
    from choregos_core import ValidationError

    source = body.yaml or yaml_lib.safe_dump(body.json_doc or {}, sort_keys=False, allow_unicode=True)
    try:
        workflow, report = parse_workflow(source, strict=False)
    except ValidationError as exc:
        return WorkflowValidation(
            valid=False,
            errors=[WorkflowIssue(**issue.to_dict()) for issue in exc.issues],
        )
    return WorkflowValidation(
        valid=report.valid,
        errors=[WorkflowIssue(**issue.to_dict()) for issue in report.errors],
        warnings=[WorkflowIssue(**issue.to_dict()) for issue in report.warnings],
        graph=to_graph(workflow),
        process=to_process(workflow),
    )


@router.post("/workflows/edit", response_model=WorkflowEditResult, operation_id="editWorkflow")
async def edit(body: WorkflowEditRequest) -> WorkflowEditResult:
    """Des opérations typées, greffées dans le texte ; rien n'est enregistré (ADR 0031).

    La carte et la vue processus de la console l'appellent à chaque geste : le texte rendu se relit
    en diff, s'annule par `inverse`, et s'enregistre par le PUT du workflow, avec `base_version`.
    """
    import difflib

    from choregos_core.dsl.edition import EditionRefusee, editer
    from pydantic import ValidationError as ErreurDeModele

    try:
        edition = editer(body.yaml, body.operations)
    except ErreurDeModele as erreur:
        raise unprocessable(
            "malformed operation",
            [{"loc": ["operations", *e["loc"]], "msg": e["msg"]} for e in erreur.errors()],
        ) from erreur
    except EditionRefusee as refus:
        raise unprocessable(str(refus)) from refus
    rapport = await validate(WorkflowValidateRequest(yaml=edition.yaml))
    diff = "".join(
        difflib.unified_diff(
            body.yaml.splitlines(keepends=True), edition.yaml.splitlines(keepends=True), "before", "after"
        )
    )
    return WorkflowEditResult(
        **rapport.model_dump(),
        yaml=edition.yaml,
        diff=diff,
        inverse=[o.model_dump(mode="json", by_alias=True, exclude_none=True) for o in edition.inverse],
        notices=edition.avertissements,
    )


def _dto(row: WorkflowDef, project: Project) -> WorkflowDefDto:
    return WorkflowDefDto(
        id=row.id,
        name=row.name,
        version=row.version,
        source=row.source,
        yaml=row.yaml,
        json_doc=row.json_doc,
        checksum=row.checksum,
        is_active=row.is_active,
        is_default=row.name == project.default_workflow,
        created_by=row.created_by,
        created_at=row.created_at,
    )


@router.get("/projects/{id}/workflow", response_model=WorkflowDefDto, operation_id="getWorkflow")
async def get_workflow(ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    """L'alias du workflow PAR DÉFAUT (ADR 0031)."""
    row = await default_workflow(session, ctx.id)
    if row is None:
        row, _ = await ensure_defaults(session, ctx.project)
    return _dto(row, ctx.project)


@router.put("/projects/{id}/workflow", response_model=WorkflowDefDto, operation_id="putWorkflow")
async def put_workflow(ctx: ProjectCtx, body: WorkflowPut, session: Db) -> WorkflowDefDto:
    """L'alias du défaut : ce qu'il publie DEVIENT le défaut. Un workflow invalide reçoit 422, localisé."""
    ctx.require(Permission.WORKFLOW_WRITE)
    row = await publier_workflow(
        session,
        ctx.principal,
        ctx.project,
        body.yaml,
        source=body.source,
        activate=body.activate,
        base_version=body.base_version,
        devient_le_defaut=True,
    )
    return _dto(row, ctx.project)


@router.get("/projects/{id}/workflows", response_model=list[WorkflowSummaryDto], operation_id="listWorkflows")
async def list_workflows(ctx: ProjectCtx, session: Db) -> list[WorkflowSummaryDto]:
    actifs = (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == ctx.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.name)
        )
    ).scalars()
    # Un `Result` a une méthode `keys()` (ses colonnes) : `dict(result)` l'indicerait. On le parcourt.
    comptes = await session.execute(
        select(WorkflowDef.name, func.count(WorkItem.id))
        .join(WorkItem, WorkItem.workflow_def_id == WorkflowDef.id)
        .where(WorkflowDef.project_id == ctx.id, WorkItem.closed_at.is_(None))
        .group_by(WorkflowDef.name)
    )
    ouverts: dict[str, int] = {str(nom): int(n) for nom, n in comptes.tuples()}
    return [
        WorkflowSummaryDto(
            name=row.name,
            version=row.version,
            description=(row.json_doc or {}).get("metadata", {}).get("description"),
            is_default=row.name == ctx.project.default_workflow,
            open_items=int(ouverts.get(row.name, 0)),
            created_by=row.created_by,
            updated_at=row.created_at,
        )
        for row in actifs
    ]


async def _actif(session: Any, ctx: Any, name: str) -> WorkflowDef:
    row = await workflow_actif(session, ctx.id, name)
    if row is None:
        raise not_found("Workflow", name)
    return row


@router.get("/projects/{id}/workflows/{name}", response_model=WorkflowDefDto, operation_id="getNamedWorkflow")
async def get_named_workflow(name: str, ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    return _dto(await _actif(session, ctx, name), ctx.project)


@router.put("/projects/{id}/workflows/{name}", response_model=WorkflowDefDto, operation_id="putNamedWorkflow")
async def put_named_workflow(name: str, ctx: ProjectCtx, body: WorkflowPut, session: Db) -> WorkflowDefDto:
    """Publie la version suivante de CE workflow, sans toucher aux autres (ADR 0031)."""
    ctx.require(Permission.WORKFLOW_WRITE)
    row = await publier_workflow(
        session,
        ctx.principal,
        ctx.project,
        body.yaml,
        source=body.source,
        activate=body.activate,
        base_version=body.base_version,
        nom_attendu=name,
        creation_seule=body.create_only,
    )
    return _dto(row, ctx.project)


def _cibles_du_routage(project: Project) -> set[str]:
    return {str(regle.get("workflow")) for regle in (project.workflow_routing or [])}


@router.post(
    "/projects/{id}/workflows/{name}/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deactivateWorkflow",
)
async def deactivate_workflow(name: str, ctx: ProjectCtx, session: Db) -> Response:
    """Un workflow désactivé ne reçoit plus de ticket ; ses tickets épinglés finissent sur leur version."""
    ctx.require(Permission.WORKFLOW_WRITE)
    row = await _actif(session, ctx, name)
    if name == ctx.project.default_workflow:
        raise conflict(f"`{name}` is the default workflow: choose another one before deactivating it")
    if name in _cibles_du_routage(ctx.project):
        raise conflict(f"`{name}` is the target of a routing rule: remove the rule first")
    row.is_active = False
    await record(
        session,
        ctx.principal,
        "workflow.deactivate",
        org_id=ctx.project.org_id,
        target_type="workflow",
        target_id=row.id,
        name=name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/projects/{id}/workflows/{name}/versions",
    response_model=list[WorkflowDefDto],
    operation_id="listWorkflowVersions",
)
async def list_versions(name: str, ctx: ProjectCtx, session: Db) -> list[WorkflowDefDto]:
    rows = (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == ctx.id, WorkflowDef.name == name)
            .order_by(WorkflowDef.version.desc())
        )
    ).scalars()
    return [_dto(row, ctx.project) for row in rows]


@router.post(
    "/projects/{id}/workflows/{name}/versions/{version}/restore",
    response_model=WorkflowDefDto,
    operation_id="restoreWorkflowVersion",
)
async def restore_version(name: str, version: int, ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    """Restaurer, c'est republier : l'ancienne version devient la suivante. Rien n'est écrasé."""
    ctx.require(Permission.WORKFLOW_WRITE)
    ancienne = (
        await session.execute(
            select(WorkflowDef).where(
                WorkflowDef.project_id == ctx.id, WorkflowDef.name == name, WorkflowDef.version == version
            )
        )
    ).scalar_one_or_none()
    if ancienne is None:
        raise not_found("Version", f"{name} v{version}")
    row = await publier_workflow(
        session, ctx.principal, ctx.project, ancienne.yaml, source=ancienne.source, nom_attendu=name
    )
    return _dto(row, ctx.project)


@router.get(
    "/projects/{id}/workflow-routing", response_model=WorkflowRoutingDto, operation_id="getWorkflowRouting"
)
async def get_routing(ctx: ProjectCtx, session: Db) -> WorkflowRoutingDto:
    defaut = ctx.project.default_workflow
    if not defaut:
        row = await default_workflow(session, ctx.id)
        defaut = row.name if row is not None else ""
    return WorkflowRoutingDto(default=defaut, rules=list(ctx.project.workflow_routing or []))


@router.put(
    "/projects/{id}/workflow-routing", response_model=WorkflowRoutingDto, operation_id="putWorkflowRouting"
)
async def put_routing(ctx: ProjectCtx, body: WorkflowRoutingDto, session: Db) -> WorkflowRoutingDto:
    """Le défaut et les règles qui choisissent le workflow d'un ticket venu d'un tracker."""
    ctx.require(Permission.WORKFLOW_WRITE)
    actifs = set(
        (
            await session.execute(
                select(WorkflowDef.name).where(
                    WorkflowDef.project_id == ctx.id, WorkflowDef.is_active.is_(True)
                )
            )
        ).scalars()
    )
    inconnus = sorted({body.default, *(r.workflow for r in body.rules)} - actifs)
    if inconnus:
        raise unprocessable(f"unknown or inactive workflow(s): {', '.join(inconnus)}")
    ctx.project.default_workflow = body.default
    ctx.project.workflow_routing = [r.model_dump(mode="json") for r in body.rules]
    await record(
        session,
        ctx.principal,
        "workflow.routing",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        default=body.default,
        rules=len(body.rules),
    )
    return body


@router.get("/projects/{id}/policy", response_model=PolicyDto, operation_id="getPolicy")
async def get_policy(ctx: ProjectCtx, session: Db) -> PolicyDto:
    row = await active_policy(session, ctx.id)
    if row is None:
        _, row = await ensure_defaults(session, ctx.project)
    return PolicyDto(
        id=row.id,
        name=row.name,
        version=row.version,
        yaml=row.yaml,
        json_doc=row.json_doc,
        is_active=row.is_active,
    )


@router.put("/projects/{id}/policy", response_model=PolicyDto, operation_id="putPolicy")
async def put_policy(ctx: ProjectCtx, body: PolicyPut, session: Db) -> PolicyDto:
    ctx.require(Permission.POLICY_WRITE)
    policy = parse_policy(body.yaml)
    current = await active_policy(session, ctx.id)
    version = policy.metadata.version
    if current is not None and current.version >= version:
        version = current.version + 1
    if body.activate and current is not None:
        current.is_active = False
    row = PolicyDef(
        project_id=ctx.id,
        name=policy.metadata.name,
        version=version,
        yaml=body.yaml,
        json_doc=policy.model_dump(mode="json", exclude_none=True),
        is_active=body.activate,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        ctx.principal,
        "policy.put",
        org_id=ctx.project.org_id,
        target_type="policy",
        target_id=row.id,
        name=row.name,
    )
    return PolicyDto(
        id=row.id,
        name=row.name,
        version=row.version,
        yaml=row.yaml,
        json_doc=row.json_doc,
        is_active=row.is_active,
    )
