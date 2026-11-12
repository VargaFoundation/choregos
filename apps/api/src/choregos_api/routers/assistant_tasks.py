# SPDX-License-Identifier: Apache-2.0
"""A person's subscriptions to an assistant's tasks, their on-demand runs and their inbox (S63-13, ADR 0137).

`GET, PUT, DELETE /me/assistants/{org}/{slug}/tasks/{task}` subscribe, set the trigger and time zone, and
unsubscribe; `POST …/run` starts the task now; `GET /me/inbox` lists what the caller's tasks delivered. A
person outside the assistant's audience is answered 404 everywhere.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..audit import record
from ..db.models import TaskEvent
from ..db.models import TaskSubscription as Row
from ..db.org_aliases import organisation_by_slug_or_alias
from ..deps import Db, Me, PlatformDb
from ..errors import PROBLEM_JSON, not_found, unauthorized
from ..schemas.assistants import SLUG
from ..schemas.tasks import (
    InboxList,
    TaskEventAck,
    TaskEventKey,
    TaskEventLine,
    TaskEventList,
    TaskRun,
    TaskSubscription,
    TaskSubscriptionPut,
    TaskTrigger,
    TeamsDelivery,
    TeamsDeliveryPut,
    WebhookSecret,
    WebhookSecretRequest,
)
from ..security import body_digest
from ..services import assistant_tasks, assistants, task_events
from ..services.organisations import organisation_by_slug
from ..services.task_delivery import (
    clear_teams_webhook,
    inbox,
    reveal_webhook_secret,
    set_teams_webhook,
    teams_state,
)

router = APIRouter(tags=["agents"])

#: The most an event reads: a ticket, a calendar entry, an alert, not a file.
MAX_EVENT_BODY = 256 * 1024

Slug = Annotated[str, Path(pattern=SLUG)]
TASKS = "/me/assistants/{org}/{slug}/tasks/{task}"


def _view(sub: Row, declared: dict[str, Any]) -> TaskSubscription:
    return TaskSubscription(
        task=sub.task,
        trigger=TaskTrigger(
            kind=sub.trigger["kind"],
            cron=sub.trigger.get("cron"),
            timezone=sub.trigger.get("timezone", "UTC"),
        ),
        delegation=declared.get("delegation", "none"),
        deliver=list(declared.get("deliver") or ["inbox"]),
        enabled=sub.enabled,
        delegation_id=sub.delegation_id,
        last_run_at=sub.last_run_at,
        last_refusal=sub.last_refusal,
        created_at=sub.created_at,
    )


@router.get(TASKS, response_model=TaskSubscription, operation_id="getMyTaskSubscription")
async def get_task(org: str, slug: Slug, task: str, session: Db, principal: Me) -> TaskSubscription:
    sub, declared = await assistant_tasks.get_subscription(session, principal, org, slug, task)
    return _view(sub, declared)


@router.put(TASKS, response_model=TaskSubscription, operation_id="putMyTaskSubscription")
async def put_task(
    org: str, slug: Slug, task: str, body: TaskSubscriptionPut, session: Db, principal: Me
) -> TaskSubscription:
    sub, declared = await assistant_tasks.put_subscription(session, principal, org, slug, task, body)
    return _view(sub, declared)


@router.delete(TASKS, status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteMyTaskSubscription")
async def delete_task(org: str, slug: Slug, task: str, session: Db, principal: Me) -> Response:
    await assistant_tasks.delete_subscription(session, principal, org, slug, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    TASKS + "/run",
    response_model=TaskRun,
    status_code=status.HTTP_201_CREATED,
    operation_id="runMyTask",
)
async def run_task(org: str, slug: Slug, task: str, session: Db, principal: Me) -> TaskRun:
    item, project = await assistant_tasks.run_now(session, principal, org, slug, task)
    return TaskRun(work_item_id=item.id, key=item.tracker_key, project=project)


@router.get("/me/inbox", response_model=InboxList, operation_id="listMyInbox")
async def list_inbox(
    session: Db, principal: Me, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> InboxList:
    return InboxList(items=await inbox(session, principal, limit=limit))


@router.post(
    "/orgs/{org}/assistants/{slug}/webhook-secret",
    response_model=WebhookSecret,
    operation_id="revealAssistantWebhookSecret",
)
async def webhook_secret(
    org: str, slug: Slug, session: Db, principal: Me, body: WebhookSecretRequest | None = None
) -> WebhookSecret:
    """The key a task webhook is signed with, shown once to whoever manages the installation
    (`assistant:manage`); `rotate` makes a new one."""
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    assistants.require_manager(principal, org, row)
    return await reveal_webhook_secret(
        session, principal, org, row, rotate=body.rotate if body is not None else False
    )


# -- the events routed to a `trigger` task (S63-21) ----------------------------------------------------------


async def _event_task(
    session: Any, org: str, slug: str, task: str
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """The organisation, the installation, the manifest's task and its `trigger` declaration."""
    organisation = await organisation_by_slug_or_alias(session, org)
    row = await assistants.installation(session, organisation.id, slug) if organisation is not None else None
    document = await assistants.manifest(session, row) if row is not None else None
    declared = assistant_tasks.task_declaration(document, task) if document is not None else None
    trigger = assistant_tasks.trigger_declaration(declared, "trigger") if declared is not None else None
    if organisation is None or row is None or declared is None or trigger is None:
        raise not_found("Task events", f"{org}/{slug}/{task}")
    return organisation, row, declared, trigger


@router.post(
    "/orgs/{org}/assistants/{slug}/tasks/{task}/event-key",
    response_model=TaskEventKey,
    operation_id="revealTaskEventKey",
)
async def event_key(org: str, slug: Slug, task: str, session: Db, principal: Me) -> TaskEventKey:
    """The URL and the HMAC key of a `trigger` task's event door, for whoever manages the installation
    (`assistant:manage`). The key is derived from the installation's webhook key: rotating that one changes
    it. Every showing is in the audit log (never the key)."""
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    assistants.require_manager(principal, org, row)
    await _event_task(session, org, slug, task)
    await record(
        session,
        principal,
        "assistant.task_event_key.shown",
        org_id=row.org_id,
        target_type="assistant",
        target_id=row.id,
        organisation=org,
        task=task,
    )
    generation = row.webhook_secret_generation
    return TaskEventKey(
        url=task_events.event_url(org, slug, task),
        secret=task_events.event_key(row.id, generation, task),
        generation=generation,
    )


@router.get(
    "/orgs/{org}/assistants/{slug}/task-events",
    response_model=TaskEventList,
    operation_id="listTaskEvents",
)
async def list_task_events(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> TaskEventList:
    """The journal of the events the installation's `trigger` tasks received, newest first, refused ones
    included: what was decided, never a payload."""
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    assistants.require_manager(principal, org, row)
    rows = (
        (
            await session.execute(
                select(TaskEvent)
                .where(TaskEvent.installation_id == row.id)
                .order_by(TaskEvent.received_at.desc(), TaskEvent.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return TaskEventList(
        items=[
            TaskEventLine(
                task=e.task,
                event_id=e.event_id,
                outcome=e.outcome,
                summary=e.summary,
                work_item_key=e.work_item_key,
                received_at=e.received_at,
            )
            for e in rows
        ]
    )


def _problem(error: Any) -> JSONResponse:
    return JSONResponse(error.to_problem(), status_code=error.status_code, media_type=PROBLEM_JSON)


@router.post(
    "/webhooks/assistants/{org}/{slug}/tasks/{task}/events",
    tags=["webhooks"],
    response_model=TaskEventAck,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="taskEventWebhook",
)
async def task_event(
    org: Slug,
    slug: Slug,
    task: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{0,62}$")],
    request: Request,
    session: PlatformDb,
    idempotency_key: Annotated[str, Header()] = "",
    x_choregos_signature: Annotated[str, Header()] = "",
) -> TaskEventAck | JSONResponse:
    """A system posts an event for one person: the task's `trigger` names, by a JSON pointer, the email of the
    subscriber, and the task starts in their personal space. 404: no such task, or none with a `trigger`;
    401: a wrong or missing `X-Choregos-Signature` (HMAC-SHA256 of the body under the key of
    `.../tasks/{task}/event-key`); 413: a body over 256 KiB; 422: not a JSON object, or an event that names no
    subscriber, several, or one nobody matches; 409: the subscriber's budget, delegation or audience
    refuses it. Refused events are journaled. A replayed event (the trigger's `idempotency` pointer, else
    `Idempotency-Key`, else the body's digest) answers `duplicate` and starts nothing."""
    organisation, row, _, trigger = await _event_task(session, org, slug, task)
    body = await request.body()
    key = task_events.event_key(row.id, row.webhook_secret_generation, task)
    if not task_events.verify_signature(key, body, x_choregos_signature):
        raise unauthorized("invalid or missing X-Choregos-Signature")
    if len(body) > MAX_EVENT_BODY:
        return JSONResponse(
            {"type": "about:blank", "title": "Payload too large", "status": 413},
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            media_type=PROBLEM_JSON,
        )
    try:
        payload: Any = json.loads(body or b"{}")
    except ValueError:
        payload = None
    digest = body_digest(body)
    if not isinstance(payload, dict):
        task_events.journal_refusal(session, row, task, digest, "refused: the event is not a JSON object")
        return _problem(
            task_events.EventRefusedError(422, "Unprocessable event", "an event is a JSON object")
        )
    event_id = task_events.event_id_of(trigger, payload, idempotency_key, digest)
    try:
        outcome, work_item_key = await task_events.route_event(
            session, organisation, row, task, payload, event_id
        )
    except task_events.EventRefusedError as refused:
        return _problem(refused)  # the journal line is in the session: returned, not raised, it commits
    return TaskEventAck(accepted=True, outcome=outcome, work_item_key=work_item_key)


# -- the Teams channel of an installation's deliveries (S63-25) ---------------------------------------

TEAMS_DELIVERY = "/orgs/{org}/assistants/{slug}/delivery/teams"


async def _managed(org: str, slug: str, session: Any, principal: Any) -> Any:
    organisation = await organisation_by_slug(session, org)
    row = await assistants.installation(session, organisation.id, slug)
    assistants.require_manager(principal, org, row)
    return row


@router.get(TEAMS_DELIVERY, response_model=TeamsDelivery, operation_id="getTeamsDelivery")
async def get_teams_delivery(org: str, slug: Slug, session: Db, principal: Me) -> TeamsDelivery:
    """Whether the installation delivers task outputs and owner notices to Teams (`assistant:manage`)."""
    return await teams_state(session, await _managed(org, slug, session, principal))


@router.put(TEAMS_DELIVERY, response_model=TeamsDelivery, operation_id="putTeamsDelivery")
async def put_teams_delivery(
    org: str, slug: Slug, body: TeamsDeliveryPut, session: Db, principal: Me
) -> TeamsDelivery:
    """Seals the Teams incoming webhook or Workflows URL of the installation (`assistant:manage`). The URL is
    a credential: 422 unless it is `https` without credentials in it, never returned, never logged, and the
    organisation's egress allowlist decides whether its host may be called. 409 when the store has no key."""
    row = await _managed(org, slug, session, principal)
    return await set_teams_webhook(session, principal, org, row, body.webhook_url)


@router.delete(TEAMS_DELIVERY, status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteTeamsDelivery")
async def delete_teams_delivery(org: str, slug: Slug, session: Db, principal: Me) -> Response:
    await clear_teams_webhook(session, principal, org, await _managed(org, slug, session, principal))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
