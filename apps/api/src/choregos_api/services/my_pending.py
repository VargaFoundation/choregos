# SPDX-License-Identifier: Apache-2.0
"""What waits for the person, across the organisations they belong to (ADR 0154 §4, G8).

Four kinds, each read from its own table and each filtered by the rule of the route that decides it, so
that the list never offers what the decision would refuse:

- `human_step`: a waiting human request the person may answer themselves (`own_requests`: read right,
  `item:decide`, the request addressed to them or their groups, never an administrator's override).
- `governed_action`: an action `pending_approval` of a project they read, that they may approve: the rule's
  rank, its group, its separation of duties (`_one_of_its_approvers`, the very check `decide` applies), and
  not one they have already decided.
- `conversation_action` and `launch`: the writes and the workflow starts an assistant proposed in THEIR
  conversations (`ConversationAction.kind`), `proposed` and not decided.

The list is a read: it decides nothing, and a decision still needs the person's session (ADR 0154 §7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Action, ConversationAction, Organization, Project, WorkItem
from ..errors import ApiError
from ..knowledge.marked import readable
from ..rbac import Permission, Principal
from ..schemas.pending import PendingDecision, PendingItem, PendingWhere
from . import actions as governed
from .conversation_actions import confirm_url
from .decision_drafts import own_requests
from .personal_spaces import listed

#: How many of each kind are read before the merge: the list is cut after it, newest first.
READ_AHEAD = 500


def _console(path: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}{path}"


def _utc(value: datetime) -> datetime:
    from choregos_core.domain import aware

    return aware(value) or value


async def _human_steps(session: AsyncSession, principal: Principal) -> list[PendingItem]:
    found: list[PendingItem] = []
    for own in await own_requests(session, principal, limit=READ_AHEAD):
        payload = own.request.payload or {}
        what = payload.get("question") or payload.get("summary") or own.item.title
        found.append(
            PendingItem(
                kind="human_step",
                id=own.request.id,
                what=str(what),
                where=PendingWhere(
                    org=own.org,
                    project_slug=own.project.slug,
                    work_item_id=own.item.id,
                    work_item_key=own.item.tracker_key,
                ),
                since=_utc(own.request.requested_at),
                due_at=_utc(own.request.due_at) if own.request.due_at else None,
                decision=PendingDecision(
                    path=f"/work-items/{own.item.id}/decisions",
                    url=_console(f"/p/{own.project.slug}/items/{own.item.id}#decision"),
                ),
            )
        )
    return found


async def _governed_actions(session: AsyncSession, principal: Principal) -> list[PendingItem]:
    orgs = sorted(set(principal.org_roles) | {key.split("/", 1)[0] for key in principal.project_roles})
    if not orgs:
        return []
    rows = (
        await session.execute(
            select(Action, Project, Organization.slug)
            .join(Project, Action.project_id == Project.id)
            .join(Organization, Project.org_id == Organization.id)
            .where(Action.status == "pending_approval", Organization.slug.in_(orgs), listed())
            .order_by(Action.created_at.desc())
            .limit(READ_AHEAD)
        )
    ).all()
    found: list[PendingItem] = []
    me = principal.email.lower()
    for action, project, org in rows:
        if not principal.can(Permission.PROJECT_READ, org, project.slug):
            continue
        item = await session.get(WorkItem, action.work_item_id) if action.work_item_id else None
        if item is not None and not await readable(session, principal, item, project.org_id):
            continue  # the action is the run's content (ADR 0114)
        if any(str(d.get("by", "")).lower() == me for d in action.decisions or []):
            continue  # already decided: the decision route says so
        try:
            await governed._one_of_its_approvers(
                session, project, org, action, principal, "deciding", "decide"
            )
        except ApiError:
            continue  # rank, group or separation of duties: not theirs to decide
        found.append(
            PendingItem(
                kind="governed_action",
                id=action.id,
                what=action.title,
                where=PendingWhere(
                    org=org,
                    project_slug=project.slug,
                    work_item_id=item.id if item else None,
                    work_item_key=item.tracker_key if item else None,
                ),
                since=_utc(action.created_at),
                decision=PendingDecision(
                    path=f"/projects/{project.id}/actions/{action.id}/decision",
                    url=_console(f"/p/{project.slug}/actions/{action.id}"),
                ),
            )
        )
    return found


async def _conversation_actions(session: AsyncSession, principal: Principal) -> list[PendingItem]:
    rows = (
        await session.execute(
            select(ConversationAction, Organization.slug)
            .join(Organization, ConversationAction.org_id == Organization.id)
            .where(
                ConversationAction.user_id == principal.user_id,
                ConversationAction.status == "proposed",
                Organization.slug.in_(sorted(principal.org_roles)),
            )
            .order_by(ConversationAction.created_at.desc())
            .limit(READ_AHEAD)
        )
    ).all()
    found: list[PendingItem] = []
    for action, org in rows:
        target: dict[str, Any] = action.target or {}
        launch = action.kind == "launch"
        found.append(
            PendingItem(
                kind="launch" if launch else "conversation_action",
                id=action.id,
                what=action.summary,
                where=PendingWhere(
                    org=org,
                    project_slug=target.get("project") if launch else None,
                    conversation_id=action.conversation_id,
                ),
                since=_utc(action.created_at),
                decision=PendingDecision(
                    path=f"/me/conversations/{action.conversation_id}/actions/{action.id}/confirm",
                    url=confirm_url(action.conversation_id, action.id),
                ),
            )
        )
    return found


async def pending_for(session: AsyncSession, principal: Principal, *, limit: int = 100) -> list[PendingItem]:
    """Everything that waits for the person, newest first, cut at `limit`."""
    items = [
        *await _human_steps(session, principal),
        *await _governed_actions(session, principal),
        *await _conversation_actions(session, principal),
    ]
    items.sort(key=lambda i: (i.since, i.id), reverse=True)
    return items[:limit]
