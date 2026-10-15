"""An agent's monthly cap: alerts at 50, 80 and 100 %, and a hard stop at 100 % (S27-09)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .test_budget_alerts import _alerts, _spend


async def _agent(client: AsyncClient, monthly: float | None) -> None:
    budget = {"monthly_usd": monthly} if monthly else {}
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": "coordinator", "display_name": "Coordinator", "spec": {"budget": budget}},
    )
    assert created.status_code == 201, created.text


async def _status(client: AsyncClient) -> str:
    response = await client.get("/api/v1/orgs/varga/agents/coordinator")
    return str(response.json()["status"])


async def test_the_monthly_cap_alerts_at_50_80_100_once_each_and_suspends_at_100(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client, monthly=10)
    await _spend(project["id"], 4.0, agent="coordinator")
    assert await _alerts(client) == [] and await _status(client) == "active"

    await _spend(project["id"], 1.5, agent="coordinator")  # 5.5: half
    await _spend(project["id"], 2.6, agent="coordinator")  # 8.1: 80 %
    assert sorted(a["threshold"] for a in await _alerts(client)) == [50, 80]
    assert await _status(client) == "active", "nothing is stopped before the cap"

    await _spend(project["id"], 2.0, agent="coordinator")  # 10.1: the cap
    await _spend(project["id"], 1.0, agent="coordinator")  # still the same three alerts
    alerts = await _alerts(client)
    assert sorted(a["threshold"] for a in alerts) == [50, 80, 100]
    assert {a["period"] for a in alerts} == {"month"}
    assert await _status(client) == "suspended"


async def test_an_agent_without_a_monthly_cap_is_never_suspended(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent(client, monthly=None)
    await _spend(project["id"], 100.0, agent="coordinator")  # under the project's own day budget
    assert await _alerts(client) == [] and await _status(client) == "active"
