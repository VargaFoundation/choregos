# SPDX-License-Identifier: Apache-2.0
"""Projections of work items, runs, human requests and releases; the key of an internal work
item; the storing of a stage's outputs."""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from choregos_adapters.errors import AdapterError
from choregos_contracts import EventType
from choregos_core.domain import NewItem
from choregos_core.restricted import RESTRICTED_TITLE, WITHHELD, mark
from choregos_core.secrets import SecretNotFound
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import (
    Connector,
    CostLedger,
    Event,
    Finding,
    HumanRequest,
    Project,
    Release,
    Run,
    WorkItem,
)
from ..errors import upstream
from ..rbac import Principal
from ..schemas import (
    DecisionDraft,
    HumanRequestAssignee,
    HumanRequestDto,
    ReleaseDto,
    ReleaseItemDto,
    RunDto,
    RunSummary,
    TimelineEntry,
    ToolConnector,
    Totals,
    TrackerWrite,
    WorkflowFailure,
    WorkItemCreate,
    WorkItemDto,
)
from ..secret_store import SecretStoreUnavailable, resolved_configuration
from ..temporal import get_temporal, interpreter_id
from .costs import estimate_cost
from .definitions import work_item_workflow, workflow_model
from .events import persist_event
from .human_assignee import StepAssignee, actor_of_the_step, assignee_from_actor, assignee_of_row
from .initiator import of_principal
from .routing import WorkItemBirth, new_work_item, verify_birth
from .work_item_repository import repository_view


def _decision_detail(decision: dict[str, Any]) -> str:
    """What a decision says of itself; a task, its proof: the attested sentence and the values."""
    parts = []
    if decision.get("attestation"):
        parts.append(f"attested: “{decision['attestation']}”")
    values = dict(decision.get("values") or {})
    if values:
        parts.append(", ".join(f"{name} = {value}" for name, value in sorted(values.items())))
    if decision.get("reason"):
        parts.append(str(decision["reason"]))
    return " — ".join(parts)


def totals_from(raw: dict[str, Any] | None) -> Totals:
    """The totals the DTO declares, from a JSON column several writers add to (#1348).

    A run's `tokens` also carry the suspension's bookkeeping (`resumed`, `resumed_for`), and an
    orchestrator newer than this API may write a figure `Totals` does not declare yet: neither may
    make the run unreadable. Known fields are still validated.
    """
    declared = {key: value for key, value in (raw or {}).items() if key in Totals.model_fields}
    return Totals.model_validate(declared)


def run_summary(run: Run) -> RunSummary:
    return RunSummary(
        id=run.id,
        status=run.status,
        stage_role=run.stage_role,
        attempt=run.attempt,
        backend=run.backend,
        model=run.model,
        cost_usd=run.cost_usd,
        started_at=run.started_at,
    )


def run_dto(run: Run, project_slug: str, tool_connectors: list[ToolConnector] | None = None) -> RunDto:
    """A run as the API serves it; `tool_connectors` is filled by the routes that resolve them
    (`tool_connectors_of`), which a list of runs does not do for each one."""
    from choregos_contracts import StageResult

    return RunDto(
        id=run.id,
        status=run.status,
        stage_role=run.stage_role,
        attempt=run.attempt,
        backend=run.backend,
        model=run.model,
        cost_usd=run.cost_usd,
        started_at=run.started_at,
        work_item_id=run.work_item_id,
        project_slug=project_slug,
        transition_id=run.transition_id,
        actor=run.actor,
        executor_kind=run.executor_kind,
        executor_ref=run.executor_ref,
        ended_at=run.ended_at,
        tokens=totals_from(run.tokens),
        gateway_key_id=run.gateway_key_id,
        result=StageResult.model_validate(run.result) if run.result else None,
        transcript_url=run.transcript_url,
        context_pack_url=run.context_pack_url,
        playbook_checksum=run.playbook_checksum,
        allowed_paths=list(run.allowed_paths or []),
        agent_slug=run.agent_slug,
        agent_version=run.agent_version,
        initiated_by=run.initiated_by,
        tool_connectors=list(tool_connectors or []),
    )


async def tool_connectors_of(session: AsyncSession, run: Run, project: Project) -> list[ToolConnector]:
    """The organisation connectors this run's agent may call through the broker, and the account each
    one calls as (ADR 0107): the broker's own selection, resolved here so that a project member who
    cannot read the organisation's connectors still sees them. Never a secret."""
    from choregos_adapters import account_of

    from .broker import broker_tools

    seen: dict[str, ToolConnector] = {}
    for tool in (await broker_tools(session, run, project)).values():
        connector = tool.connector
        if connector.name in seen:
            continue
        config = dict(connector.config or {})
        # An MCP connector sends its token as a Bearer unless it says `basic` (S39-10e).
        scheme = (config.get("auth_scheme") or "bearer") if connector.kind == "mcp" else None
        seen[connector.name] = ToolConnector(
            name=connector.name,
            type=connector.type,
            auth_scheme=str(scheme) if scheme else None,
            account=account_of(connector.kind, connector.type, config),
        )
    return sorted(seen.values(), key=lambda c: c.name)


def human_request_dto(row: HumanRequest, assignee: StepAssignee | None = None) -> HumanRequestDto:
    """The request as the API shows it; `assignee` is who the workflow addresses it to, replaced by an
    administrator's reassignment when the row carries one (ADR 0153)."""
    who = assignee_of_row(row) or assignee
    return HumanRequestDto(
        id=row.id,
        work_item_id=row.work_item_id,
        transition_id=row.transition_id,
        kind=row.kind,
        payload=row.payload,
        requested_at=row.requested_at,
        due_at=row.due_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision=row.decision,
        assignee=HumanRequestAssignee(**who.as_dict()) if who else None,
        draft=DecisionDraft.model_validate(row.draft) if row.draft else None,
    )


async def work_item_dto(
    session: AsyncSession, item: WorkItem, project: Project, *, with_temporal: bool = False
) -> WorkItemDto:
    # The workflow OF THE WORK ITEM: its pinned version, otherwise the project's default, and no
    # longer the `default-simple` template, which showed a workflow the project never had.
    workflow_row = await work_item_workflow(session, item)
    workflow = workflow_model(workflow_row)
    state = workflow.states.get(item.state)
    current = (
        await session.execute(
            select(Run)
            .where(Run.work_item_id == item.id, Run.status.in_(["queued", "running"]))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    pending = (
        await session.execute(
            select(HumanRequest)
            .where(HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None))
            .order_by(HumanRequest.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    totals = totals_from(item.totals)
    # What the ticket has cost is the ledger's sum — models AND tools (S27-03): the counter kept on
    # the row adds model spend only. A ticket with no ledger line keeps its counter.
    booked = (
        await session.execute(
            select(func.sum(CostLedger.cost_usd), func.sum(CostLedger.cost_eur)).where(
                CostLedger.work_item_id == item.id
            )
        )
    ).one()
    if booked[0] is not None:
        totals = totals.model_copy(update={"cost_usd": float(booked[0]), "cost_eur": float(booked[1] or 0.0)})
    repository, repository_fixed = await repository_view(session, project, item)
    return WorkItemDto(
        id=item.id,
        fields=dict(item.fields or {}),
        project_slug=project.slug,
        tracker_key=item.tracker_key,
        title=item.title,
        body_snapshot=item.body_snapshot,
        url=item.url,
        size=item.size,
        risk=item.risk,
        state=item.state,
        state_display=state.display if state else item.state,
        state_outcome=str(state.outcome) if state and state.terminal else None,
        workflow_name=workflow_row.name if workflow_row else workflow.metadata.name,
        workflow_version=workflow_row.version if workflow_row else workflow.metadata.version,
        temporal_wf_id=item.temporal_wf_id,
        workflow_status=await _temporal_status(item) if with_temporal else None,
        failure=WorkflowFailure(**item.failure) if item.failure else None,
        paused=item.paused,
        current_run=run_summary(current) if current else None,
        pending_request=(
            human_request_dto(
                pending,
                assignee_from_actor(actor_of_the_step(workflow, pending.transition_id), project.slug),
            )
            if pending
            else None
        ),
        pr_url=item.pr_url,
        repository=repository,
        repository_fixed=repository_fixed,
        totals=totals,
        budget_usd=item.budget_usd,
        estimate=await estimate_cost(session, project.id, item),
        initiated_by=item.initiated_by,
        reporter=item.reporter,
        restricted_knowledge=list(item.restricted_knowledge or []),
        created_at=item.created_at,
        closed_at=item.closed_at,
    )


async def _temporal_status(item: WorkItem) -> str | None:
    """The status of the work item's workflow, if Temporal knows it.

    Never an error: a work item must be readable without Temporal.
    """
    if not item.temporal_wf_id:
        return None
    try:
        state = await get_temporal().describe(item.temporal_wf_id)
    except Exception:
        return None
    return state.status if state else None


def release_dto(row: Release, project_slug: str) -> ReleaseDto:
    # The train stores the boarding signal in the release as is (`approval`, `merged_at`,
    # `infra_pr_url`, `start_payload`...); the contract only exposes the item's fields and refuses
    # the rest. On the dev tenant, on 10/10, the first real release answered 500 to
    # `getTrainStatus` and `listReleases` (S22-25).
    fields = ReleaseItemDto.model_fields
    return ReleaseDto(
        id=row.id,
        project_slug=project_slug,
        env=row.env,
        batch_no=row.batch_no,
        status=row.status,
        items=[{k: v for k, v in item.items() if k in fields} for item in row.items or []],
        started_at=row.started_at,
        ended_at=row.ended_at,
        approved_by=row.approved_by,
        verdict=row.verdict,
        notes=row.notes,
        promotion_url=row.promotion_url,
    )


async def internal_work_item_key(session: AsyncSession, project: Project, *, numerotee: bool = True) -> str:
    """Assigns `<PREFIX>-<n>` when the platform keeps the work items itself (`tracker: internal`).

    Counting in the database rather than keeping a counter avoids a second place where the state
    lives; the race between two simultaneous creations is left to the uniqueness constraint
    `(project_id, tracker_key)`. It served the orchestrator (findings); it also serves the API
    since `POST /projects/{id}/work-items` exists.

    `numerotee=False` returns the prefix alone: a schedule builds its own key on it (S38-11).
    """
    connector = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    configured = (connector.config or {}).get("key_prefix") if connector else None
    prefix = str(configured or project.slug).upper()
    if not numerotee:
        return prefix
    existing = (
        (
            await session.execute(
                select(WorkItem.tracker_key).where(
                    WorkItem.project_id == project.id, WorkItem.tracker_key.like(f"{prefix}-%")
                )
            )
        )
        .scalars()
        .all()
    )
    numbers = [
        int(suffix) for tracker_key in existing if (suffix := tracker_key.rsplit("-", 1)[-1]).isdigit()
    ]
    return f"{prefix}-{max(numbers, default=0) + 1}"


async def tracker_is_internal(session: AsyncSession, project: Project) -> bool:
    connector = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    return connector is None or connector.type in {"internal", "fake"}


async def project_tracker(session: AsyncSession, project: Project) -> tuple[Connector | None, Any]:
    """The project's tracker connector and, when the tracker is external, its adapter.

    Returns `(row, None)` for an internal tracker (no row, `internal`, `fake`): the platform mints
    the keys itself. Kept apart so a test can replace it with a `FakeTracker`.
    """
    row = (
        await session.execute(
            select(Connector).where(Connector.project_id == project.id, Connector.kind == "tracker")
        )
    ).scalar_one_or_none()
    if row is None or row.type in {"internal", "fake"}:
        return row, None
    from choregos_adapters import build

    try:
        from .plugin_tracker import pin_plugin_tracker

        pinned = await pin_plugin_tracker(session, project.org_id, "tracker", row.type, row.config)
        # Through the platform's resolver: a token typed in the console is a sealed `store:` value.
        config = await resolved_configuration(
            session, project.org_id, "tracker", row.type, pinned, row.secret_refs, row.secret_ref
        )
        return row, build("tracker", row.type, config)
    except (AdapterError, SecretNotFound, SecretStoreUnavailable, ValueError, KeyError) as error:
        raise upstream(row.type, f"the tracker connector cannot be built: {error}") from error


ORIGIN_MARKER = "<!-- choregos:origin -->"
FILED_LABEL = "source:choregos"


SOFTWARE_DOCUMENTS = ("spec_markdown", "plan_markdown", "review_markdown", "release_notes_markdown")


def outputs_to_store(outputs: Any, declared: list[str] | None = None) -> dict[str, Any]:
    """The outputs of a stage that are stored in `item.documents`: the four software documents
    present, and each output the transition declares. They are the ones that get a receipt (S25-04)."""
    values = (
        outputs.model_dump(mode="json", exclude_none=True)
        if hasattr(outputs, "model_dump")
        else dict(outputs)
    )
    stored = {field: values[field] for field in SOFTWARE_DOCUMENTS if values.get(field)}
    for name in declared or []:
        if name in values and values[name] not in (None, "", [], {}):
            stored[name] = values[name]
    return stored


def store_outputs(item: WorkItem, outputs: Any, declared: list[str] | None = None) -> dict[str, Any]:
    """Stores a stage's outputs in `item.documents`, so that the NEXT stage reads them.

    The four software documents were stored; the outputs named by the trade (`profiles`,
    `evaluation`) never were: `outputs_present` saw them in the result, then they disappeared. On
    the 2026-09-24 bench, the HR qualification looked for "the profiles proposed at the previous
    stage" in an empty workspace. An output declared by the transition (`outputs: [profiles]`) is
    stored under its name.
    """
    documents = {**(item.documents or {}), **outputs_to_store(outputs, declared)}
    item.documents = documents
    return documents


async def create_work_item(  # noqa: PLR0913 - one creation path, the birth facts keyword-only
    session: AsyncSession,
    principal: Principal,
    project: Project,
    request: WorkItemCreate,
    *,
    canal: str = "api",
    key: str | None = None,
    initiated_by: str | None = None,
    item_id: str | None = None,
    restricted_knowledge: list[str] | None = None,
) -> WorkItem:
    """Files a work item in Choregos, whatever the project's tracker (ADR 0059).

    The only creation path of a person's request: the REST route and the MCP door (ADR 0030) both
    use it, so a work item created from Claude differs in nothing from one created in the console.
    With an internal tracker the platform mints the key (`<PREFIX>-<n>`). With an external one
    (GitHub, Jira, GitLab) it first creates the issue there, labelled `source:choregos` and NOT
    `agent-ready` (the webhook must not race this transaction), and records the work item under
    the issue's key. The initial state is the chosen workflow's, and the interpreter starts at once
    unless `start=False`. `item_id` is a caller-derived id (ADR 0008), the A2A door's (ADR 0078).

    `initiated_by` names who started it when the principal does not (a schedule, a parent work
    item); otherwise it is the principal through `canal` (ADR 0077 §2).

    `restricted_knowledge` marks it at birth (ADR 0114): a fan-out child of a marked parent. An
    external tracker then receives a neutral title and no body; the work item keeps its own.
    """
    row, adapter = await project_tracker(session, project)
    url: str | None = None
    if adapter is None:
        # A schedule gives its own key, one per window, so that a second firing finds the first (S38-11).
        key = key or await internal_work_item_key(session, project)
    else:
        # Validate first: a refused field or budget must not leave an issue behind.
        await verify_birth(
            session,
            project,
            WorkItemBirth(
                tracker_key="-",
                title=request.title,
                size=request.size,
                workflow=request.workflow,
                labels=tuple(request.labels),
                fields=dict(request.fields),
                budget_usd=request.budget_usd,
            ),
        )
        assert row is not None
        # A schedule gives its own key, one per window (S38-11); an external tracker mints its own.
        outward = (
            request.model_copy(update={"title": RESTRICTED_TITLE, "body": WITHHELD})
            if restricted_knowledge
            else request
        )
        key, url = await _file_in_tracker(row.type, adapter, principal, outward)
    item = await new_work_item(
        session,
        project,
        WorkItemBirth(
            tracker_key=key,
            title=request.title,
            body=request.body,
            url=url,
            size=request.size,
            risk=request.risk,
            created_by=principal.email,
            # The person who filed it here, even when the issue lands in an external tracker under
            # the platform's own account (#1179).
            reporter=principal.email,
            initiated_by=initiated_by or of_principal(principal, canal),
            workflow=request.workflow,
            labels=tuple(request.labels),
            fields=dict(request.fields),
            budget_usd=request.budget_usd,
            item_id=item_id,
        ),
    )
    mark(item, restricted_knowledge or [])
    await persist_event(
        session,
        EventType.WORKITEM_CREATED,
        project_id=project.id,
        work_item_id=item.id,
        project_slug=project.slug,
        subject=key,
        key=key,
        title=request.title,
    )
    if request.start:
        await start_work_item(project, item)
    await record(
        session,
        principal,
        "workitem.create",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        key=key,
        canal=canal,
        tracker=row.type if row is not None else "internal",
    )
    return item


async def start_work_item(project: Project, item: WorkItem) -> None:
    """Starts the work item's interpreter: what `create_work_item` does unless `start=False`.

    The A2A door starts it itself, once the files a message carried are stored: the first step
    must find the inputs it declares (ADR 0078 §6)."""
    workflow_id = interpreter_id(project.slug, item.tracker_key)
    await get_temporal().start_interpreter(
        workflow_id,
        {
            "project_id": project.id,
            "project_slug": project.slug,
            "work_item_id": item.id,
            "tracker_key": item.tracker_key,
        },
    )
    item.temporal_wf_id = workflow_id


async def _file_in_tracker(
    tracker_type: str, adapter: Any, principal: Principal, request: WorkItemCreate
) -> tuple[str, str | None]:
    """Creates the issue in the external tracker; returns its key and, best effort, its URL."""
    body = f"{request.body or ''}\n\n{ORIGIN_MARKER}Filed from Choregos by {principal.email}".strip()
    labels = [*dict.fromkeys([*request.labels, FILED_LABEL])]
    try:
        key = str(await adapter.create_item(NewItem(title=request.title, body=body, labels=labels)))
    except (AdapterError, SecretNotFound, httpx.HTTPError) as error:
        raise upstream(
            tracker_type,
            f"the tracker {tracker_type} refused the issue: {error}. Nothing was created in Choregos.",
        ) from error
    try:
        return key, (await adapter.fetch_item(key)).url
    except Exception as error:
        structlog.get_logger().warning("issue_url_unavailable", key=key, error=str(error))
        return key, None


def _via(value: Any) -> TrackerWrite | None:
    """A recorded tracker write (`{"type", "account"}`), or nothing for an event from before S42-06g."""
    if not isinstance(value, dict) or not value.get("type"):
        return None
    account = value.get("account")
    return TrackerWrite(type=str(value["type"]), account=str(account) if account else None)


async def timeline(session: AsyncSession, item: WorkItem) -> list[TimelineEntry]:
    """States, runs, decisions, findings: the complete story of a work item, in order."""
    entries: list[TimelineEntry] = []
    runs = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    for run in runs:
        summary = (run.result or {}).get("summary", "")
        entries.append(
            TimelineEntry(
                ts=run.started_at or run.created_at,
                kind="run",
                title=f"{run.stage_role} — tentative {run.attempt}",
                detail=summary or run.status,
                actor=run.actor or run.backend,
                actor_kind="agent",
                ref_id=run.id,
                cost_usd=run.cost_usd,
            )
        )
    requests = (
        (
            await session.execute(
                select(HumanRequest)
                .where(HumanRequest.work_item_id == item.id)
                .order_by(HumanRequest.requested_at)
            )
        )
        .scalars()
        .all()
    )
    events = (
        (await session.execute(select(Event).where(Event.work_item_id == item.id).order_by(Event.ts)))
        .scalars()
        .all()
    )
    # The tracker write behind a request (ADR 0107 point 4): its question went out as a comment.
    via_of_request = {
        str(e.payload.get("request_id")): e.payload.get("via")
        for e in events
        if e.type == str(EventType.WORKITEM_HUMAN_REQUESTED) and e.payload.get("via")
    }
    for req in requests:
        entries.append(
            TimelineEntry(
                ts=req.requested_at,
                kind="decision",
                title=f"human request: {req.kind}",
                detail=str(req.payload.get("question") or req.payload.get("summary") or ""),
                actor=req.decided_by,
                actor_kind="user",
                ref_id=req.id,
                via=_via(via_of_request.get(req.id)),
            )
        )
        if req.decided_at:
            entries.append(
                TimelineEntry(
                    ts=req.decided_at,
                    kind="decision",
                    title=f"decision: {(req.decision or {}).get('kind', 'answered')}",
                    detail=_decision_detail(req.decision or {}),
                    actor=req.decided_by,
                    actor_kind="user",
                    ref_id=req.id,
                )
            )
    for event in events:
        if event.type == str(EventType.WORKITEM_STATE_CHANGED):
            entries.append(
                TimelineEntry(
                    ts=event.ts,
                    kind="state_change",
                    title=f"{event.payload.get('from', '?')} → {event.payload.get('to', '?')}",
                    detail=str(event.payload.get("reason") or ""),
                    actor=str(event.payload.get("by") or "orchestrateur"),
                    actor_kind="system",
                    via=_via(event.payload.get("via")),
                )
            )
    findings = (
        (await session.execute(select(Finding).where(Finding.origin_work_item_id == item.id))).scalars().all()
    )
    for finding in findings:
        entries.append(
            TimelineEntry(
                ts=finding.created_at,
                kind="finding",
                title=f"finding {finding.severity} : {finding.title}",
                detail=finding.evidence[:300],
                actor_kind="agent",
                ref_id=finding.id,
            )
        )
    return sorted(entries, key=lambda e: e.ts)
