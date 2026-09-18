"""Workflow et politique d'un projet : lecture, validation localisée, activation."""

from __future__ import annotations

from choregos_core import checksum, parse_policy, parse_workflow, to_graph
from choregos_core.dsl import TEMPLATE_NAMES, load_template, template_yaml
from fastapi import APIRouter

from ..audit import record
from ..db.models import PolicyDef, WorkflowDef
from ..deps import Db, ProjectCtx
from ..errors import unprocessable
from ..rbac import Permission
from ..schemas import (
    PolicyDto,
    PolicyPut,
    WorkflowDefDto,
    WorkflowIssue,
    WorkflowPut,
    WorkflowTemplateDto,
    WorkflowValidateRequest,
    WorkflowValidation,
)
from ..services import active_policy, active_workflow, ensure_defaults

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
    )


@router.get("/projects/{id}/workflow", response_model=WorkflowDefDto, operation_id="getWorkflow")
async def get_workflow(ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    row = await active_workflow(session, ctx.id)
    if row is None:
        row, _ = await ensure_defaults(session, ctx.project)
    return WorkflowDefDto(
        id=row.id,
        name=row.name,
        version=row.version,
        source=row.source,
        yaml=row.yaml,
        json_doc=row.json_doc,
        checksum=row.checksum,
        is_active=row.is_active,
    )


@router.put("/projects/{id}/workflow", response_model=WorkflowDefDto, operation_id="putWorkflow")
async def put_workflow(ctx: ProjectCtx, body: WorkflowPut, session: Db) -> WorkflowDefDto:
    """Un workflow invalide est refusé en 422 avec la ligne et la colonne fautives."""
    ctx.require(Permission.WORKFLOW_WRITE)
    workflow, report = parse_workflow(body.yaml, strict=False)
    if not report.valid:
        raise unprocessable(
            "workflow invalide",
            [
                {"loc": [i.path or ""], "msg": i.message, "code": i.code, "line": i.line, "column": i.column}
                for i in report.errors
            ],
        )
    current = await active_workflow(session, ctx.id)
    version = workflow.metadata.version
    if current is not None and current.name == workflow.metadata.name and current.version >= version:
        version = current.version + 1
    if body.activate and current is not None:
        current.is_active = False
    row = WorkflowDef(
        project_id=ctx.id,
        name=workflow.metadata.name,
        version=version,
        source=body.source,
        yaml=body.yaml,
        json_doc=workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
        checksum=checksum(workflow),
        is_active=body.activate,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        ctx.principal,
        "workflow.put",
        target_type="workflow",
        target_id=row.id,
        name=row.name,
        version=row.version,
    )
    return WorkflowDefDto(
        id=row.id,
        name=row.name,
        version=row.version,
        source=row.source,
        yaml=row.yaml,
        json_doc=row.json_doc,
        checksum=row.checksum,
        is_active=row.is_active,
    )


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
    await record(session, ctx.principal, "policy.put", target_type="policy", target_id=row.id, name=row.name)
    return PolicyDto(
        id=row.id,
        name=row.name,
        version=row.version,
        yaml=row.yaml,
        json_doc=row.json_doc,
        is_active=row.is_active,
    )
