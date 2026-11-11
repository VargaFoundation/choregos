# SPDX-License-Identifier: Apache-2.0
"""An assistant's tasks: subscriptions, schedules per subscriber, `poll`, runs (S63-13, ADR 0137).

A task is a workflow of the plugin run in the SUBSCRIBER's personal space: one work item per run
(`tracker: internal`), the subscriber as initiator, the task named in the work item's `documents.task`.
Nothing here changes a Temporal workflow: the scheduler is a periodic job of the API (`task_loop`) that
opens ordinary work items, the way a person's click does.

- **schedule**: the cron is read in the subscriber's IANA time zone (`choregos_core.cron`). The window is
  the last fire minute within `CATCH_UP` of the clock; it is CLAIMED by one conditional update of
  `last_window`, and the work item's key carries the window, so a second pass, a second replica or a
  retried run opens one work item (ADR 0008). A window missed for longer than `CATCH_UP` is skipped.
- **poll**: on its window, the declared read operation is called through the broker under the
  connector's own (service) identity; the run starts only when `when` holds, once per object
  (`idempotency` pointer): the keys seen are kept on the subscription and in the work item's key.
- **on demand**: `run_now`, with the caller as initiator; no delegation is needed (the session's identity).
- **delegation**: a task with `delegation: read_only` can be subscribed, but its scheduled and polled runs
  are REFUSED with a reason unless the subscription's standing delegation (S63-14,
  `assistant_delegations`) is valid at the run's clock. A polled read under it is made as the person (the
  need's identity, the delegation's offline token or connected account) and journaled with the
  delegation's id. Its on-demand run needs none.
- **budget**: a run is refused when the subscriber's month, then the installation's month, is spent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from choregos_contracts import Role
from choregos_core import parse_workflow, utcnow
from choregos_core.cron import next_fire
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AssistantInstallation,
    AssistantToolBinding,
    ConnectorOperation,
    Delegation,
    Organization,
    OrgConnector,
    User,
    WorkItem,
)
from ..db.models import TaskSubscription as Subscription
from ..errors import ApiError, conflict, not_found, unprocessable
from ..rbac import SYSTEM, Permission, Principal
from ..schemas import WorkItemCreate
from ..schemas.tasks import TaskSubscriptionPut
from . import assistant_delegations, assistants, plugin_installer
from .assistant_access import caps_of, holds, month_spend
from .assistant_conversations import audience_installation
from .broker import BrokerTool, call
from .definitions import active_workflow_named, publish_workflow
from .initiator import initiator
from .personal_spaces import personal_space
from .task_poll import due_objects
from .tickets import create_work_item, start_work_item

log = structlog.get_logger("choregos.assistant_tasks")

#: How far back a window is still run: a loop that was late by a few passes runs the window it missed, one
#: that was down for longer skips it rather than run it late (as the schedules of workflows, S38-11).
CATCH_UP = timedelta(minutes=60)
#: The keys a poll remembers per subscription: the newest ones.
MAX_SEEN = 2000
INTERVAL_S = 60.0

DELEGATION_REFUSAL = (
    "this task reads on the subscriber's behalf and has no standing delegation: agree to it "
    "(POST /me/delegations) for its scheduled runs, or run it on demand"
)


# -- the manifest ------------------------------------------------------------------------------------------


def task_declaration(document: dict[str, Any], task: str) -> dict[str, Any] | None:
    return next(
        (t for t in document.get("tasks") or [] if isinstance(t, dict) and t.get("name") == task), None
    )


def trigger_declaration(task: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next(
        (t for t in task.get("triggers") or [] if isinstance(t, dict) and t.get("kind") == kind), None
    )


async def _task_of(
    session: AsyncSession, principal: Principal, org: str, slug: str, task: str
) -> tuple[Organization, AssistantInstallation, dict[str, Any], dict[str, Any]]:
    organisation, row = await audience_installation(session, principal, org, slug)
    document = await assistants.manifest(session, row)
    declared = task_declaration(document, task) if document is not None else None
    if document is None or declared is None:
        raise not_found("Task", task)
    return organisation, row, document, declared


# -- the clock ---------------------------------------------------------------------------------------------


def latest_window(cron: str, timezone: str, now: datetime) -> datetime | None:
    """The last fire minute (UTC) of `cron`, read in `timezone`, within `CATCH_UP` before `now`."""
    window: datetime | None = None
    moment = next_fire(cron, now - CATCH_UP - timedelta(minutes=1), timezone)
    while moment <= now:
        window = moment
        moment = next_fire(cron, moment, timezone)
    return window


def _stamp(window: datetime) -> str:
    return window.astimezone(UTC).strftime("%Y%m%dT%H%MZ")


# -- subscriptions -----------------------------------------------------------------------------------------


async def find(session: AsyncSession, user_id: str, installation_id: str, task: str) -> Subscription | None:
    return (
        await session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.installation_id == installation_id,
                Subscription.task == task,
            )
        )
    ).scalar_one_or_none()


def _trigger_of(declared: dict[str, Any], body: TaskSubscriptionPut) -> dict[str, Any]:
    trigger = trigger_declaration(declared, body.kind)
    if trigger is None:
        kinds = sorted({str(t.get("kind")) for t in declared.get("triggers") or []})
        raise unprocessable(f"the task `{declared['name']}` has no `{body.kind}` trigger (it has: {kinds})")
    if body.kind == "on_demand":
        if body.cron or body.timezone:
            raise unprocessable("an on-demand trigger has no cron and no time zone")
        return {"kind": "on_demand"}
    own_hour = bool(trigger.get("user_timezone"))
    cron = body.cron or str(trigger["cron"])
    if cron != trigger["cron"] and not (body.kind == "schedule" and own_hour):
        raise unprocessable("only a schedule that is read in the person's time zone may change its cron")
    if body.timezone and not own_hour:
        raise unprocessable("this trigger is not read in the person's time zone (`user_timezone`)")
    timezone = body.timezone or "UTC"
    try:
        next_fire(cron, utcnow(), timezone)
    except ValueError as error:
        raise unprocessable(str(error)) from error
    return {"kind": body.kind, "cron": cron, "timezone": timezone}


async def put_subscription(
    session: AsyncSession,
    principal: Principal,
    org: str,
    slug: str,
    task: str,
    body: TaskSubscriptionPut,
    *,
    now: datetime | None = None,
) -> tuple[Subscription, dict[str, Any]]:
    """Subscribe, or change the trigger, the time zone or the switch of the caller's subscription. A new
    or changed schedule starts at the window after `now`: it never runs a window that passed before."""
    now = now or utcnow()
    organisation, row, _, declared = await _task_of(session, principal, org, slug, task)
    trigger = _trigger_of(declared, body)
    sub = await find(session, principal.user_id, row.id, task)
    if sub is None:
        sub = Subscription(
            org_id=organisation.id,
            installation_id=row.id,
            user_id=principal.user_id,
            task=task,
            trigger=trigger,
            enabled=body.enabled,
            seen_keys=[],
        )
        session.add(sub)
    elif sub.trigger != trigger:
        sub.trigger = trigger
        sub.seen_keys = []
    sub.enabled = body.enabled
    sub.last_refusal = None
    if trigger["kind"] != "on_demand":
        window = latest_window(trigger["cron"], trigger["timezone"], now)
        if window is not None and sub.last_window is None:
            sub.last_window = _stamp(window)
    await session.flush()
    return sub, declared


async def get_subscription(
    session: AsyncSession, principal: Principal, org: str, slug: str, task: str
) -> tuple[Subscription, dict[str, Any]]:
    _, row, _, declared = await _task_of(session, principal, org, slug, task)
    sub = await find(session, principal.user_id, row.id, task)
    if sub is None:
        raise not_found("Task subscription", task)
    return sub, declared


async def delete_subscription(
    session: AsyncSession, principal: Principal, org: str, slug: str, task: str
) -> None:
    sub, _ = await get_subscription(session, principal, org, slug, task)
    await session.delete(sub)
    await session.flush()


# -- starting a run ----------------------------------------------------------------------------------------


def _user_principal(user: User) -> Principal:
    return Principal(user_id=user.id, email=user.email, display_name=user.display_name or "")


async def _over_budget(
    session: AsyncSession, row: AssistantInstallation, document: dict[str, Any], user_id: str
) -> str | None:
    """The subscriber's month, then the installation's (ADR 0137 point 1)."""
    caps = caps_of(document, row)
    cap = caps.get("per_user_month_usd")
    if cap is not None and await month_spend(session, row.id, user_id) >= cap:
        return "the subscriber's monthly budget for this assistant is spent"
    cap = caps.get("per_installation_month_usd")
    if cap is not None and await month_spend(session, row.id) >= cap:
        return "the installation's monthly budget is spent"
    return None


async def _publish_task_workflow(
    session: AsyncSession, row: AssistantInstallation, declared: dict[str, Any], space_id: str, space: Any
) -> str:
    """The task's workflow, published in the personal space when it is not there at that text; its name."""
    text = await plugin_installer.plugin_text(session, row.org_id, row.plugin, str(declared["workflow"]))
    if text is None:
        raise conflict(f"the plugin `{row.plugin}` no longer ships `{declared['workflow']}`")
    workflow, report = parse_workflow(text, strict=False)
    if not report.valid:
        raise unprocessable(
            f"the workflow `{declared['workflow']}` of the task is invalid",
            [{"loc": [i.path or ""], "msg": i.message, "code": i.code} for i in report.errors],
        )
    name = workflow.metadata.name
    current = await active_workflow_named(session, space_id, name)
    if current is None or current.yaml != text:
        await publish_workflow(
            session,
            SYSTEM,
            space,
            text,
            source="plugin",
            origin=f"plugin:{row.plugin}/{declared['workflow']}"[:200],
        )
    return name


async def start_run(  # noqa: PLR0913 - one run: who, what, why, keyword-only
    session: AsyncSession,
    *,
    organisation: Organization,
    row: AssistantInstallation,
    user: User,
    declared: dict[str, Any],
    key_suffix: str,
    trigger: str,
    object_key: str | None = None,
    title_suffix: str = "",
    delegation_id: str | None = None,
) -> tuple[WorkItem, bool]:
    """One work item of the task in the subscriber's personal space; `(item, created)`. The key makes the
    run idempotent: the same window or the same object finds the work item it opened."""
    space = await personal_space(session, organisation, user)
    name = await _publish_task_workflow(session, row, declared, space.id, space)
    task = str(declared["name"])
    key = f"TASK-{task}-{user.id[:8]}-{key_suffix}"[:255]
    existing = (
        await session.execute(
            select(WorkItem).where(WorkItem.project_id == space.id, WorkItem.tracker_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    try:
        async with session.begin_nested():
            item = await create_work_item(
                session,
                _user_principal(user),
                space,
                WorkItemCreate(
                    title=f"{task}{title_suffix}"[:500],
                    workflow=name,
                    labels=[f"task:{row.slug}:{task}"],
                    start=False,
                ),
                key=key,
                initiated_by=initiator("person", user.email),
            )
            item.documents = {
                **(item.documents or {}),
                "task": {
                    "installation": row.slug,
                    "installation_id": row.id,
                    "task": task,
                    "trigger": trigger,
                    "object": object_key,
                    "delegation_id": delegation_id,
                    "deliver": list(declared.get("deliver") or ["inbox"]),
                },
            }
            await session.flush()
    except IntegrityError:
        raced = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == space.id, WorkItem.tracker_key == key)
            )
        ).scalar_one_or_none()
        if raced is None:
            raise
        return raced, False
    await start_work_item(space, item)
    return item, True


async def run_now(
    session: AsyncSession,
    principal: Principal,
    org: str,
    slug: str,
    task: str,
    *,
    now: datetime | None = None,
) -> tuple[WorkItem, str]:
    """`POST …/tasks/{task}/run`: the caller starts the task, under the session's identity, with no
    delegation. Every click is a run of its own. Returns the work item and the project slug."""
    now = now or utcnow()
    organisation, row, document, declared = await _task_of(session, principal, org, slug, task)
    if trigger_declaration(declared, "on_demand") is None:
        raise conflict(f"the task `{task}` has no `on_demand` trigger")
    status, _ = await assistants.refresh(session, row)
    if status != assistants.READY:
        raise conflict(f"`{slug}` is {status}: its tasks cannot run until its readiness is complete")
    refused = await _over_budget(session, row, document, principal.user_id)
    if refused:
        raise conflict(refused)
    user = await session.get(User, principal.user_id)
    if user is None:
        raise not_found("User", principal.user_id)
    item, _ = await start_run(
        session,
        organisation=organisation,
        row=row,
        user=user,
        declared=declared,
        key_suffix=f"manual-{_stamp(now)}-{utcnow().strftime('%S%f')}",
        trigger="on_demand",
        title_suffix=f" — {now:%Y-%m-%d %H:%M} UTC",
    )
    sub = await find(session, user.id, row.id, task)
    if sub is not None:
        sub.last_run_at = now
    space = await personal_space(session, organisation, user)
    return item, space.slug


# -- the poll ----------------------------------------------------------------------------------------------


async def read_operation(
    session: AsyncSession,
    row: AssistantInstallation,
    operation: str,
    arguments: dict[str, Any],
    *,
    delegation: Delegation | None = None,
    now: datetime | None = None,
) -> Any:
    """The structured result of a read operation `<need>__<operation>` the installation's need is bound to,
    called through the broker under the connector's own identity (`service`), or, under a standing
    delegation (S63-14), as the person the need's identity says. A delegated read is judged first
    (`assistant_delegations.guard_read`: a write is refused, `DelegationRefused`) and journaled with the
    delegation's id."""
    need, _, name = operation.partition("__")
    binding = (
        await session.execute(
            select(AssistantToolBinding).where(
                AssistantToolBinding.installation_id == row.id,
                AssistantToolBinding.need == need,
                AssistantToolBinding.enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    connector = await session.get(OrgConnector, binding.connector_id) if binding is not None else None
    op = (
        (
            await session.execute(
                select(ConnectorOperation).where(
                    ConnectorOperation.connector_id == connector.id,
                    ConnectorOperation.name == name,
                    ConnectorOperation.policy == "allowed",
                )
            )
        ).scalar_one_or_none()
        if connector is not None
        else None
    )
    if connector is None or op is None or binding is None:
        raise ValueError(f"the need `{need}` is not bound to a connector that serves `{name}`")
    acting = None
    if delegation is not None:
        acting = await assistant_delegations.guard_read(
            session, delegation, row, need=need, access=op.access, binding=binding, now=now
        )
    elif op.access != "read":
        raise ValueError(f"the need `{need}` is not bound to a connector that serves the read `{name}`")
    status, result = await call(
        session, BrokerTool(name=operation, connector=connector, operation=op), arguments, acting=acting
    )
    if delegation is not None and status == 200:
        await assistant_delegations.journal(
            session, delegation, need=need, operation=operation, result=result, now=now
        )
    if status != 200:
        raise ValueError(f"`{operation}` answered {status}: {str(result.get('error') or result)[:200]}")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            try:
                return json.loads(str(part.get("text")))
            except ValueError:
                return None
    return None


async def _poll(  # noqa: PLR0913 - one poll: who, what, when, under which delegation
    session: AsyncSession,
    sub: Subscription,
    organisation: Organization,
    row: AssistantInstallation,
    user: User,
    declared: dict[str, Any],
    window: datetime,
    *,
    delegation: Delegation | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Call the poll's operation, test `when`, start one run per object not seen; the keys started."""
    poll = trigger_declaration(declared, "poll") or {}
    try:
        result = await read_operation(
            session,
            row,
            str(poll["operation"]),
            dict(poll.get("arguments") or {}),
            delegation=delegation,
            now=now,
        )
    except ValueError as failure:
        sub.last_refusal = str(failure)[:500]
        return []
    due = due_objects(dict(poll.get("when") or {}), poll.get("idempotency"), result)
    seen = list(sub.seen_keys or [])
    started: list[str] = []
    for object_key in due:
        if object_key in seen:
            continue
        digest = object_key.removeprefix("sha256:")[:24]
        suffix = "obj-" + "".join(c if c.isalnum() else "-" for c in digest)[:48]
        await start_run(
            session,
            organisation=organisation,
            row=row,
            user=user,
            declared=declared,
            key_suffix=suffix,
            trigger="poll",
            delegation_id=delegation.id if delegation is not None else None,
            object_key=object_key,
            title_suffix=f" — {object_key}"[:120],
        )
        seen.append(object_key)
        started.append(object_key)
    sub.seen_keys = seen[-MAX_SEEN:]
    sub.last_refusal = None
    return started


# -- the scheduler -----------------------------------------------------------------------------------------


async def _claim(session: AsyncSession, sub: Subscription, window: datetime) -> bool:
    """Take the window for this pass: one conditional update, so two passes never both run it."""
    stamp = _stamp(window)
    claimed = await session.execute(
        update(Subscription)
        .where(
            Subscription.id == sub.id,
            (Subscription.last_window.is_(None)) | (Subscription.last_window != stamp),
        )
        .values(last_window=stamp)
        .execution_options(synchronize_session=False)
    )
    await session.refresh(sub)
    return bool(claimed.rowcount)  # type: ignore[attr-defined]


async def _run_one(session: AsyncSession, sub: Subscription, now: datetime) -> str | None:
    """One subscription's pass; returns what it started (`schedule:<window>` or `poll:<key>`), or nothing."""
    kind = str(sub.trigger.get("kind"))
    if kind not in ("schedule", "poll"):
        return None
    window = latest_window(str(sub.trigger["cron"]), str(sub.trigger.get("timezone") or "UTC"), now)
    if window is None or sub.last_window == _stamp(window):
        return None
    organisation = await session.get(Organization, sub.org_id)
    row = await session.get(AssistantInstallation, sub.installation_id)
    user = await session.get(User, sub.user_id)
    if organisation is None or row is None or user is None:
        return None
    document = await assistants.manifest(session, row)
    declared = task_declaration(document, sub.task) if document is not None else None
    refusal: str | None = None
    delegation: Delegation | None = None
    delegation_refusal: str | None = None
    if declared is not None and declared.get("delegation") == "read_only":
        # Judged first, whatever else refuses the run: a delegation that does not hold is marked at once.
        delegation = await session.get(Delegation, sub.delegation_id) if sub.delegation_id else None
        delegation_refusal = (
            DELEGATION_REFUSAL
            if delegation is None
            else await assistant_delegations.check(session, delegation, now=now)
        )
    if document is None or declared is None:
        refusal = "the task is no longer declared by the assistant"
    elif row.status != assistants.READY:
        refusal = f"the installation is {row.status}"
    elif not holds(
        Principal(
            user_id=user.id,
            email=user.email,
            org_roles={organisation.slug: Role.VIEWER},
            groups=list(user.groups or []),
        ),
        Permission.ASSISTANT_USE,
        organisation.slug,
        row,
    ):
        refusal = "the subscriber is no longer in the assistant's audience"
    elif delegation_refusal is not None:
        refusal = delegation_refusal
    if refusal is None and document is not None and declared is not None:
        refusal = await _over_budget(session, row, document, user.id)
    if refusal is not None or declared is None:
        if await _claim(session, sub, window):
            sub.last_refusal = refusal
            log.info("task.refused", task=sub.task, reason=refusal)
        return None
    if not await _claim(session, sub, window):
        return None
    if kind == "poll":
        started = await _poll(
            session, sub, organisation, row, user, declared, window, delegation=delegation, now=now
        )
        if started:
            sub.last_run_at = now
        return f"poll:{','.join(started)}" if started else None
    await start_run(
        session,
        organisation=organisation,
        row=row,
        user=user,
        declared=declared,
        key_suffix=_stamp(window),
        trigger="schedule",
        title_suffix=f" — {window:%Y-%m-%d %H:%M} UTC",
        delegation_id=delegation.id if delegation is not None else None,
    )
    sub.last_run_at = now
    sub.last_refusal = None
    return f"schedule:{_stamp(window)}"


async def run_due(session: AsyncSession, now: datetime | None = None) -> dict[str, str]:
    """One scheduler pass over every enabled subscription (the platform scope): `{subscription id: what it
    started}`. A subscription that fails is logged and left for the next pass, never the others."""
    now = now or utcnow()
    started: dict[str, str] = {}
    subs = (
        (
            await session.execute(
                select(Subscription).where(Subscription.enabled.is_(True)).order_by(Subscription.id)
            )
        )
        .scalars()
        .all()
    )
    for sub in subs:
        try:
            async with session.begin_nested():
                what = await _run_one(session, sub, now)
        except (ApiError, ValueError) as failure:
            log.warning("task.pass_failed", task=sub.task, error=str(failure)[:200])
            continue
        if what:
            started[sub.id] = what
    return started


async def task_loop(interval_s: float = INTERVAL_S) -> None:
    """Background task of the API: the scheduler pass, then the delivery pass, each minute. A failed pass
    does not stop the API."""
    import asyncio

    from ..db.session import session_scope
    from .task_delivery import deliver_pending

    while True:
        try:
            async with session_scope(orgs="*") as session:
                await run_due(session)
            async with session_scope(orgs="*") as session:
                await deliver_pending(session)
            async with session_scope(orgs="*") as session:
                await assistant_delegations.remind_expiring(session)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("task.loop_failed", error=str(error)[:200])
        await asyncio.sleep(interval_s)
