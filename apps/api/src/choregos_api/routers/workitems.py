# SPDX-License-Identifier: Apache-2.0
"""Work items : liste, détail, timeline, décisions humaines, actions de contrôle."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_contracts import Control, EventType, HumanDecision, HumanRequestKind
from choregos_core import WorkflowEngine, utcnow
from fastapi import APIRouter, Query, status
from sqlalchemy import select

from ..audit import record
from ..db.models import HumanRequest, Project, Run, WorkflowDef, WorkItem
from ..deps import Db, Me, Pagination, ProjectCtx, resolve_project
from ..errors import conflict, forbidden, not_found, unprocessable
from ..greffons import DemandeDeGeste, controler
from ..logging import get_logger
from ..rbac import Permission
from ..schemas import (
    DecisionRequest,
    HumanRequestDto,
    PageMeta,
    TimelineEntry,
    WorkItemAction,
    WorkItemCreate,
    WorkItemDto,
    WorkItemPage,
    WorkItemUpdate,
)
from ..services import (
    chronologie,
    creer_un_ticket,
    human_request_dto,
    persist_event,
    work_item_dto,
    workflow_du_ticket,
    workflow_model,
)
from ..services.routage import valider_les_champs
from ..temporal import deliver_control, deliver_decision, get_temporal, interpreter_id

router = APIRouter(tags=["work-items"])
logger = get_logger("choregos.work_items")


async def _load(session: Any, item_id: str) -> tuple[WorkItem, Project]:
    item = await session.get(WorkItem, item_id)
    if item is None:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == item_id).limit(1))
        ).scalar_one_or_none()
    if item is None:
        raise not_found("Work item", item_id)
    project = await session.get(Project, item.project_id)
    if project is None:
        raise not_found("Project", item.project_id)
    return item, project


@router.get("/projects/{id}/work-items", response_model=WorkItemPage, operation_id="listWorkItems")
async def list_work_items(
    ctx: ProjectCtx,
    session: Db,
    page: Pagination,
    state: Annotated[str | None, Query()] = None,
    size: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> WorkItemPage:
    query = select(WorkItem).where(WorkItem.project_id == ctx.id)
    if state:
        query = query.where(WorkItem.state == state)
    if size:
        query = query.where(WorkItem.size == size)
    rows = (await session.execute(query.order_by(WorkItem.created_at.desc()))).scalars().all()
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r.title.lower() or needle in r.tracker_key.lower()]
    window, cursor, has_more = page.slice(list(rows))
    return WorkItemPage(
        items=[await work_item_dto(session, item, ctx.project) for item in window],
        meta=PageMeta(next_cursor=cursor, has_more=has_more),
    )


@router.post(
    "/projects/{id}/work-items",
    response_model=WorkItemDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWorkItem",
)
async def create_work_item(ctx: ProjectCtx, body: WorkItemCreate, session: Db) -> WorkItemDto:
    """Pose une demande dans Choregos — seulement quand le tracker est interne.

    `tracker: internal` était documenté comme la façon de faire tourner un métier sans
    GitHub ni Jira, et rien ne permettait de créer une demande (état des lieux du
    2026-09-24) : ni route, ni commande, ni écran. La clé est frappée par la plateforme
    (`<PRÉFIXE>-<n>`), l'état initial est celui du workflow du projet, et l'interpréteur
    démarre tout de suite sauf `start: false`.
    """
    ctx.require(Permission.ITEM_CONTROL)
    item = await creer_un_ticket(session, ctx.principal, ctx.project, body)
    return await work_item_dto(session, item, ctx.project)


@router.get("/work-items/{id}", response_model=WorkItemDto, operation_id="getWorkItem")
async def get_work_item(id: str, session: Db, principal: Me) -> WorkItemDto:
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()
    return await work_item_dto(session, item, project, with_temporal=True)


@router.patch("/work-items/{id}", response_model=WorkItemDto, operation_id="updateWorkItem")
async def update_work_item(id: str, body: WorkItemUpdate, session: Db, principal: Me) -> WorkItemDto:
    """Les champs d'un ticket changent (ADR 0031, S20-05) : validés par SON workflow, celui où il est
    épinglé, et l'interpréteur l'apprend — une date d'arrivée déplacée réarme l'action qui l'attend."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.ITEM_CONTROL, org_slug, project.slug):
        raise forbidden("changing the fields of a work item needs at least the developer role")
    workflow = workflow_model(await workflow_du_ticket(session, item))
    champs = {k: v for k, v in {**(item.fields or {}), **body.fields}.items() if v is not None}
    valider_les_champs(workflow.metadata.inputs, champs, workflow.metadata.name)
    item.fields = champs
    await record(
        session,
        principal,
        "workitem.fields",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        fields=sorted(body.fields),
    )
    if item.temporal_wf_id:
        try:
            await get_temporal().signal(
                interpreter_id(project.slug, item.tracker_key), "fields_changed", {"fields": champs}
            )
        except Exception as erreur:  # un ticket fini n'a plus d'interpréteur : les champs restent
            logger.warning("champs non transmis à l'interpréteur", work_item=item.id, erreur=str(erreur))
    return await work_item_dto(session, item, project, with_temporal=True)


@router.get(
    "/work-items/{id}/timeline", response_model=list[TimelineEntry], operation_id="getWorkItemTimeline"
)
async def timeline(id: str, session: Db, principal: Me) -> list[TimelineEntry]:
    """États, runs, décisions, findings : l'histoire complète d'un ticket, dans l'ordre."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.PROJECT_READ, org_slug, project.slug):
        raise forbidden()

    return await chronologie(session, item)


async def _completer_la_tache(
    session: Any, item: WorkItem, demande: HumanRequest, body: DecisionRequest
) -> tuple[dict[str, Any], str | None]:
    """Une tâche faite (S20-06) : ses valeurs validées par SON formulaire — rien qui n'y soit —,
    l'attestation quand elle en demande une, puis les valeurs versées dans les champs du ticket,
    validés à leur tour par le workflow épinglé. La décision les garde, avec la phrase attestée telle
    qu'elle a été montrée : c'est la preuve."""
    import jsonschema

    payload = dict(demande.payload or {})
    formulaire = dict(payload.get("form") or {})
    hors = sorted(set(body.values) - set(formulaire.get("properties") or {}))
    if hors:
        raise unprocessable(f"not in the task's form: {', '.join(hors)}")
    erreurs = sorted(
        jsonschema.Draft202012Validator(formulaire).iter_errors(body.values), key=lambda e: list(e.path)
    )
    if erreurs:
        raise unprocessable(
            "the task is not filled in", [{"loc": ["values", *e.path], "msg": e.message} for e in erreurs]
        )
    attest = payload.get("attest")
    if attest and not body.attested:
        raise unprocessable(f"the task asks you to attest: “{attest}”")
    workflow = workflow_model(await workflow_du_ticket(session, item))
    champs = {**(item.fields or {}), **body.values}
    valider_les_champs(workflow.metadata.inputs, champs, workflow.metadata.name)
    item.fields = champs
    return dict(body.values), (str(attest) if attest else None)


@router.post(
    "/work-items/{id}/decisions",
    response_model=HumanRequestDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="postDecision",
)
async def post_decision(id: str, body: DecisionRequest, session: Db, principal: Me) -> HumanRequestDto:
    """Enregistre la décision, la transmet au workflow, et ferme la demande humaine."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.ITEM_DECIDE, org_slug, project.slug):
        raise forbidden("deciding needs at least the developer role")

    query = select(HumanRequest).where(
        HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None)
    )
    if body.request_id:
        query = select(HumanRequest).where(HumanRequest.id == body.request_id)
    request_row = (
        await session.execute(query.order_by(HumanRequest.requested_at.desc()).limit(1))
    ).scalar_one_or_none()
    if request_row is None:
        raise conflict("no human request is pending on this work item")
    if request_row.decided_at is not None:
        raise conflict("this request has already been decided")
    await controler(
        session,
        DemandeDeGeste(
            "workitem.decision",
            org_slug,
            principal,
            {
                "project": project.slug,
                "work_item": item.tracker_key,
                "transition_id": request_row.transition_id,
                "request_kind": request_row.kind,
                "request_payload": request_row.payload,
                "decision": body.kind,
            },
        ),
    )

    tache = request_row.kind == HumanRequestKind.TASK.value
    if body.kind == "complete" and not tache:
        raise unprocessable("only a task is completed (`complete`); here: approve, reject or answer")
    if tache and body.kind not in {"complete", "reject"}:
        raise unprocessable("a task is completed (`complete`, with its values) or sent back (`reject`)")
    valeurs, attestation = (
        await _completer_la_tache(session, item, request_row, body) if body.kind == "complete" else ({}, None)
    )

    kind_map = {
        "approve": HumanRequestKind.APPROVAL,
        "reject": HumanRequestKind.APPROVAL,
        "answer": HumanRequestKind.QUESTION,
        "scope_change": HumanRequestKind.SCOPE_CHANGE,
        "complete": HumanRequestKind.TASK,
    }
    decision = HumanDecision(
        request_id=request_row.id,
        kind=HumanRequestKind.TASK if tache else kind_map[body.kind],
        approved=body.kind in {"approve", "scope_change", "complete"} if body.kind != "reject" else False,
        answer=body.answer,
        granted_paths=body.granted_paths,
        reason=body.reason,
        values=valeurs,
        attestation=attestation,
        decided_by=principal.email,
        channel="web",
    )
    request_row.decided_at = utcnow()
    request_row.decided_by = principal.email
    request_row.decision = decision.model_dump(mode="json")

    await deliver_decision(project.slug, item.tracker_key, decision)
    await persist_event(
        session,
        EventType.WORKITEM_HUMAN_DECIDED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=item.tracker_key,
        kind=body.kind,
        by=principal.email,
        **({"fields": sorted(valeurs), "attestation": attestation} if tache else {}),
    )
    await record(
        session,
        principal,
        "workitem.decision",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        kind=body.kind,
    )
    return human_request_dto(request_row)


async def _cible_de_migration(
    session: Any, item: WorkItem, project: Project, body: WorkItemAction
) -> dict[str, Any]:
    """La définition cible d'un `migrate`, lue et vérifiée ICI (ADR 0031).

    L'interpréteur ne fait aucune entrée/sortie : il reçoit la définition elle-même, pas son
    identifiant. L'état courant du ticket doit exister dans la cible, ou y être mappé — sinon 422,
    et rien n'est transmis. Un ticket occupé (décision humaine attendue, run en cours) changerait
    d'état avant d'avoir migré, et le mapping vérifié ici ne vaudrait plus : 409.
    """
    if not body.workflow_def_id:
        raise unprocessable("migrate needs workflow_def_id: the version to migrate the work item to")
    ligne = await session.get(WorkflowDef, body.workflow_def_id)
    if ligne is None or ligne.project_id != project.id:
        raise not_found("Workflow definition", body.workflow_def_id)
    attend = select(HumanRequest.id).where(
        HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None)
    )
    if (await session.execute(attend.limit(1))).first() is not None:
        raise conflict(
            "the work item is waiting for a human decision: decide, or stop it, before migrating it"
        )
    tourne = select(Run.id).where(Run.work_item_id == item.id, Run.status.in_(("queued", "running")))
    if (await session.execute(tourne.limit(1))).first() is not None:
        raise conflict(
            "a run of this work item is in progress: pause the work item, let the run finish, then migrate"
        )
    cible = workflow_model(ligne)
    try:
        WorkflowEngine(cible).can_migrate_to(cible, item.state, body.state_mapping)
    except ValueError as erreur:
        raise unprocessable(str(erreur)) from erreur
    return cible.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.post(
    "/work-items/{id}/actions", status_code=status.HTTP_202_ACCEPTED, operation_id="postWorkItemAction"
)
async def post_action(id: str, body: WorkItemAction, session: Db, principal: Me) -> dict[str, str]:
    """pause, resume, stop, migrate, rerun_stage, mark_agent_ready."""
    item, project = await _load(session, id)
    _, org_slug = await resolve_project(session, project.id)
    if not principal.can(Permission.ITEM_CONTROL, org_slug, project.slug):
        raise forbidden("controlling a work item needs at least the developer role")

    if body.action == "mark_agent_ready":
        payload = {
            "project_id": project.id,
            "project_slug": project.slug,
            "work_item_id": item.id,
            "tracker_key": item.tracker_key,
        }
        workflow_id = interpreter_id(project.slug, item.tracker_key)
        await get_temporal().start_interpreter(workflow_id, payload)
        item.temporal_wf_id = workflow_id
    else:
        cible = await _cible_de_migration(session, item, project, body) if body.action == "migrate" else None
        control = Control(
            action=body.action,
            workflow_def_id=body.workflow_def_id,
            workflow=cible,
            state_mapping=body.state_mapping,
            run_id=body.run_id,
            reason=body.reason,
            by=principal.email,
        )
        await deliver_control(project.slug, item.tracker_key, control)
        if body.action == "pause":
            item.paused = True
        elif body.action in {"resume", "stop"}:
            item.paused = False

    await record(
        session,
        principal,
        f"workitem.{body.action}",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        reason=body.reason,
    )
    return {"status": "accepted", "action": body.action}
