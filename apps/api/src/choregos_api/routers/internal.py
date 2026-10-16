# SPDX-License-Identifier: Apache-2.0
"""API interne : le seul canal d'écriture d'un runner (JWT de run, portée = un run).

Aucune de ces routes n'accepte une session humaine ; aucun token GitHub large ne
descend dans le workspace : tout passe ici (§1.10, §2.2).
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Annotated, Any

from choregos_adapters.storage import ObjectStoreError
from choregos_contracts import (
    ARTIFACT_TYPES,
    Artifact,
    ContextPack,
    EventType,
    Finding,
    StageInput,
    StageResult,
    artifact_digest,
)
from choregos_core import PolicyEngine, matches_any, utcnow
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import func, select

from ..catalogue import appeler as appeler_outil
from ..catalogue import outils_du_projet
from ..config import get_settings
from ..db.models import CostLedger, HumanRequest, Project, Run, RunEvent, WorkItem
from ..db.models import Finding as FindingRow
from ..deps import Db, RunAuth
from ..errors import ApiError, conflict, not_found, unprocessable
from ..object_store import object_store
from ..schemas import (
    CiLogs,
    FindingAck,
    GitToken,
    QuestionIn,
    RunEventsBatch,
    RunInputFile,
    RunTicket,
    RunTicketComment,
    ScopeChangeDecision,
    ScopeChangeRequestIn,
)
from ..schemas.knowledge import KnowledgeSearchRequest, KnowledgeSearchResult
from ..schemas.network import NetworkDecisionBatch, RunNetwork
from ..services import effective_policy, persist_event, ranger_les_sorties, web_fetch
from ..services.artifacts import (
    ArtifactCorruptedError,
    filename_of,
    read_content,
    record_inline_outputs,
    store_file,
    to_contract,
)
from ..services.artifacts import latest as latest_artifact
from ..services.budget_alerts import check_after_spend
from ..services.couts import dimensions_of
from ..services.network import record_decisions, run_network
from ..services.recus import DOCUMENTS_DU_PROMPT, LUE, PRODUITE, consigner, record_digests
from ..services.tickets import sorties_a_ranger
from ..temporal import get_temporal, interpreter_id

router = APIRouter(tags=["internal"], prefix="/internal")


async def _run_and_item(session: Any, run_id: str) -> tuple[Run, WorkItem, Project]:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", run_id)
    item = await session.get(WorkItem, run.work_item_id)
    project = await session.get(Project, run.project_id)
    if item is None or project is None:
        raise not_found("Work item of the run", run_id)
    return run, item, project


@router.get("/runs/{id}/input", response_model=StageInput, operation_id="getRunInput")
async def get_input(id: str, session: Db, claims: RunAuth) -> StageInput:
    """Le `StageInput` du run. Si le résultat est déjà posté, 409 : le runner sort en 0."""
    run, _, _ = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run")
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


@router.get("/runs/{id}/network", response_model=RunNetwork, operation_id="getRunNetwork")
async def get_run_network(id: str, session: Db, claims: RunAuth) -> RunNetwork:
    """The network THIS run may use (ADR 0070): the egress proxy reads it with the run's own token."""
    run, _, _ = await _run_and_item(session, id)
    return await run_network(session, run)


@router.post(
    "/runs/{id}/network/decisions",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="postRunNetworkDecisions",
)
async def post_network_decisions(
    id: str, body: NetworkDecisionBatch, session: Db, claims: RunAuth
) -> dict[str, int]:
    """What the proxy or the runner let through or refused: cumulative totals, replay-safe."""
    run, _, project = await _run_and_item(session, id)
    return {"accepted": await record_decisions(session, run, project, body)}


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
    declarees = list(((run.stage_input or {}).get("transition") or {}).get("outputs") or [])
    ranger_les_sorties(item, outputs, declarees)
    # Chaque sortie rangée reçoit son reçu « produite », sous son empreinte (S25-04).
    await consigner(
        session,
        org_id=project.org_id,
        work_item_id=item.id,
        run_id=run.id,
        stage=run.stage_role,
        sorties=sorties_a_ranger(outputs, declarees),
        genre=PRODUITE,
    )
    # And each becomes a typed artifact revision, traced to this run (S31-01, ADR 0049).
    await record_inline_outputs(
        session,
        org_id=project.org_id,
        item=item,
        run=run,
        outputs=sorties_a_ranger(outputs, declarees),
        types=dict(((run.stage_input or {}).get("transition") or {}).get("output_types") or {}),
    )

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
    engine = PolicyEngine(await effective_policy(session, project.id))
    cap = engine.max_findings_per_run()
    existing = (
        (await session.execute(select(FindingRow).where(FindingRow.origin_run_id == run.id))).scalars().all()
    )
    if len(existing) >= cap:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Findings cap reached",
            f"this run has already filed {len(existing)} findings (cap {cap})",
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
    engine = PolicyEngine(await effective_policy(session, project.id))
    current = list(run.allowed_paths or item.allowed_paths or [])
    new_paths = [p for p in body.paths if not matches_any(p, current)]
    if not new_paths:
        return ScopeChangeDecision(
            decision="granted", allowed_paths=current, reason="already within the scope"
        )
    if any(engine.path_denied(p) for p in new_paths):
        return ScopeChangeDecision(
            decision="denied",
            allowed_paths=current,
            reason="paths forbidden by the policy (deny_paths)",
        )
    if engine.scope_auto_grant(new_paths):
        granted = [*current, *new_paths]
        run.allowed_paths = granted
        item.allowed_paths = granted
        return ScopeChangeDecision(decision="granted", allowed_paths=granted, reason="granted automatically")

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
    return ScopeChangeDecision(decision="pending", allowed_paths=current, reason="sent to a person")


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


@router.get("/runs/{id}/skills", response_model=list[dict[str, Any]], operation_id="getRunSkills")
async def get_skills(id: str, session: Db, claims: RunAuth) -> list[dict[str, Any]]:
    """Les fichiers des skills que le `StageInput` du run nomme (ADR 0033).

    Le runner les lit ici, avec son jeton de run, puis recalcule leur empreinte : celle du
    `StageInput`, posée quand le run a été préparé, fait foi. Une skill de l'organisation du projet,
    et d'elle seule.
    """
    from ..db.models import Skill, SkillVersion

    run, _, project = await _run_and_item(session, id)
    rendues: list[dict[str, Any]] = []
    for ref in (run.stage_input or {}).get("skills", []):
        ligne = (
            await session.execute(
                select(SkillVersion)
                .join(Skill, Skill.id == SkillVersion.skill_id)
                .where(
                    Skill.org_id == project.org_id,
                    Skill.slug == ref["slug"],
                    SkillVersion.version == ref["version"],
                )
            )
        ).scalar_one_or_none()
        if ligne is None:
            raise not_found("Skill of the run", f"{ref['slug']}@{ref['version']}")
        if ligne.review_status != "approved":
            # Defence in depth (S32-06): prepare_stage never names a version waiting for review.
            raise conflict(f"skill `{ref['slug']}` v{ligne.version} waits for its second reviewer")
        rendues.append(
            {"slug": ref["slug"], "version": ligne.version, "digest": ligne.digest, "files": ligne.files}
        )
    return rendues


def _declared_inputs(run: Run) -> list[str]:
    return [str(name) for name in ((run.stage_input or {}).get("transition") or {}).get("inputs") or []]


@router.get("/runs/{id}/inputs", response_model=list[RunInputFile], operation_id="getRunInputs")
async def get_inputs(id: str, session: Db, claims: RunAuth) -> list[RunInputFile]:
    """The declared inputs of the run that an earlier step produced, latest revision of each (ADR 0049).

    The runner mounts them as files in `.choregos/inputs/`, so a large report no longer has to fit
    in the prompt. An input nothing has produced yet is simply absent."""
    run, item, _ = await _run_and_item(session, id)
    files: list[RunInputFile] = []
    for name in _declared_inputs(run):
        row = await latest_artifact(session, item.id, name)
        if row is None:
            continue
        files.append(
            RunInputFile(
                name=row.name,
                version=row.version,
                filename=filename_of(row),
                type=row.type,
                media_type=row.media_type,
                digest=row.digest,
                size=row.size,
            )
        )
    return files


@router.get("/runs/{id}/inputs/{name}", operation_id="getRunInputContent")
async def get_input_content(id: str, name: str, session: Db, claims: RunAuth) -> Response:
    """The bytes of a declared input, digest checked; reading it leaves a *read* receipt (S25-04)
    under the digest of what was read. Only the inputs the run's transition declares are served."""
    run, item, project = await _run_and_item(session, id)
    if name not in _declared_inputs(run):
        raise not_found("Declared input of the run", name)
    row = await latest_artifact(session, item.id, name)
    if row is None:
        raise not_found("Artifact", name)
    try:
        data = await read_content(object_store(), row)
    except ArtifactCorruptedError as exc:
        raise conflict(str(exc)) from exc
    except ObjectStoreError as exc:
        raise conflict(f"input `{name}` cannot be read from the object store: {exc}") from exc
    # An inline value keeps the receipt convention of S25-04 (its canonical JSON); a file, its sha256.
    if row.storage_key is None:
        await consigner(
            session,
            org_id=project.org_id,
            work_item_id=item.id,
            run_id=run.id,
            stage=run.stage_role,
            sorties={name: row.content},
            genre=LUE,
        )
    else:
        await record_digests(
            session,
            org_id=project.org_id,
            work_item_id=item.id,
            run_id=run.id,
            stage=run.stage_role,
            digests={name: row.digest},
            genre=LUE,
        )
    return Response(content=data, media_type=row.media_type, headers={"X-Choregos-Digest": row.digest})


@router.put("/runs/{id}/outputs/{name}", response_model=Artifact, operation_id="putRunOutput")
async def put_output(
    id: str,
    name: str,
    request: Request,
    session: Db,
    claims: RunAuth,
    type: Annotated[str, Query(description="a type of the artifact vocabulary (ADR 0049)")],
) -> Artifact:
    """A deliverable the agent wrote in `.choregos/outputs/` (S31-05, ADR 0049).

    The API hashes the bytes it received and refuses them unless they match the digest the runner
    computed; it stores them in the object store and records a typed artifact revision with its
    *produced* receipt. Idempotent: the same bytes again make no new version. Once the result is
    posted, the run's outputs are closed."""
    run, item, project = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run: its outputs are closed")
    if type not in ARTIFACT_TYPES:
        raise unprocessable(f"unknown artifact type `{type}`")
    limit = get_settings().artifact_max_bytes
    declared_size = request.headers.get("content-length", "")
    if declared_size.isdigit() and int(declared_size) > limit:
        raise ApiError(status.HTTP_413_CONTENT_TOO_LARGE, "Artifact too large", f"limit {limit} bytes")
    data = await request.body()
    if len(data) > limit:
        raise ApiError(status.HTTP_413_CONTENT_TOO_LARGE, "Artifact too large", f"limit {limit} bytes")
    received = artifact_digest(data)
    if request.headers.get("x-choregos-digest") != received:
        raise unprocessable(f"the bytes received hash to {received}, not to the digest announced")
    try:
        row = await store_file(
            session,
            object_store(),
            org_id=project.org_id,
            item=item,
            run=run,
            name=name,
            type_=type,
            data=data,
        )
    except ValueError as exc:
        raise unprocessable(str(exc)) from exc
    except ObjectStoreError as exc:
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "Object store unavailable", str(exc)) from exc
    await record_digests(
        session,
        org_id=project.org_id,
        work_item_id=item.id,
        run_id=run.id,
        stage=run.stage_role,
        digests={name: row.digest},
        genre=PRODUITE,
    )
    return to_contract(row)


@router.get("/runs/{id}/knowledge", response_model=list[dict[str, Any]], operation_id="getRunKnowledge")
async def get_knowledge(id: str, session: Db, claims: RunAuth) -> list[dict[str, Any]]:
    """The pinned documents of the `files` knowledge bases the run's `StageInput` names (ADR 0051).

    Exactly the documents pinned when the run was prepared, and only while the base is still served
    to the run's project; the runner recomputes the digest before laying them down.
    """
    from ..knowledge.serving import KnowledgeRefusedError, deliver

    run, _, project = await _run_and_item(session, id)
    delivered: list[dict[str, Any]] = []
    for ref in (run.stage_input or {}).get("knowledge", []):
        if ref.get("mode", "files") != "files":
            continue
        try:
            delivered.append(await deliver(session, project.org_id, project.slug, ref))
        except KnowledgeRefusedError as refusal:
            raise not_found("Knowledge base of the run", str(ref.get("slug"))) from refusal
    return delivered


@router.post(
    "/runs/{id}/knowledge/search",
    response_model=KnowledgeSearchResult,
    operation_id="searchRunKnowledge",
)
async def search_knowledge(
    id: str, body: KnowledgeSearchRequest, session: Db, claims: RunAuth
) -> KnowledgeSearchResult:
    """`knowledge_search` of the sidecar (ADR 0051): the `index` bases the run's `StageInput` names,
    and only while they are served to the run's project. Answers citations."""
    from dataclasses import asdict

    from ..db.models import KnowledgeBase
    from ..knowledge.access import served_to_project
    from ..knowledge.index import search

    run, _, project = await _run_and_item(session, id)
    slugs = [
        str(ref.get("slug"))
        for ref in (run.stage_input or {}).get("knowledge", [])
        if ref.get("mode") == "index" and body.knowledge_base in (None, ref.get("slug"))
    ]
    bases = [
        base
        for base in (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.org_id == project.org_id, KnowledgeBase.slug.in_(slugs)
                )
            )
        ).scalars()
        if served_to_project(base, project.slug)
    ]
    result = await search(session, bases, body.query, k=body.k)
    return KnowledgeSearchResult.model_validate(
        {"mode": result.mode, "hits": [asdict(h) for h in result.hits]}
    )


@router.get("/runs/{id}/ticket", response_model=RunTicket, operation_id="getRunTicket")
async def get_ticket(id: str, session: Db, claims: RunAuth) -> RunTicket:
    run, item, project = await _run_and_item(session, id)
    documents = item.documents or {}
    # Ce que le run lit ici, sous l'empreinte de ce qu'il lit (S25-04) : la révision exacte.
    await consigner(
        session,
        org_id=project.org_id,
        work_item_id=item.id,
        run_id=run.id,
        stage=run.stage_role,
        sorties={nom: documents.get(nom) for nom in DOCUMENTS_DU_PROMPT},
        genre=LUE,
    )
    return RunTicket(
        key=item.tracker_key,
        title=item.title,
        body=item.body_snapshot or "",
        fields=dict(item.fields or {}),
        url=item.url,
        spec_markdown=documents.get("spec_markdown"),
        plan_markdown=documents.get("plan_markdown"),
        comments=[
            RunTicketComment(author=c.get("author", ""), body=c.get("body", ""), ts=c.get("ts", utcnow()))
            for c in documents.get("comments", [])
        ],
    )


def depot_github(url: str) -> str | None:
    """`owner/name` d'une URL GitHub en https, `None` sinon (un dépôt ailleurs n'a pas d'App)."""
    import re

    trouve = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url.strip())
    return f"{trouve.group(1)}/{trouve.group(2)}" if trouve else None


@router.post("/runs/{id}/git-token", response_model=GitToken, operation_id="postRunGitToken")
async def post_git_token(id: str, session: Db, claims: RunAuth) -> GitToken:
    """Le jeton git du run (docs/security.md : « émis par run, portée du dépôt, 1 h, en mémoire »).

    La page le promettait, le code ne le faisait pas : le runner clonait sans identifiant, et un dépôt
    privé — le bac à sable du locataire dev, le 08/10 — rendait « Repository not found » à chaque run
    (S22-07). Le jeton est un jeton d'installation de l'App, restreint au SEUL dépôt du run et à
    `contents: write` ; sans App, ou hors de GitHub, il est nul et le run clone sans.
    """
    import os
    from datetime import UTC, datetime

    import httpx
    from choregos_adapters.github import RUNNER_PERMISSIONS, GitHubAppAuth

    run, _item, _project = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run")
    url = str(((run.stage_input or {}).get("repo") or {}).get("url") or "")
    depot = depot_github(url)
    app_id = os.environ.get("CHOREGOS_GITHUB_APP_ID", "")
    cle = os.environ.get("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "")
    if depot is None or not app_id or not cle:
        return GitToken(token=None, repository=depot)
    auth = GitHubAppAuth(app_id=app_id, private_key=cle)
    async with httpx.AsyncClient(timeout=20) as client:
        # Cloner et pousser sa branche : `contents: write`, rien d'autre — la PR, la plateforme l'ouvre.
        jeton = await auth.token_for(client, depot, permissions=RUNNER_PERMISSIONS, ttl_s=3600)
    return GitToken(
        token=jeton.token, expires_at=datetime.fromtimestamp(jeton.expires_at, tz=UTC), repository=depot
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


def _groupes_du_projet(project: Project) -> list[str]:
    """Les groupes auxquels il appartient — ce que le déploiement lui ouvre."""
    return [str(g) for g in ((project.config or {}).get("groups") or [])]


async def _outils_des_greffons(
    session: Any, run: Run, project: Project, deja: set[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """{nom: (greffon, outil MCP)} des fournisseurs déclarés par les greffons (`greffons.py`).

    Un nom déjà pris — par le catalogue ou par un autre greffon — est refusé en 409 plutôt que
    servi par l'un des deux : un outil masqué en silence, c'est un agent qui croit appeler l'un et
    appelle l'autre.
    """
    from ..greffons import fournisseurs_d_outils

    trouves: dict[str, tuple[str, dict[str, Any]]] = {}
    for greffon, fournisseur in fournisseurs_d_outils().items():
        for outil in await fournisseur.lister(session, run, project):
            nom = str(outil["name"])
            if nom in deja or nom in trouves:
                autre = trouves[nom][0] if nom in trouves else "the catalogue"
                raise conflict(f"tool `{nom}` declared by {autre} and by the plugin `{greffon}`")
            trouves[nom] = (greffon, outil)
    return trouves


def json_sha256(valeur: Any) -> str:
    """The SHA-256 of the canonical JSON of a value: sorted keys, compact separators, UTF-8."""
    canonique = json.dumps(valeur, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


@router.get("/runs/{id}/tools", operation_id="getRunTools")
async def get_tools(id: str, session: Db, claims: RunAuth) -> dict[str, Any]:
    """Les outils que CE run peut appeler, au format MCP (`name`, `description`, `inputSchema`)."""
    from ..services.courtier import outils_du_courtier

    run, _item, project = await _run_and_item(session, id)
    outils = outils_du_projet(_outils_autorises(project), _groupes_du_projet(project))
    des_greffons = await _outils_des_greffons(session, run, project, {o.name for o in outils})
    # Le courtier (ADR 0034) : les opérations des connecteurs de l'organisation que l'agent du run
    # sélectionne et que la politique permet. Leur nom (`connecteur__opération`) ne peut pas
    # rencontrer celui d'un outil du catalogue.
    du_courtier = await outils_du_courtier(session, run, project)
    # The governed way to read the web (S40-01f, ADR 0070): offered when the run's network allows a host.
    # Its name cannot be taken by another tool: a masked tool is an agent calling the wrong one.
    web = [web_fetch.announcement()] if await web_fetch.offered(session, run) else []
    if web and (
        web_fetch.TOOL_NAME in du_courtier
        or web_fetch.TOOL_NAME in des_greffons
        or any(o.name == web_fetch.TOOL_NAME for o in outils)
    ):
        raise conflict(f"tool `{web_fetch.TOOL_NAME}` is built in: another tool cannot take its name")
    return {
        "tools": [
            *({"name": o.name, "description": o.description, "inputSchema": o.input_schema} for o in outils),
            *(outil for _, outil in des_greffons.values()),
            *(outil.annonce() for outil in du_courtier.values()),
            *web,
        ]
    }


@router.post("/runs/{id}/tools/{name}", operation_id="callRunTool")
async def call_tool(id: str, name: str, body: dict[str, Any], session: Db, claims: RunAuth) -> dict[str, Any]:
    """Appelle un outil du catalogue ou d'un greffon pour le run, et l'inscrit au registre de coûts."""
    run, item, project = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run")
    from ..services.courtier import outils_du_courtier

    du_catalogue = outils_du_projet(_outils_autorises(project), _groupes_du_projet(project))
    outil = next((o for o in du_catalogue if o.name == name), None)
    des_greffons = await _outils_des_greffons(session, run, project, {o.name for o in du_catalogue})
    du_courtier = await outils_du_courtier(session, run, project)
    is_web = name == web_fetch.TOOL_NAME and await web_fetch.offered(session, run)
    if is_web and (outil is not None or name in des_greffons or name in du_courtier):
        raise conflict(f"tool `{name}` is built in: another tool cannot take its name")
    if outil is None and name not in des_greffons and name not in du_courtier and not is_web:
        # Ne pas distinguer « inconnu » de « non autorisé » : un agent n'a pas à découvrir
        # le catalogue du déploiement en essayant des noms.
        raise not_found("Tool", name)

    engine = PolicyEngine(await effective_policy(session, project.id))
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
            "Tool call cap reached",
            f"this run has already called {deja} tools (cap {plafond})",
        )

    suspicions: list[dict[str, str]] = []
    prix_usd = 0.0
    if is_web:
        code, corps, fournisseur = await _web_fetch(session, run, project, body or {})
        prix = 0.0
        code, corps, suspicions = await _garde_contre_l_injection(
            session, run, project, SimpleNamespace(nom=name), code, corps
        )
    elif outil is not None:
        code, corps = await appeler_outil(outil, body or {})
        fournisseur, prix = outil.provider, outil.price_eur
    elif name in du_courtier and du_courtier[name].sous_validation:
        # Sous validation (ADR 0035) : l'appel PROPOSE l'action ; rien ne part vers le serveur. La
        # proposition compte dans le plafond du run, pour qu'une boucle ne remplisse pas la boîte.
        from ..services.courtier import ArgumentsRefuses, proposer_l_appel, verifier_les_arguments

        try:
            verifier_les_arguments(du_courtier[name], body or {})
        except ArgumentsRefuses as refus:
            raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refus)) from refus
        code, corps = 202, await proposer_l_appel(session, run, project, du_courtier[name], body or {})
        fournisseur, prix = f"mcp:{du_courtier[name].connecteur.name}", 0.0
    elif name in du_courtier and du_courtier[name].ecriture:
        # Une écriture, même permise, est une action gouvernée (#241) : faite une fois, consignée,
        # compensable — jouée par l'`ActionWorkflow`. Son prix se compte quand elle naît.
        code, corps, suspicions, nouvelle = await _ecriture_par_le_courtier(
            session, run, project, du_courtier[name], body or {}
        )
        fournisseur, prix = f"mcp:{du_courtier[name].connecteur.name}", 0.0
        prix_usd = (du_courtier[name].operation.price_usd or 0.0) if nouvelle else 0.0
    elif name in du_courtier:
        code, corps, suspicions = await _par_le_courtier(session, run, project, du_courtier[name], body or {})
        fournisseur, prix = f"mcp:{du_courtier[name].connecteur.name}", 0.0
        prix_usd = du_courtier[name].operation.price_usd or 0.0
    else:
        from ..greffons import fournisseurs_d_outils

        greffon = des_greffons[name][0]
        code, corps = await fournisseurs_d_outils()[greffon].appeler(session, run, project, name, body or {})
        fournisseur, prix = f"greffon:{greffon}", 0.0
    # A call the organisation's network policy stopped never reached the server: it is not counted
    # against the per-host rate either, or an agent that keeps retrying would never recover (ADR 0070).
    refused_by_network = isinstance(corps, dict) and corps.get("type") in {"network_policy", "rate_limited"}
    if not refused_by_network:
        session.add(
            CostLedger(
                project_id=project.id,
                work_item_id=item.id,
                run_id=run.id,
                # `ts` has no model-side default: the column is NOT NULL and forgetting it
                # fails the INSERT, not the read.
                ts=utcnow(),
                kind="tool",
                provider=fournisseur,
                model=name,
                cost_eur=prix,
                cost_usd=prix_usd,
                stage_role=run.stage_role,
                **await dimensions_of(session, run.id),
            )
        )
    await check_after_spend(session, run.id)
    await persist_event(
        session,
        EventType.TOOL_CALLED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=run.id,
        tool=name,
        provider=fournisseur,
        status_code=code,
        # Provenance: WHAT went in and WHAT came back, as digests of the canonical JSON — the event
        # keeps no payload, but a result that is later disputed can be matched to the bytes served.
        request_sha256=json_sha256(body or {}),
        response_sha256=json_sha256(corps),
    )
    return {
        "status_code": code,
        "result": corps,
        "remaining": max(0, plafond - deja - 1) if plafond else None,
        **({"suspicions": suspicions} if suspicions else {}),
    }


async def _web_fetch(
    session: Any, run: Run, project: Project, arguments: dict[str, Any]
) -> tuple[int, Any, str]:
    """`web_fetch` (S40-01f): arguments checked against the announced schema BEFORE anything is read
    (400, nothing counted by the schema), then the governed read (`services/web_fetch.py`)."""
    import jsonschema

    try:
        jsonschema.validate(arguments, web_fetch.INPUT_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "Arguments refused",
            f"argument refused by the schema of web_fetch at {where}: {exc.message}",
        ) from exc
    return await web_fetch.fetch(session, run, project, arguments, fakes=get_settings().fakes)


async def _par_le_courtier(
    session: Any, run: Run, project: Project, outil: Any, arguments: dict[str, Any]
) -> tuple[int, Any, list[dict[str, str]]]:
    """Un appel par le courtier — une LECTURE : arguments vérifiés AVANT (400, rien de compté), clé
    du connecteur résolue ICI, et ce que le serveur rend passé à la garde contre l'injection — un
    serveur tiers écrit ce que l'agent lira."""
    from ..services.courtier import ArgumentsRefuses, appeler, verifier_les_arguments

    try:
        verifier_les_arguments(outil, arguments)
    except ArgumentsRefuses as refus:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refus)) from refus
    code, corps = await appeler(session, outil, arguments, run=run, project=project)
    return await _garde_contre_l_injection(session, run, project, outil, code, corps)


async def _ecriture_par_le_courtier(
    session: Any, run: Run, project: Project, outil: Any, arguments: dict[str, Any]
) -> tuple[int, Any, list[dict[str, str]], bool]:
    """Une écriture appelée par un run : arguments vérifiés AVANT (400, rien de proposé), puis une
    action gouvernée dont le courtier attend l'issue ; ce qu'elle rend passe la même garde."""
    from ..services.courtier import ArgumentsRefuses, ecrire_par_une_action, verifier_les_arguments

    try:
        verifier_les_arguments(outil, arguments)
    except ArgumentsRefuses as refus:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refus)) from refus
    code, corps, nouvelle = await ecrire_par_une_action(session, run, project, outil, arguments)
    code, corps, suspicions = await _garde_contre_l_injection(session, run, project, outil, code, corps)
    return code, corps, suspicions, nouvelle


async def _garde_contre_l_injection(
    session: Any, run: Run, project: Project, outil: Any, code: int, corps: Any
) -> tuple[int, Any, list[dict[str, str]]]:
    """Ce qu'un serveur tiers rend, l'agent le lira : `warn` le dit, `block` le retient."""
    from choregos_core.injection import suspicions as chercher

    from ..services.courtier import texte_du_resultat

    mode = PolicyEngine(await effective_policy(session, project.id)).policy.sandbox.prompt_injection
    trouvees = [] if mode == "ignore" else chercher(texte_du_resultat(corps), source=f"tool.{outil.nom}")
    if not trouvees:
        return code, corps, []
    await persist_event(
        session,
        EventType.SECURITY_INJECTION_SUSPECTED,
        project_id=project.id,
        work_item_id=run.work_item_id,
        project_slug=project.slug,
        subject=run.id,
        run_id=run.id,
        tool=outil.nom,
        suspicions=[s.to_dict() for s in trouvees],
    )
    if mode == "block":
        retenu = {"error": f"result of {outil.nom} withheld: suspected injection — a human reviews it"}
        return 451, retenu, [s.to_dict() for s in trouvees]
    return code, corps, [s.to_dict() for s in trouvees]
