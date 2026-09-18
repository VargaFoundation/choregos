"""`ReleaseTrain` : batch, fenêtres, gel, approbation, canary cassé → rollback + gel."""

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
    raise AssertionError(f"condition jamais atteinte (dernier état : {await handle.query('status_query')})")


async def test_two_merges_make_one_batch(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "un"}
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#2", "sha": "a2", "title": "deux"}
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "un"}
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
    assert len(releases) == 1, "un seul lot pour deux merges rapprochés"
    assert len(releases[0].items) == 2
    assert releases[0].status == "done"
    assert setup.adapters.cd.promotions, "une promotion GitOps a bien eu lieu"


async def test_freeze_blocks_departure(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal("freeze", {"reason": "incident en cours", "by": "marie"})
            await handle.signal("merged", {"work_item_key": "varga/billing-api#3", "sha": "b1"})
            await handle.signal("depart_now", {"by": "augustin"})
            status = await _wait(handle, lambda s: s["frozen"] is True)
            assert status["freeze_reason"] == "incident en cours"
            await asyncio.sleep(0.5)
            assert not setup.adapters.cd.promotions, "un train gelé ne promeut rien"
            await handle.signal("unfreeze", {"by": "marie"})
            await _wait(handle, lambda s: s["frozen"] is False, 30)
            await handle.signal("abort", {"by": "test"})
            await handle.result()


async def test_broken_canary_rolls_back_and_freezes(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Canary cassé → abandon du rollout, release `rolled_back`, incident, train gelé."""
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
    assert any(f.severity == "critical" for f in findings), "un rollback laisse une trace critique"
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
            assert status["pending_items"] == ["varga/billing-api#5"], "le lot repart au prochain tour"
            await handle.signal("abort", {"by": "test"})
            await handle.result()


async def test_hotfix_uses_express_lane(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """Un `hotfix` part hors horaire, avec un soak réduit."""
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup)
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#6", "sha": "e1", "labels": ["hotfix"]}
            )
            await _wait(handle, lambda s: s["status"] == "awaiting_approval", 60)
            await handle.signal("approve", {"by": "marie"})
            await _wait(handle, lambda s: s["status"] == "collecting", 60)
            await handle.signal("abort", {"by": "test"})
            await handle.result()
    assert setup.adapters.cd.promotions


async def test_auto_sync_env_has_no_train(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await start_train(temporal_env, setup, env_name="dev")
            await _wait(handle, lambda s: s["status"] == "auto_sync", 30)
            await handle.signal("abort", {"by": "test"})
            result = await handle.result()
    assert result["status"] == "auto_sync"
    assert not setup.adapters.cd.promotions
