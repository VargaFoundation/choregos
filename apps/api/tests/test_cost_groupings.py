"""The cost report, in every grouping, at project and organisation scope, in JSON and CSV (S29-02).

One seeded ledger; each grouping must give the totals it should, and every grouping must add up to
the same total.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

GROUPS = ["day", "stage", "model", "backend", "size", "kind", "agent", "workflow", "work_item", "project"]


async def _seed(project_id: str) -> None:
    from choregos_api.db.models import CostLedger, WorkItem
    from choregos_api.db.session import session_scope

    now = datetime.now(UTC)
    async with session_scope() as session:
        one = WorkItem(project_id=project_id, tracker_key="B-1", title="One", state="ready")
        two = WorkItem(project_id=project_id, tracker_key="B-2", title="Two", state="ready")
        session.add_all([one, two])
        await session.flush()

        def line(item: Any, usd: float, **kw: Any) -> CostLedger:
            return CostLedger(
                project_id=project_id,
                work_item_id=item.id if item else None,
                ts=kw.pop("ts", now),
                cost_usd=usd,
                cost_eur=usd * 0.5,
                tokens_in=10,
                tokens_out=5,
                **kw,
            )

        session.add_all(
            [
                line(one, 4.0, kind="model", model="m-big", backend="claude-code", stage_role="implement",
                     size="M", agent_slug="coordinator", agent_version=1, workflow_name="delivery"),
                line(one, 2.0, kind="model", model="m-small", backend="claude-code", stage_role="review",
                     size="M", agent_slug="reviewer", agent_version=1, workflow_name="delivery"),
                line(two, 1.0, kind="tool", model="crm_export", stage_role="implement", size="S",
                     agent_slug="coordinator", agent_version=1, workflow_name="onboarding"),
                line(None, 3.0, kind="model", model="m-big", stage_role="plan",
                     ts=now - timedelta(days=45)),  # outside the default 30 days
            ]
        )  # fmt: skip


@pytest.fixture
async def seeded(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    await _seed(project["id"])
    return project


def _by_key(report: dict[str, Any]) -> dict[str, float]:
    return {row["key"]: row["cost_usd"] for row in report["rows"]}


EXPECTED = {
    "agent": {"coordinator": 5.0, "reviewer": 2.0},
    "workflow": {"delivery": 6.0, "onboarding": 1.0},
    "work_item": {"B-1": 6.0, "B-2": 1.0},
    "project": {"billing-api": 7.0},
    "kind": {"model": 6.0, "tool": 1.0},
    "model": {"m-big": 4.0, "m-small": 2.0, "crm_export": 1.0},
    "stage": {"implement": 5.0, "review": 2.0},
}


@pytest.mark.parametrize("group", GROUPS)
async def test_every_grouping_adds_up_at_project_scope(
    client: AsyncClient, seeded: dict[str, Any], group: str
) -> None:
    response = await client.get(f"/api/v1/projects/{seeded['id']}/costs", params={"group_by": group})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["group_by"] == group
    assert report["total"]["cost_usd"] == 7.0, "the 45-day-old line is outside the default period"
    assert sum(r["cost_usd"] for r in report["rows"]) == pytest.approx(7.0)
    if group in EXPECTED:
        wanted = EXPECTED[group]
        got = {k: v for k, v in _by_key(report).items() if k != "—"}
        assert got == wanted


@pytest.mark.parametrize("group", GROUPS)
async def test_every_grouping_adds_up_at_organisation_scope(
    client: AsyncClient, seeded: dict[str, Any], group: str
) -> None:
    report = (await client.get("/api/v1/orgs/varga/costs", params={"group_by": group})).json()
    assert report["group_by"] == group
    assert report["total"]["cost_usd"] == 7.0
    if group in EXPECTED:
        assert {k: v for k, v in _by_key(report).items() if k != "—"} == EXPECTED[group]


async def test_a_line_without_an_agent_is_shown_as_such_not_dropped(
    client: AsyncClient, seeded: dict[str, Any]
) -> None:
    window = {"group_by": "agent", "since": (datetime.now(UTC) - timedelta(days=60)).date().isoformat()}
    report = (await client.get(f"/api/v1/projects/{seeded['id']}/costs", params=window)).json()
    assert _by_key(report)["—"] == 3.0 and report["total"]["cost_usd"] == 10.0


async def test_the_period_filters_both_scopes(client: AsyncClient, seeded: dict[str, Any]) -> None:
    today = datetime.now(UTC).date()
    old = {
        "group_by": "agent",
        "since": str(today - timedelta(days=60)),
        "until": str(today - timedelta(days=40)),
    }
    for url in (f"/api/v1/projects/{seeded['id']}/costs", "/api/v1/orgs/varga/costs"):
        report = (await client.get(url, params=old)).json()
        assert report["total"]["cost_usd"] == 3.0, url
    assert (await client.get("/api/v1/orgs/varga/costs", params={"group_by": "bogus"})).status_code == 422


@pytest.mark.parametrize("group", GROUPS)
async def test_the_csv_follows_the_grouping(client: AsyncClient, seeded: dict[str, Any], group: str) -> None:
    for url in (f"/api/v1/projects/{seeded['id']}/costs.csv", "/api/v1/orgs/varga/costs.csv"):
        response = await client.get(url, params={"group_by": group})
        assert response.status_code == 200, response.text
        lines = response.text.lstrip("﻿").splitlines()
        assert lines[0].startswith(f"{group};cout_usd;"), lines[0]
        assert any(line.startswith("total;7.000000;") for line in lines), lines
    detail = (await client.get("/api/v1/orgs/varga/costs.csv", params={"group_by": "agent"})).text
    assert "coordinator;5.000000;" in detail and "reviewer;2.000000;" in detail


async def test_the_kind_filter_separates_models_from_tools(
    client: AsyncClient, seeded: dict[str, Any]
) -> None:
    for url in (f"/api/v1/projects/{seeded['id']}/costs", "/api/v1/orgs/varga/costs"):
        tools = (await client.get(url, params={"group_by": "model", "kind": "tool"})).json()
        assert _by_key(tools) == {"crm_export": 1.0}, url
        models = (await client.get(url, params={"group_by": "model", "kind": "model"})).json()
        assert _by_key(models) == {"m-big": 4.0, "m-small": 2.0}, url
    csv = await client.get("/api/v1/orgs/varga/costs.csv", params={"group_by": "model", "kind": "tool"})
    assert "crm_export;1.000000;" in csv.text and "m-big" not in csv.text
    assert (await client.get("/api/v1/orgs/varga/costs", params={"kind": "bogus"})).status_code == 422
