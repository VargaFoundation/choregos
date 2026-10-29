# SPDX-License-Identifier: Apache-2.0
"""Workflow et politique d'un projet : lecture, validation localisée, activation."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from choregos_contracts import Role
from choregos_core import parse_policy, parse_workflow, to_graph, to_process, utcnow
from choregos_core.dsl import TEMPLATE_NAMES, load_template, template_yaml
from choregos_core.policy_ceiling import PolicyCeiling, widenings
from fastapi import APIRouter, Query, Response, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization, PolicyDef, Project, WorkflowDef, WorkItem
from ..deps import Db, Me, ProjectCtx
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission, Principal
from ..schemas import (
    PolicyCeilingDto,
    PolicyCeilingPut,
    PolicyDto,
    PolicyPut,
    StateActivity,
    StepActivity,
    WorkflowActivity,
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
from ..schemas.network import EffectiveNetworkDto, ProjectNetwork
from ..services import (
    active_policy,
    default_workflow,
    effective_policy_and_clamps,
    ensure_defaults,
    publier_workflow,
    signaler_ce_que_la_politique_n_applique_pas,
    workflow_actif,
)
from ..services.definitions import CEILING_KEY, ceiling_of, policy_model
from ..services.network import project_effective_network

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
        document=workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@router.post(
    "/projects/{id}/workflows/validate",
    response_model=WorkflowValidation,
    operation_id="validateProjectWorkflow",
)
async def validate_for_project(
    ctx: ProjectCtx, body: WorkflowValidateRequest, session: Db
) -> WorkflowValidation:
    """The same validation, in the context of a project: it also warns about a human actor whose group
    nobody in the project's organisation carries (a step addressed to it waits for an administrator)."""
    from ..services.human_groups import groups_known_in, unknown_group_warnings

    validation = await validate(body)
    if not validation.valid or validation.graph is None:
        return validation
    import yaml as yaml_lib

    source = body.yaml or yaml_lib.safe_dump(body.json_doc or {}, sort_keys=False, allow_unicode=True)
    workflow, _ = parse_workflow(source, strict=False)
    known = await groups_known_in(session, ctx.project.org_id)
    validation.warnings += [WorkflowIssue(**issue) for issue in unknown_group_warnings(workflow, known)]
    return validation


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


def workflow_dto(row: WorkflowDef, project: Project) -> WorkflowDefDto:
    return WorkflowDefDto(
        id=row.id,
        name=row.name,
        version=row.version,
        source=row.source,
        origin=row.origin,
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
    return workflow_dto(row, ctx.project)


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
    return workflow_dto(row, ctx.project)


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
    from ..services.agent_governance import is_admin

    can_share = is_admin(ctx.principal, ctx.org_slug)
    return [
        WorkflowSummaryDto(
            name=row.name,
            version=row.version,
            description=(row.json_doc or {}).get("metadata", {}).get("description"),
            is_default=row.name == ctx.project.default_workflow,
            open_items=int(ouverts.get(row.name, 0)),
            created_by=row.created_by,
            updated_at=row.created_at,
            can_share=can_share,
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
    return workflow_dto(await _actif(session, ctx, name), ctx.project)


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
    return workflow_dto(row, ctx.project)


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
    return [workflow_dto(row, ctx.project) for row in rows]


@router.get(
    "/projects/{id}/workflows/{name}/activity",
    response_model=WorkflowActivity,
    operation_id="getWorkflowActivity",
)
async def workflow_activity(
    name: str, ctx: ProjectCtx, session: Db, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> WorkflowActivity:
    """Where a workflow's open work items wait, by state, and what each transition's runs did over the
    last `days` days (count, outcome, cost, median duration): what the map shows on its states and
    steps. Every version of the workflow counts. Read only."""
    from statistics import median

    from ..db.models import Run

    await _actif(session, ctx, name)
    versions = select(WorkflowDef.id).where(WorkflowDef.project_id == ctx.id, WorkflowDef.name == name)
    open_rows = (
        await session.execute(
            select(WorkItem.state, func.count(), func.min(WorkItem.created_at))
            .where(
                WorkItem.project_id == ctx.id,
                WorkItem.workflow_def_id.in_(versions),
                WorkItem.closed_at.is_(None),
            )
            .group_by(WorkItem.state)
            .order_by(WorkItem.state)
        )
    ).all()
    since = utcnow() - timedelta(days=days)
    runs = (
        await session.execute(
            select(Run.transition_id, Run.status, Run.cost_usd, Run.started_at, Run.ended_at)
            .join(WorkItem, WorkItem.id == Run.work_item_id)
            .where(
                Run.project_id == ctx.id,
                WorkItem.workflow_def_id.in_(versions),
                Run.transition_id.is_not(None),
                Run.created_at >= since,
            )
        )
    ).all()
    by_step: dict[str, list[Any]] = {}
    for row in runs:
        by_step.setdefault(str(row.transition_id), []).append(row)
    steps = []
    for transition, rows in sorted(by_step.items()):
        durations = [
            (row.ended_at - row.started_at).total_seconds() / 60
            for row in rows
            if row.started_at is not None and row.ended_at is not None
        ]
        steps.append(
            StepActivity(
                transition=transition,
                runs=len(rows),
                succeeded=sum(1 for row in rows if row.status == "succeeded"),
                failed=sum(1 for row in rows if row.status in ("failed", "timed_out")),
                cost_usd=round(sum(float(row.cost_usd or 0) for row in rows), 4),
                median_minutes=round(median(durations), 1) if durations else None,
            )
        )
    return WorkflowActivity(
        workflow=name,
        days=days,
        states=[
            StateActivity(state=state, open_items=count, oldest_created_at=oldest)
            for state, count, oldest in open_rows
        ],
        steps=steps,
    )


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
    return workflow_dto(row, ctx.project)


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
    """The project's policy as written, and what the organisation's ceiling brings down in it."""
    row = await active_policy(session, ctx.id)
    if row is None:
        _, row = await ensure_defaults(session, ctx.project)
    _, clamps = await effective_policy_and_clamps(session, ctx.id)
    return PolicyDto(
        id=row.id,
        name=row.name,
        version=row.version,
        yaml=row.yaml,
        json_doc=row.json_doc,
        is_active=row.is_active,
        clamps=clamps,
    )


@router.get("/projects/{id}/network", response_model=ProjectNetwork, operation_id="getProjectNetwork")
async def get_project_network(ctx: ProjectCtx, session: Db) -> ProjectNetwork:
    """What the project's runs may reach: its own `sandbox.network`, the organisation's ceiling, and the
    effective lists once the ceiling is applied (the repository host is implied)."""
    row = await active_policy(session, ctx.id)
    stored = policy_model(row)
    _, clamps = await effective_policy_and_clamps(session, ctx.id)
    effective, ceiling = await project_effective_network(session, ctx.project)
    return ProjectNetwork(
        policy=stored.sandbox.network.model_dump(mode="json"),
        ceiling=ceiling.network.model_dump(mode="json") if ceiling else None,
        effective=EffectiveNetworkDto(
            allow=effective.allow,
            deny=effective.deny,
            implied=effective.implied,
            per_host_per_minute=effective.per_host_per_minute,
            per_run_per_minute=effective.per_run_per_minute,
            org_per_host_per_minute=effective.org_per_host_per_minute,
            builtin_web_tools=effective.builtin_web_tools,
        ),
        clamps=[c for c in clamps if c.startswith("network.")],
    )


@router.put("/projects/{id}/policy", response_model=PolicyDto, operation_id="putPolicy")
async def put_policy(ctx: ProjectCtx, body: PolicyPut, session: Db) -> PolicyDto:
    ctx.require(Permission.POLICY_WRITE)
    policy = parse_policy(body.yaml)
    # Below the organisation's ceiling a project chooses; above it, it does not (S27-05).
    widened = widenings(policy, ceiling_of(await session.get(Organization, ctx.project.org_id)))
    if widened:
        raise unprocessable(
            "the policy is wider than the organisation's ceiling: " + "; ".join(widened),
            [{"loc": ["policy", *w.split(":", 1)[0].split(".")], "msg": w} for w in widened],
        )
    # Valide, mais `approvals.*` et `review.require_human_for_risk` n'y gardent rien (ADR 0044).
    signaler_ce_que_la_politique_n_applique_pas(policy, ctx.project.slug)
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


def _ceiling_dto(organisation: Organization) -> PolicyCeilingDto:
    stored = (organisation.settings or {}).get(CEILING_KEY) or {}
    return PolicyCeilingDto(
        ceiling=stored.get("ceiling"),
        updated_by=stored.get("updated_by"),
        updated_at=stored.get("updated_at"),
    )


async def _organisation(session: AsyncSession, principal: Principal, org: str) -> Organization:
    if principal.org_roles.get(org) is None:
        raise forbidden(f"reading the policy ceiling of {org} requires being a member of it")
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", org)
    return organisation


@router.get("/orgs/{org}/policy-ceiling", response_model=PolicyCeilingDto, operation_id="getPolicyCeiling")
async def get_policy_ceiling(org: str, session: Db, principal: Me) -> PolicyCeilingDto:
    """The caps every project policy of the organisation stays under (S27-05)."""
    return _ceiling_dto(await _organisation(session, principal, org))


@router.put("/orgs/{org}/policy-ceiling", response_model=PolicyCeilingDto, operation_id="putPolicyCeiling")
async def put_policy_ceiling(
    org: str, body: PolicyCeilingPut, session: Db, principal: Me
) -> PolicyCeilingDto:
    """Sets the ceiling (`org_admin`). A project policy already above it is not refused: the policy
    that runs is clamped to it, and the project's policy page says what was clamped."""
    organisation = await _organisation(session, principal, org)
    if principal.org_roles.get(org) != Role.ORG_ADMIN:
        raise forbidden("setting the policy ceiling needs the org_admin role")
    settings = dict(organisation.settings or {})
    if body.ceiling is None:
        settings.pop(CEILING_KEY, None)
    else:
        try:
            ceiling = PolicyCeiling.model_validate(body.ceiling)
        except PydanticValidationError as exc:
            raise unprocessable(
                "not a policy ceiling",
                [{"loc": ["ceiling", *map(str, e["loc"])], "msg": e["msg"]} for e in exc.errors()],
            ) from exc
        settings[CEILING_KEY] = {
            "ceiling": ceiling.model_dump(mode="json", exclude_defaults=True),
            "updated_by": principal.email,
            "updated_at": utcnow().isoformat(),
        }
    organisation.settings = settings
    await session.flush()
    await record(
        session,
        principal,
        "policy.ceiling",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        ceiling=(settings.get(CEILING_KEY) or {}).get("ceiling"),
    )
    return _ceiling_dto(organisation)
