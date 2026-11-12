# SPDX-License-Identifier: Apache-2.0
"""The activities of the `ActionWorkflow` (ADR 0035): load the action, play an effect under its key,
compensate it, close.

The journal (`action_effects`) is the memory that makes a resume safe:
- an effect is RECORDED (`started`) in its own transaction BEFORE it is attempted;
- it is CONFIRMED (`done`, with its response) after;
- a replayed activity that finds its key `done` returns the recorded response and redoes nothing;
- a `started` key that was never confirmed (a worker killed in between) is retried: effects are
  idempotent by construction (`declare_effect`);
- an effect that was sent and never answered is recorded `in_doubt` with what was sent (ADR 0149
  §5): it is not attempted again, nothing the action had done is compensated, and the action is
  closed `in_doubt`, whatever the workflow that plays it asks for.
"""

from __future__ import annotations

import contextlib
from typing import Any

from choregos_api.db.models import Action, ActionEffect, Project, WorkItem
from choregos_api.effects import EffectContext, EffectInDoubt, EffectRefused, effect, render
from choregos_api.logging import get_logger
from choregos_api.services import persist_event
from choregos_contracts import ActionOrigin, ActionStatus, EventType
from choregos_core import utcnow
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..wire_names import read_key, require_key
from .base import db

logger = get_logger("choregos.actions")

#: What an `ActionWorkflow` may run: an approval that was committed, or a run of it already begun
#: (`load_action` retried after it moved the row).
EXECUTABLE = frozenset({ActionStatus.APPROVED.value, ActionStatus.RUNNING.value})

#: Error types that histories record (ADR 0098 section 4). Raised in English since S45-39; the
#: workflows' retry policies read both spellings (`wire_names.both_names`).
ACTION_NOT_FOUND = "ActionNotFound"
ACTION_NOT_VISIBLE_YET = "ActionNotYetVisible"
EFFECT_REFUSED = "EffectRefused"
#: The effect was sent and nobody knows whether it was executed (ADR 0149 §5). The `ActionWorkflow`
#: reads this type: it compensates nothing and closes the action in doubt.
EFFECT_IN_DOUBT = "EffectInDoubt"
#: The status of an effect, and of its action, that ended in doubt.
IN_DOUBT = ActionStatus.IN_DOUBT.value


async def _load(session: Any, action_id: str) -> tuple[Action, Project]:
    action = await session.get(Action, action_id)
    if action is None:
        raise ApplicationError(
            f"action not found: {action_id}",
            type=ACTION_NOT_FOUND,
            non_retryable=True,
        )
    project = await session.get(Project, action.project_id)
    if project is None:
        raise ApplicationError(f"project not found: {action.project_id}", non_retryable=True)
    return action, project


async def _done_effects(session: Any, action_id: str, before: int) -> list[dict[str, Any]]:
    """The responses of the effects already done, in order: what the following ones may cite."""
    rows = (
        await session.execute(
            select(ActionEffect)
            .where(ActionEffect.action_id == action_id, ActionEffect.position < before)
            .order_by(ActionEffect.position)
        )
    ).scalars()
    return [dict(row.result or {}) for row in rows]


@activity.defn(name="load_action")
async def load_action(action_id: str) -> dict[str, Any]:
    async with db() as session:
        if await session.get(Action, action_id) is None:
            # Started after the commit since ADR 0085; a replica of an older version still starts
            # it before. A passing error, which Temporal retries.
            raise ApplicationError(f"action {action_id} not visible yet", type=ACTION_NOT_VISIBLE_YET)
        action, _project = await _load(session, action_id)
        if action.status not in EXECUTABLE:
            # Fail closed (ADR 0085): a workflow started by anything but a committed approval — a
            # decision whose transaction failed, a hand-made start, a finished action started again —
            # runs nothing, and leaves the row as it found it. Final: retrying would not approve it.
            raise ApplicationError(
                f"action {action_id} is {action.status}, not approved: nothing is done",
                type="ActionNotApproved",
                non_retryable=True,
            )
        if action.status == ActionStatus.APPROVED.value:
            action.status = ActionStatus.RUNNING.value
        return {"effects": len(action.effects or []), "status": action.status}


@activity.defn(name="execute_effect")
async def execute_effect(payload: dict[str, Any]) -> dict[str, Any]:
    action_id, position = str(payload["action_id"]), int(payload["position"])
    key = f"{action_id}:{position}"
    async with db() as session:
        action, project = await _load(session, action_id)
        row = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == key))
        ).scalar_one_or_none()
        if row is not None and row.status == "done":
            # Already done: the recorded response, nothing redone. That is the whole guarantee.
            return {"key": key, "replayed": True, "result": row.result}
        doubts = _doubts(row)
        if doubts and not _may_be_sent_again(row):
            # In doubt, and final (ADR 0149 §5): the effect is not attempted again, by a task Temporal
            # delivers twice no more than by anything else.
            raise ApplicationError(
                str(row.error if row is not None else "") or f"the effect {key} is in doubt",
                type=EFFECT_IN_DOUBT,
                non_retryable=True,
            )
        spec = (action.effects or [])[position]
        context = {
            "params": action.params or {},
            "effects": await _done_effects(session, action_id, position),
        }
        try:
            params = render(spec.get("with") or {}, context)
        except EffectRefused as refused:
            raise ApplicationError(str(refused), type=EFFECT_REFUSED, non_retryable=True) from refused
        if row is None:
            row = ActionEffect(
                org_id=action.org_id,
                action_id=action_id,
                position=position,
                key=key,
                effect=str(spec["effect"]),
                params=params,
                status="started",
                attempts=0,
            )
            session.add(row)
        row.attempts += 1
        row.params, row.error = params, None
        if not doubts:
            # A doubt is not forgotten by the attempt that follows it: the row stays `in_doubt` until
            # a delivery is answered, so that whatever ends the action in between finds it there.
            row.status = "started"
    # Recorded and committed BEFORE the attempt: a worker killed here leaves a `started` key.
    once_durable: list[Any] = []
    try:
        async with db() as session:
            action, project = await _load(session, action_id)
            played = EffectContext(
                session, action, project, key=key, doubts=tuple(doubts), once_durable=once_durable
            )
            result = await effect(str(spec["effect"]))(played, params)
    except EffectInDoubt as doubt:
        # Neither a refusal nor a failure: recorded with what was sent, before anything is said to
        # the workflow. Retried only when the effect says the same key may be delivered again.
        await _note_doubt(key, doubt, doubts, once_durable)
        raise ApplicationError(str(doubt), type=EFFECT_IN_DOUBT, non_retryable=not doubt.again) from doubt
    except EffectRefused as refused:
        await _note_failure(key, str(refused))
        kind = EFFECT_IN_DOUBT if doubts else EFFECT_REFUSED
        raise ApplicationError(str(refused), type=kind, non_retryable=True) from refused
    except Exception as failure:
        await _note_failure(key, f"{type(failure).__name__}: {failure}")
        if doubts:
            # An earlier delivery is still in doubt: whatever this attempt met, the effect is not a
            # failure the action may compensate. As retryable as the failure itself was.
            raise ApplicationError(f"{type(failure).__name__}: {failure}", type=EFFECT_IN_DOUBT) from failure
        raise
    async with db() as session:
        row = (await session.execute(select(ActionEffect).where(ActionEffect.key == key))).scalar_one()
        row.status, row.result, row.finished_at = "done", result, utcnow()
        # The result is recorded: what the effect kept elsewhere until now goes with this commit.
        for settle in once_durable:
            await settle(session)
        action, project = await _load(session, action_id)
        await persist_event(
            session,
            EventType.ACTION_EFFECT_DONE,
            project_id=project.id,
            work_item_id=action.work_item_id,
            project_slug=project.slug,
            subject=action_id,
            key=key,
            effect=row.effect,
        )
    return {"key": key, "replayed": False, "result": result}


async def _note_failure(key: str, error: str) -> None:
    async with db() as session:
        row = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == key))
        ).scalar_one_or_none()
        if row is not None:
            row.error = error[:2000]


def _doubts(row: ActionEffect | None) -> list[dict[str, Any]]:
    """What this effect's earlier attempts sent and never saw answered, one entry per delivery."""
    if row is None or row.status != IN_DOUBT:
        return []
    return [dict(sent) for sent in (row.result or {}).get("in_doubt") or []]


def _may_be_sent_again(row: ActionEffect | None) -> bool:
    """The effect said its doubt is not final: the same key may be delivered again."""
    return row is not None and bool((row.result or {}).get("again"))


async def _note_doubt(
    key: str, doubt: EffectInDoubt, before: list[dict[str, Any]], once_durable: list[Any]
) -> None:
    """Record that the effect is in doubt, with every delivery that was sent and never answered:
    what a person is shown (ADR 0149 §5). What the effect kept elsewhere goes with this record."""
    async with db() as session:
        row = (await session.execute(select(ActionEffect).where(ActionEffect.key == key))).scalar_one()
        row.status, row.error = IN_DOUBT, str(doubt)[:2000]
        row.result = {"in_doubt": [*before, dict(doubt.sent)], "again": doubt.again}
        for settle in once_durable:
            await settle(session)


async def _in_doubt(session: Any, action_id: str) -> ActionEffect | None:
    """The effect of an action that is in doubt, if one is."""
    found: ActionEffect | None = (
        (
            await session.execute(
                select(ActionEffect)
                .where(ActionEffect.action_id == action_id, ActionEffect.status == IN_DOUBT)
                .order_by(ActionEffect.position)
            )
        )
        .scalars()
        .first()
    )
    return found


@activity.defn(name="compensate_effect")
async def compensate_effect(payload: dict[str, Any]) -> dict[str, Any]:
    """Undo a done effect, with what the action declared for it. Never raises: what it could not
    undo, it says, and the workflow compensates the following ones anyway."""
    action_id, position = str(payload["action_id"]), int(payload["position"])
    key = f"{action_id}:{position}"
    async with db() as session:
        action, project = await _load(session, action_id)
        row = (
            await session.execute(select(ActionEffect).where(ActionEffect.key == key))
        ).scalar_one_or_none()
        if await _in_doubt(session, action_id) is not None:
            # One of the action's effects is in doubt: undoing what came before it would act on a
            # state nobody knows (ADR 0149 §5). Nothing is compensated automatically, whoever asks.
            return {"key": key, "undone": False, "reason": "the action is in doubt: nothing is compensated"}
        if row is None or row.status != "done":
            return {"key": key, "undone": False, "reason": "not done"}
        compensation = (action.effects or [])[position].get("compensate")
        if not compensation:
            row.status = "compensation_failed"
            row.error = "no compensation declared"
            return {"key": key, "undone": False, "reason": "no compensation declared"}
        context = {
            "params": action.params or {},
            "effects": await _done_effects(session, action_id, position),
            "result": row.result or {},
        }
        # The effect's key, and another call: through the connector its effect went through, a
        # compensation is a write of its own and must not meet its effect's id (ADR 0149 §5).
        undoing = EffectContext(session, action, project, key=f"{key}:compensation")
        try:
            params = render(compensation.get("with") or {}, context)
            await effect(str(compensation["effect"]))(undoing, params)
        except Exception as failure:
            row.status, row.error = "compensation_failed", f"{type(failure).__name__}: {failure}"[:2000]
            return {"key": key, "undone": False, "reason": row.error}
        row.status = "compensated"
        for settle in undoing.once_durable:
            await settle(session)
        await persist_event(
            session,
            EventType.ACTION_EFFECT_COMPENSATED,
            project_id=project.id,
            work_item_id=action.work_item_id,
            project_slug=project.slug,
            subject=action_id,
            key=key,
        )
        return {"key": key, "undone": True}


@activity.defn(name="close_action")
async def close_action(payload: dict[str, Any]) -> dict[str, Any]:
    action_id, status = str(payload["action_id"]), str(payload["status"])
    ticket: str | None = None
    async with db() as session:
        action, project = await _load(session, action_id)
        ticket = await _waiting_interpreter(session, action, project)
        doubted = await _in_doubt(session, action_id) if status != ActionStatus.SUCCEEDED.value else None
        if doubted is not None:
            # An action one of whose effects is in doubt did not fail: it is closed in doubt, whatever
            # the workflow that plays it asks for (one that predates the status asks for `failed`).
            status = IN_DOUBT
        action.status = status
        action.error = payload.get("error")
        # Merged: an effect may have recorded what it awaits there, an evidence what it saw (S20-08).
        action.result = {**(action.result or {}), "compensations": payload.get("compensations") or []}
        if doubted is not None:
            action.error = action.error or doubted.error
            sent = list((doubted.result or {}).get("in_doubt") or [])
            action.result = {**action.result, "in_doubt": {"position": doubted.position, "sent": sent}}
        action.finished_at = utcnow()
        await persist_event(
            session,
            # No event type of its own for an action in doubt: the terminal event that says "it did
            # not succeed" carries the status it ended with.
            EventType.ACTION_SUCCEEDED if status == ActionStatus.SUCCEEDED.value else EventType.ACTION_FAILED,
            project_id=project.id,
            work_item_id=action.work_item_id,
            project_slug=project.slug,
            subject=action_id,
            status=status,
            error=action.error,
        )
    # After the commit: the work item that awaits it will re-read an already written state.
    if ticket is not None:
        await _notify(ticket, action_id, status)
    return {"action_id": action_id, "status": status}


async def _waiting_interpreter(session: Any, action: Action, project: Project) -> str | None:
    """The interpreter whose TRANSITION proposed the action (S20-05), or nothing: the one the
    proposal recorded (it is the one waiting), else the work item's."""
    from choregos_api.temporal import interpreter_id

    if action.origin != ActionOrigin.TRANSITION.value or action.work_item_id is None:
        return None
    recorded = (action.proposed_by or {}).get("workflow_id")
    if recorded:
        return str(recorded)
    item = await session.get(WorkItem, action.work_item_id)
    return interpreter_id(project.slug, item.tracker_key) if item is not None else None


async def _notify(workflow_id: str, action_id: str, status: str) -> None:
    """`action_settled`: the work item does not wait for its next re-read to know. A work item
    stopped in the meantime no longer listens; that is not a failure of the action."""
    try:
        await (
            activity.client()
            .get_workflow_handle(workflow_id)
            .signal("action_settled", {"action_id": action_id, "status": status})
        )
    except Exception as error:
        logger.warning("work item not notified", workflow_id=workflow_id, action=action_id, error=str(error))


@activity.defn(name="propose_transition_action")
async def propose_transition_action(payload: dict[str, Any]) -> dict[str, Any]:
    """The action of a system transition (S20-05), proposed once per attempt. Born approved (every
    operation is allowed to this project), it leaves at once in its `ActionWorkflow`.

    A final refusal (a missing variable, an unknown effect, a forbidden operation) comes back in
    `refused`: the transition fails, named; it is not a failure to retry."""
    from choregos_api.errors import ApiError
    from choregos_api.services.actions import propose_for_transition

    async with db() as session:
        project = await session.get(Project, payload["project_id"])
        item = await session.get(WorkItem, payload["work_item_id"])
        if project is None or item is None:
            return {"refusal": "work item or project not found"}
        try:
            action = await propose_for_transition(
                session,
                project,
                item,
                dict(payload["action"]),
                transition=str(payload["transition"]),
                attempt=int(payload["attempt"]),
                workflow_id=str(payload["workflow_id"]),
            )
        except EffectRefused as refused:
            return {"refusal": str(refused)}
        except ApiError as refused:
            return {"refusal": str(refused.detail or refused.title)}
        identifier, status = action.id, action.status
    if status == ActionStatus.APPROVED.value:
        await _start(identifier)
    return {"action_id": identifier, "status": status}


async def _start(action_id: str) -> None:
    """`action-<id>`, once: a replayed activity finds the workflow already started."""
    from choregos_api.temporal import action_id as identifier
    from temporalio.exceptions import WorkflowAlreadyStartedError

    with contextlib.suppress(WorkflowAlreadyStartedError):
        await activity.client().start_workflow(
            "ActionWorkflow",
            {"action_id": action_id},
            id=identifier(action_id),
            task_queue=activity.info().task_queue,
        )


@activity.defn(name="mark_awaiting_evidence")
async def mark_awaiting_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """The action awaits the evidence of one of its effects (S20-08) (`awaiting_evidence`, with the
    effect and the deadline), or no longer awaits it. What is shown; the wait itself is the workflow's."""
    action_id = str(payload["action_id"])
    async with db() as session:
        action, _project = await _load(session, action_id)
        result = dict(action.result or {})
        # The workflow's keys are read under both spellings (S45-03).
        if read_key(payload, "waiting"):
            action.status = ActionStatus.AWAITING_EVIDENCE.value
            until = require_key(payload, "until")
            result["waiting"] = {"position": payload["position"], "until": until}
        else:
            action.status = ActionStatus.RUNNING.value
            # A row written before S45-39 holds `attente`: both are dropped.
            result.pop("waiting", None)
            result.pop("attente", None)  # lang: fr-ok — a row persisted before S45-39
            # Not `evidence`: an ontology action keeps its own file of that name in `result`.
            result.setdefault("evidence_received", []).append(dict(read_key(payload, "evidence") or {}))
        action.result = result
    return {"action_id": action_id, "status": action.status}


@activity.defn(name="action_state")
async def action_state(action_id: str) -> dict[str, Any]:
    """Re-read from the database: the net when an `action_settled` signal is lost."""
    async with db() as session:
        action = await session.get(Action, action_id)
        return {"status": action.status if action is not None else ActionStatus.FAILED.value}


# ─────────────── the French names, still registered (S34-02, ADR 0046) ───────────────
#
# A recorded history, and every workflow in flight when this version deploys, schedules these
# activities under their French names: the worker must keep answering to them, with the same
# implementation, or those workflows stop. New workflows schedule the English names (behind the
# `english-activity-names` patch). Remove an alias only when no history that uses it can replay.

ENGLISH_TO_FRENCH: dict[str, str] = {  # lang: fr-ok
    "load_action": "charger_l_action",
    "execute_effect": "executer_l_effet",
    "compensate_effect": "compenser_l_effet",
    "close_action": "cloturer_l_action",
    "propose_transition_action": "proposer_l_action_de_transition",
    "mark_awaiting_evidence": "marquer_l_attente",
    "action_state": "etat_de_l_action",
}


@activity.defn(name="charger_l_action")  # lang: fr-ok
async def charger_l_action(action_id: str) -> dict[str, Any]:  # lang: fr-ok
    return await load_action(action_id)


@activity.defn(name="executer_l_effet")  # lang: fr-ok
async def executer_l_effet(payload: dict[str, Any]) -> dict[str, Any]:  # lang: fr-ok
    return await execute_effect(payload)


@activity.defn(name="compenser_l_effet")  # lang: fr-ok
async def compenser_l_effet(payload: dict[str, Any]) -> dict[str, Any]:  # lang: fr-ok
    return await compensate_effect(payload)


@activity.defn(name="cloturer_l_action")  # lang: fr-ok
async def cloturer_l_action(payload: dict[str, Any]) -> dict[str, Any]:  # lang: fr-ok
    return await close_action(payload)


@activity.defn(name="proposer_l_action_de_transition")  # lang: fr-ok
async def proposer_l_action_de_transition(payload: dict[str, Any]) -> dict[str, Any]:  # lang: fr-ok
    return await propose_transition_action(payload)


@activity.defn(name="marquer_l_attente")  # lang: fr-ok
async def marquer_l_attente(payload: dict[str, Any]) -> dict[str, Any]:  # lang: fr-ok
    return await mark_awaiting_evidence(payload)


@activity.defn(name="etat_de_l_action")  # lang: fr-ok
async def etat_de_l_action(action_id: str) -> dict[str, Any]:  # lang: fr-ok
    return await action_state(action_id)
