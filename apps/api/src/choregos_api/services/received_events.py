# SPDX-License-Identifier: Apache-2.0
"""The webhook events a project received, and what the platform did with each (S42-06f).

Since S51-01 (ADR 0113) every door that starts work without a person in front of it leaves its row
here too: an HTTP trigger (`trigger:<name>`) and a schedule (`schedule:<name>`), including the calls
the platform refused, so that "why did nothing start?" has an answer on the project's page.

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
        "ignored",
        "already_open",
        "refused",
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
    record_firing(
        session,
        org_id=project.org_id,
        project_id=project.id,
        source=event.source,
        event_type=str(event.type),
        delivery_id=event.delivery_id,
        outcome=outcome,
        summary=summary_of(event),
        item_id=item.id if item is not None else None,
        item_key=event.work_item_key,
    )


def record_firing(  # noqa: PLR0913 - one row, field by field, keyword-only
    session: Any,
    *,
    org_id: str,
    project_id: str,
    source: str,
    event_type: str,
    delivery_id: str | None,
    outcome: str,
    summary: str,
    item_id: str | None = None,
    item_key: str | None = None,
) -> None:
    """The row itself, for a door that has no `InboundEvent` (a trigger, a schedule, S51-01). The
    caller builds `summary` from what it decided — never from the payload's free text."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome `{outcome}`")
    session.add(
        ReceivedEvent(
            org_id=org_id,
            project_id=project_id,
            work_item_id=item_id,
            work_item_key=_short(item_key, 255) if item_key else None,
            source=_short(source, 160),
            event_type=_short(event_type, 80),
            delivery_id=_short(delivery_id, 255) or None if delivery_id else None,
            outcome=outcome,
            summary=_short(summary, 300),
            received_at=utcnow(),
        )
    )
