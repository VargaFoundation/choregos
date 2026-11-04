"""A real `ReleaseTrain` on the demo `cd` (S21-24): the train of a demo project
promotes, verifies and, when the promotion is broken on purpose, rolls back and freezes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _until(handle: Any, predicate: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        state = await handle.query("status_query")
        if predicate(state):
            return dict(state)
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached: {await handle.query('status_query')}")


def _demo_cd(setup: Fixture) -> Any:
    from choregos_adapters.fakes.demo_cd import DemoCd, FakeDemoCd
    from choregos_adapters.fakes.hr import Desk

    fake = FakeDemoCd()
    setup.adapters.cd = DemoCd(Desk(fake))
    return fake


async def _departure(temporal_env: Any, setup: Fixture) -> Any:
    train = await temporal_env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": "prod"},
        id=f"train-{setup.project_slug}-prod",
        task_queue="test",
    )
    await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "un"})
    await train.signal("depart_now", {"by": "augustin"})
    return train


async def _releases() -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release))).scalars())


async def test_the_train_delivers_on_the_demo_cd(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    fake = _demo_cd(setup)
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _departure(temporal_env, setup)
            await _until(train, lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            await _until(train, lambda s: s["status"] == "collecting" and s["batch_size"] == 0)
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done"
    assert release.promotion_url == "demo://prod/1"
    assert fake.applications["billing-api"]["status"] == "Healthy"


async def test_a_promotion_broken_on_purpose_rolls_the_train_back_and_freezes_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    fake = _demo_cd(setup)
    fake.break_next_rollout()
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _departure(temporal_env, setup)
            await _until(train, lambda s: s["frozen"] is True)
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "rolled_back"
    assert fake.applications["billing-api"]["rollout"] == "Aborted"
