"""The organisation's policy ceiling (S27-05): a project policy may only tighten it."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

CEILING = {
    "budgets": {"ticket_usd": {"M": 20}, "stage_usd": {"default": {"M": 6}}, "max_turns": {"default": 60},
                "daily_project_usd": 100},
    "attempts": {"default_max": 2},
}  # fmt: skip


def _policy(ticket_m: float = 10, stage_m: float = 5, turns: int = 40, daily: float = 50) -> str:
    return f"""apiVersion: choregos/v1
kind: Policy
metadata: {{ name: tight, version: 1 }}
budgets:
  ticket_usd: {{ S: 5, M: {ticket_m}, L: 15, XL: 20 }}
  stage_usd: {{ default: {{ S: 2, M: {stage_m}, L: 6, XL: 6 }} }}
  max_turns: {{ default: {turns} }}
  daily_project_usd: {daily}
attempts: {{ default_max: 1 }}
"""


async def _ceiling(client: AsyncClient, ceiling: Any = CEILING) -> Any:
    return await client.put("/api/v1/orgs/varga/policy-ceiling", json={"ceiling": ceiling})


async def test_a_policy_wider_than_the_organisation_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The acceptance of S27-05: 422, each widened field named."""
    assert (await _ceiling(client)).status_code == 200
    wider = await client.put(
        f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(ticket_m=30, turns=90, daily=500)}
    )
    assert wider.status_code == 422, wider.text
    detail = wider.json()["detail"]
    assert "budgets.ticket_usd.M: 30 exceeds the organisation's ceiling of 20" in detail
    assert "budgets.max_turns.default: 90 exceeds the organisation's ceiling of 60" in detail
    assert "budgets.daily_project_usd: 500 exceeds the organisation's ceiling of 100" in detail
    assert {tuple(e["loc"]) for e in wider.json()["errors"]} >= {("policy", "budgets", "ticket_usd", "M")}

    within = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy()})
    assert within.status_code == 200, within.text
    assert within.json()["clamps"] == []


async def test_a_policy_above_a_newer_ceiling_runs_clamped_and_says_so(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import policy_engine

    saved = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(ticket_m=30)})
    assert saved.status_code == 200
    assert (await _ceiling(client)).status_code == 200

    read = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()
    assert "ticket_usd: { S: 5, M: 30" in read["yaml"], "what was written stays as written"
    assert read["clamps"] == ["budgets.ticket_usd.M: 30 exceeds the organisation's ceiling of 20"]
    async with session_scope() as session:
        engine = await policy_engine(session, project["id"])
    assert engine.budget_ticket("M") == 20, "what runs is clamped"
    assert engine.budget_ticket("L") == 15


async def test_the_orchestrator_runs_the_clamped_policy(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import project_bundle

    await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(stage_m=9)})
    await _ceiling(client)
    async with session_scope() as session:
        bundle = await project_bundle(session, project["id"])
    assert bundle.engine.stage_usd("implement", "M") == 6


async def test_only_an_org_admin_sets_the_ceiling(client: AsyncClient, admin: str) -> None:
    await client.post("/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"})
    await login(client, "dev@varga.dev")
    assert (await _ceiling(client)).status_code == 403
    assert (await client.get("/api/v1/orgs/varga/policy-ceiling")).json()["ceiling"] is None


@pytest.mark.parametrize(
    "ceiling",
    [
        {"budgets": {"ticket_usd": {"M": "a lot"}}},
        {"budgets": {"unknown_cap": 1}},
        {"attempts": {"default_max": 0}},
    ],
)
async def test_a_ceiling_that_is_not_one_is_refused(client: AsyncClient, admin: str, ceiling: Any) -> None:
    assert (await _ceiling(client, ceiling)).status_code == 422


async def test_a_ceiling_is_read_audited_and_removed(client: AsyncClient, admin: str) -> None:
    set_ = (await _ceiling(client)).json()
    assert set_["ceiling"]["budgets"]["ticket_usd"] == {"M": 20.0}
    assert set_["updated_by"] == "admin@varga.dev" and set_["updated_at"]
    assert (await client.get("/api/v1/orgs/varga/policy-ceiling")).json()["ceiling"] == set_["ceiling"]
    assert (await _ceiling(client, None)).json()["ceiling"] is None
