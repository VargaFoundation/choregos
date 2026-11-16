# SPDX-License-Identifier: Apache-2.0
"""An event routed to a subscriber starts their task: the `trigger` kind (S63-21, ADR 0137 point 2).

An installation's task declares `{kind: trigger, subscriber: <JSON pointer>}`. A system posts its event (ADR
0113: JSON, signed, de-duplicated) to the task door; the pointer designates, in the payload, the email of
the person the event is for, and that person's ENABLED subscription to the task (`kind: trigger`) is the
subscriber. Routing never guesses:

- the pointer designates nothing, or something that is not text: refused (no subscriber named);
- it designates several different people (a `*` in the pointer, two addresses): refused, ambiguous: one event
  names one person, the sender posts one event per person;
- no enabled subscription to this task matches (case-insensitively), or two do (`a@x` and `A@x` are two
  accounts): refused, unmatched or ambiguous. The answer never says whether the person exists.

A refused event is journaled (`task_events`, readable by whoever manages the installation) and answers
its 4xx; it starts nothing and consumes nothing. An event the subscriber's gate refuses (the audience, the
delegation, the budget: `assistant_tasks.gate`, the rules of a schedule) is journaled the same way and its
reason is the subscription's `last_refusal`.

Once per event: the event id is the payload's value at the trigger's `idempotency` pointer, else the
`Idempotency-Key` header, else the body's digest; it is part of the work item's key, so a replay finds the
run it opened (`duplicate`) and starts nothing, whichever replica takes it. The journal holds what was
decided (the decision, the task, the event id), never the payload and never the address.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import AssistantInstallation, Organization, TaskEvent, User
from ..db.models import TaskSubscription as Subscription
from ..errors import ApiError, conflict
from . import assistants, task_delivery
from .assistant_tasks import gate, start_run, task_declaration, trigger_declaration
from .task_poll import resolve

SIGNATURE_PREFIX = "sha256="
#: What an event id keeps of a long value.
MAX_EVENT_ID = 200


def event_key(installation_id: str, generation: int, task: str) -> str:
    """The HMAC key of one task's door: derived from the installation's webhook key (so it rotates with it)
    and the task (so one task's key opens no other door); nothing more is stored."""
    root = task_delivery.webhook_secret(installation_id, generation)
    label = f"task-event:{task}"
    return "chtev_" + hmac.new(root.encode(), label.encode(), hashlib.sha256).hexdigest()


def verify_signature(key: str, body: bytes, header: str) -> bool:
    """`sha256=<hex>` of the body under `key`, compared in constant time."""
    if not header.lower().startswith(SIGNATURE_PREFIX):
        return False
    given = header[len(SIGNATURE_PREFIX) :].strip().lower()
    return hmac.compare_digest(given, hmac.new(key.encode(), body, hashlib.sha256).hexdigest())


def event_url(org: str, slug: str, task: str) -> str:
    api = get_settings().api_url.rstrip("/")
    return f"{api}/api/v1/webhooks/assistants/{org}/{slug}/tasks/{task}/events"


class EventRefusedError(ApiError):
    """The event does not name exactly one subscriber, or its subscriber's gate refuses it."""


def _journal(
    session: AsyncSession,
    row: AssistantInstallation,
    task: str,
    event_id: str,
    outcome: str,
    summary: str,
    *,
    user_id: str | None = None,
    work_item_key: str | None = None,
) -> None:
    session.add(
        TaskEvent(
            org_id=row.org_id,
            installation_id=row.id,
            task=task,
            user_id=user_id,
            event_id=event_id[:255],
            outcome=outcome,
            summary=summary[:300],
            work_item_key=work_item_key,
            received_at=utcnow(),
        )
    )


def journal_refusal(
    session: AsyncSession, row: AssistantInstallation, task: str, event_id: str, why: str
) -> None:
    _journal(session, row, task, event_id, "refused", why)


def event_id_of(trigger: dict[str, Any], payload: dict[str, Any], header: str, digest: str) -> str:
    """The payload's own identifier when the trigger names one, else the sender's `Idempotency-Key`, else
    the body's digest."""
    pointer = trigger.get("idempotency")
    if isinstance(pointer, str):
        values = [
            v for v in resolve(payload, pointer) if isinstance(v, str | int) and not isinstance(v, bool)
        ]
        if len(values) == 1 and str(values[0]).strip():
            return str(values[0]).strip()[:MAX_EVENT_ID]
    return (header.strip() or digest)[:MAX_EVENT_ID]


def _subscriber_emails(trigger: dict[str, Any], payload: dict[str, Any]) -> tuple[list[str], str | None]:
    """The distinct addresses the pointer designates, or why there is none to use."""
    try:
        found = resolve(payload, str(trigger["subscriber"]))
    except ValueError as failure:
        return [], str(failure)
    if any(not isinstance(v, str) for v in found):
        return [], "the subscriber pointer designates something that is not text"
    emails = sorted({str(v).strip().lower() for v in found if str(v).strip()})
    if not emails:
        return [], "the event names no subscriber at the trigger's pointer"
    return emails, None


async def _route(
    session: AsyncSession, row: AssistantInstallation, task: str, emails: list[str]
) -> tuple[Subscription | None, str | None]:
    """The one enabled `trigger` subscription of the task for the person the event names."""
    if len(emails) > 1:
        return None, "the event names several people: one event names one subscriber"
    found = (
        (
            await session.execute(
                select(Subscription)
                .join(User, User.id == Subscription.user_id)
                .where(
                    Subscription.installation_id == row.id,
                    Subscription.task == task,
                    Subscription.enabled.is_(True),
                    func.lower(User.email) == emails[0],
                )
                .order_by(Subscription.id)
            )
        )
        .scalars()
        .all()
    )
    matches = [sub for sub in found if (sub.trigger or {}).get("kind") == "trigger"]
    if not matches:
        return None, "no enabled subscription of this task matches the person the event names"
    if len(matches) > 1:
        return None, "ambiguous: several subscriptions match the person the event names"
    return matches[0], None


async def route_event(
    session: AsyncSession,
    organisation: Organization,
    row: AssistantInstallation,
    task: str,
    payload: dict[str, Any],
    event_id: str,
    *,
    now: datetime | None = None,
) -> tuple[str, str | None]:
    """Route one authentic event of `task`; `(outcome, work item key)`. Raises `EventRefusedError` (422 for
    a routing the event cannot get, 409 for a subscriber whose gate refuses) after journaling it: the
    caller commits the journal and answers the problem."""
    document = await assistants.manifest(session, row)
    declared = task_declaration(document, task) if document is not None else None
    trigger = trigger_declaration(declared, "trigger") if declared is not None else None
    if declared is None or trigger is None:
        raise conflict(f"the task `{task}` has no `trigger` trigger")  # unreachable: the door checked it
    now = now or utcnow()
    emails, why = _subscriber_emails(trigger, payload)
    subscription: Subscription | None = None
    if why is None:
        subscription, why = await _route(session, row, task, emails)
    if subscription is None:
        _journal(session, row, task, event_id, "refused", f"refused: {why}")
        raise EventRefusedError(422, "Unprocessable event", why or "no subscriber")
    judged = await gate(session, subscription, now)
    if judged is None or judged.declared is None:
        _journal(session, row, task, event_id, "refused", "refused: the subscription cannot run")
        raise EventRefusedError(409, "Conflict", "the subscription cannot run")
    if judged.refusal is not None:
        subscription.last_refusal = judged.refusal
        _journal(
            session,
            row,
            task,
            event_id,
            "refused",
            f"refused: {judged.refusal}",
            user_id=subscription.user_id,
        )
        raise EventRefusedError(409, "Conflict", judged.refusal)
    digest = hashlib.sha256(event_id.encode()).hexdigest()[:24]
    item, created = await start_run(
        session,
        organisation=organisation,
        row=row,
        user=judged.user,
        declared=judged.declared,
        key_suffix=f"evt-{digest}",
        trigger="trigger",
        object_key=event_id,
        title_suffix=f" — {event_id}"[:120],
        delegation_id=judged.delegation.id if judged.delegation is not None else None,
    )
    outcome = "started" if created else "duplicate"
    if created:
        subscription.last_run_at = now
        subscription.last_refusal = None
    _journal(
        session,
        row,
        task,
        event_id,
        outcome,
        "started the task" if created else "already started by this event",
        user_id=subscription.user_id,
        work_item_key=item.tracker_key,
    )
    return outcome, item.tracker_key
