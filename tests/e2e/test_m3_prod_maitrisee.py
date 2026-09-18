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

    # La ligne de déploiement est close : sans cela, les mesures de livraison ne sauraient
    # pas distinguer une mise en production réussie d'une promotion restée en l'air.
    from choregos_api.db.models import Deployment
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(Deployment))).scalars().all()
        assert [row.status for row in rows] == ["succeeded"]
        assert all(row.ended_at is not None for row in rows)

    dora = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/metrics/dora")).json()
    assert dora["deployments"] == 1
    assert dora["change_failure_rate"]["value"] == 0.0
    assert dora["deployment_frequency"]["level"] != "unknown"


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

    dora = (await platform.client.get(f"/api/v1/projects/{platform.project_id}/metrics/dora")).json()
    assert dora["deployments"] == 0, "un rollback n'est pas une mise en production"
    assert dora["change_failure_rate"]["value"] == 1.0
    assert dora["change_failure_rate"]["level"] == "low"


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


async def test_l_apply_terraform_part_pendant_le_depart_apres_approbation(
    platform: Platform, worker: Any
) -> None:
    """Terraform s'applique dans la fenêtre du train, jamais entre deux départs (S9-05)."""
    await login(platform.client)
    from choregos_core.domain import PrRef, PrState

    infra = "https://github.com/varga/billing-api-infra/pull/9"
    ref = PrRef(repo="varga/billing-api-infra", number=9, url=infra)
    platform.adapters.scm.prs[("varga/billing-api-infra", 9)] = PrState(ref=ref, title="infra")
    platform.adapters.scm.atlantis("success")

    with platform.env.auto_time_skipping_disabled():
        async with worker():
            handle = await platform.env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": platform.project_slug, "env": "prod"},
                id=f"train-{platform.project_slug}-prod",
                task_queue="e2e",
            )
            await handle.signal(
                "merged",
                {"work_item_key": "varga/billing-api#42", "sha": "d1", "infra_pr_url": infra},
            )
            await handle.signal("depart_now", {"by": "augustin@varga.dev"})
            await _train_status(handle, lambda status: status["status"] == "awaiting_approval")

            # Rien n'est appliqué tant que l'approbation n'est pas donnée.
            assert platform.adapters.scm.comments == [], platform.adapters.scm.comments

            await handle.signal("approve", {"by": "marie@varga.dev"})
            await _train_status(handle, lambda status: status["status"] == "collecting")
            await handle.signal("abort", {"by": "test"})
            await handle.result()

    assert ("varga/billing-api-infra", 9, "atlantis apply") in platform.adapters.scm.comments
