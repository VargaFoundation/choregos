# SPDX-License-Identifier: Apache-2.0
"""Tracker activities: state mirror, status comment, board fields, human requests."""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Deployment, Finding, HumanRequest, Release, Run, WorkItem
from choregos_api.logging import get_logger
from choregos_api.services import (
    WorkItemBirth,
    came_back_since,
    new_human_request,
    new_work_item,
    persist_event,
    record_human_verdict,
    record_terminal_outcome,
    reopen_work_item,
    restart_payload,
)
from choregos_api.services.initiator import initiator
from choregos_contracts import EventType, HumanActor, HumanRequestKind, StageResult
from choregos_core import Message, MessageAction, TrackerStateMapping, elapsed_seconds, utcnow
from choregos_core.domain import TRACKER_FIELD
from choregos_core.gates import ALREADY_FIXED_GATE
from choregos_core.restricted import WITHHELD, is_marked
from sqlalchemy import select, update
from temporalio import activity

from ..config import get_settings
from ..markdown import STATUS_MARKER, StageLine, StatusComment, render_human_request
from .base import (
    db,
    load_work_item,
    notification_target,
    project_bundle,
    send_courtesy,
    tracker_owns_items,
    work_item_workflow,
)
from .interpretation import FINISHED_RUNS

logger = get_logger("choregos.tracker")

#: The delivery comment: one per work item, rewritten if needed, so a replayed close_out does not
#: post a second one.
DELIVERED_MARKER = "<!-- choregos:delivered -->"
#: The comment that closes a work item already fixed on the default branch (S22-47): one, upserted.
ALREADY_FIXED_MARKER = "<!-- choregos:already-fixed -->"


#: The reason the interpreter mirrors the state it (re)starts from: no decision is applied then.
STARTED_REASON = "started"
#: How a work item stopped outside a terminal state ended, for its decisions: dropped.
CANCELLED_OUTCOME = "cancelled"


def _via(bundle: Any) -> dict[str, Any]:
    """The tracker write an event stands for (ADR 0107 point 4): its connector type and account."""
    return {"via": bundle.tracker_via} if bundle.tracker_via else {}


@activity.defn(name="mirror_state")
async def mirror_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Mirrors the workflow state in the tracker: the board column and the scoped label.

    Any other mirror than the start applies a decision (or an external move, a migration): the runs
    of the work item that are over have then had their outcome applied, and are marked decided
    (`runs.decided_at`, #552) in the same transaction as the state. A restarted interpreter reads
    back only a succeeded run not decided yet. Idempotent: a run already marked keeps its time.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        state_name = payload["state"]
        state = (await work_item_workflow(session, item)).states.get(state_name)
        previous = item.state
        item.state = state_name
        if payload.get("reason") != STARTED_REASON:
            await session.execute(
                update(Run)
                .where(
                    Run.work_item_id == item.id,
                    Run.status.in_(FINISHED_RUNS),
                    Run.decided_at.is_(None),
                )
                .values(decided_at=utcnow())
            )
        if state is not None and state.terminal and item.closed_at is None:
            item.closed_at = utcnow()
        mapping = TrackerStateMapping(
            status=(state.tracker.status if state and state.tracker else None),
            label=(state.tracker.label if state and state.tracker else f"choregos:{state_name}"),
        )
        await bundle.adapters.tracker.set_state(item.tracker_key, mapping)
        if previous != state_name:
            # An escalation says where the work item resumes (S22-41): an interpreter started again
            # from the database reads it there (`escalation_context`).
            parked = {"parked": list(payload["parked"])} if payload.get("parked") else {}
            await persist_event(
                session,
                EventType.WORKITEM_STATE_CHANGED,
                project_id=bundle.project.id,
                work_item_id=item.id,
                project_slug=bundle.slug,
                subject=item.tracker_key,
                **{"from": previous, "to": state_name, "reason": payload.get("reason", ""), **parked},
                **_via(bundle),
            )
        return {"state": state_name, "display": state.display if state else state_name}


@activity.defn(name="update_status_comment")
async def update_status_comment(payload: dict[str, Any]) -> dict[str, str]:
    """Rewrite the single status comment and the structured fields of the board (§4.3)."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        # The WORK ITEM's workflow (ADR 0031): the comment names the one it was born in.
        workflow = await work_item_workflow(session, item)
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
            .scalars()
            .all()
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
        findings = (
            (await session.execute(select(Finding).where(Finding.origin_work_item_id == item.id)))
            .scalars()
            .all()
        )
        finding_labels: list[str] = []
        for finding in findings:
            created = (
                await session.get(WorkItem, finding.created_work_item_id)
                if finding.created_work_item_id
                else None
            )
            reference = created.tracker_key if created else finding.id[:8]
            finding_labels.append(f"{reference} ({finding.type}, {finding.severity})")

        lines: list[StageLine] = []
        events: list[tuple[Any, str]] = [(run, "run") for run in runs] + [(req, "human") for req in requests]
        events.sort(key=lambda pair: getattr(pair[0], "created_at", None) or pair[0].requested_at)
        for index, (entry, kind) in enumerate(events, start=1):
            if kind == "run":
                tokens = entry.tokens or {}
                state = (
                    workflow.states.get(entry.stage_input.get("transition", {}).get("to", ""))
                    if entry.stage_input
                    else None
                )
                duration = elapsed_seconds(entry.started_at, entry.ended_at)
                # A marked work item's run summaries stay in the console (ADR 0114 §6).
                summary = (entry.result or {}).get("summary", entry.status)
                outcome = entry.status if is_marked(item) else summary
                lines.append(
                    StageLine(
                        index=index,
                        label=state.display if state else entry.stage_role,
                        actor=f"agent {entry.stage_role}",
                        backend=entry.backend,
                        model=(entry.model or "").split("/")[-1],
                        tokens_in=int(tokens.get("tokens_in", 0)),
                        tokens_out=int(tokens.get("tokens_out", 0)),
                        tokens_cached=int(tokens.get("tokens_cached", 0)),
                        cost_eur=float(tokens.get("cost_eur", 0.0)),
                        duration_s=duration,
                        outcome=str(outcome)[:120],
                    )
                )
            else:
                decided = entry.decided_at
                duration = elapsed_seconds(entry.requested_at, decided)
                decision = (entry.decision or {}).get("approved")
                outcome = "waiting" if decided is None else ("approved" if decision else "sent back")
                lines.append(
                    StageLine(
                        index=index,
                        label={
                            "approval": "Approval",
                            "question": "Question",
                            "scope_change": "Scope",
                        }.get(entry.kind, entry.kind),
                        actor=entry.decided_by or "a person",
                        duration_s=duration,
                        outcome=outcome,
                    )
                )

        totals = item.totals or {}
        engine = bundle.engine
        budget_usd = engine.budget_ticket(item.size)
        state = workflow.states.get(item.state)
        estimate = payload.get("estimate_eur")
        comment = StatusComment(
            workflow=workflow.metadata.name,
            workflow_version=workflow.metadata.version,
            size=item.size,
            risk=item.risk,
            state_display=state.display if state else item.state,
            budget_eur=round(budget_usd * settings.fx_usd_eur, 2),
            spent_eur=float(totals.get("cost_eur", 0.0)),
            pr_url=item.pr_url,
            run_url=f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
            estimate_eur=estimate,
            over_estimate=bool(payload.get("over_estimate")),
            findings=finding_labels,
            memory_note=None if is_marked(item) else payload.get("memory_note"),
            lines=lines,
        )
        markdown = comment.render()
        await bundle.adapters.tracker.upsert_status_comment(item.tracker_key, markdown, STATUS_MARKER)
        await bundle.adapters.tracker.set_fields(
            item.tracker_key,
            {
                # A board from before S22-21 (`Coût (€)`, `Taille`, `Risque`) receives these values in
                # its French fields: the adapter writes into the one that exists.
                "Cost (€)": round(float(totals.get("cost_eur", 0.0)), 2),
                "Size": item.size or "",
                "Risk": item.risk or "",
                "Run": f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
            },
        )
        return {"markdown": markdown}


@activity.defn(name="create_human_request")
async def create_human_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Create the human request, post it on the work item and notify; idempotent by transition."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        existing = (
            await session.execute(
                select(HumanRequest).where(
                    HumanRequest.work_item_id == item.id,
                    HumanRequest.transition_id == payload.get("transition_id"),
                    HumanRequest.kind == payload["kind"],
                    HumanRequest.decided_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"request_id": existing.id, "reused": True}

        sla_hours = int(payload.get("sla_hours") or 24)
        row = new_human_request(
            work_item_id=item.id,
            project_id=bundle.project.id,
            transition_id=payload.get("transition_id"),
            kind=payload["kind"],
            payload=payload.get("payload", {}),
            sla_hours=sla_hours,
        )
        session.add(row)
        await session.flush()

        # A marked work item's question is the run's content: the tracker gets the link (ADR 0114 §6).
        outward = (
            {"question": WITHHELD, "summary": WITHHELD, "justification": WITHHELD}
            if is_marked(item)
            else payload.get("payload", {})
        )
        markdown = render_human_request(payload["kind"], outward, settings.public_url, item.tracker_key)
        await bundle.adapters.tracker.comment(item.tracker_key, markdown)
        channel = notification_target(bundle)
        # The notification is a COURTESY, not the request. The request exists (in the database)
        # and can be read (comment on the work item, front-end screen, API) without it.
        #
        # Before 2026-09-27 it was in the critical path, and the flaw was brutal: on a deployment
        # without Slack (the dev tenant) `notify.send` raises, the exception rolls back the
        # TRANSACTION (hence the request just written), the five attempts fail at the same place,
        # and the work item dies on "[slack] neither bot_token nor webhook_url configured".
        # In other words: the human gate was unreachable on any installation without a notifier,
        # and the message blamed Slack instead of saying "the request is there, nobody was told".
        #
        # `notified` goes in the event and in the return value: a silence shows instead of being
        # guessed. Saying nothing would have replaced a noisy failure with a silent one.
        notified = True
        try:
            await bundle.adapters.notify.send(
                channel,
                Message(
                    title=f"Choregos — {payload['kind']} on {item.tracker_key}",
                    body=WITHHELD if is_marked(item) else item.title,
                    url=f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
                    severity="warning",
                    actions=[
                        MessageAction(id="approve", label="Approve", style="primary", value=row.id),
                        MessageAction(id="reject", label="Send back", style="danger", value=row.id),
                    ],
                ),
            )
        # Deliberately broad: no notifier failure may kill a work item.
        except Exception as exc:
            notified = False
            activity.logger.warning(
                "human request %s created but NOT notified on %s: %s", row.id, channel, exc
            )
        await persist_event(
            session,
            EventType.WORKITEM_HUMAN_REQUESTED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            kind=payload["kind"],
            request_id=row.id,
            notified=notified,
            **_via(bundle),
        )
        return {"request_id": row.id, "reused": False, "notified": notified}


@activity.defn(name="close_human_request")
async def close_human_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Close the request and keep the reason for a refusal where the next agent reads it: a
    transition that declares `inputs: [human_feedback]` receives it. It used to be lost: the agent
    redid the same work without knowing why it was sent back. An approval erases it: it has served."""
    async with db() as session:
        row = await session.get(HumanRequest, payload["request_id"]) if payload.get("request_id") else None
        if row is None:
            return {"closed": False}
        if row.decided_at is None:
            row.decided_at = utcnow()
            row.decided_by = payload.get("decided_by", "the platform")
            row.decision = payload.get("decision", {})
        # Even if the API closed it before the activity ran (the case of an answer), the
        # request leaves its reason or its answer where the next agent reads it.
        await _keep_the_reason(session, row, dict(payload.get("decision") or {}))
        await _judge_the_decisions(session, row, dict(payload.get("decision") or row.decision or {}))
        return {"closed": True}


#: The requests whose verdict judges the decisions before them: a person approves or does the step.
JUDGING_REQUESTS = frozenset({HumanRequestKind.APPROVAL.value, HumanRequestKind.TASK.value})


async def _judge_the_decisions(session: Any, request: HumanRequest, decision: dict[str, Any]) -> None:
    """A human step's verdict on the decisions recorded before it was asked (ADR 0121 §6): sent back
    or rejected, they are overridden; approved or completed, they are not. Only a human step counts —
    an approval or a task on a transition a `human` actor carries. An escalation's approval, an answer
    to a question, a request closed because the work item resumed judge nothing. The first verdict
    after a decision is its verdict: a later one changes nothing (`record_human_verdict`)."""
    if decision.get("superseded") or request.kind not in JUDGING_REQUESTS or not request.transition_id:
        return
    item = await session.get(WorkItem, request.work_item_id)
    if item is None:
        return
    workflow = await work_item_workflow(session, item)
    step = next((t for t in workflow.transitions if t.key == request.transition_id), None)
    if step is None or not isinstance(workflow.actor_of(step), HumanActor):
        return
    await record_human_verdict(session, item.id, request.requested_at, bool(decision.get("approved")))


async def _keep_the_reason(session: Any, request: HumanRequest, decision: dict[str, Any]) -> None:
    item = await session.get(WorkItem, request.work_item_id)
    if item is None:
        return
    documents = dict(item.documents or {})
    answer = str(decision.get("answer") or "").strip()
    if decision.get("kind") == "question" and answer:
        # The answer goes to the agent that resumes (#313), whatever inputs it declares: it
        # settles the question that had stopped it, and used to be lost.
        question = str((request.payload or {}).get("question") or "").strip()
        documents["human_answer"] = f"Question: {question}\n\nAnswer: {answer}" if question else answer
        item.documents = documents
        return
    reason = str(decision.get("reason") or "").strip()
    if decision.get("approved"):
        documents.pop("human_feedback", None)
    elif reason:
        documents["human_feedback"] = reason
    else:
        return
    item.documents = documents


@activity.defn(name="notify")
async def notify(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort notification (the 24 h reminder of a pending human request, #497).

    The interpreter runs it with `NO_RETRY`: raising here failed the whole WorkItem interpreter,
    which left the work item in `needs_human` with nobody to receive its decision. A reminder is
    a courtesy; the request it reminds of lives on without it."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        channel = payload.get("channel") or notification_target(bundle)
        sent = await send_courtesy(
            bundle.adapters.notify, channel, Message.model_validate(payload["message"])
        )
        return {"notified": sent}


@activity.defn(name="close_out")
async def close_out(payload: dict[str, Any]) -> dict[str, Any]:
    """End of a work item: terminal state mirrored, final comment, lesson written to memory."""
    from .children import notify_parent, parent_of

    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        if item.closed_at is None:
            item.closed_at = utcnow()
        await record_terminal_outcome(session, item.id, await _terminal_outcome(session, item))
        # A child work item (S38-12) tells its parent once it is closed, after the commit below.
        settled = parent_of(item)
        await persist_event(
            session,
            EventType.WORKITEM_CLOSED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            state=item.state,
            cost_usd=float((item.totals or {}).get("cost_usd", 0.0)),
        )
        # A marked work item writes no shared memory: later runs would read it unchecked (ADR 0114 §3).
        if bundle.engine.memory_enabled() and not is_marked(item):
            from choregos_core import Fact, Provenance

            runs = (await session.execute(select(Run).where(Run.work_item_id == item.id))).scalars().all()
            attempts = max((run.attempt for run in runs), default=1)
            lesson = (
                f"Work item {item.tracker_key} ({item.size or '?'}, risk {item.risk or '?'}) finished in "
                f"{len(runs)} runs, {attempts} attempt(s) at most, "
                f"{float((item.totals or {}).get('cost_usd', 0.0)):.2f} USD."
            )
            await bundle.adapters.memory.write_fact(
                bundle.slug,
                Fact(
                    kind="ticket_summary",
                    subject=f"ticket_summary:{item.tracker_key}",
                    content=lesson,
                    external_id=f"workitem:{item.tracker_key}",
                    provenance=Provenance(source="orchestrator", work_item_key=item.tracker_key),
                ),
            )
        tracker_closed = await _close_delivered_item(
            session, bundle, item
        ) or await _close_already_fixed_item(session, bundle, item)
        state = item.state
    if settled is not None:
        await notify_parent(settled)
    return {"closed": True, "state": state, "tracker_closed": tracker_closed}


async def _terminal_outcome(session: Any, item: WorkItem) -> str:
    """How the work item ended, for its decisions (ADR 0121 §6): its terminal state's outcome, or
    `cancelled` when it was stopped outside a terminal state."""
    state = (await work_item_workflow(session, item)).states.get(item.state)
    if state is not None and state.terminal:
        return str(state.outcome)
    return CANCELLED_OUTCOME


async def _close_delivered_item(session: Any, bundle: Any, item: WorkItem) -> bool:
    """A delivered work item closes its tracker issue, with a comment naming its release (S22-28).

    Nothing ever closed the issue: `close_out` mirrored the terminal state (the *Done* column) and
    stopped there, and the pull request only says "Work item: ..." since a merge is not a delivery.
    Delivered means a terminal state other than the abandon state, reached by a work item that a
    release train deployed (`deployed_release`, set by `finish_release`). The tracker is a courtesy
    here: a failure is logged and never fails the end of the work item."""
    workflow = await work_item_workflow(session, item)
    state = workflow.states.get(item.state)
    defaults = workflow.defaults.needs_human if workflow.defaults else None
    abandon = defaults.on_abandon if defaults else None
    release_id = (item.documents or {}).get("deployed_release")
    if state is None or not state.terminal or item.state == abandon or not release_id:
        return False
    release = await session.get(Release, str(release_id))
    name = await _release_name(session, release) if release is not None else str(release_id)
    env = f" to `{release.env}`" if release is not None else ""
    text = (
        f"{DELIVERED_MARKER}\n### Choregos — delivered\n"
        f"Delivered{env} in release `{name}` and closed as **{state.display}**."
    )
    try:
        await bundle.adapters.tracker.upsert_status_comment(item.tracker_key, text, DELIVERED_MARKER)
        await bundle.adapters.tracker.close_item(item.tracker_key)
    # Deliberately broad: no tracker failure may kill a work item that is already delivered.
    except Exception as exc:
        logger.warning("delivered work item NOT closed in the tracker", key=item.tracker_key, error=str(exc))
        return False
    return True


async def _close_already_fixed_item(session: Any, bundle: Any, item: WorkItem) -> bool:
    """A work item whose fix was already on the default branch closes its tracker issue as completed,
    with a comment citing the measured evidence (S22-47, #539).

    It ended in the target of the workflow's "already fixed" exit — a `system` transition guarded by
    `already_fixed_proven`, which the interpreter takes only on that evidence. The comment cites the
    evidence of the work item's last run, the one the guarantee judged. As for a delivery, the
    tracker is a courtesy: a failure is logged and never fails the end of the work item."""
    workflow = await work_item_workflow(session, item)
    state = workflow.states.get(item.state)
    exits = [
        t
        for t in workflow.transitions
        if t.to == item.state and any(g.name == ALREADY_FIXED_GATE for g in t.gates)
    ]
    if state is None or not state.terminal or not exits:
        return False
    run = (
        (
            await session.execute(
                select(Run)
                .where(Run.work_item_id == item.id, Run.result.is_not(None))
                .order_by(Run.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    evidence = StageResult.model_validate(run.result).evidence if run is not None else None
    tests = (
        f"- tests on the default branch: {evidence.tests_run} run, {evidence.tests_failed or 0} failed;\n"
        if evidence is not None
        else ""
    )
    text = (
        f"{ALREADY_FIXED_MARKER}\n### Choregos — already fixed\n"
        "The implementation changed nothing: the fix is already on the default branch. "
        "Evidence measured by the platform, not reported by the agent:\n"
        "- change: 0 files, 0 lines, no commit, no branch pushed;\n"
        f"{tests}"
        f"- run: `{run.id if run is not None else '?'}`.\n\n"
        f"Closed as **{state.display}**, with no pull request."
    )
    try:
        await bundle.adapters.tracker.upsert_status_comment(item.tracker_key, text, ALREADY_FIXED_MARKER)
        await bundle.adapters.tracker.close_item(item.tracker_key)
    # Deliberately broad: no tracker failure may kill a work item that is already settled.
    except Exception as exc:
        logger.warning(
            "already fixed work item NOT closed in the tracker", key=item.tracker_key, error=str(exc)
        )
        return False
    return True


async def _release_name(session: Any, release: Release) -> str:
    """The tag the promotion set (`R-2026.10.10-1`), else the batch number."""
    revision = (
        await session.execute(
            select(Deployment.revision)
            .where(Deployment.release_id == release.id, Deployment.revision.is_not(None))
            .order_by(Deployment.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    return str(revision) if revision else f"batch {release.batch_no}"


def _poll_may_start(tracker: Any, config: Any, data: Any) -> bool:
    """Whether the poll may open a work item the project does not hold yet (#1112).

    GitHub, GitLab and Jira already search for `agent-ready` and the start rules (S56-03). A plugin
    tracker's server returns its own candidates (ServiceNow: its open queue), so the poll asks the
    item what the webhook asks the event: `agent-ready`, or a start rule that holds."""
    from choregos_adapters.tracker.plugin import PluginTracker

    if not isinstance(tracker, PluginTracker):
        return True
    labels = {str(label) for label in data.labels if label}
    if "agent-ready" in labels:
        return True
    item_type = str(data.item_type) if data.item_type else None
    state = str(data.state) if data.state else None
    return any(rule.matches(labels, item_type, state) for rule in config.start_rules)


@activity.defn(name="reconcile_tracker")
async def reconcile_tracker(payload: dict[str, Any]) -> dict[str, Any]:
    """Catch-up for lost webhooks: the `agent-ready` work items nobody saw (S3-06).

    A webhook can get lost: failed delivery, unreachable platform, changed secret. The tracker is
    then the only source of truth: its candidates are read again and what is missing is created.
    The start goes through a deterministic identifier, so a work item already handled never starts
    a second time. An ENDED work item whose ticket the tracker says came back after the end (reopened,
    moved back to work) starts its new pass (C1), once: the pass is claimed in the database, so the
    next pass finds it running.
    """
    from choregos_api.temporal import interpreter_id

    from ..train_client import start_workflow_once

    created: list[str] = []
    started: list[str] = []
    reopened: list[str] = []
    skipped: list[str] = []
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if bundle.project.status in {"archived", "suspended"}:
            # An archived project's loop is stopped, a suspended one starts nothing: should the
            # signal have missed, a pass still takes nothing on.
            return {"skipped": bundle.project.status, "created": [], "started": []}
        if tracker_owns_items(bundle.adapters.tracker):
            candidates = await bundle.adapters.tracker.list_candidates(bundle.config)
        else:
            # No outside: the candidates are the database's work items that have not finished.
            # Without this branch, `list_candidates` returned `[]` and a work item created by the
            # platform (a promoted finding, a request typed in the front end) was never discovered:
            # it stayed in its initial state forever.
            candidates = list(
                (
                    await session.execute(
                        select(WorkItem.tracker_key).where(
                            WorkItem.project_id == bundle.project.id,
                            WorkItem.closed_at.is_(None),
                            WorkItem.paused.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for key in candidates:
            item = (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == bundle.project.id, WorkItem.tracker_key == key
                    )
                )
            ).scalar_one_or_none()
            if item is None:
                data = await bundle.adapters.tracker.fetch_item(key)
                if not _poll_may_start(bundle.adapters.tracker, bundle.config, data):
                    skipped.append(key)
                    continue
                # Born in the workflow its labels or its type designate (ADR 0031).
                item = await new_work_item(
                    session,
                    bundle.project,
                    WorkItemBirth(
                        tracker_key=key,
                        title=data.title,
                        body=data.body,
                        url=data.url,
                        size=str(data.size) if data.size else None,
                        risk=str(data.risk) if data.risk else None,
                        labels=tuple(data.labels),
                        item_type=data.item_type,
                        reporter=data.author,
                        initiated_by=initiator("tracker", "sync"),
                        # Who filed it, its labels, its form's answers (S51-04): `fields.tracker`.
                        tracker=data.fields.get(TRACKER_FIELD),
                    ),
                )
                created.append(key)
            elif item.closed_at is not None:
                if await _restart_if_back(session, bundle, item, interpreter_id(bundle.slug, key)):
                    reopened.append(key)
                continue
            state = (await work_item_workflow(session, item)).states.get(item.state)
            if item.paused or item.closed_at is not None or (state is not None and state.terminal):
                continue
            workflow_id = interpreter_id(bundle.slug, key)
            fresh = await start_workflow_once(
                "WorkflowInterpreter",
                workflow_id,
                {
                    "project_id": bundle.project.id,
                    "project_slug": bundle.slug,
                    "work_item_id": item.id,
                    "tracker_key": key,
                },
            )
            if fresh:
                item.temporal_wf_id = workflow_id
                started.append(key)
        if created or started or reopened:
            await persist_event(
                session,
                EventType.WORKITEM_CREATED,
                project_id=bundle.project.id,
                project_slug=bundle.slug,
                subject=bundle.slug,
                source="reconciliation",
                created=created,
                started=started,
                reopened=reopened,
            )
    return {
        "candidates": len(candidates),
        "created": created,
        "started": started,
        "reopened": reopened,
        "skipped": skipped,
    }


async def _restart_if_back(session: Any, bundle: Any, item: WorkItem, workflow_id: str) -> bool:
    """An ended work item among the candidates: its ticket is read again, and if it came back after the
    end (`came_back_since`), the pass is claimed and its interpreter started on the same id (C1)."""
    from ..train_client import restart_interpreter

    try:
        data = await bundle.adapters.tracker.fetch_item(item.tracker_key)
    # Deliberately broad: a ticket the tracker cannot answer for must not stop the catch-up of the others.
    except Exception as exc:
        logger.warning("ended work item's ticket not read again", key=item.tracker_key, error=str(exc)[:300])
        return False
    if not came_back_since(item, data, await work_item_workflow(session, item)):
        return False
    recurrence = await reopen_work_item(
        session, bundle.project, item, door="tracker:sync", reason="the tracker says the ticket came back"
    )
    if recurrence is None:
        return False
    await restart_interpreter(workflow_id, restart_payload(bundle.project, item))
    item.temporal_wf_id = workflow_id
    return True
