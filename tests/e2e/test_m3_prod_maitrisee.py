"""M3 — la prod maîtrisée : deux tickets dans un lot, canary cassé, rollback, gel."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _train_status(handle: Any, predicate: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        status = await handle.query("status_query")
        if predicate(status):
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"condition jamais atteinte : {await handle.query('status_query')}")


async def test_deux_merges_partent_dans_le_meme_lot(platform: Platform, worker: Any) -> None:
    await login(platform.client)
    with platform.env.auto_time_skipping_disabled():
        async with worker():
            handle = await platform.env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": platform.project_slug, "env": "prod"},
                id=f"train-{platform.project_slug}-prod",
                task_queue="e2e",
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "title": "un"}
            )
            await handle.signal(
                "merged", {"work_item_key": "varga/billing-api#2", "sha": "a2", "title": "deux"}
            )
            await _train_status(handle, lambda status: status["batch_size"] == 2)

            # L'opérateur fait partir le lot depuis l'API.
            depart = await platform.client.post(f"/api/v1/projects/{platform.project_id}/trains/prod/depart")
            assert depart.status_code == 202
            await handle.signal("depart_now", {"by": "augustin@varga.dev"})

            await _train_status(handle, lambda status: status["status"] == "awaiting_approval")
            releases = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/releases")).json()
            release = releases["items"][0]
            assert len(release["items"]) == 2, "un seul lot pour deux merges"

            approval = await platform.client.post(f"/api/v1/releases/{release['id']}/approve", json={})
            assert approval.status_code == 202
            await handle.signal("approve", {"by": "marie@varga.dev"})

            await _train_status(
                handle, lambda status: status["status"] == "collecting" and status["batch_size"] == 0
            )
            await handle.signal("abort", {"by": "test"})
            await handle.result()

    assert platform.adapters.cd.promotions, "la promotion GitOps a eu lieu"
    env, changes, tag = platform.adapters.cd.promotions[0]
    assert env == "prod" and changes[0].app == "billing-api" and tag.startswith("R-")


async def test_canary_casse_declenche_rollback_et_gel(platform: Platform, worker: Any) -> None:
    await login(platform.client)
    platform.adapters.cd.fail_next_analysis()
    with platform.env.auto_time_skipping_disabled():
        async with worker():
            handle = await platform.env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": platform.project_slug, "env": "prod"},
                id=f"train-{platform.project_slug}-prod",
                task_queue="e2e",
            )
            await handle.signal("merged", {"work_item_key": "varga/billing-api#3", "sha": "c1"})
            await handle.signal("depart_now", {"by": "augustin@varga.dev"})
            await _train_status(handle, lambda status: status["status"] == "awaiting_approval")
            await handle.signal("approve", {"by": "marie@varga.dev"})
            await _train_status(handle, lambda status: status["frozen"] is True)

            state = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/trains/prod")).json()
            assert state["frozen"] is True
            await handle.signal("abort", {"by": "test"})
            await handle.result()

    releases = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/releases")).json()
    assert releases["items"][0]["status"] == "rolled_back"
    assert "billing-api" in platform.adapters.cd.aborted, "le rollout a été abandonné"

    findings = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/findings")).json()
    assert any(item["severity"] == "critical" for item in findings["items"]), "l'incident est tracé"
    assert any("Rollback" in message.title for _, message in platform.adapters.notify.sent)


async def test_gel_manuel_bloque_le_depart(platform: Platform, worker: Any) -> None:
    await login(platform.client)
    with platform.env.auto_time_skipping_disabled():
        async with worker():
            handle = await platform.env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": platform.project_slug, "env": "prod"},
                id=f"train-{platform.project_slug}-prod",
                task_queue="e2e",
            )
            frozen = await platform.client.post(
                f"/api/v1/projects/{platform.project_id}/trains/prod/freeze",
                json={"reason": "incident de production en cours"},
            )
            assert frozen.status_code == 202
            await handle.signal("freeze", {"reason": "incident de production en cours", "by": "marie"})
            await handle.signal("merged", {"work_item_key": "varga/billing-api#4", "sha": "d1"})
            await handle.signal("depart_now", {"by": "augustin"})
            status = await _train_status(handle, lambda status: status["frozen"] is True)
            assert status["freeze_reason"] == "incident de production en cours"
            await asyncio.sleep(0.5)
            assert not platform.adapters.cd.promotions, "un train gelé ne promeut rien"
            await handle.signal("abort", {"by": "test"})
            await handle.result()


async def test_le_gel_exige_un_motif(platform: Platform) -> None:
    await login(platform.client)
    response = await platform.client.post(
        f"/api/v1/projects/{platform.project_id}/trains/prod/freeze", json={"reason": ""}
    )
    assert response.status_code == 422, "geler sans motif est refusé par le contrat"
