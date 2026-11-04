"""Delivery metrics: DORA computed on the deployments, CSV export of the costs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient


async def _seed_releases(project_id: str) -> None:
    """Three production releases: two successful, one rolled back then restored."""
    from choregos_api.db.models import Deployment, Release
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    now = utcnow()
    plan = [
        # (days before now, status, hours between merge and the end of the deployment)
        (20, "succeeded", 6.0),
        (10, "rolled_back", None),
        (10, "succeeded", 30.0),  # the restoration, 3 h after the rollback
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


async def test_dora_is_computed_on_the_deployments(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed_releases(project["id"])
    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/dora")).json()

    assert report["env"] == "prod"
    assert report["deployments"] == 2, "only the successful production releases count"
    assert report["deployment_frequency"]["value"] == pytest.approx(2 / 30, abs=0.01)
    assert report["lead_time"]["value"] == pytest.approx(18.0, abs=0.5), "median of 6 h and 30 h"
    assert report["change_failure_rate"]["value"] == pytest.approx(1 / 3, abs=0.01)
    assert report["change_failure_rate"]["level"] == "low"
    assert report["time_to_restore"]["sample"] == 1
    assert report["time_to_restore"]["value"] is not None


async def test_dora_without_a_deployment_rates_nothing(client: AsyncClient, project: dict[str, Any]) -> None:
    """No data allows no verdict: `unknown`, not `low`."""
    report = (await client.get(f"/api/v1/projects/{project['id']}/metrics/dora")).json()
    assert report["deployments"] == 0
    for metric in ("deployment_frequency", "lead_time", "change_failure_rate", "time_to_restore"):
        assert report[metric]["level"] == "unknown", metric
        assert report[metric]["sample"] == 0


async def test_dora_filters_by_environment(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed_releases(project["id"])
    staging = (
        await client.get(f"/api/v1/projects/{project['id']}/metrics/dora", params={"env": "staging"})
    ).json()
    assert staging["deployments"] == 0


async def test_csv_export_of_the_costs(client: AsyncClient, project: dict[str, Any]) -> None:
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
    assert body.startswith("﻿"), "the BOM keeps Excel from breaking the accents"
    lines = [line for line in body.splitlines() if line]
    assert (
        lines[0].lstrip("﻿").startswith("stage;cout_usd;cout_eur")
    )  # lang: fr-ok (the CSV header is still French)
    assert any(line.startswith("implement;0.500000;0.460000;1200;340;0;1") for line in lines)
    assert any(line.startswith("total;0.500000") for line in lines)


async def test_the_csv_export_respects_the_rights(client: AsyncClient, project: dict[str, Any]) -> None:
    """An export is a read of the project: without a session, no CSV."""
    client.cookies.clear()
    response = await client.get(f"/api/v1/projects/{project['id']}/costs.csv")
    assert response.status_code == 401
