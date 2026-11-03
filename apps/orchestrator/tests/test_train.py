"""`ReleaseTrain`: batch, windows, freeze, approval, broken canary -> rollback + freeze."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def start_train(env: Any, setup: Fixture, env_name: str = "prod", queue: str = "test") -> Any:
    return await env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": env_name},
        id=f"train-{setup.project_slug}-{env_name}",
        task_queue=queue,
    )


async def _wait(handle: Any, predicate: Any, timeout: float = 30.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        status = await handle.query("status_query")
        if predicate(status):
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"condition never reached (last state: {await handle.query('status_query')})")


async def test_two_merges_make_one_batch(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "one"}
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#2", "sha": "a2", "title": "two"}
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "one"}
            )
            status = await _wait(handle, lambda s: s["batch_size"] == 2)
            assert status["pending_items"] == ["varga/billing-api#1", "varga/billing-api#2"]

            await handle.signal("depart_now", {"by": "augustin"})
            await _wait(handle, lambda s: s["status"] == "awaiting_approval", 60)
            await handle.signal("approve", {"by": "marie"})
            await _wait(handle, lambda s: s["batch_size"] == 0 and s["status"] == "collecting", 60)
            await handle.signal("abort", {"by": "test"})
            await handle.result()

    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        releases = (await session.execute(select(Release))).scalars().all()
    assert len(releases) == 1, "a single batch for two merges close together"
    assert len(releases[0].items) == 2
    assert releases[0].status == "done"
    assert setup.adapters.cd.promotions, "a GitOps promotion did take place"


async def test_freeze_blocks_departure(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal("freeze", {"reason": "incident in progress", "by": "marie"})
            await handle.signal("merged", {"work_item_key": "varga/billing-api#3", "sha": "b1"})
            await handle.signal("depart_now", {"by": "augustin"})
            status = await _wait(handle, lambda s: s["frozen"] is True)
            assert status["freeze_reason"] == "incident in progress"
            await asyncio.sleep(0.5)
            assert not setup.adapters.cd.promotions, "a frozen train promotes nothing"
            await handle.signal("unfreeze", {"by": "marie"})
            await _wait(handle, lambda s: s["frozen"] is False, 30)
            await handle.signal("abort", {"by": "test"})
            await handle.result()


async def test_broken_canary_rolls_back_and_freezes(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Broken canary -> rollout abandoned, release `rolled_back`, incident, train frozen."""
    setup.adapters.cd.set_health("billing-api", "Healthy")
    setup.adapters.cd.fail_next_analysis()
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal("merged", {"work_item_key": "varga/billing-api#4", "sha": "c1"})
            await handle.signal("depart_now", {"by": "augustin"})
            await _wait(handle, lambda s: s["status"] in {"awaiting_approval", "promoting"}, 60)
            if (await handle.query("status_query"))["status"] == "awaiting_approval":
                await handle.signal("approve", {"by": "marie"})
            await _wait(handle, lambda s: s["frozen"] is True, 60)
            await handle.signal("abort", {"by": "test"})
            await handle.result()

    from choregos_api.db.models import Finding, Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        releases = (await session.execute(select(Release))).scalars().all()
        findings = (await session.execute(select(Finding))).scalars().all()
    assert releases[0].status == "rolled_back"
    assert "billing-api" in setup.adapters.cd.aborted
    assert any(f.severity == "critical" for f in findings), "a rollback leaves a critical trace"
    messages = [m.title for _, m in setup.adapters.notify.sent]
    assert any("Rollback" in title for title in messages)


async def test_rejected_approval_returns_batch(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal("merged", {"work_item_key": "varga/billing-api#5", "sha": "d1"})
            await handle.signal("depart_now", {"by": "augustin"})
            await _wait(handle, lambda s: s["status"] == "awaiting_approval", 60)
            await handle.signal("reject", {"by": "marie"})
            status = await _wait(handle, lambda s: s["status"] == "collecting" and s["batch_size"] == 1, 60)
            assert status["pending_items"] == ["varga/billing-api#5"], (
                "the batch leaves again on the next turn"
            )
            await handle.signal("abort", {"by": "test"})
            await handle.result()


async def test_hotfix_uses_express_lane(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """A `hotfix` leaves outside hours, with a reduced soak."""
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#6", "sha": "e1", "labels": ["hotfix"]}
            )
            await _wait(handle, lambda s: s["status"] == "awaiting_approval", 60)
            # The hotfix left outside hours: production is promoted from the departure.
            assert [p for p in setup.adapters.cd.promotions if p[0] == "prod"]
            assert not setup.adapters.cd.rollouts, "the canary started before the approval"
            await handle.signal("approve", {"by": "marie"})
            # The EFFECT of the approval (the canary followed), not an intermediate state: the train
            # can go back through `collecting` between two polls, and waiting for a state was a
            # race (#135).
            for _ in range(600):
                if setup.adapters.cd.rollouts:
                    break
                await asyncio.sleep(0.1)
            await handle.signal("abort", {"by": "test"})
            await handle.result()
    assert setup.adapters.cd.rollouts, "the approval did not start the canary"


async def test_auto_sync_env_has_no_train(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup, env_name="dev")
            await _wait(handle, lambda s: s["status"] == "auto_sync", 30)
            await handle.signal("abort", {"by": "test"})
            result = await handle.result()
    assert result["status"] == "auto_sync"
    assert not setup.adapters.cd.promotions
