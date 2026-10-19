"""Le rapport A/B part une fois par semaine, et il est posté là où les humains le lisent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_orchestrator.activities import memory as memory_activities

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _closed_tickets(project_id: str, count: int, attempts: int, cost: float) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        for index in range(count):
            item = WorkItem(
                project_id=project_id,
                tracker_key=f"varga/billing-api#9{index}",
                title=f"clos {index}",
                state="deployed_prod",
                closed_at=utcnow() - timedelta(days=2),
            )
            session.add(item)
            await session.flush()
            for attempt in range(1, attempts + 1):
                session.add(
                    Run(
                        work_item_id=item.id,
                        project_id=project_id,
                        stage_role="implement",
                        attempt=attempt,
                        status="succeeded",
                        cost_usd=cost / attempts,
                    )
                )


async def test_le_rapport_est_poste_sur_le_canal_du_projet(setup: Fixture) -> None:
    await _closed_tickets(setup.project_id, count=3, attempts=1, cost=1.0)

    report = await memory_activities.memory_ab_report({"org": "varga", "weeks": 4, "notify": True})

    assert report["org"] == "varga"
    assert report["verdict"] == "not enough data", "trois tickets ne prouvent rien"
    titres = [message.title for _, message in setup.adapters.notify.sent]
    assert any("A/B report" in titre for titre in titres), titres


async def test_sans_projet_mesurable_rien_n_est_poste(setup: Fixture) -> None:
    report = await memory_activities.memory_ab_report({"org": "varga", "weeks": 4, "notify": True})
    assert report["groups"]["with_memory"]["tickets"] == 0
    assert setup.adapters.notify.sent == []


async def _skip_to(temporal_env: Any, weekday: int, at: timedelta) -> None:
    """#727: move the test server's clock, which starts at wall-clock time, to a fixed weekday.

    `weekday` is ISO (1 = Monday), `at` the time of day (UTC). Without it, a run straddling Monday 00:00 UTC
    sees two ISO weeks and the workflow rightly sends two reports.
    """
    now = await temporal_env.get_current_time()
    days = (weekday - now.isoweekday()) % 7 or 7
    target = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=days) + at
    await temporal_env.sleep(target - now)


async def _ingest_three_cycles(setup: Fixture, temporal_env: Any, worker_factory: Any) -> list[str]:
    async with worker_factory("test"):
        handle = await temporal_env.client.start_workflow(
            "MemoryIngestion",
            {
                "project_slug": setup.project_slug,
                "interval_minutes": 1,
                "max_cycles": 3,
                "org": "varga",
            },
            id=f"mem-{setup.project_slug}",
            task_queue="test",
        )
        outcome = await handle.result()

    assert outcome["cycles"] == 3
    return [m.title for _, m in setup.adapters.notify.sent if "A/B report" in m.title]


async def test_le_rapport_ne_part_qu_une_fois_par_semaine(  # lang: fr-ok (test id kept)
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Three ingestion cycles inside one ISO week (a Tuesday noon): one report."""
    await _closed_tickets(setup.project_id, count=2, attempts=1, cost=1.0)
    await _skip_to(temporal_env, weekday=2, at=timedelta(hours=12))

    rapports = await _ingest_three_cycles(setup, temporal_env, worker_factory)

    assert len(rapports) == 1, rapports


async def test_cycles_across_monday_midnight_report_once_per_week(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """#727: cycles at Sunday 23:59:30, Monday 00:00:30 and 00:01:30 UTC span two ISO weeks.

    Two reports. This is the run that made the test above flaky; it is pinned here so
    the clock positioning is proven to decide the count.
    """
    await _closed_tickets(setup.project_id, count=2, attempts=1, cost=1.0)
    await _skip_to(temporal_env, weekday=7, at=timedelta(hours=23, minutes=58, seconds=30))

    rapports = await _ingest_three_cycles(setup, temporal_env, worker_factory)

    assert len(rapports) == 2, rapports


async def test_the_report_without_a_notifier_is_still_returned(setup: Fixture) -> None:
    """#497: a notifier failure (no Slack on the tenant) never fails the report that notifies."""
    await _closed_tickets(setup.project_id, count=3, attempts=1, cost=1.0)
    setup.adapters.notify.panne = (
        "[slack] ni `bot_token` ni `webhook_url` configurés"  # lang: fr-ok (the verbatim upstream error)
    )

    report = await memory_activities.memory_ab_report({"org": "varga", "weeks": 4, "notify": True})

    assert report["org"] == "varga"
    assert report["notified"] is False, report
