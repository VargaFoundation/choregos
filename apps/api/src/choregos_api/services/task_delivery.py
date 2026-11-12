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
  of "<t>.<body>">`, under a key derived from the deployment's session secret, the installation id and its
  generation (`webhook_secret`; an owner is shown it once, `reveal_webhook_secret`), with `Idempotency-Key`
  the artifact's id.
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
from choregos_core import utcnow
from choregos_core.domain import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record as audit
from ..config import get_settings
from ..db.models import ArtifactRecord, AssistantInstallation, Organization, Project, User, WorkItem
from ..errors import conflict, unprocessable
from ..rbac import Principal
from ..schemas.tasks import InboxItem, TeamsDelivery, WebhookSecret
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


def notifier_for(
    row: AssistantInstallation, channel: str, secrets: dict[str, Any] | None = None
) -> Notifier | None:
    """The notifier of a channel of the installation: `policy_overrides.delivery.<channel>` is
    `{type, config, secret_refs}`. `secrets` are the configuration's secret fields already resolved
    (`notifier_in` reads them); a type that needs one (`teams`) is not built without it."""
    if _factory:
        return _factory[0](row, channel)
    configured = ((row.policy_overrides or {}).get("delivery") or {}).get(channel)
    if not isinstance(configured, dict) or not configured.get("type"):
        return None
    from choregos_adapters import build

    config = {**dict(configured.get("config") or {}), **(secrets or {})}
    notifier: Notifier = build("notify", str(configured["type"]), config)
    return notifier


def teams_secret_name(slug: str) -> str:
    """Where the installation's Teams webhook URL is sealed in the organisation's secret store."""
    return f"assistant/{slug}/delivery/teams"


async def notifier_in(session: AsyncSession, row: AssistantInstallation, channel: str) -> Notifier | None:
    """`notifier_for` with the secret fields of the configuration read from the sealed store (the Teams
    webhook URL, `store:` references only). A secret that cannot be read raises: the delivery is recorded
    as failed and retried, never skipped as if it were not configured."""
    if _factory:
        return _factory[0](row, channel)
    configured = ((row.policy_overrides or {}).get("delivery") or {}).get(channel)
    if not isinstance(configured, dict) or not configured.get("type"):
        return None
    refs = dict(configured.get("secret_refs") or {})
    resolved: dict[str, Any] = {}
    if refs:
        from ..secret_store import resolved_configuration

        resolved = await resolved_configuration(
            session, row.org_id, "notify", str(configured["type"]), dict(configured.get("config") or {}), refs
        )
    return notifier_for(row, channel, resolved)


async def teams_state(session: AsyncSession, row: AssistantInstallation) -> TeamsDelivery:
    """Whether the installation delivers to Teams, and the version of the sealed URL: never the URL."""
    from ..secret_store import get_secret_store

    configured = (((row.policy_overrides or {}).get("delivery") or {}).get("teams") or {}).get("type")
    name = teams_secret_name(row.slug)
    meta = (await get_secret_store().meta(session, row.org_id, [name])).get(name) if configured else None
    return TeamsDelivery(
        configured=bool(configured and meta is not None),
        version=meta.version if meta is not None else None,
        updated_at=meta.rotated_at if meta is not None else None,
    )


async def set_teams_webhook(
    session: AsyncSession, principal: Principal, org: str, row: AssistantInstallation, url: str
) -> TeamsDelivery:
    """Seals the Teams webhook / Workflows URL of the installation in the organisation's store and points
    `policy_overrides.delivery.teams` at it. The URL is validated (https, no credentials), remembered by the
    log redaction, never returned and never audited."""
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.notify.teams import check_webhook_url
    from choregos_core.redaction import remember_secret

    from ..secret_store import NO_KEY_MESSAGE, SecretStoreUnavailable, get_secret_store, store_ref

    try:
        clean = check_webhook_url(url)
    except AdapterError as refusal:
        raise unprocessable(str(refusal)) from None
    remember_secret(clean)
    store = get_secret_store()
    if not store.available():
        raise conflict(NO_KEY_MESSAGE)
    name = teams_secret_name(row.slug)
    try:
        await store.put(session, row.org_id, name, clean, principal.email)
    except SecretStoreUnavailable as exc:
        raise conflict(str(exc)) from exc
    overrides = dict(row.policy_overrides or {})
    delivery = dict(overrides.get("delivery") or {})
    delivery["teams"] = {"type": "teams", "config": {}, "secret_refs": {"webhook_url": store_ref(name)}}
    overrides["delivery"] = delivery
    row.policy_overrides = overrides
    await session.flush()
    await audit(
        session,
        principal,
        "assistant.delivery.teams.set",
        org_id=row.org_id,
        target_type="assistant",
        target_id=row.id,
        organisation=org,
    )
    return await teams_state(session, row)


async def clear_teams_webhook(
    session: AsyncSession, principal: Principal, org: str, row: AssistantInstallation
) -> None:
    """Forgets the Teams channel: the sealed URL is deleted and the configuration removed."""
    from ..secret_store import get_secret_store

    await get_secret_store().delete(session, row.org_id, teams_secret_name(row.slug))
    overrides = dict(row.policy_overrides or {})
    delivery = dict(overrides.get("delivery") or {})
    if delivery.pop("teams", None) is not None:
        overrides["delivery"] = delivery
        row.policy_overrides = overrides
    await session.flush()
    await audit(
        session,
        principal,
        "assistant.delivery.teams.cleared",
        org_id=row.org_id,
        target_type="assistant",
        target_id=row.id,
        organisation=org,
    )


def webhook_secret(installation_id: str, generation: int = 0) -> str:
    """The HMAC key of an installation's webhook: derived from the session secret, the installation and its
    generation (0 until a rotation), nothing more stored."""
    key = get_settings().session_secret.encode()
    label = f"task-webhook:{installation_id}" + (f":{generation}" if generation else "")
    return "chtask_" + hmac.new(key, label.encode(), hashlib.sha256).hexdigest()


async def reveal_webhook_secret(
    session: AsyncSession,
    principal: Principal,
    org: str,
    row: AssistantInstallation,
    *,
    rotate: bool,
) -> WebhookSecret:
    """Shows the key once: the first call returns it and remembers it was shown; later calls are 409 unless
    `rotate`, which makes a new generation and shows that one. Every showing is in the audit log (never the
    key)."""
    if row.webhook_secret_shown_at is not None and not rotate:
        raise conflict(
            "the webhook key was already shown: rotate it to get a new one",
            extra={"code": "already_shown", "generation": row.webhook_secret_generation},
        )
    if rotate:
        row.webhook_secret_generation += 1
    now = utcnow()
    row.webhook_secret_shown_at = now
    await session.flush()
    await audit(
        session,
        principal,
        "assistant.webhook_secret.rotated" if rotate else "assistant.webhook_secret.shown",
        org_id=row.org_id,
        target_type="assistant",
        target_id=row.id,
        organisation=org,
        generation=row.webhook_secret_generation,
    )
    return WebhookSecret(
        secret=webhook_secret(row.id, row.webhook_secret_generation),
        generation=row.webhook_secret_generation,
        shown_at=now,
    )


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
        secret = webhook_secret(row.id, row.webhook_secret_generation)
        headers = {
            "Content-Type": "application/json",
            SIGNATURE: f"t={stamp},v1={sign(secret, stamp, body)}",
            "Idempotency-Key": artifact.id,
        }
        await _post(session, organisation, url, body, headers)
        return
    from .egress import egress_scope

    notifier = await notifier_in(session, row, channel)
    if notifier is None:
        raise LookupError("not_configured")
    # The organisation's allowlist decides which hosts a notifier may reach, as for the webhook channel.
    async with egress_scope(session, organisation, "api", "assistant.task"):
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
