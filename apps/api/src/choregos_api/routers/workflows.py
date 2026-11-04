# SPDX-License-Identifier: Apache-2.0
"""Workflow and policy of a project: reading, localised validation, activation."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from choregos_contracts import Role
from choregos_core import parse_policy, parse_workflow, to_graph, to_process, utcnow
from choregos_core.calibration import DEFAULT_TARGET
from choregos_core.dsl import TEMPLATE_NAMES, load_template, template_yaml
from choregos_core.policy import unenforced_governance
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
    RoutingPreviewDto,
    RoutingPreviewRequest,
    StateActivity,
    StepActivity,
    StepCalibration,
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
from ..services.decision_ledger import step_calibration as measure_step
from ..services.definitions import CEILING_KEY, ceiling_of, policy_engine, policy_model
from ..services.llm_routing import book
from ..services.memory_policy import (
    active_workflows_of,
    log_memory_disabled_warnings,
    memory_disabled_warnings,
)
from ..services.network import project_effective_network
from ..services.organisations import organisation_by_slug
from ..services.routage import Naissance, route

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
    """Validation without side effects: what the front-end editor calls on every keystroke."""
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
    # An agent that asks for memory the project's policy disables gets none: said, not refused (S51-01).
    engine = await policy_engine(session, ctx.id)
    for issue in await memory_disabled_warnings(
        session, ctx.project.org_id, [workflow], memory_enabled=engine.memory_enabled()
    ):
        validation.warnings.append(
            WorkflowIssue(code=issue["code"], message=issue["message"], path=issue["path"])
        )
    return validation


@router.post("/workflows/edit", response_model=WorkflowEditResult, operation_id="editWorkflow")
async def edit(body: WorkflowEditRequest) -> WorkflowEditResult:
    """Typed operations, grafted into the text; nothing is saved (ADR 0031).

    The console's map and process view call it on every gesture: the rendered text is read back
    as a diff, undone through `inverse`, and saved by the workflow's PUT, with `base_version`.
    """
    import difflib

    from choregos_core.dsl.edition import EditRefused
    from choregos_core.dsl.edition import edit as edit_text
    from pydantic import ValidationError as ModelError

    try:
        edition = edit_text(body.yaml, body.operations)
    except ModelError as model_error:
        raise unprocessable(
            "malformed operation",
            [{"loc": ["operations", *e["loc"]], "msg": e["msg"]} for e in model_error.errors()],
        ) from model_error
    except EditRefused as refusal:
        raise unprocessable(str(refusal)) from refusal
    report = await validate(WorkflowValidateRequest(yaml=edition.yaml))
    diff = "".join(
        difflib.unified_diff(
            body.yaml.splitlines(keepends=True), edition.yaml.splitlines(keepends=True), "before", "after"
        )
    )
    return WorkflowEditResult(
        **report.model_dump(),
        yaml=edition.yaml,
        diff=diff,
        inverse=[o.model_dump(mode="json", by_alias=True, exclude_none=True) for o in edition.inverse],
        notices=edition.warnings,
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
    """The alias of the DEFAULT workflow (ADR 0031)."""
    row = await default_workflow(session, ctx.id)
    if row is None:
        row, _ = await ensure_defaults(session, ctx.project)
    return workflow_dto(row, ctx.project)


@router.put("/projects/{id}/workflow", response_model=WorkflowDefDto, operation_id="putWorkflow")
async def put_workflow(ctx: ProjectCtx, body: WorkflowPut, session: Db) -> WorkflowDefDto:
    """The alias of the default: what it publishes BECOMES the default. An invalid workflow gets 422."""
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
    active = (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == ctx.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.name)
        )
    ).scalars()
    # A `Result` has a `keys()` method (its columns): `dict(result)` would index it. Iterate over it instead.
    counts = await session.execute(
        select(WorkflowDef.name, func.count(WorkItem.id))
        .join(WorkItem, WorkItem.workflow_def_id == WorkflowDef.id)
        .where(WorkflowDef.project_id == ctx.id, WorkItem.closed_at.is_(None))
        .group_by(WorkflowDef.name)
    )
    open_counts: dict[str, int] = {str(workflow_name): int(n) for workflow_name, n in counts.tuples()}
    from ..services.agent_governance import is_admin

    can_share = is_admin(ctx.principal, ctx.org_slug)
    return [
        WorkflowSummaryDto(
            name=row.name,
            version=row.version,
            description=(row.json_doc or {}).get("metadata", {}).get("description"),
            is_default=row.name == ctx.project.default_workflow,
            open_items=int(open_counts.get(row.name, 0)),
            created_by=row.created_by,
            updated_at=row.created_at,
            can_share=can_share,
        )
        for row in active
    ]


async def _active(session: Any, ctx: Any, name: str) -> WorkflowDef:
    row = await workflow_actif(session, ctx.id, name)
    if row is None:
        raise not_found("Workflow", name)
    return row


@router.get("/projects/{id}/workflows/{name}", response_model=WorkflowDefDto, operation_id="getNamedWorkflow")
async def get_named_workflow(name: str, ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    return workflow_dto(await _active(session, ctx, name), ctx.project)


@router.put("/projects/{id}/workflows/{name}", response_model=WorkflowDefDto, operation_id="putNamedWorkflow")
async def put_named_workflow(name: str, ctx: ProjectCtx, body: WorkflowPut, session: Db) -> WorkflowDefDto:
    """Publish the next version of THIS workflow, leaving the others alone (ADR 0031)."""
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


def _routing_targets(project: Project) -> set[str]:
    return {str(rule.get("workflow")) for rule in (project.workflow_routing or [])}


@router.post(
    "/projects/{id}/workflows/{name}/deactivate",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deactivateWorkflow",
)
async def deactivate_workflow(name: str, ctx: ProjectCtx, session: Db) -> Response:
    """A deactivated workflow receives no more tickets; its pinned tickets finish on their version."""
    ctx.require(Permission.WORKFLOW_WRITE)
    row = await _active(session, ctx, name)
    if name == ctx.project.default_workflow:
        raise conflict(f"`{name}` is the default workflow: choose another one before deactivating it")
    if name in _routing_targets(ctx.project):
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

    from choregos_contracts import EventType

    from ..db.models import Event, Run

    await _active(session, ctx, name)
    versions = select(WorkflowDef.id).where(WorkflowDef.project_id == ctx.id, WorkflowDef.name == name)
    open_items = (
        await session.execute(
            select(WorkItem.id, WorkItem.state, WorkItem.created_at).where(
                WorkItem.project_id == ctx.id,
                WorkItem.workflow_def_id.in_(versions),
                WorkItem.closed_at.is_(None),
            )
        )
    ).all()
    # When each open item entered its state: its last `state_changed` event into that state, else its
    # filing (an item still in its initial state has none).
    entered: dict[str, Any] = {item.id: item.created_at for item in open_items}
    state_of = {item.id: item.state for item in open_items}
    if open_items:
        changes = (
            await session.execute(
                select(Event.work_item_id, Event.payload, Event.ts)
                .where(
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                    Event.work_item_id.in_(list(state_of)),
                )
                .order_by(Event.ts)
            )
        ).all()
        for change in changes:
            if (change.payload or {}).get("to") == state_of.get(str(change.work_item_id)):
                entered[str(change.work_item_id)] = change.ts
    by_state: dict[str, list[Any]] = {}
    for item in open_items:
        by_state.setdefault(item.state, []).append(entered[item.id])
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
            StateActivity(state=state, open_items=len(times), waiting_since=min(times))
            for state, times in sorted(by_state.items())
        ],
        steps=steps,
    )


@router.get(
    "/projects/{id}/workflows/{name}/steps/{step}/calibration",
    response_model=StepCalibration,
    operation_id="getStepCalibration",
)
async def step_calibration(
    name: str,
    step: str,
    ctx: ProjectCtx,
    session: Db,
    target: Annotated[float, Query(gt=0, le=1)] = DEFAULT_TARGET,
) -> StepCalibration:
    """How well a step's probabilities predicted what followed (ADR 0121 §7): for each question and
    model version, the reliability bins, the expected calibration error, the coverage and accuracy at
    each threshold from 0.50 to 0.95, and the lowest threshold that reaches `target` accuracy (none
    under 30 observed decisions). `step` is the state the step runs from. Every version of the
    workflow counts. Read only."""
    await _active(session, ctx, name)
    return await measure_step(session, ctx.id, name, step, target)


@router.post(
    "/projects/{id}/workflows/{name}/versions/{version}/restore",
    response_model=WorkflowDefDto,
    operation_id="restoreWorkflowVersion",
)
async def restore_version(name: str, version: int, ctx: ProjectCtx, session: Db) -> WorkflowDefDto:
    """Restoring means republishing: the old version becomes the next one. Nothing is overwritten."""
    ctx.require(Permission.WORKFLOW_WRITE)
    earlier = (
        await session.execute(
            select(WorkflowDef).where(
                WorkflowDef.project_id == ctx.id, WorkflowDef.name == name, WorkflowDef.version == version
            )
        )
    ).scalar_one_or_none()
    if earlier is None:
        raise not_found("Version", f"{name} v{version}")
    row = await publier_workflow(
        session, ctx.principal, ctx.project, earlier.yaml, source=earlier.source, nom_attendu=name
    )
    return workflow_dto(row, ctx.project)


@router.get(
    "/projects/{id}/workflow-routing", response_model=WorkflowRoutingDto, operation_id="getWorkflowRouting"
)
async def get_routing(ctx: ProjectCtx, session: Db) -> WorkflowRoutingDto:
    default_name = ctx.project.default_workflow
    if not default_name:
        row = await default_workflow(session, ctx.id)
        default_name = row.name if row is not None else ""
    return WorkflowRoutingDto(
        default=default_name,
        rules=list(ctx.project.workflow_routing or []),
        strategy=ctx.project.routing_strategy or "labels",
        hints=dict(ctx.project.routing_hints or {}),
        min_confidence=ctx.project.routing_min_confidence,
    )


@router.put(
    "/projects/{id}/workflow-routing", response_model=WorkflowRoutingDto, operation_id="putWorkflowRouting"
)
async def put_routing(ctx: ProjectCtx, body: WorkflowRoutingDto, session: Db) -> WorkflowRoutingDto:
    """The default and the rules that choose the workflow of a ticket coming from a tracker."""
    ctx.require(Permission.WORKFLOW_WRITE)
    active = set(
        (
            await session.execute(
                select(WorkflowDef.name).where(
                    WorkflowDef.project_id == ctx.id, WorkflowDef.is_active.is_(True)
                )
            )
        ).scalars()
    )
    unknown = sorted({body.default, *(r.workflow for r in body.rules), *(body.hints or {})} - active)
    if unknown:
        raise unprocessable(f"unknown or inactive workflow(s): {', '.join(unknown)}")
    ctx.project.default_workflow = body.default
    ctx.project.workflow_routing = [r.model_dump(mode="json") for r in body.rules]
    if body.strategy is not None:
        ctx.project.routing_strategy = body.strategy
    if body.hints is not None:
        ctx.project.routing_hints = {name: hint.strip() for name, hint in body.hints.items() if hint.strip()}
    if "min_confidence" in body.model_fields_set:
        ctx.project.routing_min_confidence = body.min_confidence
    await record(
        session,
        ctx.principal,
        "workflow.routing",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        default=body.default,
        rules=len(body.rules),
        strategy=ctx.project.routing_strategy,
        hints=sorted(ctx.project.routing_hints or {}),
        min_confidence=ctx.project.routing_min_confidence,
    )
    return body.model_copy(
        update={
            "strategy": ctx.project.routing_strategy or "labels",
            "hints": dict(ctx.project.routing_hints or {}),
            "min_confidence": ctx.project.routing_min_confidence,
        }
    )


@router.post(
    "/projects/{id}/workflow-routing/preview",
    response_model=RoutingPreviewDto,
    operation_id="previewWorkflowRouting",
)
async def preview_routing(ctx: ProjectCtx, body: RoutingPreviewRequest, session: Db) -> RoutingPreviewDto:
    """Where a work item would be born, and why (ADR 0116). Nothing is created; a call to the model
    is real spend, booked on the project."""
    ctx.require(Permission.WORKFLOW_WRITE)
    routed = await route(
        session,
        ctx.project,
        Naissance(
            tracker_key="preview",
            title=body.title,
            body=body.body,
            labels=tuple(body.labels),
            item_type=body.item_type,
        ),
    )
    if routed.decision is not None:
        await book(session, ctx.project, routed.decision, None)
    return RoutingPreviewDto(
        workflow=routed.row.name if routed.row is not None else None,
        by=routed.by,
        reason=routed.reason,
        model=routed.decision.model if routed.decision is not None else None,
        confidence=routed.decision.confidence if routed.decision is not None else None,
    )


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
    # A guard nobody applies is refused when written, not warned about (ADR 0101).
    refused = unenforced_governance(policy)
    if refused:
        raise unprocessable(
            "the policy declares guards Choregos does not enforce (ADR 0101): "
            + "; ".join(i.message for i in refused),
            [
                {"loc": ["policy", *(i.path or "").split(".")], "msg": i.message, "code": i.code}
                for i in refused
            ],
        )
    # Below the organisation's ceiling a project chooses; above it, it does not (S27-05).
    widened = widenings(policy, ceiling_of(await session.get(Organization, ctx.project.org_id)))
    if widened:
        raise unprocessable(
            "the policy is wider than the organisation's ceiling: " + "; ".join(widened),
            [{"loc": ["policy", *w.split(":", 1)[0].split(".")], "msg": w} for w in widened],
        )
    # The network warnings stay logged: they are not refused.
    signaler_ce_que_la_politique_n_applique_pas(policy, ctx.project.slug)
    # Turning memory off under workflows whose agents ask for it: said, never refused (S51-01).
    log_memory_disabled_warnings(
        await memory_disabled_warnings(
            session,
            ctx.project.org_id,
            await active_workflows_of(session, ctx.id),
            memory_enabled=policy.memory.enabled,
        ),
        ctx.project.slug,
    )
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


async def _member_organisation(session: AsyncSession, principal: Principal, org: str) -> Organization:
    if principal.org_roles.get(org) is None:
        raise forbidden(f"reading the policy ceiling of {org} requires being a member of it")
    return await organisation_by_slug(session, org)


@router.get("/orgs/{org}/policy-ceiling", response_model=PolicyCeilingDto, operation_id="getPolicyCeiling")
async def get_policy_ceiling(org: str, session: Db, principal: Me) -> PolicyCeilingDto:
    """The caps every project policy of the organisation stays under (S27-05)."""
    return _ceiling_dto(await _member_organisation(session, principal, org))


@router.put("/orgs/{org}/policy-ceiling", response_model=PolicyCeilingDto, operation_id="putPolicyCeiling")
async def put_policy_ceiling(
    org: str, body: PolicyCeilingPut, session: Db, principal: Me
) -> PolicyCeilingDto:
    """Sets the ceiling (`org_admin`). A project policy already above it is not refused: the policy
    that runs is clamped to it, and the project's policy page says what was clamped."""
    organisation = await _member_organisation(session, principal, org)
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
