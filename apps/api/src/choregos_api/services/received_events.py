# SPDX-License-Identifier: Apache-2.0
"""The webhook events a project received, and what the platform did with each (S42-06f).

Owner review of 2026-10-28: "we don't see the webhooks, or at least the events received". A row is
written for each event of a verified delivery that reached a project, in the webhook's own session,
so it commits with what the event did. The summary is built from typed fields only — the key, a
label, a status move, a conclusion, pull request numbers — never a title, a body, a comment, an
author or an email: those stay in the tracker.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import InboundEvent
from choregos_core import utcnow

from ..db.models import Project, ReceivedEvent, WorkItem

#: What the platform did with an event (the contract's `ReceivedEvent.outcome`).
OUTCOMES = frozenset(
    {
        "created_work_item",
        "started",
        "signalled",
        "boarded",
        "labels_updated",
        "ignored_no_item_key",
        "ignored_not_agent_ready",
    }
)


def _short(value: Any, limit: int = 80) -> str:
    return " ".join(str(value).split())[:limit]


def summary_of(event: InboundEvent) -> str:
    """One line from the event's typed fields: never free text a person wrote."""
    payload = event.payload or {}
    parts = [str(event.type)]
    if event.work_item_key:
        parts.append(_short(event.work_item_key))
    label = payload.get("label")
    if isinstance(label, str) and label:
        parts.append(f"label {'removed' if payload.get('removed') else 'added'}: {_short(label)}")
    if payload.get("from_status") or payload.get("to_status"):
        parts.append(
            f"{_short(payload.get('from_status') or '?')} → {_short(payload.get('to_status') or '?')}"
        )
    conclusion = payload.get("conclusion")
    if isinstance(conclusion, str) and conclusion:
        parts.append(f"conclusion {_short(conclusion)}")
    for field in ("env", "health", "alertname", "severity"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            parts.append(f"{field} {_short(value, 40)}")
    numbers = [n for n in payload.get("pr_numbers") or [] if isinstance(n, int)]
    if numbers:
        parts.append("PR " + ", ".join(f"#{n}" for n in numbers[:5]))
    return " · ".join(parts)[:300]


def record(
    session: Any, project: Project, event: InboundEvent, outcome: str, item: WorkItem | None = None
) -> None:
    """Adds the row to the webhook's session: it commits, or rolls back, with what the event did."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome `{outcome}`")
    session.add(
        ReceivedEvent(
            org_id=project.org_id,
            project_id=project.id,
            work_item_id=item.id if item is not None else None,
            work_item_key=_short(event.work_item_key, 255) if event.work_item_key else None,
            source=_short(event.source, 160),
            event_type=_short(event.type, 80),
            delivery_id=_short(event.delivery_id, 255) or None,
            outcome=outcome,
            summary=summary_of(event),
            received_at=utcnow(),
        )
    )
