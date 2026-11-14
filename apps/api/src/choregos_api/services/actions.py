# SPDX-License-Identifier: Apache-2.0
"""Governed actions (ADR 0035): propose them, decide them, never execute them here.

An approved action leaves for Temporal (`ActionWorkflow`, `action-<id>`): the HTTP response leaves
the API before the slightest effect. The decision is guarded four ways:
- a recent HUMAN SESSION (`step_up_required` beyond the freshness asked for): an API token or an
  MCP client never decides (ADR 0030);
- the RANK: at least that of a requested approver;
- the SEPARATION OF DUTIES: whoever proposes does not decide, neither the human who proposed it nor
  the owner of the agent that proposed it;
- CONCURRENCY: the row is re-read locked, and the move to `approved` is a compare-and-set; of two
  simultaneous approvals, only one starts the action.
"""

from __future__ import annotations

import uuid
from typing import Any

from choregos_contracts import ActionOrigin, ActionStatus, EventType
from choregos_core import utcnow
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Action, ActionEffect, Agent, Project, User, WorkItem
from ..errors import ApiError, conflict, forbidden, unprocessable
from ..freshness import authentication_age, require_fresh_authentication
from ..schemas.actions import ActionCreate, ActionDto, ActionEffectDto, ActionSettlementBody, ApprovalSpec
from .events import persist_event
from .human_groups import groups_for_org

#: From lowest to highest: an approver must have at least the rank asked for.
RANKS = {"viewer": 0, "developer": 1, "release_captain": 2, "project_owner": 3, "org_admin": 4}


def rank(principal: Any, org: str, project: str) -> int:
    roles = [principal.role_for(org, project), principal.org_roles.get(org)]
    return max((RANKS.get(str(r), -1) for r in roles if r is not None), default=-1)


#: The namespace of transition action ids: a work item, a transition and an attempt always give the
#: same one, so a replayed activity finds the action instead of creating another.
_TRANSITION_NAMESPACE = uuid.UUID("6f1c9a52-3b0e-4d7a-9e21-5c8b0d4f7a13")


async def action_policy(
    session: AsyncSession, project: Project, effects: list[dict[str, Any]], params: dict[str, Any]
) -> tuple[str, list[str]]:
    """The strictest of the policies that its effects, and their compensations, demand for THIS
    project (ADR 0034), and what imposes it. `forbidden` refuses the action; `approval` asks for a
    person; `allowed`: the organisation's policy is enough."""
    from ..effects import EffectRefused, effect_policy, render
    from .connectors import RANK, policy_for_project

    worst, reasons = "allowed", []
    for spec in [*effects, *(dict(e["compensate"]) for e in effects if e.get("compensate"))]:
        name = str(spec.get("effect"))
        if name == "connector.call":
            try:
                call = render(dict(spec.get("with") or {}), {"params": params, "effects": []})
                connector, operation = str(call.get("connector", "")), str(call.get("operation", ""))
            except EffectRefused:
                # The connector is deduced from a previous effect: we do not know yet which one,
                # a person decides.
                connector, operation, policy = "?", "?", "approval"
            else:
                policy = await policy_for_project(session, project, connector, operation)
            subject = f"{connector}/{operation}"
        else:
            policy, subject = effect_policy(name), name
        if policy != "allowed":
            reasons.append(f"{subject}: {policy}")
        if RANK[policy] > RANK[worst]:
            worst = policy
    return worst, reasons


async def propose(
    session: AsyncSession,
    project: Project,
    body: ActionCreate,
    *,
    origin: str,
    proposed_by: dict[str, Any],
    principal: Any,
    run_id: str | None = None,
    action_id: str | None = None,
) -> Action:
    """Record an action awaiting a decision; its effects must be declared, and none may call what
    this project forbids itself: a doomed action is not presented."""
    from ..effects import declared_effects

    known = declared_effects()
    named = {e.effect for e in body.effects} | {
        str(e.compensate.get("effect")) for e in body.effects if e.compensate is not None
    }
    unknown = sorted(named - known)
    if unknown:
        raise unprocessable(f"unknown effects: {', '.join(unknown)} (known: {', '.join(sorted(known))})")
    effects = [e.model_dump(by_alias=True, exclude_none=True) for e in body.effects]
    worst, reasons = await action_policy(session, project, effects, body.params)
    if worst == "forbidden":
        forbidden_ones = [m for m in reasons if m.endswith("forbidden")]
        raise unprocessable(f"action forbidden in this project: {'; '.join(forbidden_ones)}")
    action = Action(
        **({"id": action_id} if action_id else {}),
        org_id=project.org_id,
        project_id=project.id,
        work_item_id=body.work_item_id,
        run_id=run_id,
        origin=origin,
        kind=body.kind,
        title=body.title,
        justification=body.justification,
        params=body.params,
        effects=effects,
        proposed_by=proposed_by,
        approval=body.approval.model_dump(mode="json"),
        decisions=[],
        status=ActionStatus.PENDING_APPROVAL.value,
    )
    session.add(action)
    await session.flush()
    await record(
        session,
        principal,
        "action.propose",
        org_id=project.org_id,
        target_type="action",
        target_id=action.id,
        kind=body.kind,
        origin=origin,
    )
    await persist_event(
        session,
        EventType.ACTION_PROPOSED,
        project_id=project.id,
        work_item_id=body.work_item_id,
        project_slug=project.slug,
        subject=action.id,
        kind=body.kind,
        title=body.title,
        proposed_by=proposed_by,
    )
    return action


async def propose_for_transition(
    session: AsyncSession,
    project: Project,
    item: WorkItem,
    spec: dict[str, Any],
    *,
    transition: str,
    attempt: int,
    workflow_id: str,
) -> Action:
    """The action a SYSTEM transition proposes (S20-05), once per attempt.

    Its title, justification and parameters are rendered with the work item's fields. When each of
    the operations it calls is allowed to this project and the workflow demands no validation, the
    policy decides: it is born approved. Otherwise it waits for a person, like any other: the
    workflow can add a validation, never remove one.
    A missing variable, an unknown effect, a forbidden operation: `EffectRefused` or 422.
    """
    from choregos_contracts import TransitionAction

    from ..effects import render
    from ..rbac import SYSTEM

    identifier = str(uuid.uuid5(_TRANSITION_NAMESPACE, f"{item.id}:{transition}:{attempt}"))
    existing = await session.get(Action, identifier)
    if existing is not None:
        return existing
    action = TransitionAction.model_validate(spec)
    context = {
        "fields": dict(item.fields or {}),
        "work_item": {"id": item.id, "key": item.tracker_key, "title": item.title},
    }
    body = ActionCreate(
        kind=action.kind,
        title=str(render(action.title, context))[:300],
        justification=str(render(action.justification, context)) if action.justification else None,
        params=dict(render(action.params, context)),
        effects=[e.model_dump(by_alias=True, exclude_none=True) for e in action.effects],
        approval=ApprovalSpec.model_validate(action.approval.model_dump())
        if action.approval
        else ApprovalSpec(),
        work_item_id=item.id,
    )
    proposed = await propose(
        session,
        project,
        body,
        origin=ActionOrigin.TRANSITION.value,
        proposed_by={
            "kind": "system",
            "id": "system",
            "via": "transition",
            "transition": transition,
            "workflow_id": workflow_id,
        },
        principal=SYSTEM,
        action_id=identifier,
    )
    worst, _reasons = await action_policy(session, project, proposed.effects, proposed.params or {})
    if action.approval is None and worst == "allowed":
        # Started by the activity that proposed it, once the row is written (orchestrator).
        await approved_by_policy(session, project, proposed)
    return proposed


def start_once_committed(session: AsyncSession, action: Action, *, again: bool = False) -> None:
    """The approved action leaves for Temporal once its approval is COMMITTED, not before (ADR 0085).

    Started inside the transaction, a workflow outlived a commit that failed: it ran the effects of
    an approval nobody kept. The `approved` row is the outbox: if the start after the commit is lost
    (Temporal down, the process killed), `action_relay` finds the row and starts it again — under
    the same id, `action-<id>`, so never twice."""
    from ..db.session import after_commit
    from ..temporal import action_id as workflow_id_of
    from ..temporal import get_temporal

    action_id = action.id
    action.temporal_wf_id = workflow_id_of(action_id)

    async def start() -> None:
        gateway = get_temporal()
        # A settled action's first run completed (S65-26): only a restart reuses its id.
        begin = gateway.restart_action if again else gateway.start_action
        await begin(workflow_id_of(action_id), {"action_id": action_id})

    after_commit(session, start)


async def approved_by_policy(session: AsyncSession, project: Project, action: Action) -> None:
    """Each operation it calls is allowed to this project: the policy decides, the action is born
    approved, journalled, compensable, played by the `ActionWorkflow` like any other. Whoever starts
    it (a transition's activity, the broker) does so once the row is VISIBLE."""
    from ..temporal import action_id as temporal_workflow_id

    action.status = ActionStatus.APPROVED.value
    action.temporal_wf_id = temporal_workflow_id(action.id)
    action.decisions = [
        {
            "by": "policy",
            "decision": "approve",
            "reason": "every operation it calls is allowed for this project",
            "at": utcnow().isoformat(),
            "auth_age_seconds": None,
        }
    ]
    await persist_event(
        session,
        EventType.ACTION_DECIDED,
        project_id=project.id,
        work_item_id=action.work_item_id,
        project_slug=project.slug,
        subject=action.id,
        decision="approve",
        by="policy",
        status=action.status,
    )


async def _notify_work_item(session: AsyncSession, project: Project, action: Action) -> None:
    """A transition action settled outside its workflow (rejected here) wakes the work item that
    waits for it (`action_settled`); one that succeeds or fails does so from the `ActionWorkflow`."""
    if action.origin != ActionOrigin.TRANSITION.value or action.work_item_id is None:
        return
    from ..temporal import get_temporal, interpreter_id

    target = (action.proposed_by or {}).get("workflow_id")
    if not target:
        item = await session.get(WorkItem, action.work_item_id)
        if item is None:
            return
        target = interpreter_id(project.slug, item.tracker_key)
    await get_temporal().signal(
        str(target), "action_settled", {"action_id": action.id, "status": action.status}
    )


async def signal_evidence(action_id: str, position: int, ok: bool, detail: str) -> None:
    """The seam of an EVIDENCE (S20-08): an effect asked to wait for it; what
    establishes it, a collector report or an outside event, hands it to the action.
    Favourable, the action resumes; unfavourable, it fails and what it had done is compensated."""
    from ..temporal import action_id as workflow_id_of
    from ..temporal import get_temporal

    await get_temporal().signal(
        # `evidence`: the English name of the signal (S34-02); the workflow still accepts `preuve`.
        workflow_id_of(action_id),
        "evidence",
        {"position": position, "ok": ok, "detail": detail},
    )


async def _agent_owner(session: AsyncSession, proposed_by: dict[str, Any], org_id: str) -> str | None:
    if proposed_by.get("kind") != "agent":
        return None
    slug = str(proposed_by.get("id", "")).removeprefix("agent:")
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None or agent.owner_id is None:
        return None
    owner = await session.get(User, agent.owner_id)
    return owner.email.lower() if owner is not None else None


async def _one_of_its_approvers(
    session: AsyncSession,
    project: Project,
    org_slug: str,
    action: Action,
    principal: Any,
    gesture: str,
    verb: str,
) -> None:
    """The rank, the groups and the separation of duties of the action's rule: who may approve it, and
    who may settle it once it is in doubt (S65-26). `gesture` and `verb` name the refusal."""
    approval = action.approval or {}
    approvers = approval.get("approvers") or [{"role": "project_owner", "min": 1}]
    my_rank = rank(principal, org_slug, project.slug)
    high_enough = [a for a in approvers if my_rank >= RANKS.get(str(a.get("role") or "project_owner"), 3)]
    if not high_enough:
        raise forbidden(f"{gesture} this action needs an approver's rank")
    # A group named by the rule (ADR 0052) is a second condition, not an alternative: a project owner
    # outside `netops-approvers` does not approve a firewall rule. The groups are the identity
    # provider's, as of the decider's last sign-in through it.
    my_groups = groups_for_org(principal.groups, org_slug)
    # A rule written `{project}-maintainers` (a workflow's action, S59-11) names this project's group.
    from choregos_core.dsl.groups import group_for

    def group_of(rule: dict[str, Any]) -> str:
        return group_for(str(rule["group"]), project.slug)

    if not any(not a.get("group") or group_of(a) in my_groups for a in high_enough):
        groups = sorted({group_of(a) for a in high_enough if a.get("group")})
        raise forbidden(
            f"{gesture} this action needs membership of the group {' or '.join(groups)} "
            "(identity-provider groups, as of your last sign-in)"
        )
    if approval.get("separation_of_duties", True):
        proposed_by = action.proposed_by or {}
        me = principal.email.lower()
        if (proposed_by.get("kind") == "user" and str(proposed_by.get("id", "")).lower() == me) or (
            await _agent_owner(session, proposed_by, project.org_id)
        ) == me:
            raise unprocessable(
                f"separation of duties: whoever proposes (or owns the agent that proposes) does not {verb}"
            )


async def decide(
    session: AsyncSession,
    project: Project,
    org_slug: str,
    action_id: str,
    decision: str,
    reason: str | None,
    principal: Any,
) -> Action:
    if principal.kind != "user" or principal.authenticated_at is None:
        # An API token, an MCP client: they propose, they do not decide (ADR 0030).
        raise forbidden("a decision is made in a human session (decision_requires_session)")
    if decision == "reject" and not (reason or "").strip():
        raise unprocessable("a rejection must say why")
    action = (
        await session.execute(
            select(Action).where(Action.id == action_id, Action.project_id == project.id).with_for_update()
        )
    ).scalar_one_or_none()
    if action is None:
        raise ApiError(404, "Action not found", action_id)
    if action.status != ActionStatus.PENDING_APPROVAL.value:
        raise conflict(f"the action is already {action.status}")
    approval = action.approval or {}
    approvers = approval.get("approvers") or [{"role": "project_owner", "min": 1}]
    await _one_of_its_approvers(session, project, org_slug, action, principal, "deciding", "decide")
    if any(d.get("by", "").lower() == principal.email.lower() for d in action.decisions or []):
        raise conflict("you have already decided this action")
    # `step_up_minutes` absent (`None`): the rule demands no recent authentication, which an
    # extension's policy (the ontology) can say; an action proposed by the API always has one.
    freshness = approval.get("step_up_minutes", 10)
    age = (
        require_fresh_authentication(principal, int(freshness))
        if decision == "approve" and freshness is not None
        else authentication_age(principal)
    )
    entry = {
        "by": principal.email,
        "decision": decision,
        "reason": reason,
        "at": utcnow().isoformat(),
        "auth_age_seconds": age,
    }
    decisions = [*(action.decisions or []), entry]
    approved = {d["by"].lower() for d in decisions if d.get("decision") == "approve"}
    minimum = max(int(a.get("min", 1)) for a in approvers)
    new_status = (
        ActionStatus.REJECTED.value
        if decision == "reject"
        else ActionStatus.APPROVED.value
        if len(approved) >= minimum
        else ActionStatus.PENDING_APPROVAL.value
    )
    # Compare-and-set: the row only moves while it is still pending. On PostgreSQL the read's lock
    # already serialises; elsewhere this is what guards against the second decider.
    done = await session.execute(
        update(Action)
        .where(Action.id == action.id, Action.status == ActionStatus.PENDING_APPROVAL.value)
        .values(decisions=decisions, status=new_status, updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if done.rowcount != 1:  # type: ignore[attr-defined]
        raise conflict("another decision has just been made")
    await session.refresh(action)
    await record(
        session,
        principal,
        "action.decide",
        org_id=project.org_id,
        target_type="action",
        target_id=action.id,
        decision=decision,
        status=new_status,
        auth_age_seconds=age,
    )
    await persist_event(
        session,
        EventType.ACTION_DECIDED,
        project_id=project.id,
        work_item_id=action.work_item_id,
        project_slug=project.slug,
        subject=action.id,
        decision=decision,
        by=principal.email,
        status=new_status,
    )
    if new_status == ActionStatus.APPROVED.value:
        start_once_committed(session, action)
    elif new_status == ActionStatus.REJECTED.value:
        await _notify_work_item(session, project, action)
    return action


async def dto(session: AsyncSession, action: Action) -> ActionDto:
    journal = (
        await session.execute(
            select(ActionEffect).where(ActionEffect.action_id == action.id).order_by(ActionEffect.position)
        )
    ).scalars()
    result = ActionDto.model_validate(action)
    result.journal = [ActionEffectDto.model_validate(e) for e in journal]
    result.settlement = next((e.settlement for e in result.journal if e.settlement is not None), None)
    project = await session.get(Project, action.project_id)
    result.project_slug = project.slug if project is not None else None
    return result


#: The 409 codes of a settlement (`settleAction`).
NOT_IN_DOUBT = "action_not_in_doubt"
ALREADY_SETTLED = "action_already_settled"
#: The most a stated result may weigh, as JSON.
SETTLED_RESULT_MAX_BYTES = 16 * 1024


async def settle(
    session: AsyncSession,
    project: Project,
    org_slug: str,
    action_id: str,
    body: ActionSettlementBody,
    principal: Any,
) -> Action:
    """A person records what they found of an action in doubt (S65-26, ADR 0149 §5).

    The write was sent through an outpost and its answer never came: the platform does nothing more
    by itself. Whoever may approve the action may settle it, under the same four guards (a human
    session, an approver's rank and group, a recent authentication, the separation of duties): what
    they record decides whether the rest of an approved action runs, or what it did is undone.

    - `made`: the effect is `done`, with the result they state; the action is `approved` again and
      its `ActionWorkflow` runs once more (`start_once_committed`, a restart of the same id): the
      effects already done are
      answered from the journal, the ones after it are played, and a later failure compensates as
      for any action, the settled effect included.
    - `not_made`: the effect is `not_made`, an effect `execute_effect` never attempts again; the
      action is `approved` again and its workflow meets that refusal at once: what was done before
      it is compensated backwards and the action ends `failed`.

    The doubt's evidence moves with the settlement onto the effect's row. Nothing runs here: the
    workflow leaves after the commit, and the relay starts it if that start is lost."""
    import json

    from choregos_contracts import SettlementOutcome

    if principal.kind != "user" or principal.authenticated_at is None:
        raise forbidden("a settlement is made in a human session (decision_requires_session)")
    outcome = SettlementOutcome(body.outcome)
    if outcome is SettlementOutcome.NOT_MADE and body.result is not None:
        raise unprocessable(
            "a write that was not made returned nothing: `result` goes with `made` only",
            errors=[{"loc": ["body", "result"], "msg": "with `made` only", "code": "result_with_not_made"}],
        )
    if body.result is not None and len(json.dumps(body.result, default=str)) > SETTLED_RESULT_MAX_BYTES:
        raise unprocessable(
            f"the stated result weighs more than {SETTLED_RESULT_MAX_BYTES // 1024} KiB as JSON",
            errors=[{"loc": ["body", "result"], "msg": "too large", "code": "result_too_large"}],
        )
    action = (
        await session.execute(
            select(Action).where(Action.id == action_id, Action.project_id == project.id).with_for_update()
        )
    ).scalar_one_or_none()
    if action is None:
        raise ApiError(404, "Action not found", action_id)
    # Guarded before anything is said of the action's state: a second request is guarded as the first.
    await _one_of_its_approvers(session, project, org_slug, action, principal, "settling", "settle")
    freshness = (action.approval or {}).get("step_up_minutes", 10)
    age = (
        require_fresh_authentication(principal, int(freshness))
        if freshness is not None
        else authentication_age(principal)
    )
    effects = list(
        (
            await session.execute(
                select(ActionEffect)
                .where(ActionEffect.action_id == action.id)
                .order_by(ActionEffect.position)
                .with_for_update()
            )
        ).scalars()
    )
    settled = next((e for e in effects if e.settlement is not None), None)
    if settled is not None:
        # Settled already: the same outcome again changes nothing (a second click, a retried request);
        # the other one is a conflict a person resolves, never a second settlement.
        if (settled.settlement or {}).get("outcome") == outcome.value:
            return action
        raise conflict(
            f"this action was already settled `{(settled.settlement or {}).get('outcome')}` by "
            f"{(settled.settlement or {}).get('by')}",
            extra={"code": ALREADY_SETTLED},
        )
    doubted = next((e for e in effects if e.status == ActionStatus.IN_DOUBT.value), None)
    if action.status != ActionStatus.IN_DOUBT.value or doubted is None:
        raise conflict(f"the action is {action.status}, not in doubt", extra={"code": NOT_IN_DOUBT})
    now = utcnow()
    record_of_it = {
        "position": doubted.position,
        "outcome": outcome.value,
        "by": principal.email,
        "at": now.isoformat(),
        "note": body.note,
        "result": body.result if outcome is SettlementOutcome.MADE else None,
        "auth_age_seconds": age,
        "in_doubt": list((doubted.result or {}).get("in_doubt") or []),
    }
    doubted.settlement = record_of_it
    doubted.finished_at = now
    if outcome is SettlementOutcome.MADE:
        doubted.status, doubted.result, doubted.error = "done", dict(body.result or {}), None
    else:
        doubted.status, doubted.result = "not_made", None
        doubted.error = f"not made, as {principal.email} recorded: {body.note}"[:2000]
    # Compare-and-set, as a decision: of two settlements at once only one moves the action.
    moved = await session.execute(
        update(Action)
        .where(Action.id == action.id, Action.status == ActionStatus.IN_DOUBT.value)
        .values(status=ActionStatus.APPROVED.value, error=None, finished_at=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if moved.rowcount != 1:  # type: ignore[attr-defined]
        raise conflict("another settlement has just been made", extra={"code": ALREADY_SETTLED})
    await session.refresh(action)
    await record(
        session,
        principal,
        "action.settle",
        org_id=project.org_id,
        target_type="action",
        target_id=action.id,
        outcome=outcome.value,
        position=doubted.position,
        auth_age_seconds=age,
    )
    await persist_event(
        session,
        EventType.ACTION_SETTLED,
        project_id=project.id,
        work_item_id=action.work_item_id,
        project_slug=project.slug,
        subject=action.id,
        outcome=outcome.value,
        position=doubted.position,
        by=principal.email,
    )
    start_once_committed(session, action, again=True)
    return action
