"""Who may acknowledge a budget alert (decision of 2026-10-16): a project owner or above.

Acknowledging takes the alert out of everyone's inbox, so a developer or a release captain, who can
decide a ticket but not change a budget, may not. A project's alert is checked against that project;
an agent's spans the organisation and is checked against the organisation role.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .test_budget_alerts import _alerts, _set_daily_budget, _spend
from .test_knowledge_project_owned import _as, _member


async def _project_alert(client: AsyncClient, project: dict[str, Any]) -> str:
    await _set_daily_budget(client, project, 10)
    await _spend(project["id"], 9.0)
    (alerte,) = await _alerts(client)
    return str(alerte["id"])


async def _agent_alert(client: AsyncClient, project: dict[str, Any]) -> str:
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": "coordinator", "display_name": "Coordinator", "spec": {"budget": {"daily_usd": 4}}},
    )
    assert created.status_code == 201, created.text
    await _spend(project["id"], 3.5, agent="coordinator")
    (alerte,) = await _alerts(client)
    assert alerte["scope"] == "agent"
    return str(alerte["id"])


@pytest.mark.parametrize("role", ["developer", "release_captain", "viewer"])
async def test_an_organisation_role_below_project_owner_cannot_acknowledge(
    client: AsyncClient, project: dict[str, Any], role: str
) -> None:
    alert = await _project_alert(client, project)
    await _member(f"{role}@varga.dev", role)
    async with _as(client, f"{role}@varga.dev") as member:
        refused = await member.post(f"/api/v1/orgs/varga/budget-alerts/{alert}/acknowledge")
        assert refused.status_code == 403, refused.text
        # They still see it: reading the inbox is every member's.
        assert [a["id"] for a in (await member.get("/api/v1/orgs/varga/budget-alerts")).json()] == [alert]
    assert [a["status"] for a in await _alerts(client)] == ["open"], "a refusal leaves it in the inbox"


async def test_the_owner_of_the_project_acknowledges_its_alert(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    alert = await _project_alert(client, project)
    await _member("lea@varga.dev", "developer")
    await _member("lea@varga.dev", "project_owner", project["slug"])
    async with _as(client, "lea@varga.dev") as owner:
        done = await owner.post(f"/api/v1/orgs/varga/budget-alerts/{alert}/acknowledge")
    assert done.status_code == 200, done.text
    assert done.json()["acknowledged_by"] == "lea@varga.dev"


async def test_owning_one_project_does_not_acknowledge_an_agents_alert(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    alert = await _agent_alert(client, project)
    await _member("lea@varga.dev", "developer")
    await _member("lea@varga.dev", "project_owner", project["slug"])
    async with _as(client, "lea@varga.dev") as owner:
        refused = await owner.post(f"/api/v1/orgs/varga/budget-alerts/{alert}/acknowledge")
    assert refused.status_code == 403, refused.text
    await _member("max@varga.dev", "project_owner")
    async with _as(client, "max@varga.dev") as org_owner:
        done = await org_owner.post(f"/api/v1/orgs/varga/budget-alerts/{alert}/acknowledge")
    assert done.status_code == 200, done.text
