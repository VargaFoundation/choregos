# SPDX-License-Identifier: Apache-2.0
"""HTTP triggers: another system opens a work item in a project (S51-01, ADR 0113).

A project declares `triggers:` in its configuration. Each one is a door at
`POST /webhooks/triggers/{org}/{project}/{name}`, opened by its own token (only its SHA-256 is kept),
that maps the caller's JSON onto a work item — title, description, labels, fields, and optionally a
key — and starts it in the trigger's workflow, or in the one the project's routing picks from its
labels (`trigger:<name>` first). The same mapping serves a person who fires the trigger by hand.

What makes it safe to call twice: the delivery is de-duplicated (`Idempotency-Key`, else the body's
digest), and a trigger with a `key` opens one work item per value of the payload's identifier — a
second call for the same incident finds the first work item and says `already_open`.

Every call that reaches a project leaves a row in `received_events`, refused ones included, with a
summary built from what the platform decided (trigger, workflow, key, refused field names), never
from the payload's free text.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import TriggerConfig
from choregos_contracts.project import TRIGGER_PLACEHOLDER
from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Project, TriggerToken, WebhookDelivery, WorkItem
from ..errors import ApiError, conflict
from ..rbac import Principal
from ..schemas import WorkItemCreate
from . import received_events
from .definitions import project_config, workflow_du_ticket, workflow_model

#: A token reads as one in a log or a secret scanner.
TOKEN_PREFIX = "chtrg_"  # noqa: S105 - a prefix, not a secret
#: How the dedup table names a trigger's deliveries (`webhook_deliveries.source` is 32 characters).
DELIVERY_SOURCE = "trigger"
#: A label read from a payload is cut there, as a tracker's label would be.
MAX_LABEL = 100


class TriggerRefusedError(ApiError):
    """The payload does not make a work item: a missing key, an external tracker (422 or 409)."""


def mint_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def declared(project: Project, name: str) -> TriggerConfig | None:
    """The trigger `name` of the project's configuration, or nothing."""
    return next((t for t in project_config(project).triggers if t.name == name), None)


#: What a signature header starts with; the rest is the HMAC-SHA256 of the body, hex or base64.
SIGNATURE_PREFIX = "sha256="


def signing_secret(token_sha256: str) -> str:
    """The HMAC key of a trigger with `auth: signature` (S51-04): derived from the token's digest
    under the deployment's session secret, so nothing more is stored and it changes with the token.
    Rotating `CHOREGOS_SESSION_SECRET` changes it too: the senders take the new one from a new token."""
    from ..config import get_settings

    key = get_settings().session_secret.encode()
    return "chsig_" + hmac.new(key, f"trigger-signing:{token_sha256}".encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """`sha256=<hex>` or `sha256=<base64>` of the body under `secret`, compared in constant time."""
    if not header.lower().startswith(SIGNATURE_PREFIX):
        return False
    given = header[len(SIGNATURE_PREFIX) :].strip()
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(given.lower(), mac.hex()) or hmac.compare_digest(
        given, base64.b64encode(mac).decode()
    )


async def token_row(session: AsyncSession, project: Project, name: str) -> TriggerToken | None:
    return (
        await session.execute(
            select(TriggerToken).where(TriggerToken.project_id == project.id, TriggerToken.name == name)
        )
    ).scalar_one_or_none()


async def token_rows(session: AsyncSession, project: Project) -> list[TriggerToken]:
    rows = await session.execute(select(TriggerToken).where(TriggerToken.project_id == project.id))
    return list(rows.scalars())


async def verify_token(session: AsyncSession, project: Project, name: str, provided: str) -> bool:
    """Compared in constant time, digest to digest: a trigger without a token answers no."""
    row = await token_row(session, project, name)
    return row is not None and hmac.compare_digest(row.token_sha256, token_digest(provided or ""))


async def rotate_token(session: AsyncSession, project: Project, name: str, created_by: str | None) -> str:
    """A new token for the trigger; the previous one stops working in the same transaction."""
    token = mint_token()
    row = await token_row(session, project, name)
    if row is None:
        session.add(
            TriggerToken(
                org_id=project.org_id,
                project_id=project.id,
                name=name,
                token_sha256=token_digest(token),
                created_by=created_by,
            )
        )
    else:
        row.token_sha256 = token_digest(token)
        row.created_by = created_by
        row.created_at = utcnow()
    await session.flush()
    return token


# ───────────────────────────── The mapping ─────────────────────────────


def resolve(pointer: str, payload: Any) -> tuple[bool, Any]:
    """The value at a JSON pointer (RFC 6901), and whether it is there."""
    node = payload
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def render(template: str, payload: Any) -> str:
    """`{/pointer}` replaced by what the payload holds there; nothing when it holds nothing."""
    return re.sub(TRIGGER_PLACEHOLDER, lambda m: _as_text(resolve(m.group(1), payload)[1]), template)


def _one_line(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


@dataclass(frozen=True, slots=True)
class Mapped:
    """What a payload becomes, before anything is written."""

    title: str
    body: str
    labels: list[str]
    fields: dict[str, Any] = field(default_factory=dict)
    #: The payload's own identifier, when the trigger declares `key`.
    key_value: str | None = None


def matches(trigger: TriggerConfig, payload: Any) -> bool:
    """Whether the payload holds, at every pointer of `when`, the text or one of the texts listed
    (S51-05). A pointer the payload lacks never matches."""
    for pointer, expected in trigger.when.items():
        found, value = resolve(pointer, payload)
        allowed = [expected] if isinstance(expected, str) else expected
        if not found or _as_text(value) not in allowed:
            return False
    return True


def map_payload(trigger: TriggerConfig, payload: Any) -> Mapped:
    """The work item a payload describes. A declared `key` the payload lacks is refused (422): one
    work item per incident is the promise, and a missing identifier cannot keep it."""
    key_value: str | None = None
    if trigger.key is not None:
        found, value = resolve(trigger.key, payload)
        key_value = _as_text(value).strip() if found and isinstance(value, str | int) else ""
        if not key_value:
            raise TriggerRefusedError(
                422,
                "Unprocessable payload",
                f"trigger `{trigger.name}`: the payload holds no text or number at `{trigger.key}`",
            )
    labels = [f"trigger:{trigger.name}"]
    for template in trigger.labels:
        label = _one_line(render(template, payload), MAX_LABEL)
        if label and label not in labels:
            labels.append(label)
    fields: dict[str, Any] = {}
    for name, pointer in trigger.fields.items():
        found, value = resolve(pointer, payload)
        if found and value is not None:
            fields[name] = value
    title = _one_line(render(trigger.title, payload), 500) or f"{trigger.name} {key_value or ''}".strip()
    return Mapped(
        title=title,
        body=render(trigger.body, payload).strip(),
        labels=labels,
        fields=fields,
        key_value=key_value,
    )


def item_key(prefix: str, trigger: str, value: str) -> str:
    """`<PREFIX>-<trigger>.<value>`: the value keeps letters, digits, `.` and `_`. The last `-` segment
    always holds a `.`, so the platform's own numbering (`<PREFIX>-<n>`) never counts it."""
    safe = re.sub(r"[^A-Za-z0-9._]+", "_", value).strip("_")[:120]
    return f"{prefix}-{trigger}.{safe or 'x'}"


# ───────────────────────────── Firing ─────────────────────────────


@dataclass(slots=True)
class Firing:
    """What a call did: the outcome recorded in `received_events`, or `duplicate` (nothing recorded)."""

    outcome: str
    item: WorkItem | None = None
    workflow: str | None = None
    refusal: ApiError | None = None


async def _first_delivery(session: AsyncSession, project: Project, name: str, delivery_id: str) -> bool:
    """Writes the delivery's dedup row; False when it is already there (a replayed call)."""
    scoped = f"{project.id}:{name}:{delivery_id}"[:255]
    seen = (
        await session.execute(
            select(WebhookDelivery.id).where(
                WebhookDelivery.source == DELIVERY_SOURCE, WebhookDelivery.delivery_id == scoped
            )
        )
    ).scalar_one_or_none()
    if seen is not None:
        return False
    session.add(
        WebhookDelivery(
            source=DELIVERY_SOURCE,
            delivery_id=scoped,
            event_type=f"trigger:{name}"[:80],
            ts=utcnow(),
            payload_digest=delivery_id[:80],
        )
    )
    await session.flush()
    return True


def _summary(
    event_type: str, trigger: str, outcome: str, workflow: str | None, refusal: ApiError | None
) -> str:
    """From what the platform decided: never a title, a description or a field's value."""
    parts = [event_type, trigger]
    if workflow:
        parts.append(f"workflow {workflow}")
    if refusal is not None:
        parts.append(f"refused: {_one_line(refusal.detail or refusal.title, 160)}")
        names = [".".join(str(p) for p in e.get("loc", [])) for e in refusal.errors if isinstance(e, dict)]
        if names := [n for n in names if n]:
            parts.append("at " + ", ".join(names[:5]))
    elif outcome == "already_open":
        parts.append("already open")
    elif outcome == "ignored":
        parts.append("did not match `when`")
    return " · ".join(parts)


async def fire(
    session: AsyncSession,
    project: Project,
    trigger: TriggerConfig,
    payload: Any,
    *,
    delivery_id: str,
    principal: Principal,
    initiated_by: str,
    event_type: str,
) -> Firing:
    """Maps the payload and opens (and starts) its work item, once per delivery and per key.

    Everything that may be refused happens inside a savepoint: a refusal leaves no work item and no
    dedup row (the corrected call goes through), and its `received_events` row is written in the
    caller's transaction, which then commits."""
    org_id, project_id = project.org_id, project.id
    try:
        async with session.begin_nested():
            firing = await _open(session, project, trigger, payload, delivery_id, principal, initiated_by)
    except IntegrityError:
        # Two replicas took the same delivery, or the same key, at once: the other one has it.
        return Firing("duplicate")
    except ApiError as refused:
        firing = Firing("refused", refusal=refused)
    if firing.outcome == "duplicate":
        return firing
    if firing.item is not None:
        firing.workflow = workflow_model(await workflow_du_ticket(session, firing.item)).metadata.name
    received_events.record_firing(
        session,
        org_id=org_id,
        project_id=project_id,
        source=f"trigger:{trigger.name}",
        event_type=event_type,
        delivery_id=delivery_id,
        outcome=firing.outcome,
        summary=_summary(event_type, trigger.name, firing.outcome, firing.workflow, firing.refusal),
        item_id=firing.item.id if firing.item is not None else None,
        item_key=firing.item.tracker_key if firing.item is not None else None,
    )
    return firing


async def _open(
    session: AsyncSession,
    project: Project,
    trigger: TriggerConfig,
    payload: Any,
    delivery_id: str,
    principal: Principal,
    initiated_by: str,
) -> Firing:
    from .tickets import cle_de_ticket_interne, creer_un_ticket, le_tracker_est_interne

    if not await _first_delivery(session, project, trigger.name, delivery_id):
        return Firing("duplicate")
    if not await le_tracker_est_interne(session, project):
        raise conflict(
            f"trigger `{trigger.name}`: the project's tracker is external; a trigger opens work items "
            "in the internal tracker only"
        )
    if not isinstance(payload, dict):
        raise TriggerRefusedError(422, "Unprocessable payload", "a trigger takes a JSON object")
    if not matches(trigger, payload):
        return Firing("ignored")
    mapped = map_payload(trigger, payload)
    key: str | None = None
    if mapped.key_value is not None:
        prefix = await cle_de_ticket_interne(session, project, numerotee=False)
        key = item_key(prefix, trigger.name, mapped.key_value)
        existing = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project.id, WorkItem.tracker_key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return Firing("already_open", existing)
    item = await creer_un_ticket(
        session,
        principal,
        project,
        WorkItemCreate(
            title=mapped.title,
            body=mapped.body,
            workflow=trigger.workflow,
            labels=mapped.labels,
            fields=mapped.fields,
            start=True,
        ),
        canal="trigger",
        cle=key,
        initiated_by=initiated_by,
    )
    return Firing("created_work_item", item)
