"""Métriques de livraison : DORA calculé sur les déploiements, export CSV des coûts."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient


async def _seed_releases(project_id: str) -> None:
    """Trois mises en production : deux réussies, une annulée puis rétablie."""
    from choregos_api.db.models import Deployment, Release
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    now = utcnow()
    plan = [
        # (jours avant maintenant, statut, heures entre fusion et fin de déploiement)
        (20, "succeeded", 6.0),
        (10, "rolled_back", None),
        (10, "succeeded", 30.0),  # le rétablissement, 3 h après le rollback
    ]
    async with session_scope() as session:
        for index, (days, status, lead_hours) in enumerate(plan):
            started = now - timedelta(days=days) + timedelta(hours=index)
            ended = started + timedelta(minutes=20)
            merged = ended - timedelta(hours=lead_hours) if lead_hours else ended
            release = Release(
                project_id=project_id,
                env="prod",
                batch_no=index + 1,
                status="done" if status == "succeeded" else "rolled_back",
                items=[{"work_item_key": f"varga/billing-api#{index}", "merged_at": merged.isoformat()}],
                started_at=started,
                ended_at=ended,
            )
            session.add(release)
            await session.flush()
            session.add(
                Deployment(
                    release_id=release.id,
                    env="prod",
                    revision=f"R-{index + 1}",
                    status=status,
                    started_at=started,
                    ended_at=ended if status != "rolled_back" else started + timedelta(hours=1),
                )
            )


async def test_dora_se_calcule_sur_les_deploiements(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed_releases(project["id"])
    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/dora")).json()

    assert report["env"] == "prod"
    assert report["deployments"] == 2, "seules les mises en production réussies comptent"
    assert report["deployment_frequency"]["value"] == pytest.approx(2 / 30, abs=0.01)
    assert report["lead_time"]["value"] == pytest.approx(18.0, abs=0.5), "médiane de 6 h et 30 h"
    assert report["change_failure_rate"]["value"] == pytest.approx(1 / 3, abs=0.01)
    assert report["change_failure_rate"]["level"] == "low"
    assert report["time_to_restore"]["sample"] == 1
    assert report["time_to_restore"]["value"] is not None


async def test_dora_sans_deploiement_ne_note_rien(client: AsyncClient, project: dict[str, Any]) -> None:
    """Aucune donnée n'autorise aucun verdict : `unknown`, pas `low`."""
    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/dora")).json()
    assert report["deployments"] == 0
    for metric in ("deployment_frequency", "lead_time", "change_failure_rate", "time_to_restore"):
        assert report[metric]["level"] == "unknown", metric
        assert report[metric]["sample"] == 0


async def test_dora_filtre_par_environnement(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed_releases(project["id"])
    staging = (
        await client.get(f"/api/v1/projects/{project['id']}/metrics/dora", params={"env": "staging"})
    ).json()
    assert staging["deployments"] == 0


async def test_export_csv_des_couts(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        session.add(
            CostLedger(
                project_id=project["id"],
                provider="anthropic",
                model="platform/standard",
                stage_role="implement",
                size="M",
                tokens_in=1200,
                tokens_out=340,
                cost_usd=0.5,
                cost_eur=0.46,
                fx_rate=0.92,
                ts=utcnow(),
            )
        )

    response = await client.get(f"/api/v1/projects/{project['id']}/costs.csv", params={"group_by": "stage"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("﻿"), "le BOM évite qu'Excel casse les accents"
    lignes = [ligne for ligne in body.splitlines() if ligne]
    assert lignes[0].lstrip("﻿").startswith("stage;cout_usd;cout_eur")
    assert any(ligne.startswith("implement;0.500000;0.460000;1200;340;0;1") for ligne in lignes)
    assert any(ligne.startswith("total;0.500000") for ligne in lignes)


async def test_l_export_csv_respecte_les_droits(client: AsyncClient, project: dict[str, Any]) -> None:
    """Un export est une lecture du projet : sans session, pas de CSV."""
    client.cookies.clear()
    response = await client.get(f"/api/v1/projects/{project['id']}/costs.csv")
    assert response.status_code == 401
