"""API interne : le seul canal d'écriture d'un runner (JWT de run, portée = un run).

Aucune de ces routes n'accepte une session humaine ; aucun token GitHub large ne
descend dans le workspace : tout passe ici (§1.10, §2.2).
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import ContextPack, EventType, Finding, StageInput, StageResult
from choregos_core import PolicyEngine, matches_any, utcnow
from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from ..catalogue import appeler as appeler_outil
from ..catalogue import outils_du_projet
from ..db.models import CostLedger, HumanRequest, Project, Run, RunEvent, WorkItem
from ..db.models import Finding as FindingRow
from ..deps import Db, RunAuth
from ..errors import ApiError, conflict, not_found
from ..schemas import (
    CiLogs,
    FindingAck,
    QuestionIn,
    RunEventsBatch,
    RunTicket,
    RunTicketComment,
    ScopeChangeDecision,
    ScopeChangeRequestIn,
)
from ..services import active_policy, persist_event, policy_model
from ..temporal import get_temporal, interpreter_id

router = APIRouter(tags=["internal"], prefix="/internal")


async def _run_and_item(session: Any, run_id: str) -> tuple[Run, WorkItem, Project]:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", run_id)
    item = await session.get(WorkItem, run.work_item_id)
    project = await session.get(Project, run.project_id)
    if item is None or project is None:
        raise not_found("Ticket du run", run_id)
    return run, item, project


@router.get("/runs/{id}/input", response_model=StageInput, operation_id="getRunInput")
async def get_input(id: str, session: Db, claims: RunAuth) -> StageInput:
    """Le `StageInput` du run. Si le résultat est déjà posté, 409 : le runner sort en 0."""
    run, _, _ = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("résultat déjà posté pour ce run")
    if not run.stage_input:
        raise not_found("StageInput", id)
    return StageInput.model_validate(run.stage_input)


@router.post("/runs/{id}/events", status_code=status.HTTP_202_ACCEPTED, operation_id="postRunEvents")
async def post_events(id: str, body: RunEventsBatch, session: Db, claims: RunAuth) -> dict[str, int]:
    """Journal ACP en lot, idempotent par `seq` (un rejeu n'écrit pas deux fois)."""
    run, _, project = await _run_and_item(session, id)
    known = set(
        (await session.execute(select(RunEvent.seq).where(RunEvent.run_id == run.id))).scalars().all()
    )
    written = 0
    for event in body.events:
        if event.seq in known:
            continue
        session.add(
            RunEvent(
                run_id=run.id,
                seq=event.seq,
                type=event.type,
                payload=event.payload,
                ts=event.ts or utcnow(),
            )
        )
        known.add(event.seq)
        written += 1
    if written:
        await persist_event(
            session,
            EventType.RUN_PROGRESS,
            project_id=project.id,
            work_item_id=run.work_item_id,
            project_slug=project.slug,
            subject=run.id,
            run_id=run.id,
            events=written,
            last_seq=max(e.seq for e in body.events),
        )
    return {"accepted": written}


@router.post("/runs/{id}/result", operation_id="postRunResult")
async def post_result(id: str, body: StageResult, session: Db, claims: RunAuth) -> dict[str, str]:
    """Dépôt du `StageResult`. Idempotent : un second appel identique répond 200 sans rien changer."""
    run, item, project = await _run_and_item(session, id)
    payload = body.model_dump(mode="json", by_alias=True)
    if run.result is not None:
        return {"status": "already_recorded", "run_id": run.id}
    run.result = payload
    run.status = "succeeded" if body.status == "done" else "failed"
    run.ended_at = utcnow()
    if body.artifacts.transcript_url:
        run.transcript_url = body.artifacts.transcript_url
    if body.artifacts.pr_url:
        item.pr_url = body.artifacts.pr_url
    outputs = body.outputs
    if outputs.size:
        item.size = str(outputs.size)
    if outputs.risk:
        item.risk = str(outputs.risk)
    if outputs.allowed_paths:
        item.allowed_paths = list(outputs.allowed_paths)
    documents = dict(item.documents or {})
    for field in ("spec_markdown", "plan_markdown", "review_markdown", "release_notes_markdown"):
        value = getattr(outputs, field, None)
        if value:
            documents[field] = value
    item.documents = documents

    await persist_event(
        session,
        EventType.RUN_FINISHED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=run.id,
        run_id=run.id,
        stage=run.stage_role,
        status=str(body.status),
    )
    return {"status": "recorded", "run_id": run.id}


@router.post(
    "/runs/{id}/findings",
    response_model=FindingAck,
    status_code=status.HTTP_201_CREATED,
    operation_id="postRunFinding",
)
async def post_finding(id: str, body: Finding, session: Db, claims: RunAuth) -> FindingAck:
    """Dépôt d'un finding, plafonné par `policy.findings.max_per_run`."""
    run, item, project = await _run_and_item(session, id)
    engine = PolicyEngine(policy_model(await active_policy(session, project.id)))
    cap = engine.max_findings_per_run()
    existing = (
        (await session.execute(select(FindingRow).where(FindingRow.origin_run_id == run.id))).scalars().all()
    )
    if len(existing) >= cap:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Plafond de findings atteint",
            f"ce run a déjà déposé {len(existing)} findings (plafond {cap})",
        )
    row = FindingRow(
        project_id=project.id,
        origin_work_item_id=item.id,
        origin_run_id=run.id,
        title=body.title,
        type=str(body.type),
        severity=str(body.severity),
        evidence=body.evidence,
        suggested_fix=body.suggested_fix,
        estimate=str(body.estimate) if body.estimate else None,
        status="pending",
    )
    session.add(row)
    await session.flush()
    await persist_event(
        session,
        EventType.FINDING_REPORTED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=row.id,
        finding_id=row.id,
        severity=row.severity,
        title=row.title,
    )
    await get_temporal().signal(
        f"findings-{project.slug}",
        "finding",
        {
            "finding_id": row.id,
            "project_slug": project.slug,
            "origin_work_item_key": item.tracker_key,
            "origin_run_id": run.id,
            "title": body.title,
            "type": str(body.type),
            "severity": str(body.severity),
            "evidence": body.evidence,
            "suggested_fix": body.suggested_fix,
            "estimate": str(body.estimate) if body.estimate else None,
        },
    )
    return FindingAck(accepted=True, finding_id=row.id, remaining=max(0, cap - len(existing) - 1))


@router.post("/runs/{id}/scope-change", response_model=ScopeChangeDecision, operation_id="postScopeChange")
async def post_scope_change(
    id: str, body: ScopeChangeRequestIn, session: Db, claims: RunAuth
) -> ScopeChangeDecision:
    """Élargissement de périmètre : accordé automatiquement sous seuil, sinon soumis à un humain."""
    run, item, project = await _run_and_item(session, id)
    engine = PolicyEngine(policy_model(await active_policy(session, project.id)))
    current = list(run.allowed_paths or item.allowed_paths or [])
    new_paths = [p for p in body.paths if not matches_any(p, current)]
    if not new_paths:
        return ScopeChangeDecision(decision="granted", allowed_paths=current, reason="déjà dans le périmètre")
    if any(engine.path_denied(p) for p in new_paths):
        return ScopeChangeDecision(
            decision="denied",
            allowed_paths=current,
            reason="chemins interdits par la politique (deny_paths)",
        )
    if engine.scope_auto_grant(new_paths):
        granted = [*current, *new_paths]
        run.allowed_paths = granted
        item.allowed_paths = granted
        return ScopeChangeDecision(
            decision="granted", allowed_paths=granted, reason="accordé automatiquement"
        )

    request_row = HumanRequest(
        work_item_id=item.id,
        project_id=project.id,
        transition_id=run.transition_id,
        kind="scope_change",
        payload={"paths": new_paths, "justification": body.justification, "run_id": run.id},
        requested_at=utcnow(),
    )
    session.add(request_row)
    await session.flush()
    await persist_event(
        session,
        EventType.WORKITEM_HUMAN_REQUESTED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=item.tracker_key,
        kind="scope_change",
        paths=new_paths,
    )
    return ScopeChangeDecision(decision="pending", allowed_paths=current, reason="soumis à un humain")


@router.post("/runs/{id}/question", status_code=status.HTTP_202_ACCEPTED, operation_id="postRunQuestion")
async def post_question(id: str, body: QuestionIn, session: Db, claims: RunAuth) -> dict[str, str]:
    """L'agent demande un arbitrage : le run devra se terminer en `needs_human`."""
    run, item, project = await _run_and_item(session, id)
    request_row = HumanRequest(
        work_item_id=item.id,
        project_id=project.id,
        transition_id=run.transition_id,
        kind="question",
        payload={"question": body.text, "options": body.options, "run_id": run.id},
        requested_at=utcnow(),
    )
    session.add(request_row)
    await session.flush()
    await persist_event(
        session,
        EventType.WORKITEM_HUMAN_REQUESTED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=item.tracker_key,
        kind="question",
        question=body.text,
    )
    await get_temporal().signal(
        interpreter_id(project.slug, item.tracker_key),
        "question_asked",
        {"run_id": run.id, "request_id": request_row.id, "text": body.text, "options": body.options},
    )
    return {"status": "accepted", "request_id": request_row.id}


@router.get("/runs/{id}/context", response_model=ContextPack, operation_id="getRunContext")
async def get_context(id: str, session: Db, claims: RunAuth) -> ContextPack:
    run, _, _ = await _run_and_item(session, id)
    if run.context_pack:
        return ContextPack.model_validate(run.context_pack)
    return ContextPack.empty("")


@router.get("/runs/{id}/ticket", response_model=RunTicket, operation_id="getRunTicket")
async def get_ticket(id: str, session: Db, claims: RunAuth) -> RunTicket:
    _run, item, _project = await _run_and_item(session, id)
    documents = item.documents or {}
    return RunTicket(
        key=item.tracker_key,
        title=item.title,
        body=item.body_snapshot or "",
        url=item.url,
        spec_markdown=documents.get("spec_markdown"),
        plan_markdown=documents.get("plan_markdown"),
        comments=[
            RunTicketComment(author=c.get("author", ""), body=c.get("body", ""), ts=c.get("ts", utcnow()))
            for c in documents.get("comments", [])
        ],
    )


@router.get("/runs/{id}/ci-logs", response_model=CiLogs, operation_id="getRunCiLogs")
async def get_ci_logs(
    id: str, session: Db, claims: RunAuth, tail: int = Query(default=500, le=5000)
) -> CiLogs:
    """Logs du dernier échec CI, pour le rôle `fix_ci`."""
    _run, item, _project = await _run_and_item(session, id)
    documents = item.documents or {}
    logs = str(documents.get("ci_logs", ""))
    return CiLogs(logs="\n".join(logs.splitlines()[-tail:]), ref=documents.get("ci_ref"))


# ───────────────────────── catalogue d'outils ─────────────────────────
#
# Un agent qui instruit un dossier a besoin d'API tierces, et ces API ont des clés. Les lui
# donner serait lui donner de quoi dépenser sans plafond, exfiltrer dans un commit, ou
# garder. Il nomme donc un outil ; la plateforme fabrique la requête, injecte la clé, et
# compte l'appel. Son egress à lui reste fermé.


def _outils_autorises(project: Project) -> list[str]:
    """Ce que le projet déclare pouvoir appeler (`tools` de sa configuration)."""
    return [str(t) for t in ((project.config or {}).get("tools") or [])]


@router.get("/runs/{id}/tools", operation_id="getRunTools")
async def get_tools(id: str, session: Db, claims: RunAuth) -> dict[str, Any]:
    """Les outils que CE run peut appeler, au format MCP (`name`, `description`, `inputSchema`)."""
    _run, _item, project = await _run_and_item(session, id)
    outils = outils_du_projet(_outils_autorises(project))
    return {
        "tools": [
            {"name": o.name, "description": o.description, "inputSchema": o.input_schema} for o in outils
        ]
    }


@router.post("/runs/{id}/tools/{name}", operation_id="callRunTool")
async def call_tool(id: str, name: str, body: dict[str, Any], session: Db, claims: RunAuth) -> dict[str, Any]:
    """Appelle un outil du catalogue pour le compte du run, et l'inscrit au registre de coûts."""
    run, item, project = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("résultat déjà posté pour ce run")
    outil = next((o for o in outils_du_projet(_outils_autorises(project)) if o.name == name), None)
    if outil is None:
        # Ne pas distinguer « inconnu » de « non autorisé » : un agent n'a pas à découvrir
        # le catalogue du déploiement en essayant des noms.
        raise not_found("Outil", name)

    engine = PolicyEngine(policy_model(await active_policy(session, project.id)))
    plafond = engine.max_tool_calls_per_run()
    deja = (
        await session.execute(
            select(func.count())
            .select_from(CostLedger)
            .where(CostLedger.run_id == run.id, CostLedger.kind == "tool")
        )
    ).scalar_one()
    if plafond and deja >= plafond:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Plafond d'appels d'outils atteint",
            f"ce run a déjà appelé {deja} outils (plafond {plafond})",
        )

    code, corps = await appeler_outil(outil, body or {})
    session.add(
        CostLedger(
            project_id=project.id,
            work_item_id=item.id,
            run_id=run.id,
            # `ts` n'a pas de défaut côté modèle : la colonne est NOT NULL et l'oublier
            # fait échouer l'INSERT, pas la lecture.
            ts=utcnow(),
            kind="tool",
            provider=outil.provider,
            model=outil.name,
            cost_eur=outil.price_eur,
            stage_role=run.stage_role,
        )
    )
    await persist_event(
        session,
        EventType.TOOL_CALLED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=run.id,
        tool=outil.name,
        provider=outil.provider,
        status_code=code,
    )
    return {
        "status_code": code,
        "result": corps,
        "remaining": max(0, plafond - deja - 1) if plafond else None,
    }
