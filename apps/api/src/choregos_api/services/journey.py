# SPDX-License-Identifier: Apache-2.0
"""The journey of a work item through its workflow (S22-01): what the console animates.

The timeline (`timeline`) tells a work item's story in sentences; it does not say THROUGH WHICH
edge of the graph it went, and the console had to rebuild that by rereading its titles. Here, every
move from state to state is attached to the transition that carried it: the transition itself, its
rejection, its changes request, its retry after a failure, its escalation, or a default "from any
agent state". It reads the definition THE WORK ITEM IS PINNED TO, not the project's today.

And every step of an actor (an agent run, a request to a person, a governed action, a train
departure) is rendered with its transition and its rank: the journey map thus knows which box
lights up, how many rounds a review took, and what still waits for someone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from choregos_contracts import EventType, Workflow
from choregos_contracts.workflow import AGENT_WILDCARD
from choregos_core import to_graph, to_process
from choregos_core.compat import alias_names
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Action, Event, HumanRequest, Release, Run, WorkItem
from ..schemas import JourneyMove, JourneyStep, WorkItemJourney
from .definitions import work_item_workflow, workflow_model


def move_edge(wf: Workflow, source: str | None, target: str, reason: str = "") -> tuple[str, str | None]:
    """`(kind, transition_id)` of a move `source -> target`.

    The order decides the cases where two edges lead to the same place: the transition itself
    first (an explicit transition before a wildcard one), then its rejection, its changes request,
    its retry, its escalation; then the workflow's defaults. A move that no edge explains (a work
    item moved by hand in the tracker) stays `other`: it is not invented.
    """
    if source is None:
        return "start", None
    if reason.startswith("migrated to"):
        return "migrated", None
    return _by_a_transition(wf, source, target) or _by_a_default(wf, source, target) or ("other", None)


def _by_a_transition(wf: Workflow, source: str, target: str) -> tuple[str, str | None] | None:
    candidates = wf.transitions_from(source)
    explicit = [t for t in candidates if t.from_ != AGENT_WILDCARD]
    wildcards = [t for t in candidates if t.from_ == AGENT_WILDCARD]
    for t in [*explicit, *wildcards]:
        if t.to == target:
            return "nominal", t.key
    issues: list[tuple[str, Any]] = [
        ("reject", lambda t: t.on_reject == target),
        (
            "changes_requested",
            lambda t: t.on_changes_requested is not None and t.on_changes_requested.to == target,
        ),
        ("retry", lambda t: t.on_fail is not None and t.on_fail.to == target),
        (
            "escalate",
            lambda t: any(
                r is not None and r.escalate_to == target for r in (t.on_fail, t.on_changes_requested)
            ),
        ),
    ]
    for kind, leads_there in issues:
        for t in candidates:
            if leads_there(t):
                return kind, t.key
    return None


def _by_a_default(wf: Workflow, source: str, target: str) -> tuple[str, str | None] | None:
    defaults = wf.defaults
    parking: set[str] = set()
    if defaults and defaults.from_any_agent_state:
        d = defaults.from_any_agent_state
        parking = {s for s in (d.on_question, d.on_budget_exceeded, d.on_timeout) if s}
        if target in parking and source in wf.agent_driven_states():
            return "default", None
    if defaults and defaults.needs_human and defaults.needs_human.on_abandon == target:
        return "default", None
    # Left a state where a human was awaited, towards the state it came from: the answer restarts it.
    escalations = {
        retry.escalate_to
        for t in wf.transitions
        for retry in (t.on_fail, t.on_changes_requested)
        if retry is not None
    }
    if source in parking | escalations:
        return "resume", None
    return None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _request_status(request: HumanRequest) -> str:
    """`waiting` while nobody has decided; otherwise what was decided, in one word."""
    if request.decided_at is None:
        return "waiting"
    decision = dict(request.decision or {})
    if decision.get("kind") == "task":
        return "completed" if decision.get("approved") is not False else "rejected"
    if decision.get("kind") == "scope_change":
        return "scope_change"
    if decision.get("kind") == "question":
        return "answered"
    return "approved" if decision.get("approved") else "rejected"


def _rank(counters: dict[str, int], key: str | None) -> int:
    """The rank of a step on its transition: the first, the second... in the order of time."""
    key = key or ""
    counters[key] = counters.get(key, 0) + 1
    return counters[key]


def _moment(*values: datetime | None) -> datetime | None:
    return next((v for v in values if v is not None), None)


async def journey(session: AsyncSession, item: WorkItem) -> WorkItemJourney:
    """The map of the version the work item is pinned to, its moves, and the steps of its actors."""
    definition = await work_item_workflow(session, item)
    wf = workflow_model(definition)

    state_events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.work_item_id == item.id,
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                )
                .order_by(Event.ts)
            )
        )
        .scalars()
        .all()
    )
    moves: list[JourneyMove] = []
    for event in state_events:
        source = _text(event.payload.get("from"))
        target = str(event.payload.get("to") or "")
        if not target:
            continue
        reason = str(event.payload.get("reason") or "")
        kind, transition = move_edge(wf, source, target, reason)
        moves.append(
            JourneyMove.model_validate(
                {
                    "at": event.ts,
                    "from": source,
                    "to": target,
                    "kind": kind,
                    "transition_id": transition,
                    "reason": reason or None,
                }
            )
        )

    steps: list[JourneyStep] = []
    runs = (
        (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
        .scalars()
        .all()
    )
    for run in runs:
        result = dict(run.result or {})
        outputs = dict(result.get("outputs") or {})
        evidence = {k: v for k, v in dict(result.get("evidence") or {}).items() if v is not None}
        steps.append(
            JourneyStep(
                id=run.id,
                kind="agent",
                transition_id=run.transition_id,
                actor=run.actor,
                role=run.stage_role,
                attempt=max(run.attempt or 1, 1),
                status=run.status,
                started_at=_moment(run.started_at, run.created_at),
                ended_at=run.ended_at,
                summary=_text(result.get("summary")),
                verdict=_text(outputs.get("verdict")),
                cost_usd=run.cost_usd or 0.0,
                model=run.model,
                evidence=evidence,
            )
        )

    human_actors = {t.key: t.by for t in wf.transitions}
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
    ranks: dict[str, int] = {}
    for request in requests:
        payload = dict(request.payload or {})
        steps.append(
            JourneyStep(
                id=request.id,
                kind="human",
                transition_id=request.transition_id,
                actor=human_actors.get(request.transition_id or ""),
                role=request.kind,
                attempt=_rank(ranks, request.transition_id),
                status=_request_status(request),
                started_at=request.requested_at,
                ended_at=request.decided_at,
                summary=_text(payload.get("question") or payload.get("summary")),
                decided_by=request.decided_by,
                due_at=request.due_at,
            )
        )

    actions = (
        (
            await session.execute(
                select(Action).where(Action.work_item_id == item.id).order_by(Action.created_at)
            )
        )
        .scalars()
        .all()
    )
    ranks = {}
    for action in actions:
        transition = _text(dict(action.proposed_by or {}).get("transition"))
        steps.append(
            JourneyStep(
                id=action.id,
                kind="action",
                transition_id=transition,
                actor="platform",
                role=action.kind,
                attempt=_rank(ranks, transition),
                status=action.status,
                started_at=action.created_at,
                ended_at=action.finished_at,
                summary=action.title,
            )
        )

    # The train: a release that carries the work item is the step of the `via: release_train`
    # transition towards its environment. Filed by project; only those that name it are kept.
    trains = {t.train.env: t.key for t in wf.transitions if t.via == "release_train" and t.train}
    if trains:
        releases = (
            (
                await session.execute(
                    select(Release)
                    .where(Release.project_id == item.project_id, Release.created_at >= item.created_at)
                    .order_by(Release.created_at)
                )
            )
            .scalars()
            .all()
        )
        ranks = {}
        for release in releases:
            if release.env not in trains:
                continue
            carries = any(
                e.get("work_item_id") == item.id or e.get("work_item_key") == item.tracker_key
                for e in release.items or []
                if isinstance(e, dict)
            )
            if not carries:
                continue
            transition = trains[release.env]
            steps.append(
                JourneyStep(
                    id=release.id,
                    kind="action",
                    transition_id=transition,
                    actor="release_train",
                    role=f"release to {release.env}",
                    attempt=_rank(ranks, transition),
                    status=release.status,
                    started_at=_moment(release.started_at, release.created_at),
                    ended_at=release.ended_at,
                    summary=f"batch {release.batch_no} to {release.env}, "
                    f"{len(release.items or [])} work item(s)",
                    decided_by=release.approved_by,
                )
            )

    steps.sort(key=lambda s: (s.started_at is None, s.started_at or item.created_at))
    return WorkItemJourney(
        work_item_id=item.id,
        tracker_key=item.tracker_key,
        workflow_name=wf.metadata.name,
        workflow_version=wf.metadata.version,
        initial=wf.initial_state,
        state=item.state,
        closed=item.closed_at is not None,
        graph=to_graph(wf),
        process=to_process(wf),
        moves=moves,
        steps=steps,
    )


parcours = journey  # lang: fr-ok alias:internal
arete_du_deplacement = move_edge  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "parcours": "journey",  # lang: fr-ok alias:internal
        "arete_du_deplacement": "move_edge",  # lang: fr-ok alias:internal
    },
)
