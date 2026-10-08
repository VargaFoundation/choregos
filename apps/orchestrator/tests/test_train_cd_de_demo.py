"""Un vrai `ReleaseTrain` sur le `cd` de démonstration (S21-24) : le train d'un projet de démonstration
promeut, vérifie et, quand la promotion est cassée exprès, revient en arrière et se gèle."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _jusqu_a(handle: Any, predicat: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        etat = await handle.query("status_query")
        if predicat(etat):
            return dict(etat)
        await asyncio.sleep(0.1)
    raise AssertionError(f"jamais atteint : {await handle.query('status_query')}")


def _cd_de_demo(setup: Fixture) -> Any:
    from choregos_adapters.fakes.cd_de_demo import CdDeDemo, FakeCdDeDemo
    from choregos_adapters.fakes.rh import Guichet

    faux = FakeCdDeDemo()
    setup.adapters.cd = CdDeDemo(Guichet(faux))
    return faux


async def _depart(temporal_env: Any, setup: Fixture) -> Any:
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


async def test_le_train_livre_sur_le_cd_de_demonstration(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    faux = _cd_de_demo(setup)
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _depart(temporal_env, setup)
            await _jusqu_a(train, lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            await _jusqu_a(train, lambda s: s["status"] == "collecting" and s["batch_size"] == 0)
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done"
    assert release.promotion_url == "demo://prod/1"
    assert faux.applications["billing-api"]["status"] == "Healthy"


async def test_une_promotion_cassee_expres_ramene_le_train_en_arriere_et_le_gele(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    faux = _cd_de_demo(setup)
    faux.break_next_rollout()
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _depart(temporal_env, setup)
            await _jusqu_a(train, lambda s: s["frozen"] is True)
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "rolled_back"
    assert faux.applications["billing-api"]["rollout"] == "Aborted"
