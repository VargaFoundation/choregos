"""A spend report narrowed to one agent, one workflow, one work item; grouped by run; in tokens (S64-02).

ADR 0141 §1: the cost routes keep their shape and gain the filters `agent`, `workflow` and `work_item`
(combinable with each other and with `kind`, `since`, `until`), the grouping `run` (named by the run's
work item key and stage), and the report's tokens in its total. One seeded ledger, at project and
organisation scope, in JSON and in CSV.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

SCOPES = ("project", "org")


async def _seed(project_id: str) -> dict[str, str]:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope

    now = datetime.now(UTC)
    async with session_scope() as session:
        one = WorkItem(project_id=project_id, tracker_key="A-1", title="One", state="ready")
        two = WorkItem(project_id=project_id, tracker_key="A-2", title="Two", state="ready")
        session.add_all([one, two])
        await session.flush()

        def run(run_id: str, item: WorkItem, stage: str, attempt: int, agent: str) -> Run:
            return Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project_id,
                stage_role=stage,
                attempt=attempt,
                agent_slug=agent,
                agent_version=1,
                status="succeeded",
            )

        session.add_all(
            [
                run("run-a1-impl-1", one, "implement", 1, "agent-a"),
                run("run-a1-review-1", one, "review", 1, "agent-b"),
                run("run-a1-impl-2", one, "implement", 2, "agent-a"),
                run("run-a2-impl-1", two, "implement", 1, "agent-b"),
            ]
        )
        await session.flush()

        def line(usd: float, tokens: tuple[int, int, int], **kw: Any) -> CostLedger:
            tokens_in, tokens_out, tokens_cached = tokens
            return CostLedger(
                project_id=project_id,
                ts=now,
                provider="litellm",
                cost_usd=usd,
                cost_eur=usd / 2,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                tokens_cached=tokens_cached,
                **kw,
            )

        # Each line's cost is a distinct power of two: any sum names exactly the lines it holds.
        session.add_all(
            [
                line(1.0, (100, 10, 40), kind="model", run_id="run-a1-impl-1", work_item_id=one.id,
                     agent_slug="agent-a", workflow_name="delivery", stage_role="implement"),
                line(2.0, (200, 20, 0), kind="model", run_id="run-a1-review-1", work_item_id=one.id,
                     agent_slug="agent-b", workflow_name="delivery", stage_role="review"),
                line(4.0, (400, 40, 10), kind="tool", run_id="run-a1-impl-2", work_item_id=one.id,
                     agent_slug="agent-a", workflow_name="delivery", stage_role="implement"),
                line(8.0, (800, 80, 300), kind="model", run_id="run-a2-impl-1", work_item_id=two.id,
                     agent_slug="agent-b", workflow_name="onboarding", stage_role="implement"),
                # A session's line: an agent, no run, no work item, no workflow.
                line(16.0, (1600, 160, 0), kind="model", agent_slug="agent-a"),
            ]
        )  # fmt: skip
        return {"A-1": one.id, "A-2": two.id}


@pytest.fixture
async def items(client: AsyncClient, project: dict[str, Any]) -> dict[str, str]:
    return await _seed(project["id"])


def _url(scope: str, project: dict[str, Any], *, csv: bool = False) -> str:
    suffix = "costs.csv" if csv else "costs"
    return (
        f"/api/v1/projects/{project['id']}/{suffix}" if scope == "project" else f"/api/v1/orgs/varga/{suffix}"
    )


async def _report(client: AsyncClient, scope: str, project: dict[str, Any], **params: str) -> dict[str, Any]:
    response = await client.get(_url(scope, project), params=params)
    assert response.status_code == 200, response.text
    report: dict[str, Any] = response.json()
    return report


def _by_key(report: dict[str, Any]) -> dict[str, float]:
    return {row["key"]: row["cost_usd"] for row in report["rows"]}


# ───────────────────────── the filters ─────────────────────────


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_report_narrowed_to_an_agent_counts_none_of_another_agents_lines(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str], scope: str
) -> None:
    mine = await _report(client, scope, project, group_by="agent", agent="agent-a")
    assert _by_key(mine) == {"agent-a": 21.0}, "agent-b's 2.0 and 8.0 are not agent-a's"
    assert mine["total"]["cost_usd"] == 21.0
    theirs = await _report(client, scope, project, group_by="model", agent="agent-b")
    assert theirs["total"]["cost_usd"] == 10.0


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_report_narrowed_to_a_workflow_counts_only_its_lines(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str], scope: str
) -> None:
    delivery = await _report(client, scope, project, group_by="agent", workflow="delivery")
    assert _by_key(delivery) == {"agent-a": 5.0, "agent-b": 2.0}
    onboarding = await _report(client, scope, project, group_by="day", workflow="onboarding")
    assert onboarding["total"]["cost_usd"] == 8.0
    nothing = await _report(client, scope, project, workflow="no-such-workflow")
    assert (nothing["rows"], nothing["total"]["cost_usd"]) == ([], 0.0)


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_report_narrowed_to_a_work_item_counts_only_its_lines(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str], scope: str
) -> None:
    first = await _report(client, scope, project, group_by="stage", work_item=items["A-1"])
    assert _by_key(first) == {"implement": 5.0, "review": 2.0}
    second = await _report(client, scope, project, group_by="work_item", work_item=items["A-2"])
    assert _by_key(second) == {"A-2": 8.0}


@pytest.mark.parametrize("scope", SCOPES)
async def test_the_filters_combine_with_each_other_and_with_kind(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str], scope: str
) -> None:
    both = await _report(client, scope, project, agent="agent-a", workflow="delivery")
    assert both["total"]["cost_usd"] == 5.0, "agent-a in delivery: not its session line, not agent-b's"
    tools = await _report(client, scope, project, agent="agent-a", work_item=items["A-1"], kind="tool")
    assert tools["total"]["cost_usd"] == 4.0
    disjoint = await _report(client, scope, project, agent="agent-b", work_item=items["A-1"], kind="tool")
    assert disjoint["total"]["cost_usd"] == 0.0


async def test_the_csv_follows_the_filters(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str]
) -> None:
    for scope in SCOPES:
        response = await client.get(
            _url(scope, project, csv=True), params={"group_by": "agent", "agent": "agent-b"}
        )
        assert response.status_code == 200, response.text
        lines = response.text.lstrip("﻿").splitlines()
        assert "agent-b;10.000000;5.000000;1000;100;300;2" in lines, lines
        assert not any(line.startswith("agent-a;") for line in lines), lines


@pytest.mark.parametrize(
    "params",
    [
        {"agent": "Agent_A"},
        {"agent": ""},
        {"agent": "a" * 65},
        {"workflow": ""},
        {"workflow": "w" * 65},
        {"work_item": ""},
        {"work_item": "i" * 129},
        {"group_by": "runs"},
    ],
)
async def test_a_filter_outside_the_contract_is_refused(
    client: AsyncClient, project: dict[str, Any], params: dict[str, str]
) -> None:
    for scope in SCOPES:
        for csv in (False, True):
            response = await client.get(_url(scope, project, csv=csv), params=params)
            assert response.status_code == 422, (scope, csv, params)


# ───────────────────────── the grouping by run ─────────────────────────


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_run_grouping_names_each_run_by_its_work_item_key_and_stage(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str], scope: str
) -> None:
    report = await _report(client, scope, project, group_by="run")
    assert report["group_by"] == "run"
    assert _by_key(report) == {
        "A-1 · implement": 1.0,
        "A-1 · review": 2.0,
        "A-1 · implement #2": 4.0,
        "A-2 · implement": 8.0,
        "—": 16.0,  # the session's line: no run spent it
    }
    narrowed = await _report(client, scope, project, group_by="run", agent="agent-b")
    assert _by_key(narrowed) == {"A-1 · review": 2.0, "A-2 · implement": 8.0}


async def test_the_csv_names_runs_too(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str]
) -> None:
    response = await client.get(_url("org", project, csv=True), params={"group_by": "run"})
    lines = response.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("run;cout_usd;"), lines[0]
    assert "A-1 · implement #2;4.000000;2.000000;400;40;10;1" in lines, lines


# ───────────────────────── tokens in the total ─────────────────────────


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize(
    ("params", "wanted"),
    [
        ({"group_by": "run"}, (3100, 310, 350)),
        ({"group_by": "model"}, (3100, 310, 350)),
        ({"group_by": "stage", "agent": "agent-a"}, (2100, 210, 50)),
        ({"group_by": "agent", "workflow": "delivery", "kind": "model"}, (300, 30, 40)),
    ],
)
async def test_the_totals_tokens_are_the_sum_of_the_rows(
    client: AsyncClient,
    project: dict[str, Any],
    items: dict[str, str],
    scope: str,
    params: dict[str, str],
    wanted: tuple[int, int, int],
) -> None:
    report = await _report(client, scope, project, **params)
    total = report["total"]
    for field in ("tokens_in", "tokens_out", "tokens_cached"):
        assert total[field] == sum(row[field] for row in report["rows"]), field
    assert (total["tokens_in"], total["tokens_out"], total["tokens_cached"]) == wanted


async def test_the_csv_writes_the_tokens_of_the_total(
    client: AsyncClient, project: dict[str, Any], items: dict[str, str]
) -> None:
    for scope in SCOPES:
        response = await client.get(_url(scope, project, csv=True), params={"group_by": "run"})
        lines = response.text.lstrip("﻿").splitlines()
        assert "total;31.000000;15.500000;3100;310;350" in lines, lines
