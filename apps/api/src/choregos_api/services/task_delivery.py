# SPDX-License-Identifier: Apache-2.0
"""Where a task's outputs go (ADR 0137 point 1, S63-13).

The outputs of a task's work item are artifacts (ADR 0049). The **inbox** is never a delivery of its own:
it is the artifacts of the caller's task work items, listed by `inbox`, so it always holds them. The other
channels of the task's `deliver` list are sent once per artifact revision by `deliver_pending`, and what was
sent is kept in the work item's `documents.task_delivery` (`<name>@<version>` -> channel -> state), so a
second pass sends nothing twice and a failed channel is retried up to `MAX_ATTEMPTS` times.

- `email` and `teams` go through a `Notifier` (`choregos_adapters`) that the installation's
  `policy_overrides.delivery.<channel>` describes (`{type, config}`); tests inject one with
  `set_notifier_factory`. A channel nobody configured is recorded as `not_configured`, never silent.
- `webhook` posts the artifact as JSON to `policy_overrides.delivery.webhook.url`, through the organisation's
  egress guard, signed like the A2A push notifications: `X-Choregos-Signature: t=<unix>,v1=<hex HMAC-SHA256
  of "<t>.<body>">`, under a key derived from the deployment's session secret and the installation id
  (`webhook_secret`), with `Idempotency-Key` the artifact's id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import structlog
from choregos_adapters import Notifier
from choregos_core.domain import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import ArtifactRecord, AssistantInstallation, Organization, Project, User, WorkItem
from ..rbac import Principal
from ..schemas.tasks import InboxItem
from .personal_spaces import PERSONAL

log = structlog.get_logger("choregos.task_delivery")

MAX_ATTEMPTS = 5
#: How many of the newest artifacts a pass looks at.
BATCH = 500
BODY_LIMIT = 4000
SIGNATURE = "X-Choregos-Signature"
TIMEOUT_S = 10.0
CHANNELS = ("email", "teams", "webhook")

NotifierFactory = Callable[[AssistantInstallation, str], Notifier | None]
_factory: list[NotifierFactory] = []


def set_notifier_factory(factory: NotifierFactory | None) -> None:
    """For tests and the fakes: the notifier of a channel of an installation."""
    _factory.clear()
    if factory is not None:
        _factory.append(factory)


def _notifier(row: AssistantInstallation, channel: str) -> Notifier | None:
    if _factory:
        return _factory[0](row, channel)
    configured = ((row.policy_overrides or {}).get("delivery") or {}).get(channel)
    if not isinstance(configured, dict) or not configured.get("type"):
        return None
    from choregos_adapters import build

    notifier: Notifier = build("notify", str(configured["type"]), dict(configured.get("config") or {}))
    return notifier


def webhook_secret(installation_id: str) -> str:
    """The HMAC key of an installation's webhook: derived from the session secret, nothing more stored."""
    key = get_settings().session_secret.encode()
    return "chtask_" + hmac.new(key, f"task-webhook:{installation_id}".encode(), hashlib.sha256).hexdigest()


def sign(secret: str, stamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _task_of(item: WorkItem) -> dict[str, Any] | None:
    task = (item.documents or {}).get("task")
    return task if isinstance(task, dict) else None


# -- the inbox ---------------------------------------------------------------------------------------------


async def inbox(session: AsyncSession, principal: Principal, *, limit: int = 100) -> list[InboxItem]:
    """The artifacts the caller's tasks delivered, newest first: those of work items of their own personal
    spaces that name a task. Only the owner reads a personal space (row-level security), and the query
    names the owner as well."""
    rows = (
        await session.execute(
            select(ArtifactRecord, WorkItem)
            .join(WorkItem, WorkItem.id == ArtifactRecord.work_item_id)
            .join(Project, Project.id == WorkItem.project_id)
            .where(Project.kind == PERSONAL, Project.owner_user_id == principal.user_id)
            .order_by(ArtifactRecord.created_at.desc(), ArtifactRecord.version.desc())
            .limit(BATCH)
        )
    ).all()
    found: list[InboxItem] = []
    for artifact, item in rows:
        task = _task_of(item)
        if task is None:
            continue
        found.append(
            InboxItem(
                id=artifact.id,
                task=str(task.get("task")),
                installation=str(task.get("installation")),
                work_item_id=item.id,
                name=artifact.name,
                version=artifact.version,
                type=artifact.type,
                media_type=artifact.media_type,
                content=artifact.content if artifact.storage_key is None else None,
                created_at=artifact.created_at,
            )
        )
        if len(found) >= limit:
            break
    return found


# -- the other channels ------------------------------------------------------------------------------------


def _text(artifact: ArtifactRecord) -> str:
    content = artifact.content
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=1)
    return text[:BODY_LIMIT]


async def _post(
    session: AsyncSession, organisation: Organization, url: str, body: bytes, headers: dict[str, str]
) -> None:
    from .egress import egress_scope
    from .remote_agents import a2a_transport

    async with (
        egress_scope(session, organisation, "api", "assistant.task"),
        httpx.AsyncClient(transport=a2a_transport(), timeout=TIMEOUT_S, follow_redirects=False) as http,
    ):
        answer = await http.post(url, content=body, headers=headers)
    if not 200 <= answer.status_code < 300:
        raise RuntimeError(f"HTTP {answer.status_code}")


async def _send(
    session: AsyncSession,
    channel: str,
    row: AssistantInstallation,
    organisation: Organization,
    user: User,
    item: WorkItem,
    artifact: ArtifactRecord,
    task: dict[str, Any],
) -> None:
    if channel == "webhook":
        url = (((row.policy_overrides or {}).get("delivery") or {}).get("webhook") or {}).get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise LookupError("not_configured")
        body = json.dumps(
            {
                "installation": row.slug,
                "task": task.get("task"),
                "subscriber": user.email,
                "work_item": item.id,
                "artifact": {
                    "id": artifact.id,
                    "name": artifact.name,
                    "version": artifact.version,
                    "type": artifact.type,
                    "digest": artifact.digest,
                    "content": artifact.content if artifact.storage_key is None else None,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        stamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            SIGNATURE: f"t={stamp},v1={sign(webhook_secret(row.id), stamp, body)}",
            "Idempotency-Key": artifact.id,
        }
        await _post(session, organisation, url, body, headers)
        return
    notifier = _notifier(row, channel)
    if notifier is None:
        raise LookupError("not_configured")
    await notifier.send(
        user.email,
        Message(
            title=f"{task.get('task')}: {artifact.name}",
            body=_text(artifact),
            context={"task": str(task.get("task")), "artifact": artifact.name, "work_item": item.id},
        ),
    )


async def deliver_pending(session: AsyncSession) -> int:
    """Send what the `deliver` lists ask for beyond the inbox, once per artifact revision; the number of
    deliveries made. Runs at the platform scope (the loop)."""
    rows = (
        await session.execute(
            select(ArtifactRecord, WorkItem)
            .join(WorkItem, WorkItem.id == ArtifactRecord.work_item_id)
            .join(Project, Project.id == WorkItem.project_id)
            .where(Project.kind == PERSONAL)
            .order_by(ArtifactRecord.created_at.desc())
            .limit(BATCH)
        )
    ).all()
    sent = 0
    for artifact, item in rows:
        task = _task_of(item)
        if task is None:
            continue
        wanted = [c for c in task.get("deliver") or [] if c in CHANNELS]
        ledger = {**((item.documents or {}).get("task_delivery") or {})}
        mine = dict(ledger.get(f"{artifact.name}@{artifact.version}") or {})
        todo = [
            c
            for c in wanted
            if mine.get(c, {}).get("state") != "sent" and mine.get(c, {}).get("attempts", 0) < MAX_ATTEMPTS
        ]
        if not todo:
            continue
        row = await session.get(AssistantInstallation, str(task.get("installation_id")))
        organisation = await session.get(Organization, row.org_id) if row is not None else None
        project = await session.get(Project, item.project_id)
        user = await session.get(User, project.owner_user_id) if project and project.owner_user_id else None
        if row is None or organisation is None or user is None:
            continue
        for channel in todo:
            state = dict(mine.get(channel) or {"attempts": 0})
            try:
                await _send(session, channel, row, organisation, user, item, artifact, task)
                mine[channel] = {"state": "sent", "attempts": state["attempts"] + 1}
                sent += 1
            except LookupError:
                mine[channel] = {"state": "not_configured", "attempts": MAX_ATTEMPTS}
            except Exception as failure:  # a delivery that fails is retried, never lost silently
                mine[channel] = {
                    "state": "failed",
                    "attempts": state["attempts"] + 1,
                    "error": f"{type(failure).__name__}: {failure}"[:200],
                }
                log.warning("task.delivery_failed", channel=channel, error=str(failure)[:200])
        ledger[f"{artifact.name}@{artifact.version}"] = mine
        item.documents = {**(item.documents or {}), "task_delivery": ledger}
    await session.flush()
    return sent
