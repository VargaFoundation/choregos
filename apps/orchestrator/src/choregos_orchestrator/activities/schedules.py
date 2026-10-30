# SPDX-License-Identifier: Apache-2.0
"""Work items the platform opens by itself, on a cron (S38-11).

A project declares `schedules:`; each pass of its reconciliation loop calls `fire_schedules`, which
opens the work item of the current window if it does not exist yet. The window is the last minute the
cron matched, looked for over the past `CATCH_UP`: a loop that was late by a few passes still opens
the window it missed, a loop down for longer skips it rather than open it late.

The key of the work item is the window (`RESEARCH-weekly-20261019T0600Z`), so two firings of the same
window — a retried activity, two passes inside the same hour — find the same key and open one work
item; the `(project_id, tracker_key)` constraint holds even if two ran at once. The work item is
created without starting its interpreter: `reconcile_tracker`, on the same pass, starts it under its
deterministic identifier, the one path every internal work item takes.

Each firing that opens a work item, and each one the workflow refuses, leaves a `received_events`
row (`schedule:<name>`, S51-01): the project's page answers "did the cron fire?" as it does for a
webhook. A window already served writes nothing more.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_api.db.models import ReceivedEvent, WorkItem
from choregos_api.errors import ApiError
from choregos_api.logging import get_logger
from sqlalchemy import select
from temporalio import activity

from .base import db, project_bundle
from .train import _cron_due

logger = get_logger("choregos.schedules")

#: How far back a window is still opened. Beyond, the loop was down: the window is skipped, not run late.
CATCH_UP = timedelta(minutes=60)


def window_start(cron: str, now: datetime) -> datetime | None:
    """The last minute, within `CATCH_UP` of `now`, at which `cron` matched."""
    minute = now.astimezone(UTC).replace(second=0, microsecond=0)
    for back in range(int(CATCH_UP.total_seconds() // 60) + 1):
        candidate = minute - timedelta(minutes=back)
        if _cron_due(cron, candidate):
            return candidate
    return None


def schedule_key(prefix: str, name: str, start: datetime) -> str:
    """The key of a window's work item. Its last segment is not a number: the platform's own
    numbering (`<PREFIX>-<n>`) never counts it."""
    return f"{prefix}-{name}-{start:%Y%m%dT%H%M}Z"


def window_fields(fields: dict[str, Any], start: datetime) -> dict[str, Any]:
    """The schedule's fields, `{window}` in a text value replaced by the window's date: a weekly
    campaign moves its `end` with the week instead of repeating the same test."""
    day = start.date().isoformat()
    return {k: v.replace("{window}", day) if isinstance(v, str) else v for k, v in fields.items()}


@activity.defn(name="fire_schedules")
async def fire_schedules(payload: dict[str, Any]) -> dict[str, Any]:
    """Open the work item of each schedule whose window is open and not yet served."""
    from choregos_api.rbac import SYSTEM
    from choregos_api.schemas.tickets import WorkItemCreate

    # One statement for both: the orchestrator's imports of the API are capped (ADR 0099).
    from choregos_api.services import initiator, received_events
    from choregos_api.services.tickets import cle_de_ticket_interne, creer_un_ticket, le_tracker_est_interne

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else datetime.now(UTC)
    opened: list[str] = []
    served: list[str] = []
    refused: dict[str, str] = {}
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.config.schedules:
            return {"opened": opened, "served": served, "refused": refused}
        if not await le_tracker_est_interne(session, bundle.project):
            logger.warning("schedules ignored: the project's tracker is external", project=bundle.slug)
            return {"opened": opened, "served": served, "skipped": "external tracker"}
        prefix = await cle_de_ticket_interne(session, bundle.project, numerotee=False)
        for schedule in bundle.config.schedules:
            start = window_start(schedule.cron, now)
            if start is None:
                continue
            key = schedule_key(prefix, schedule.name, start)
            exists = (
                await session.execute(
                    select(WorkItem.id).where(
                        WorkItem.project_id == bundle.project.id, WorkItem.tracker_key == key
                    )
                )
            ).scalar_one_or_none()
            if exists is not None:
                served.append(key)
                continue
            try:
                item = await creer_un_ticket(
                    session,
                    SYSTEM,
                    bundle.project,
                    WorkItemCreate(
                        title=f"{schedule.title} — {start:%Y-%m-%d %H:%M} UTC",
                        fields=window_fields(schedule.fields, start),
                        workflow=schedule.workflow,
                        labels=[f"schedule:{schedule.name}"],
                        start=False,
                    ),
                    canal="schedule",
                    cle=key,
                    initiated_by=initiator.initiator("schedule", schedule.name),
                )
            except ApiError as erreur:
                # Fields the workflow refuses, a workflow that does not exist: the schedule is wrong,
                # every retry would be refused the same way, and the other schedules must still fire.
                # Refused before anything is written, so the session stays clean.
                logger.error(
                    "schedule refused", project=bundle.slug, schedule=schedule.name, error=erreur.detail
                )
                refused[schedule.name] = erreur.detail
                if not await _refusal_recorded(session, bundle.project.id, schedule.name, key):
                    _audit(
                        received_events, session, bundle.project, schedule.name, key, "refused", erreur.detail
                    )
                continue
            _audit(
                received_events, session, bundle.project, schedule.name, key, "created_work_item", None, item
            )
            opened.append(key)
            logger.info("schedule fired", project=bundle.slug, schedule=schedule.name, key=key)
    return {"opened": opened, "served": served, "refused": refused}


async def _refusal_recorded(session: Any, project_id: str, name: str, key: str) -> bool:
    """A refused window is retried by every pass of its catch-up hour: its row is written once."""
    return (
        await session.execute(
            select(ReceivedEvent.id).where(
                ReceivedEvent.project_id == project_id,
                ReceivedEvent.source == f"schedule:{name}",
                ReceivedEvent.delivery_id == key,
            )
        )
    ).first() is not None


def _audit(
    received_events: Any,
    session: Any,
    project: Any,
    name: str,
    key: str,
    outcome: str,
    refusal: str | None,
    item: Any = None,
) -> None:
    """The firing's `received_events` row, in the pass's session (S51-01). The refusal's detail is
    the platform's own sentence (the workflow, the field paths), never a field's value."""
    received_events.record_firing(
        session,
        org_id=project.org_id,
        project_id=project.id,
        source=f"schedule:{name}",
        event_type="schedule.fired",
        delivery_id=key,
        outcome=outcome,
        summary=f"schedule.fired · {name}" + (f" · refused: {refusal}" if refusal else ""),
        item_id=item.id if item is not None else None,
        item_key=key,
    )
