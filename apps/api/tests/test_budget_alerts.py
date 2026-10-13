"""A budget crossing 80 % or 100 % lands in the inbox, once per threshold and day (S29-04)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


def test_crossed_says_which_thresholds_a_spend_has_reached() -> None:
    from choregos_api.services.budget_alerts import crossed, thresholds

    ratios = thresholds(0.8)
    assert ratios == (0.8, 1.0)
    assert crossed(7.9, 10, ratios) == []
    assert crossed(8.0, 10, ratios) == [80]
    assert crossed(10.0, 10, ratios) == [80, 100]
    assert crossed(99, None, ratios) == [] and crossed(99, 0, ratios) == [], "no budget, nothing to cross"
    assert thresholds(None) == (1.0,) and thresholds(1.0) == (1.0,)
    assert crossed(5, 10, thresholds(0.5)) == [50]


async def _set_daily_budget(client: AsyncClient, project: dict[str, Any], usd: float) -> None:
    policy = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()["yaml"]
    edited = policy.replace("daily_project_usd: 150", f"daily_project_usd: {usd}", 1)
    assert (
        await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": edited})
    ).status_code == 200


async def _spend(project_id: str, usd: float, *, agent: str | None = None, run_id: str = "r-budget") -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.couts import record_cost
    from sqlalchemy import select

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        if run is None:
            item = (await session.execute(select(WorkItem))).scalars().first()
            if item is None:
                item = WorkItem(project_id=project_id, tracker_key="B-1", title="T", state="ready")
                session.add(item)
                await session.flush()
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project_id,
                    stage_role="implement",
                    status="running",
                    agent_slug=agent,
                    agent_version=1 if agent else None,
                )
            )
            await session.flush()
        await record_cost(
            session,
            project_id=project_id,
            work_item_id=None,
            run_id=run_id,
            provider="p",
            model="m",
            backend=None,
            stage_role="implement",
            size=None,
            tokens_in=1,
            tokens_out=1,
            tokens_cached=0,
            cost_usd=usd,
            fx_rate=0.9,
        )


async def _alerts(client: AsyncClient, status: str = "open") -> list[dict[str, Any]]:
    response = await client.get("/api/v1/orgs/varga/budget-alerts", params={"status": status})
    assert response.status_code == 200, response.text
    return list(response.json())


async def test_crossing_80_then_100_percent_raises_one_item_each_and_never_twice(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _set_daily_budget(client, project, 10)
    await _spend(project["id"], 5.0)
    assert await _alerts(client) == [], "50 % is below every threshold"

    await _spend(project["id"], 3.5)  # 8.5 / 10
    (alerte,) = await _alerts(client)
    assert (alerte["scope"], alerte["subject"], alerte["threshold"]) == ("project", "billing-api", 80)
    assert (alerte["spent_usd"], alerte["budget_usd"], alerte["status"]) == (8.5, 10.0, "open")

    await _spend(project["id"], 0.2)  # 8.7: still the same alert, not a second one
    assert [a["threshold"] for a in await _alerts(client)] == [80]

    await _spend(project["id"], 2.0)  # 10.7: the budget itself
    assert sorted(a["threshold"] for a in await _alerts(client)) == [80, 100]
    await _spend(project["id"], 1.0)
    assert len(await _alerts(client)) == 2, "idempotent per threshold and day"


async def test_an_agents_daily_budget_raises_its_own_alert(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": "coordinator", "display_name": "Coordinator", "spec": {"budget": {"daily_usd": 4}}},
    )
    assert created.status_code == 201, created.text
    await _spend(project["id"], 3.5, agent="coordinator")
    (alerte,) = await _alerts(client)
    assert (alerte["scope"], alerte["subject"], alerte["threshold"]) == ("agent", "coordinator", 80)
    await _spend(project["id"], 1.0, agent="coordinator")
    assert sorted((a["scope"], a["threshold"]) for a in await _alerts(client)) == [
        ("agent", 80),
        ("agent", 100),
    ]


async def test_a_project_without_a_budget_raises_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _spend(project["id"], 100.0)  # the default daily budget is 150
    assert await _alerts(client) == []


async def test_acknowledging_removes_it_from_the_inbox_and_is_audited(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _set_daily_budget(client, project, 10)
    await _spend(project["id"], 9.0)
    (alerte,) = await _alerts(client)
    done = await client.post(f"/api/v1/orgs/varga/budget-alerts/{alerte['id']}/acknowledge")
    assert done.status_code == 200 and done.json()["acknowledged_by"] == "admin@varga.dev"
    assert await _alerts(client) == []
    assert [a["status"] for a in await _alerts(client, "acknowledged")] == ["acknowledged"]
    assert (
        await client.post(f"/api/v1/orgs/varga/budget-alerts/{alerte['id']}/acknowledge")
    ).status_code == 409
    assert (await client.post("/api/v1/orgs/varga/budget-alerts/nope/acknowledge")).status_code == 404
    audit = (await client.get("/api/v1/audit", params={"target_type": "budget_alert"})).json()["items"]
    assert [e["action"] for e in audit] == ["budget_alert.acknowledge"]
    # Another spend above the threshold does not bring it back: it was acknowledged for the day.
    await _spend(project["id"], 0.5)
    assert await _alerts(client) == []


@pytest.mark.parametrize("status", ["bogus"])
async def test_an_unknown_status_filter_is_refused(client: AsyncClient, admin: str, status: str) -> None:
    assert (
        await client.get("/api/v1/orgs/varga/budget-alerts", params={"status": status})
    ).status_code == 422
