# SPDX-License-Identifier: Apache-2.0
"""The activities proper to the interpreter: load a work item's context, record the death of an
interpreter or a migration, announce a work item to the train, and read a Temporal error.

They used to live in the workflow module, under an `import activity` at the end of the file;
a workflow has no business carrying its activities.
"""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Event, Run, WorkflowDef, WorkItem
from choregos_api.services import persist_event, work_item_workflow, workflow_model
from choregos_api.services.budgets import ticket_budget, ticket_spent
from choregos_contracts import EventType
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ActivityError, ApplicationError

from ..train_client import signal_release_train
from ..wire_names import read_key, require_key
from .base import db, load_work_item, project_bundle
from .execution import abandon_queued_runs
from .outcome import close_run_spend


@activity.defn(name="load_context")
async def load_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Load the pinned definition, the policy and the current state of the work item."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        # An interpreter that (re)starts is no longer dead: the mark drops here, and nowhere
        # else, since this is the only activity every start goes through.
        item.failure = None
        # The version the work item was born in (ADR 0031). A work item from before the pin gets it
        # here, once: replaying the activity reads the same one. Only the BODY of the activity
        # changes, with no new command in the history, so the archived histories replay.
        pinned = await work_item_workflow(session, item)
        if item.workflow_def_id is None and pinned is not None:
            item.workflow_def_id = pinned.id
        workflow = workflow_model(pinned)
        state, spent = await consistent_progress(session, item.id)
        return {
            "workflow": workflow.model_dump(mode="json", by_alias=True, exclude_none=True),
            "workflow_def_id": item.workflow_def_id,
            "state": state,
            "ticket_budget_usd": ticket_budget(bundle.engine, item),
            # What the ticket has cost so far, models and tools (S27-03): a restart keeps it.
            "ticket_cost_usd": await ticket_spent(session, item.id),
            # The policy's ceiling on a transition's attempts, by role (S27-08).
            "attempts_ceilings": bundle.engine.attempts_ceilings(),
            "tracker_key": item.tracker_key,
            "project_slug": bundle.slug,
            # What an action date reads (`not_before`, S20-05); a change then arrives through the
            # `fields_changed` signal.
            "fields": dict(item.fields or {}),
            # The attempts each transition already spent, read from its runs (#550).
            "attempts_spent": spent,
            # What the last stage answered: a transition's `when` chooses on it (S48-01).
            "last_outputs": await last_outputs(session, item.id),
        }


async def last_outputs(session: Any, work_item_id: str) -> dict[str, Any] | None:
    """The outputs of the work item's last finished run with a result, for a restarted interpreter
    (S48-01). A run still queued or running has none yet; the interpreter reads its result when it
    awaits it, and takes its outputs from there."""
    row = (
        await session.execute(
            select(Run.result)
            .where(Run.work_item_id == work_item_id, Run.result.is_not(None), Run.ended_at.is_not(None))
            .order_by(Run.ended_at.desc(), Run.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    outputs = row.get("outputs") if isinstance(row, dict) else None
    if not isinstance(outputs, dict):
        return None
    return {k: v for k, v in outputs.items() if v is not None} or None


async def consistent_progress(session: Any, work_item_id: str) -> tuple[str, dict[str, int]]:
    """The work item's state and the attempts its runs spent, as ONE moment of the database (#748).

    `mirror_state` commits the state and the runs' `decided_at` together. Read in two statements,
    a commit between them gave a restarted interpreter the OLD state with the NEW attempts, and it
    played the stage again as attempt 2. Reordering the reads is not enough (the opposite mix
    leaves the attempts one short, and a later pass would be handed the old run back), and an
    isolation level is not portable (SQLite has no REPEATABLE READ, and no snapshot outside WAL).
    So both are read twice, until two successive reads agree: a commit that moved one moved the
    other, so agreeing reads straddle no commit. Works the same on PostgreSQL and SQLite.
    """
    latest: tuple[str, dict[str, int]] | None = None
    for _ in range(_CONSISTENT_READS):
        state = str(
            (await session.execute(select(WorkItem.state).where(WorkItem.id == work_item_id))).scalar_one()
        )
        read = (state, await attempts_spent(session, work_item_id))
        if read == latest:
            return read
        latest = read
    assert latest is not None
    return latest  # still moving after several reads: the last one, as before


#: How many times the state and the attempts are read before giving up on a quiet moment.
_CONSISTENT_READS = 5

#: A run in one of these states is over: awaiting it again only reads its old result back.
FINISHED_RUNS = frozenset({"succeeded", "failed", "cancelled", "timed_out"})


async def attempts_spent(session: Any, work_item_id: str) -> dict[str, int]:
    """The attempts each transition spent, read from its runs that are over (#550).

    A run number names the run (`<work item>-<transition>-<attempt>`): an interpreter started again
    at attempt 1 got the failed run back from `prepare_stage`, and `await_run` its old result. So an
    attempt whose run ended without success is spent, and the next one is played. A run still queued
    or running does not count: a restart awaits it again (ADR 0008). Nor does a SUCCEEDED last run
    whose decision was never recorded (`decided_at`): the interpreter died before applying it, and a
    restart reads its result back rather than pay for the stage again and push its commits twice.
    A succeeded run whose decision was applied is spent like any other (#552): the work item came
    back to its transition on a later pass — a review asking for changes — which plays a new attempt.
    """
    rows = await session.execute(
        select(Run.transition_id, Run.attempt, Run.status, Run.decided_at).where(
            Run.work_item_id == work_item_id,
            Run.transition_id.is_not(None),
            Run.status.in_(FINISHED_RUNS),
        )
    )
    last: dict[str, tuple[int, bool]] = {}
    for transition, attempt, status, decided_at in rows.all():
        if attempt and int(attempt) > last.get(str(transition), (0, False))[0]:
            # Only a succeeded run not decided yet is read back; any other finished run is spent.
            read_back = status == "succeeded" and decided_at is None
            last[str(transition)] = (int(attempt), read_back)
    spent = {key: attempt - 1 if read_back else attempt for key, (attempt, read_back) in last.items()}
    return {key: count for key, count in spent.items() if count > 0}


@activity.defn(name="record_workflow_failure")
async def record_workflow_failure(payload: dict[str, Any]) -> dict[str, Any]:
    """Write on the work item that its interpreter died, and publish the event.

    The workflow calls it as it dies. It is NOT a replacement for the Temporal state (the API reads
    that too), but it is what remains once Temporal has purged the history, and it is what the
    front end and the timeline show.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        failure = {
            "message": str(payload.get("message") or "the interpreter failed"),
            "activity": payload.get("activity"),
            "state": payload.get("state") or item.state,
            "at": utcnow().isoformat(),
        }
        item.failure = failure
        await persist_event(
            session,
            EventType.WORKITEM_WORKFLOW_FAILED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            **failure,
        )
        open_runs = await _runs_without_spend(session, item.id)
    # The interpreter dies — `await_run` gave up, an activity failed for good: nothing will call
    # `collect_spend` for its run. Its spend is booked and its key revoked here (#523).
    # A run still waiting for a slot is removed at the executor first, or its suspended Job and
    # its Secret stay forever (#501); the key is revoked after, as `cancel_run` does.
    await abandon_queued_runs(payload["project_id"], open_runs)
    for run_id in open_runs:
        await close_run_spend(payload["project_id"], run_id)
    return failure


async def _runs_without_spend(session: Any, work_item_id: str) -> list[str]:
    """The ticket's runs whose spend was never collected — at most the last few."""
    rows = await session.execute(
        select(Run.id)
        .where(Run.work_item_id == work_item_id, Run.spend_collected.is_(False))
        .order_by(Run.created_at.desc())
        .limit(10)
    )
    return [str(run_id) for run_id in rows.scalars().all()]


@activity.defn(name="record_migration")
async def record_migration(payload: dict[str, Any]) -> dict[str, Any]:
    """Record a migration (`migrate`, ADR 0031): accepted, the work item's pin follows the target
    definition (a `continue_as_new` will reload it); refused, the work item stays on its own, and
    the event says so.

    Replayable without a double effect (ADR 0008): the migration's key (`<workflow>/<run>/<n>`) is
    in the event, and a key already recorded is not recorded twice.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        # The key and the refusal are read under both spellings (S45-03), in the payload and in the
        # events already persisted; the event persists `key` since S45-39 (and still reads `cle`).
        key = str(require_key(payload, "key"))
        types = (str(EventType.WORKITEM_MIGRATED), str(EventType.WORKITEM_MIGRATION_REFUSED))
        recorded = (
            await session.execute(select(Event).where(Event.work_item_id == item.id, Event.type.in_(types)))
        ).scalars()
        if any(read_key(e.payload or {}, "key") == key for e in recorded):
            return {"recorded": False}
        common = {
            "project_id": bundle.project.id,
            "work_item_id": item.id,
            "project_slug": bundle.slug,
            "subject": item.tracker_key,
            "key": key,
            "from_state": payload.get("from_state"),
        }
        refusal = read_key(payload, "refusal")
        if refusal:
            await persist_event(session, EventType.WORKITEM_MIGRATION_REFUSED, **common, reason=str(refusal))
            return {"recorded": True, "pinned": False}
        target = await session.get(WorkflowDef, str(payload.get("workflow_def_id") or ""))
        if target is not None and target.project_id == bundle.project.id:
            item.workflow_def_id = target.id
        await persist_event(
            session,
            EventType.WORKITEM_MIGRATED,
            **common,
            to_state=payload.get("to_state"),
            workflow_def_id=target.id if target is not None else None,
            workflow=payload.get("workflow"),
        )
        return {"recorded": True, "pinned": target is not None}


@activity.defn(name="escalation_context")
async def escalation_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Where a parked work item resumes, for an interpreter that did not park it (S22-41).

    An interpreter started again from the database knows its state (`needs_human`) but not the
    stage that escalated: `rerun_stage` and an answer did nothing. Since S22-41 the escalation
    writes `parked` — `[parked state, origin state, transition]` — on its `state_changed` event;
    a work item that escalated before has it derived: the transition out of the state it came
    from, other than a person's, whose escalation leads where it now stands. Also returns how
    many attempts that transition already made: a run number is never reused, it names the run.

    Read only: replaying it changes nothing.
    """
    from choregos_core import WorkflowEngine

    async with db() as session:
        item = await load_work_item(session, payload["work_item_id"])
        state = str(payload.get("state") or item.state)
        events = (
            await session.execute(
                select(Event)
                .where(
                    Event.work_item_id == item.id,
                    Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                )
                .order_by(Event.ts.desc())
            )
        ).scalars()
        arrival = next((e.payload or {} for e in events if (e.payload or {}).get("to") == state), None)
        if arrival is None:
            return {"parked": None}
        parked = arrival.get("parked")
        if not (isinstance(parked, list) and len(parked) == 3 and parked[0] == state):
            engine = WorkflowEngine(workflow_model(await work_item_workflow(session, item)))
            parked = await _derived_parking(session, item.id, engine, state, arrival)
        if parked is None:
            return {"parked": None}
        attempts = (
            await session.execute(
                select(Run.attempt).where(Run.work_item_id == item.id, Run.transition_id == parked[2])
            )
        ).scalars()
        return {"parked": [str(p) for p in parked], "attempts": max(attempts, default=0)}


async def _derived_parking(
    session: Any, work_item_id: str, engine: Any, state: str, arrival: dict[str, Any]
) -> list[str] | None:
    """The escalation context of a work item parked before S22-41, read from what it left."""
    origin = arrival.get("from")
    if not origin or origin == state or arrival.get("reason") == "external event":
        return None  # moved there by a person on the board: nothing escalated
    candidates = [
        t
        for t in engine.wf.transitions_from(origin)
        if engine.actor_kind(t) != "human" and state != t.to and state in _escalation_targets(engine, t)
    ]
    if not candidates:
        return None
    keys = {t.key for t in candidates}
    last_run = (
        await session.execute(
            select(Run.transition_id)
            .where(Run.work_item_id == work_item_id, Run.transition_id.in_(keys))
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    key = last_run or candidates[0].key
    return [state, str(origin), str(key)]


def _escalation_targets(engine: Any, transition: Any) -> set[str]:
    """Every state a stage of `transition` may park the work item in (engine: escalation,
    question, budget, stage cap)."""
    budget, defaults = engine.wf.budget, engine.wf.defaults
    from_any = defaults.from_any_agent_state if defaults else None
    targets = {
        engine.question_state(),
        getattr(from_any, "on_budget_exceeded", None),
        budget.on_exceeded if budget is not None else None,
    }
    for retry in (transition.on_fail, transition.on_changes_requested):
        if retry is not None:
            targets.add(retry.escalate_to)
    return {str(t) for t in targets if t}


#: What Temporal puts in place of an absent message, literally
#: (`converter/_failure_converter.py`, `application_failure_info` branch:
#: `failure.message or "Application error"`). The message is therefore NEVER empty, and an
#: emptiness test is not enough: the stopgap must be recognised to prefer the type.
TEMPORAL_PLACEHOLDERS = frozenset({"Application error", "Timeout", "Activity task failed"})


def message_of(exc: BaseException) -> str:
    """The useful message of a Temporal error: the cause's, not "Activity task failed".

    An ordinary exception crosses Temporal all the same: the converter makes it an
    `ApplicationError` whose `type` carries the class name and `message` the `str()`. Yet many
    network errors are raised **without a message** (`httpx.ConnectTimeout()` has none). The
    fallback was then `"Application error"`, the string Temporal substitutes for an absent
    message, and the work item said nothing more of its death. It happened on 2026-09-27 on the dev
    tenant: the real cause, the unreachable Kubernetes API, lived only in the orchestrator's log,
    which nobody reads before having a reason to. Without a message, the **type** is the most
    useful thing there is.
    """
    fallback: str | None = None
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ApplicationError):
            if cause.message and cause.message not in TEMPORAL_PLACEHOLDERS:
                return cause.message
            if cause.type and fallback is None:
                fallback = cause.type
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    return fallback or str(cause or exc)[:500]


def activity_of(exc: BaseException) -> str | None:
    return exc.activity_type if isinstance(exc, ActivityError) else None


@activity.defn(name="signal_train")
async def signal_train(payload: dict[str, Any]) -> dict[str, Any]:
    """Announce to the environment's train that a work item is ready to board, with its labels and
    the approval its workflow requires of the departure (ADR 0041), absent from earlier boardings."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        await signal_release_train(
            bundle.slug,
            payload["env"],
            "merged",
            {
                "work_item_key": item.tracker_key,
                "title": item.title,
                "merged_at": utcnow().isoformat(),
                "sha": (item.documents or {}).get("merge_sha", ""),
                # Declared by the agent in `artifacts.reports["infra_pr"]`: the train will apply it
                # through Atlantis during the departure, after approval.
                "infra_pr_url": (item.documents or {}).get("infra_pr_url"),
                "risk": item.risk,
                # The work item's labels, kept since its birth: `hotfix` opens the express lane
                # (#279). A work item born before this change kept none.
                "labels": list((item.documents or {}).get("labels") or []),
                "pr_url": item.pr_url,
                **({"approval": payload["approval"]} if payload.get("approval") else {}),
            },
        )
        return {"signalled": True}
