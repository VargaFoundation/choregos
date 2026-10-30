"""Scheduled work items (S38-11): a project's cron opens one work item per window, never two."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from choregos_orchestrator.activities import schedules
from choregos_orchestrator.activities import tracker as tracker_activities
from choregos_orchestrator.train_client import clear_fake_signals, fake_starts

from .conftest import Fixture, recording_history

pytestmark = pytest.mark.asyncio

#: A Monday, at the minute the weekly schedule fires.
MONDAY = datetime(2026, 10, 19, 6, 0, 30, tzinfo=UTC)
WEEKLY = {"name": "weekly", "cron": "0 6 * * mon", "title": "Weekly campaign"}


async def _schedule(setup: Fixture, *declared: dict[str, Any]) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        project.config = {**project.config, "schedules": list(declared)}


async def _fire(setup: Fixture, now: datetime) -> dict[str, Any]:
    return await schedules.fire_schedules({"project_slug": setup.project_slug, "now": now.isoformat()})


async def _scheduled_items(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == setup.project_id, WorkItem.tracker_key.like("%-weekly-%")
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def test_two_firings_in_the_same_window_open_one_work_item(setup: Fixture) -> None:
    assert MONDAY.weekday() == 0
    await _schedule(setup, WEEKLY)

    first = await _fire(setup, MONDAY)
    again = await _fire(setup, MONDAY.replace(minute=40))

    assert first["opened"] == ["BILLING-API-weekly-20261019T0600Z"], first
    assert again["opened"] == [] and again["served"] == first["opened"], again
    (item,) = await _scheduled_items(setup)
    assert item.title == "Weekly campaign — 2026-10-19 06:00 UTC"
    assert item.created_by == "system@choregos"
    assert (item.documents or {}).get("labels") == ["schedule:weekly"]
    assert item.temporal_wf_id is None, "the reconciliation pass starts it, under its deterministic id"


async def test_the_next_window_opens_the_next_work_item(setup: Fixture) -> None:
    await _schedule(setup, WEEKLY)

    await _fire(setup, MONDAY)
    nxt = await _fire(setup, MONDAY.replace(day=26, minute=3))

    assert nxt["opened"] == ["BILLING-API-weekly-20261026T0600Z"], nxt
    assert len(await _scheduled_items(setup)) == 2


async def test_a_window_missed_for_more_than_the_catch_up_is_skipped(setup: Fixture) -> None:
    """The loop was down from 06:00 to 07:30: the campaign is not run late, the next one is."""
    await _schedule(setup, WEEKLY)

    late = await _fire(setup, MONDAY.replace(hour=7, minute=30))
    tuesday = await _fire(setup, MONDAY.replace(day=20))

    assert late["opened"] == [] and tuesday["opened"] == []
    assert await _scheduled_items(setup) == []


async def test_the_reconciliation_pass_starts_what_the_schedule_opened(setup: Fixture) -> None:
    from choregos_adapters.tracker.interne import InternalTracker

    clear_fake_signals()
    setup.adapters.tracker = InternalTracker()
    await _schedule(setup, WEEKLY)

    await _fire(setup, MONDAY)
    started = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    again = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert "BILLING-API-weekly-20261019T0600Z" in started["started"], started
    assert again["started"] == []
    assert sum("weekly" in workflow_id for workflow_id in fake_starts()) == 1, fake_starts()


async def test_a_scheduled_key_does_not_move_the_numbering(setup: Fixture) -> None:
    """`<PREFIX>-<n>` counts numbers: the window's key ends with a letter and is not one."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.tickets import cle_de_ticket_interne

    await _schedule(setup, WEEKLY)
    await _fire(setup, MONDAY)

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        assert await cle_de_ticket_interne(session, project) == "BILLING-API-1"


async def test_a_refused_schedule_does_not_stop_the_others(setup: Fixture) -> None:
    """Fields the workflow does not declare: refused, said, and the next schedule still fires."""
    await _schedule(
        setup,
        {"name": "broken", "cron": "0 6 * * mon", "title": "Broken", "fields": {"nope": 1}},
        WEEKLY,
    )

    fired = await _fire(setup, MONDAY)

    assert list(fired["refused"]) == ["broken"], fired
    assert fired["opened"] == ["BILLING-API-weekly-20261019T0600Z"], fired


async def test_an_external_tracker_opens_nothing(setup: Fixture) -> None:
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope

    await _schedule(setup, WEEKLY)
    async with session_scope() as session:
        session.add(Connector(project_id=setup.project_id, kind="tracker", type="github", config={}))

    fired = await _fire(setup, MONDAY)

    assert fired["opened"] == [] and fired["skipped"] == "external tracker", fired


def test_a_window_moves_the_fields_that_ask_for_it() -> None:
    fields = schedules.window_fields({"end": "{window}", "start": "2018-01-01", "n": 3}, MONDAY)

    assert fields == {"end": "2026-10-19", "start": "2018-01-01", "n": 3}


def test_a_cron_has_five_fields() -> None:
    from choregos_contracts import ScheduleConfig
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="five fields"):
        ScheduleConfig(name="weekly", cron="0 6 * *", title="Weekly")


async def test_each_pass_fires_the_schedules_before_it_reconciles(temporal_env: Any) -> None:
    """The work item a schedule opens is started by the same pass, not the next one."""
    import uuid

    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows.reconciliation import TrackerReconciliation
    from temporalio import activity
    from temporalio.worker import Replayer, Worker

    calls: list[str] = []

    @activity.defn(name="fire_schedules")
    async def fire(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append("fire")
        return {"opened": ["DEV-weekly-20261019T0600Z"], "served": [], "refused": {}}

    @activity.defn(name="reconcile_tracker")
    async def reconcile(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append("reconcile")
        return {"created": [], "started": ["DEV-weekly-20261019T0600Z"]}

    client = temporal_env.client
    async with (
        Worker(client, task_queue="tracker", activities=[fire, reconcile]),
        Worker(client, task_queue="test", workflows=[TrackerReconciliation]),
    ):
        handle = await client.start_workflow(
            "TrackerReconciliation",
            {"project_slug": "dev", "interval_seconds": 1},
            id=f"reconcile-{uuid.uuid4()}",
            task_queue="test",
        )
        status: dict[str, Any] = {}
        for _ in range(200):
            status = await handle.query("status")
            if status["passes"] >= 2:
                break
            await temporal_env.sleep(1)
        await handle.signal("stop")
        await handle.result()
        history = await handle.fetch_history()

    # Archived for `tests/replay` (`CHOREGOS_RECORD_HISTORY=1`): a loop recorded with the
    # schedules must keep replaying, like the ones recorded before them (`workflow.patched`).
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        histories = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
        target = histories / "reconcile-schedules-S38-11.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
    assert calls[:4] == ["fire", "reconcile", "fire", "reconcile"], calls
    assert status["fired"]["opened"] == ["DEV-weekly-20261019T0600Z"], status
