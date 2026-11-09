# SPDX-License-Identifier: Apache-2.0
"""Internal API: a runner's only write channel (run JWT, scope = one run).

None of these routes accepts a human session; no broad GitHub token goes down into the
workspace: everything passes through here (§1.10, §2.2).
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
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
from choregos_core import PolicyEngine, matches_any, telemetry, utcnow
from choregos_core.workspace import is_read_only_scope
from fastapi import APIRouter, Header, Query, Request, Response, status
from sqlalchemy import select

from ..catalogue import call as call_catalogue_tool
from ..config import get_settings
from ..db.models import (
    AgentSessionRecord,
    CostLedger,
    Organization,
    Project,
    Run,
    RunEvent,
    WorkItem,
)
from ..db.models import Finding as FindingRow
from ..db.session import allow_release_during_calls, outside_the_transaction
from ..deps import Db, NetworkAuth, RunAuth
from ..errors import ApiError, conflict, not_found, unprocessable
from ..logging import get_logger
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
from ..schemas.memory import RunMemoryHit, RunMemorySearch
from ..schemas.network import EgressToken, NetworkDecisionBatch, RunNetwork
from ..services import (
    effective_policy,
    new_human_request,
    persist_event,
    store_outputs,
    web_fetch,
    web_search,
)
from ..services.agent_hosted_sessions import session_network
from ..services.agent_reach import spec_of_run, tool_reach
from ..services.artifacts import (
    ArtifactCorruptedError,
    filename_of,
    read_content,
    record_inline_outputs,
    store_file,
    to_contract,
    value_for_receipt,
)
from ..services.artifacts import latest as latest_artifact
from ..services.budget_alerts import check_after_spend
from ..services.costs import dimensions_of
from ..services.extension_tools import (
    ACCESS_KEY,
    extension_access,
    extension_result_cap,
    extension_tools,
)
from ..services.network import record_decisions, run_network
from ..services.receipts import PRODUCED, PROMPT_DOCUMENTS, READ, record, record_digests
from ..services.tickets import outputs_to_store
from ..services.tool_call_cap import take_tool_call_slot
from ..temporal import get_temporal, interpreter_id

router = APIRouter(tags=["internal"], prefix="/internal")
logger = get_logger("choregos.internal")

#: The `run_id` a hosted session's pod speaks as (`session_run_id`).
SESSION_RUN_PREFIX = "session-"


async def _session_of(session: Any, run_id: str) -> AgentSessionRecord:
    record: AgentSessionRecord | None = await session.get(
        AgentSessionRecord, run_id.removeprefix(SESSION_RUN_PREFIX)
    )
    if record is None:
        raise not_found("Agent session", run_id)
    return record


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
    """The run's `StageInput`. If the result is already posted, 409: the runner exits with 0."""
    run, _, _ = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run")
    if not run.stage_input:
        raise not_found("StageInput", id)
    return StageInput.model_validate(run.stage_input)


@router.post("/runs/{id}/events", status_code=status.HTTP_202_ACCEPTED, operation_id="postRunEvents")
async def post_events(id: str, body: RunEventsBatch, session: Db, claims: RunAuth) -> dict[str, int]:
    """ACP log in a batch, idempotent by `seq` (a replay does not write twice)."""
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
async def get_run_network(id: str, session: Db, claims: NetworkAuth) -> RunNetwork:
    """The network THIS run may use (ADR 0070): the egress proxy reads it with the run's own token.
    A hosted session's pod (S41-11) speaks as `session-{id}` and gets its agent version's network."""
    if id.startswith(SESSION_RUN_PREFIX):
        return await session_network(session, await _session_of(session, id))
    run, _, _ = await _run_and_item(session, id)
    return await run_network(session, run)


@router.post(
    "/runs/{id}/network/decisions",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="postRunNetworkDecisions",
)
async def post_network_decisions(
    id: str, body: NetworkDecisionBatch, session: Db, claims: NetworkAuth
) -> dict[str, int]:
    """What the proxy or the runner let through or refused: cumulative totals, replay-safe. A
    session's decisions are not stored yet (a `network_decisions` row belongs to a run): the batch
    is acknowledged, so its proxy does not retry it."""
    if id.startswith(SESSION_RUN_PREFIX):
        await _session_of(session, id)
        return {"accepted": len(body.decisions)}
    run, _, project = await _run_and_item(session, id)
    return {"accepted": await record_decisions(session, run, project, body)}


@router.post("/runs/{id}/egress-token", response_model=EgressToken, operation_id="postRunEgressToken")
async def post_egress_token(id: str, claims: RunAuth) -> EgressToken:
    """The run's egress token (ADR 0115), for its run token: the runner puts it in the proxy
    variables the agent inherits, so the agent can read its run's network and nothing else — not
    its input, not its result, not its git token, not its tools."""
    from datetime import UTC, datetime

    from ..security import mint_egress_token

    return EgressToken(
        token=mint_egress_token(claims), expires_at=datetime.fromtimestamp(claims.expires_at, UTC)
    )


@router.post("/runs/{id}/result", operation_id="postRunResult")
async def post_result(id: str, body: StageResult, session: Db, claims: RunAuth) -> dict[str, str]:
    """Post the `StageResult`. Idempotent: an identical second call answers 200 and changes nothing."""
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
    declared_outputs = list(((run.stage_input or {}).get("transition") or {}).get("outputs") or [])
    store_outputs(item, outputs, declared_outputs)
    # Each stored output receives its "produced" receipt, under its fingerprint (S25-04).
    await record(
        session,
        org_id=project.org_id,
        work_item_id=item.id,
        run_id=run.id,
        stage=run.stage_role,
        outputs=outputs_to_store(outputs, declared_outputs),
        genre=PRODUCED,
    )
    # And each becomes a typed artifact revision, traced to this run (S31-01, ADR 0049).
    await record_inline_outputs(
        session,
        org_id=project.org_id,
        item=item,
        run=run,
        outputs=outputs_to_store(outputs, declared_outputs),
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
    """Post a finding, capped by `policy.findings.max_per_run`."""
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
    """Scope widening: granted automatically under a threshold, otherwise submitted to a human."""
    run, item, project = await _run_and_item(session, id)
    engine = PolicyEngine(await effective_policy(session, project.id))
    current = list(run.allowed_paths or item.allowed_paths or [])
    new_paths = [p for p in body.paths if not matches_any(p, current)]
    if not new_paths:
        return ScopeChangeDecision(
            decision="granted", allowed_paths=current, reason="already within the scope"
        )
    if is_read_only_scope(run.allowed_paths):
        # An agent that declares `workspace: read` (ADR 0119) never widens its scope: neither the
        # policy's automatic grant nor a human turns a read into a write.
        return ScopeChangeDecision(
            decision="denied",
            allowed_paths=current,
            reason="the agent declares a read-only workspace (`workspace: read`): it writes no file "
            "of the repository; publish a version with `workspace: write` to change code",
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

    request_row = new_human_request(
        work_item_id=item.id,
        project_id=project.id,
        transition_id=run.transition_id,
        kind="scope_change",
        payload={"paths": new_paths, "justification": body.justification, "run_id": run.id},
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
    """The agent asks for an arbitration: the run will have to end in `needs_human`."""
    run, item, project = await _run_and_item(session, id)
    request_row = new_human_request(
        work_item_id=item.id,
        project_id=project.id,
        transition_id=run.transition_id,
        kind="question",
        payload={"question": body.text, "options": body.options, "run_id": run.id},
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
    """The files of the skills that the run's `StageInput` names (ADR 0033).

    The runner reads them here, with its run token, then recomputes their fingerprint: the one in
    the `StageInput`, set when the run was prepared, is authoritative. A skill of the project's
    organisation, and of that one only.
    """
    from ..db.models import Skill, SkillVersion

    run, _, project = await _run_and_item(session, id)
    rendered: list[dict[str, Any]] = []
    for ref in (run.stage_input or {}).get("skills", []):
        version_row = (
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
        if version_row is None:
            raise not_found("Skill of the run", f"{ref['slug']}@{ref['version']}")
        if version_row.review_status != "approved":
            # Defence in depth (S32-06): prepare_stage never names a version waiting for review.
            raise conflict(f"skill `{ref['slug']}` v{version_row.version} waits for its second reviewer")
        rendered.append(
            {
                "slug": ref["slug"],
                "version": version_row.version,
                "digest": version_row.digest,
                "files": version_row.files,
            }
        )
    return rendered


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
        await record(
            session,
            org_id=project.org_id,
            work_item_id=item.id,
            run_id=run.id,
            stage=run.stage_role,
            outputs={name: value_for_receipt(row, item.documents or {})},
            genre=READ,
        )
    else:
        await record_digests(
            session,
            org_id=project.org_id,
            work_item_id=item.id,
            run_id=run.id,
            stage=run.stage_role,
            digests={name: row.digest},
            genre=READ,
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
        genre=PRODUCED,
    )
    return to_contract(row)


@router.get("/runs/{id}/knowledge", response_model=list[dict[str, Any]], operation_id="getRunKnowledge")
async def get_knowledge(id: str, session: Db, claims: RunAuth) -> list[dict[str, Any]]:
    """The pinned documents of the `files` knowledge bases the run's `StageInput` names (ADR 0051).

    Exactly the documents pinned when the run was prepared, and only while the base is still served
    to the run's project and, when restricted, readable by the person the run acts for (ADR 0097);
    the runner recomputes the digest before laying them down.
    """
    from ..knowledge.on_behalf import OnBehalf
    from ..knowledge.serving import KnowledgeRefusedError, deliver

    run, item, project = await _run_and_item(session, id)
    on_behalf = OnBehalf(session, project.org_id, project.id, item.initiated_by)
    delivered: list[dict[str, Any]] = []
    for ref in (run.stage_input or {}).get("knowledge", []):
        if ref.get("mode", "files") != "files":
            continue
        try:
            delivered.append(await deliver(session, project.org_id, project.slug, ref, on_behalf))
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
    and only while they are served to the run's project and, when restricted, readable by the person
    the run acts for (ADR 0097). Answers citations."""
    from dataclasses import asdict

    from ..db.models import KnowledgeBase
    from ..knowledge.index import search
    from ..knowledge.on_behalf import OnBehalf

    run, item, project = await _run_and_item(session, id)
    on_behalf = OnBehalf(session, project.org_id, project.id, item.initiated_by)
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
        if await on_behalf.refusal(base, project.slug) is None
    ]
    result = await search(session, bases, body.query, k=body.k)
    return KnowledgeSearchResult.model_validate(
        {"mode": result.mode, "hits": [asdict(h) for h in result.hits]}
    )


@router.post(
    "/runs/{id}/memory/search",
    response_model=list[RunMemoryHit],
    operation_id="searchRunMemory",
)
async def search_memory(id: str, body: RunMemorySearch, session: Db, claims: RunAuth) -> list[RunMemoryHit]:
    """`search_memory` of the sidecar (S40-11): the memories the run's `StageInput` names, and no others.

    The run token proves the run; what it may read is what its `StageInput` resolved when it was
    prepared. A label that is not one of them is 404. Each memory is searched under the read timeout.
    """
    from ..services.memory_access import search_run_memories

    run, _, project = await _run_and_item(session, id)
    policy = await effective_policy(session, project.id)
    if not policy.memory.enabled:
        return []
    organisation = await session.get(Organization, project.org_id)
    org_slug = organisation.slug if organisation is not None else ""
    hits = await search_run_memories(
        session,
        run,
        project,
        org_slug,
        body.query,
        body.k,
        body.memory,
        policy.memory.read_timeout_ms / 1000 * 10,
    )
    return [RunMemoryHit.model_validate(hit) for hit in hits]


@router.get("/runs/{id}/ticket", response_model=RunTicket, operation_id="getRunTicket")
async def get_ticket(id: str, session: Db, claims: RunAuth) -> RunTicket:
    """The work item as the run sees it. `spec_markdown` and `plan_markdown` are earlier steps'
    outputs, so they follow the same rule as `/inputs/{name}` (ADR 0077 §4): served only when the
    run's transition declares them, and only what is served leaves a *read* receipt (S25-04)."""
    run, item, project = await _run_and_item(session, id)
    documents = item.documents or {}
    declared = set(_declared_inputs(run))
    served = {name: documents.get(name) for name in PROMPT_DOCUMENTS if name in declared}
    # What the run reads here, under the fingerprint of what it reads (S25-04): the exact revision.
    await record(
        session,
        org_id=project.org_id,
        work_item_id=item.id,
        run_id=run.id,
        stage=run.stage_role,
        outputs=served,
        genre=READ,
    )
    return RunTicket(
        key=item.tracker_key,
        title=item.title,
        body=item.body_snapshot or "",
        fields=dict(item.fields or {}),
        url=item.url,
        spec_markdown=served.get("spec_markdown"),
        plan_markdown=served.get("plan_markdown"),
        comments=[
            RunTicketComment(author=c.get("author", ""), body=c.get("body", ""), ts=c.get("ts", utcnow()))
            for c in documents.get("comments", [])
        ],
    )


def github_repository(url: str) -> str | None:
    """`owner/name` of an https GitHub URL, `None` otherwise (a repository elsewhere has no App)."""
    import re

    found_match = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url.strip())
    return f"{found_match.group(1)}/{found_match.group(2)}" if found_match else None


@router.post("/runs/{id}/git-token", response_model=GitToken, operation_id="postRunGitToken")
async def post_git_token(id: str, session: Db, claims: RunAuth) -> GitToken:
    """The run's git token (docs/security.md: "issued per run, repository scope, 1 h, in memory").

    The page promised it, the code did not do it: the runner cloned without credentials, and a
    private repository (the dev tenant's sandbox, on 08/10) answered "Repository not found" on every
    run (S22-07). The token is an installation token of the App, restricted to the run's ONLY
    repository and to `contents: write`; without an App, or outside GitHub, it is null and the run
    clones without.
    """
    import os
    from datetime import UTC, datetime

    import httpx
    from choregos_adapters.github import RUNNER_PERMISSIONS, GitHubAppAuth

    from ..services.github_app import claimed_installation_for, implicit_claim_enabled

    run, _item, project_row = await _run_and_item(session, id)
    if run.result is not None:
        raise conflict("result already posted for this run")
    url = str(((run.stage_input or {}).get("repo") or {}).get("url") or "")
    repository = github_repository(url)
    app_id = os.environ.get("CHOREGOS_GITHUB_APP_ID", "")
    private_key = os.environ.get("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "")
    if repository is None or not app_id or not private_key:
        return GitToken(token=None, repository=repository)
    # The installation THIS organisation claimed on the repository's owner, never another's (ADR 0073).
    # Without a claim, a single-organisation instance still discovers it from the repository; with
    # several organisations a repository outside the claims gets no token.
    owner = repository.split("/", 1)[0]
    claimed = await claimed_installation_for(session, project_row.org_id, owner)
    if claimed is None and not await implicit_claim_enabled(session):
        return GitToken(token=None, repository=repository)
    auth = GitHubAppAuth(
        app_id=app_id,
        private_key=private_key,
        pinned_installation=claimed.id if claimed else None,
        pinned_account=owner if claimed else None,
    )
    async with httpx.AsyncClient(timeout=20) as client:
        # Clone and push its branch: `contents: write`, nothing else; the platform opens the PR.
        installation_token = await auth.token_for(
            client, repository, permissions=RUNNER_PERMISSIONS, ttl_s=3600
        )
    return GitToken(
        token=installation_token.token,
        expires_at=datetime.fromtimestamp(installation_token.expires_at, tz=UTC),
        repository=repository,
    )


@router.get("/runs/{id}/ci-logs", response_model=CiLogs, operation_id="getRunCiLogs")
async def get_ci_logs(
    id: str, session: Db, claims: RunAuth, tail: int = Query(default=500, le=5000)
) -> CiLogs:
    """Logs of the last CI failure, for the `fix_ci` role."""
    _run, item, _project = await _run_and_item(session, id)
    documents = item.documents or {}
    logs = str(documents.get("ci_logs", ""))
    return CiLogs(logs="\n".join(logs.splitlines()[-tail:]), ref=documents.get("ci_ref"))


# ───────────────────────── tool catalogue ─────────────────────────
#
# An agent that works a case needs third-party APIs, and those APIs have keys. Giving them to it
# would give it the means to spend without a cap, to exfiltrate in a commit, or to keep them.
# So it names a tool; the platform builds the request, injects the key, and counts the call.
# Its own egress stays closed.


async def _extension_tools(
    session: Any, run: Run, project: Project, taken: set[str]
) -> dict[str, tuple[str, dict[str, Any]]]:
    """{name: (extension, MCP tool)} of the providers the extensions declare (`services.extension_tools`).
    Kept under this name: a patch on it (a test, an edition package) is seen by the run's tool routes."""
    return await extension_tools(session, run, project, taken)


def _announced(plugin: str, tool: dict[str, Any]) -> dict[str, Any]:
    """An extension's tool as the agent sees it: the platform's own keys are not the agent's business."""
    own = {k: v for k, v in tool.items() if k not in {"result_max_bytes", ACCESS_KEY}}
    return _run_tool(own, extension_access(plugin, tool))


def _run_tool(
    entry: dict[str, Any], access: str, output_schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An entry of the run's tool list: MCP's keys, MCP's `readOnlyHint` (true for a read only), the
    access the platform knows, and the output schema the operation declares, when it declares one."""
    own = entry.get("annotations")
    annotations = {**(own if isinstance(own, dict) else {}), "readOnlyHint": access == "read"}
    listed = {**entry, "annotations": annotations, ACCESS_KEY: access}
    if output_schema:
        listed["outputSchema"] = output_schema
    return listed


def canonical_json(value: Any) -> bytes:
    """The canonical JSON of a value: sorted keys, compact separators, UTF-8."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return text.encode("utf-8")


def json_sha256(value: Any) -> str:
    """The SHA-256 of the canonical JSON of a value (`canonical_json`)."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


@router.get("/runs/{id}/tools", operation_id="getRunTools")
async def get_tools(id: str, session: Db, claims: RunAuth) -> dict[str, Any]:
    """The tools THIS run can call, in MCP format (`name`, `description`, `inputSchema`), each with
    `annotations.readOnlyHint`, its access (`x-choregos-access`: `read`, `write`, `approval`) and the
    `outputSchema` its operation declares (ADR 0122). A catalogue tool and the web tools read: a
    catalogue tool that may write is not listed, and its call is refused."""
    run, _item, project = await _run_and_item(session, id)
    # What the run's agent version closes (ADR 0127) is decided in `tool_reach`, which the access
    # endpoint reads too; a run with no registry agent keeps the project's tools.
    reach = await tool_reach(
        session, run, project, await spec_of_run(session, run, project), list_extensions=_extension_tools
    )
    catalogue_tools, from_extensions, brokered = reach.catalogue, reach.extensions, reach.brokered
    # The governed way to read the web (S40-01f, ADR 0070): offered when the run's network allows a host.
    # Its name cannot be taken by another tool: a masked tool is an agent calling the wrong one.
    web = [web_fetch.announcement()] if reach.fetch else []
    # The governed way to search (S40-01g, ADR 0083): offered when a backend the ceiling accepts serves
    # the project. Its name is reserved the same way.
    if reach.search:
        web.append(web_search.announcement())
    for builtin in web:
        taken = str(builtin["name"])
        if taken in brokered or taken in from_extensions or any(o.name == taken for o in catalogue_tools):
            raise conflict(f"tool `{taken}` is built in: another tool cannot take its name")
    return {
        "tools": [
            *(
                _run_tool(
                    {"name": o.name, "description": o.description, "inputSchema": o.input_schema}, "read"
                )
                for o in catalogue_tools
            ),
            *(_announced(extension, tool) for extension, tool in from_extensions.values()),
            *(
                _run_tool(tool.announcement(), tool.access, tool.operation.output_schema)
                for tool in brokered.values()
            ),
            *(_run_tool(builtin, "read") for builtin in web),
        ]
    }


#: Who made a tool call (ADR 0122 §4): the agent's MCP client, a script through the runner's tool
#: bridge, a job step. Attribution only: no authorisation reads it.
TOOL_ORIGIN_HEADER = "X-Choregos-Tool-Origin"
TOOL_ORIGINS = ("model", "script", "job")
#: The origin of a job step's calls, which the API reads from the run, never from the call (S57-06).
JOB_ORIGIN = "job"
#: The SHA-256 of the script that made the call, as the runner's tool bridge knows it.
SCRIPT_HEADER = "X-Choregos-Script"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _call_origin(origin: str | None, script: str | None) -> tuple[str, str | None]:
    """The door a call came through and its script's digest, read from the request's headers.

    Absent, the origin is `model`, what the run's tool server sends. A value outside the contract
    is a 400 problem: an attribution nobody can read back is worse than none."""
    if origin is not None and origin not in TOOL_ORIGINS:
        detail = f"{TOOL_ORIGIN_HEADER} must be one of {', '.join(TOOL_ORIGINS)}, not {origin[:40]!r}"
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "Tool origin refused",
            detail,
            errors=[{"code": "tool_origin_refused", "loc": ["header", TOOL_ORIGIN_HEADER], "msg": detail}],
        )
    if script is not None and _SHA256.fullmatch(script) is None:
        detail = f"{SCRIPT_HEADER} must be a SHA-256 in 64 lowercase hexadecimal characters"
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "Script digest refused",
            detail,
            errors=[{"code": "script_digest_refused", "loc": ["header", SCRIPT_HEADER], "msg": detail}],
        )
    return origin or "model", script


def _attributed(run: Run, origin: str, script_sha256: str | None) -> tuple[str, str | None]:
    """The door the API records for a call of `run` (S57-06): what the call says, checked against
    what the API knows of the run.

    A job step's calls are `job` whatever the call says: the API reads it from the run, which the
    orchestrator wrote and the run cannot rewrite, so a job needs no header and cannot pass for
    another door. A script digest it sends is not kept: a digest names a script the runner's tool
    bridge journaled, and a job step has no bridge (its command's digest is on the run already).
    A run that is not a job step claiming `job` is a 400 problem, as an origin outside the contract
    is: the API knows the attribution false, and booking it would make the ledger lie. Neither rule
    decides what a call may do: every refusal of the route is the same for every door (ADR 0122 §2)."""
    from ..services.broker import is_job_step

    if is_job_step(run):
        return JOB_ORIGIN, None
    if origin == JOB_ORIGIN:
        detail = f"run {run.id} is not a job step: `{JOB_ORIGIN}` is the origin of a job step's calls"
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "Tool origin refused",
            detail,
            errors=[{"code": "tool_origin_refused", "loc": ["header", TOOL_ORIGIN_HEADER], "msg": detail}],
        )
    return origin, script_sha256


def _traced_tool_call(
    endpoint: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Every call of the tool route is an `execute_tool` span of the run's trace (ADR 0018).

    Opened before anything is checked, so a refused call is a span too, with its error status.
    """

    @functools.wraps(endpoint)
    async def traced(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with telemetry.tool_span(kwargs["id"], kwargs["name"]):
            outcome = await endpoint(*args, **kwargs)
            telemetry.record_tool_call(status_code=int(outcome["status_code"]))
            return outcome

    return traced


@router.post("/runs/{id}/tools/{name}", operation_id="callRunTool")
@_traced_tool_call
async def call_tool(
    id: str,
    name: str,
    body: dict[str, Any],
    session: Db,
    claims: RunAuth,
    tool_origin: Annotated[str | None, Header(alias=TOOL_ORIGIN_HEADER)] = None,
    script: Annotated[str | None, Header(alias=SCRIPT_HEADER)] = None,
) -> dict[str, Any]:
    """Call a tool of the catalogue or of an extension for the run, and record it in the cost ledger.

    The origin headers are read here and recorded on the span, the ledger line and the event,
    nowhere else: the call is governed the same way whatever door it came through (ADR 0122 §2)."""
    origin, script_sha256 = _call_origin(tool_origin, script)
    run, item, project = await _run_and_item(session, id)
    origin, script_sha256 = _attributed(run, origin, script_sha256)
    # On the span from here on: a call refused below is a span with its door too.
    telemetry.record_tool_origin(origin, script_sha256)
    if run.result is not None:
        raise conflict("result already posted for this run")
    reach = await tool_reach(
        session,
        run,
        project,
        await spec_of_run(session, run, project),
        list_extensions=_extension_tools,
        include_writers=True,
    )
    from_catalogue, from_extensions, brokered = reach.catalogue, reach.extensions, reach.brokered
    catalogue_tool = next((o for o in from_catalogue if o.name == name), None)
    is_web = name == web_fetch.TOOL_NAME and reach.fetch
    is_search = name == web_search.TOOL_NAME and reach.search
    if (is_web or is_search) and (catalogue_tool is not None or name in from_extensions or name in brokered):
        raise conflict(f"tool `{name}` is built in: another tool cannot take its name")
    if (
        catalogue_tool is None
        and name not in from_extensions
        and name not in brokered
        and not (is_web or is_search)
    ):
        # Do not tell "unknown" from "not allowed": an agent has no business discovering the
        # deployment's catalogue by trying names.
        raise not_found("Tool", name)

    engine = PolicyEngine(await effective_policy(session, project.id))
    cap = engine.max_tool_calls_per_run()
    # Counted under the run's lock, and this call's place taken before it leaves: calls of one run
    # made at once (a script through the tool bridge) cannot all pass at `cap - 1` (S57-03).
    slot = await take_tool_call_slot(session, run, name, cap)
    already_called = slot.already_called
    if cap and already_called >= cap:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Tool call cap reached",
            f"this run has already called {already_called} tools (cap {cap})",
        )

    suspicions: list[dict[str, str]] = []
    price_usd = 0.0
    #: What the event says of a search query: its fingerprint and its length, never its text (ADR 0015).
    about_query: dict[str, Any] = {}
    # Every external call below runs with no connection of this request (#1354): the request's
    # transaction commits before the call leaves (`outside_the_transaction`) — the place above is
    # committed already, and nothing read or written since needs the call's outcome to be kept —, and
    # the call's line is booked after it, in the transaction its return begins, under the same scope.
    allow_release_during_calls(session)
    try:
        if is_search:
            code, result_body, provider, price_usd, about_query = await _web_search(
                session, run, project, body or {}
            )
            price_eur = 0.0
            result_body = _bounded_web(run, result_body)
            code, result_body, suspicions = await _injection_guard(
                session, run, project, SimpleNamespace(name=name), code, result_body
            )
        elif is_web:
            code, result_body, provider = await _web_fetch(session, run, project, body or {})
            price_eur = 0.0
            result_body = _bounded_web(run, result_body)
            code, result_body, suspicions = await _injection_guard(
                session, run, project, SimpleNamespace(name=name), code, result_body
            )
        elif catalogue_tool is not None:
            code, result_body, suspicions = await _call_the_catalogue(
                session, run, project, catalogue_tool, body or {}
            )
            provider, price_eur = catalogue_tool.provider, catalogue_tool.price_eur
        elif name in brokered and brokered[name].needs_approval:
            # Under approval (ADR 0035): the call PROPOSES the action; nothing goes to the server. The
            # proposal counts against the run's cap, so that a loop does not fill the inbox.
            from ..services.broker import ArgumentsRefused, check_arguments, propose_call

            try:
                check_arguments(brokered[name], body or {})
            except ArgumentsRefused as refusal:
                raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refusal)) from refusal
            code, result_body = 202, await propose_call(session, run, project, brokered[name], body or {})
            provider, price_eur = f"mcp:{brokered[name].connector.name}", 0.0
        elif name in brokered and brokered[name].is_write:
            # A write, even an allowed one, is a governed action (#241): done once, journaled,
            # compensable, played by the `ActionWorkflow`. Its price is counted when it is born.
            code, result_body, suspicions, created = await _write_by_the_broker(
                session, run, project, brokered[name], body or {}
            )
            provider, price_eur = f"mcp:{brokered[name].connector.name}", 0.0
            price_usd = (brokered[name].operation.price_usd or 0.0) if created else 0.0
        elif name in brokered:
            code, result_body, suspicions = await _read_by_the_broker(
                session, run, project, brokered[name], body or {}
            )
            provider, price_eur = f"mcp:{brokered[name].connector.name}", 0.0
            price_usd = brokered[name].operation.price_usd or 0.0
        else:
            extension, extension_tool = from_extensions[name]
            code, result_body, suspicions = await _call_the_extension(
                session, run, project, name, extension, extension_tool, body or {}
            )
            provider, price_eur = f"greffon:{extension}", 0.0  # lang: fr-ok — persisted cost provider, S45-39
    except BaseException:
        # A call that fails is not booked, as it never was: its place is given back.
        await slot.give_back(session)
        raise
    bound = _result_bound(run, name, catalogue_tool, brokered, from_extensions)
    # A call the organisation's network policy stopped never reached the server: it is not counted
    # against the per-host rate either, or an agent that keeps retrying would never recover (ADR 0070).
    # A search query or a fetch address refused for what it holds was not sent either (ADR 0083 §5).
    refused_by_network = isinstance(result_body, dict) and result_body.get("type") in {
        "network_policy",
        "rate_limited",
        "query_redacted",
        "url_redacted",
    }
    # The booking commits here: the call's line, its place given back, the budget's alerts and the
    # event, whose `pg_notify` leaves with the same commit (ADR 0085); all are kept or none. After an
    # external call it is a transaction of its own, begun in the scope the call left with (#1354).
    try:
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
                    provider=provider,
                    model=name,
                    cost_eur=price_eur,
                    cost_usd=price_usd,
                    stage_role=run.stage_role,
                    **await dimensions_of(session, run.id),
                    # Which door the call came through (S57-06, ADR 0122 §4): what `group_by=origin`
                    # reads.
                    origin=origin,
                    script_sha256=script_sha256,
                )
            )
        # The provisional line goes with this transaction, which holds the call's own line (if any).
        await slot.settle(session)
        await check_after_spend(session, run.id)
        served = canonical_json(result_body)
        await persist_event(
            session,
            EventType.TOOL_CALLED,
            project_id=project.id,
            work_item_id=item.id,
            project_slug=project.slug,
            subject=run.id,
            tool=name,
            provider=provider,
            status_code=code,
            # Provenance: WHAT went in and WHAT came back, as digests of the canonical JSON — the event
            # keeps no payload, but a result that is later disputed can be matched to the bytes served.
            request_sha256=json_sha256(body or {}),
            response_sha256=hashlib.sha256(served).hexdigest(),
            # What the answer weighs (S57-01, ADR 0122): the size of the same canonical bytes, the
            # measure of what a round trip puts back into the model's context.
            response_bytes=len(served),
            # Which door the call came through (ADR 0122 §4), and the script's digest when one made it.
            origin=origin,
            **({"script_sha256": script_sha256} if script_sha256 is not None else {}),
            **about_query,
        )
        await session.commit()
    except Exception as failure:
        # The call was made and its line is not booked: said here, with what the line would have
        # held, besides the 500 the caller gets. The place taken before the call stays committed, so
        # the run's cap still counts the call until the sweep (`IN_FLIGHT_STALE_AFTER`): one call
        # fewer for the run, never one more (S57-03).
        logger.warning(
            "tool_call.not_booked",
            run_id=run.id,
            project=project.slug,
            tool=name,
            provider=provider,
            status_code=code,
            cost_eur=price_eur,
            cost_usd=price_usd,
            error=type(failure).__name__,
        )
        raise
    telemetry.record_tool_call(
        status_code=code,
        provider=provider,
        project=project.slug,
        work_item=item.tracker_key,
        stage=run.stage_role,
    )
    return {
        "status_code": code,
        "result": result_body,
        "remaining": max(0, cap - already_called - 1) if cap else None,
        **({"suspicions": suspicions} if suspicions else {}),
        # Said for every call, `null` when nothing was bounded: what a caller reads does not change
        # shape with its door (ADR 0122 §2).
        "result_max_bytes": bound,
    }


def _bounded_web(run: Run, result_body: Any) -> Any:
    """A built-in web tool's answer as the agent may read it (#1383): a page or a search's results are
    third-party text, masked and bounded like a broker read (`bounded_result`, the platform's default
    bound, the page's text kept up to it with the marker), a job step's masked only (S40-13b); a
    refusal, the platform's own JSON, as it is."""
    from ..services.broker import bounded_result

    if isinstance(result_body, dict) and isinstance(result_body.get("content"), list):
        return bounded_result(result_body, cut=run.backend != "job")
    return result_body


def _result_bound(
    run: Run,
    name: str,
    catalogue_tool: Any,
    brokered: dict[str, Any],
    from_extensions: dict[str, tuple[str, dict[str, Any]]],
) -> int | None:
    """The bound the API held a call's result to, which its answer states (`result_max_bytes`, #1383),
    so that the run's tool server cuts what it shows the model where a script's client stops reading:
    the cap of the catalogue entry, the operation or the plugin tool (`result_limit`), the platform's
    default for a built-in web tool. None when nothing was bounded: a proposal under approval is the
    platform's own words, a job step's read is masked, never cut (S40-13b)."""
    from ..services.broker import result_limit

    if catalogue_tool is not None:
        return result_limit(catalogue_tool.result_max_bytes)
    if name in brokered:
        tool = brokered[name]
        if tool.needs_approval or (not tool.is_write and run.backend == "job"):
            return None
        return result_limit(tool.operation.result_max_bytes)
    if name in from_extensions:
        extension, declared = from_extensions[name]
        return result_limit(extension_result_cap(extension, declared))
    return None if run.backend == "job" else result_limit()


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


async def _web_search(
    session: Any, run: Run, project: Project, arguments: dict[str, Any]
) -> tuple[int, Any, str, float, dict[str, Any]]:
    """`web_search` (S40-01g): arguments checked against the announced schema BEFORE anything leaves
    (400), then the governed search (`services/web_search.py`). A query that was not sent costs
    nothing."""
    import jsonschema

    try:
        jsonschema.validate(arguments, web_search.INPUT_SCHEMA)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "Arguments refused",
            f"argument refused by the schema of web_search at {where}: {exc.message}",
        ) from exc
    query = str(arguments["query"])
    about = {"query_sha256": web_search.fingerprint(query), "query_length": len(query)}
    code, result_body, backend = await web_search.search(session, run, project, arguments)
    if backend is None:
        raise not_found("Tool", web_search.TOOL_NAME)
    price = web_search.price_usd(backend) if code == 200 else 0.0
    return code, result_body, backend.provider, price, about


async def _read_by_the_broker(
    session: Any, run: Run, project: Project, tool: Any, arguments: dict[str, Any]
) -> tuple[int, Any, list[dict[str, str]]]:
    """A call through the broker, a READ: arguments checked BEFORE (400, nothing counted), the
    connector's key resolved HERE, and what the server returns passed to the injection guard: a
    third-party server writes what the agent will read."""
    from ..services.broker import ArgumentsRefused, call, check_arguments

    try:
        check_arguments(tool, arguments)
    except ArgumentsRefused as refusal:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refusal)) from refusal
    code, result_body = await call(session, tool, arguments, run=run, project=project)
    return await _injection_guard(session, run, project, tool, code, result_body)


async def _write_by_the_broker(
    session: Any, run: Run, project: Project, tool: Any, arguments: dict[str, Any]
) -> tuple[int, Any, list[dict[str, str]], bool]:
    """A write called by a run: arguments checked BEFORE (400, nothing proposed), then a
    governed action whose outcome the broker waits for; what it returns goes through the same guard."""
    from ..services.broker import ArgumentsRefused, check_arguments, write_through_action

    try:
        check_arguments(tool, arguments)
    except ArgumentsRefused as refusal:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "Arguments refused", str(refusal)) from refusal
    code, result_body, created = await write_through_action(session, run, project, tool, arguments)
    code, result_body, suspicions = await _injection_guard(session, run, project, tool, code, result_body)
    return code, result_body, suspicions, created


async def _call_the_catalogue(
    session: Any, run: Run, project: Project, tool: Any, arguments: dict[str, Any]
) -> tuple[int, Any, list[dict[str, str]]]:
    """A catalogue tool, called: refused when it may write, made with no connection of the request held
    (#1354), what the provider returns bounded and passed to the injection guard."""
    if tool.may_write:
        detail = (
            f"catalogue tool `{tool.name}` may write (an http POST or a remote MCP tool) and catalogue "
            "calls are not governed actions: the deployment marks it `read_only: true` if it only "
            "reads, or the organisation declares the server as an MCP connector, whose writes are "
            "governed (ADR 0034)"
        )
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "Catalogue write refused",
            detail,
            errors=[{"code": "catalogue_write_refused", "msg": detail}],
        )
    from ..services.broker import bounded_payload

    async with outside_the_transaction(session):
        code, result_body = await call_catalogue_tool(tool, arguments)
    # A catalogue provider writes what the agent reads, as the broker's servers do (#759).
    result_body = bounded_payload(result_body, tool.result_max_bytes)
    # ... and what it writes may be an injection, read by the same guard (S40-18).
    return await _injection_guard(session, run, project, SimpleNamespace(name=tool.name), code, result_body)


async def _call_the_extension(
    session: Any,
    run: Run,
    project: Project,
    name: str,
    extension: str,
    tool: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[int, Any, list[dict[str, str]]]:
    """An extension's tool, called, its answer bounded as the extension declares and guarded.

    The extension is handed the session with no transaction open (#1354): what it reads first begins
    one under the run token's scope, which holds a connection until the call answers unless the
    extension wraps its own external call in `outside_the_transaction` (`ToolProvider`)."""
    from ..extensions import tool_providers
    from ..services.broker import bounded_payload

    async with outside_the_transaction(session):
        code, result_body = await tool_providers()[extension].call_tool(
            session, run, project, name, arguments
        )
    result_body = bounded_payload(result_body, extension_result_cap(extension, tool))
    return await _injection_guard(session, run, project, SimpleNamespace(name=name), code, result_body)


async def _injection_guard(
    session: Any, run: Run, project: Project, tool: Any, code: int, result_body: Any
) -> tuple[int, Any, list[dict[str, str]]]:
    """What a third-party server returns, the agent will read: `warn` says so, `block` withholds it."""
    from choregos_core.injection import suspicions as find_suspicions

    from ..services.broker import readable_text

    mode = PolicyEngine(await effective_policy(session, project.id)).policy.sandbox.prompt_injection
    suspected = (
        [] if mode == "ignore" else find_suspicions(readable_text(result_body), source=f"tool.{tool.name}")
    )
    if not suspected:
        return code, result_body, []
    await persist_event(
        session,
        EventType.SECURITY_INJECTION_SUSPECTED,
        project_id=project.id,
        work_item_id=run.work_item_id,
        project_slug=project.slug,
        subject=run.id,
        run_id=run.id,
        tool=tool.name,
        suspicions=[s.to_dict() for s in suspected],
    )
    if mode == "block":
        withheld = {"error": f"result of {tool.name} withheld: suspected injection — a human reviews it"}
        return 451, withheld, [s.to_dict() for s in suspected]
    return code, result_body, [s.to_dict() for s in suspected]


# The former private name of `_extension_tools`: a patch on it (a test, an edition package) is seen by
# `get_tools` and `call_tool` (S45-16).
