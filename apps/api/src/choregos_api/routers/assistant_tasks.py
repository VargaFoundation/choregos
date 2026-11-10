# SPDX-License-Identifier: Apache-2.0
"""A person's subscriptions to an assistant's tasks, their on-demand runs and their inbox (S63-13, ADR 0137).

`GET, PUT, DELETE /me/assistants/{org}/{slug}/tasks/{task}` subscribe, set the trigger and time zone, and
unsubscribe; `POST …/run` starts the task now; `GET /me/inbox` lists what the caller's tasks delivered. A
person outside the assistant's audience is answered 404 everywhere.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response, status

from ..db.models import TaskSubscription as Row
from ..deps import Db, Me
from ..schemas.assistants import SLUG
from ..schemas.tasks import (
    InboxList,
    TaskRun,
    TaskSubscription,
    TaskSubscriptionPut,
    TaskTrigger,
)
from ..services import assistant_tasks
from ..services.task_delivery import inbox

router = APIRouter(tags=["agents"])

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
