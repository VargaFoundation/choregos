"""What the spend screen reads beyond the report itself (S29-03): the organisation's currency
(decision D4: an org setting, EUR by default) and the organisation's daily budget."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def test_the_currency_is_an_organisation_setting_eur_by_default(
    client: AsyncClient, admin: str
) -> None:
    orgs = (await client.get("/api/v1/orgs")).json()
    assert [(o["slug"], o["currency"]) for o in orgs] == [("varga", "EUR")]
    assert (await client.get("/api/v1/orgs/varga/costs")).json()["currency"] == "EUR"
    changed = await client.patch("/api/v1/orgs/varga", json={"currency": "USD"})
    assert changed.status_code == 200 and changed.json()["currency"] == "USD"
    assert (await client.get("/api/v1/orgs/varga/costs")).json()["currency"] == "USD"
    assert (await client.patch("/api/v1/orgs/varga", json={"currency": "GBP"})).status_code == 422
    assert (await client.patch("/api/v1/orgs/varga", json={"name": "Varga"})).json()["currency"] == "USD", (
        "renaming keeps the setting"
    )


async def test_the_project_report_follows_the_currency(client: AsyncClient, project: dict[str, Any]) -> None:
    url = f"/api/v1/projects/{project['id']}/costs"
    assert (await client.get(url)).json()["currency"] == "EUR"
    await client.patch("/api/v1/orgs/varga", json={"currency": "USD"})
    assert (await client.get(url)).json()["currency"] == "USD"


async def test_the_organisation_budget_is_its_projects_daily_budgets_summed(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    default = (await client.get("/api/v1/orgs/varga/costs")).json()["total"]["budget_usd"]
    assert default == 150.0, "the default policy's daily budget"
    policy = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()["yaml"]
    assert "daily_project_usd: 150" in policy
    edited = policy.replace("daily_project_usd: 150", "daily_project_usd: 25", 1)
    put = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": edited})
    assert put.status_code == 200, put.text
    assert (await client.get("/api/v1/orgs/varga/costs")).json()["total"]["budget_usd"] == 25.0
