"""Le rapport A/B part une fois par semaine, et il est posté là où les humains le lisent."""

from __future__ import annotations

from datetime import timedelta
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
    assert report["verdict"] == "échantillon insuffisant", "trois tickets ne prouvent rien"
    titres = [message.title for _, message in setup.adapters.notify.sent]
    assert any("rapport A/B" in titre for titre in titres), titres


async def test_sans_projet_mesurable_rien_n_est_poste(setup: Fixture) -> None:
    report = await memory_activities.memory_ab_report({"org": "varga", "weeks": 4, "notify": True})
    assert report["groups"]["with_memory"]["tickets"] == 0
    assert setup.adapters.notify.sent == []


async def test_le_rapport_ne_part_qu_une_fois_par_semaine(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Deux cycles d'ingestion dans la même semaine ISO : un seul rapport."""
    await _closed_tickets(setup.project_id, count=2, attempts=1, cost=1.0)
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
    rapports = [m.title for _, m in setup.adapters.notify.sent if "rapport A/B" in m.title]
    assert len(rapports) == 1, rapports
