"""A run's round trips are served (S57-01, ADR 0122): `tokens.requests` on `GET /runs/{id}`.

The orchestrator books the model calls the gateway counted (`run.tokens["requests"]`); the run's DTO
and the OpenAPI contract carry them, and a run booked before S57-01 reads `null`, not zero.
"""

from __future__ import annotations

import itertools
from typing import Any

import choregos_contracts as contracts
import pytest
from httpx import AsyncClient

_KEYS = itertools.count(1)


async def _run(project_id: str, tokens: dict[str, Any]) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project_id, tracker_key=f"TRIPS-{next(_KEYS)}", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project_id,
            stage_role="implement",
            attempt=1,
            status="succeeded",
            tokens=tokens,
            spend_collected=True,
        )
        session.add(run)
        await session.flush()
        return str(run.id)


async def test_a_run_serves_the_model_calls_it_was_booked_with(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _run(project["id"], {"tokens_in": 40_000, "tokens_out": 2_000, "requests": 7, "runs": 1})
    response = await client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["tokens"]["requests"] == 7


async def test_a_run_booked_before_the_count_serves_null_not_zero(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id = await _run(project["id"], {"tokens_in": 40_000, "tokens_out": 2_000, "runs": 1})
    tokens = (await client.get(f"/api/v1/runs/{run_id}")).json()["tokens"]
    assert tokens["requests"] is None
    assert tokens["tokens_in"] == 40_000


def test_the_contract_s_totals_and_the_dto_are_in_step() -> None:
    """The DTO and `components.schemas.Totals` name the same fields, and `requests` is a nullable
    integer in both (ADR 0001: the contract changes with the code, in the same PR)."""
    from choregos_api.schemas import Totals

    schema = contracts.load_openapi()["components"]["schemas"]["Totals"]
    assert set(schema["properties"]) == set(Totals.model_fields)
    assert schema["properties"]["requests"]["type"] == ["integer", "null"]
    assert Totals().requests is None
    assert Totals.model_validate({"requests": 3}).requests == 3


# ─────────── the report of S57-01, over the real routes (and its SQL on PostgreSQL) ───────────


def _report_tool() -> Any:
    """`tools/report_round_trips.py`, a plain script of the repository, loaded from its path."""
    import importlib.util
    import pathlib
    import sys

    path = pathlib.Path(__file__).resolve().parents[3] / "tools" / "report_round_trips.py"
    spec = importlib.util.spec_from_file_location("report_round_trips", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass looks its module up while it is defined
    spec.loader.exec_module(module)
    return module


async def _seed_window(project_id: str) -> None:
    """Three runs in the window (one booked before S57-01), one older, one still going; tool rows."""
    from datetime import timedelta

    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Diagnostics, StageResult, StageStatus
    from choregos_core import utcnow

    now = utcnow()

    def result(turns: int, calls: int, duration: float) -> dict[str, Any]:
        diagnostics = Diagnostics(turns=turns, tool_calls=calls, duration_s=duration)
        done = StageResult(status=StageStatus.DONE, summary="s", diagnostics=diagnostics)
        return done.model_dump(mode="json")

    def booked(requests: int | None) -> dict[str, Any]:
        tokens: dict[str, Any] = {"tokens_in": 30_000, "tokens_cached": 9_000, "tokens_out": 1_500, "runs": 1}
        return tokens if requests is None else {**tokens, "requests": requests}

    runs = [
        ("trips-a", "succeeded", 2.0, 1.0, booked(7), result(9, 12, 300.0), 6),
        ("trips-b", "failed", 3.0, 0.5, booked(3), result(4, 4, 0.0), 1),
        ("trips-c", "succeeded", 1.0, 0.25, {}, None, 0),
        ("trips-old", "succeeded", 40.0, 1.0, booked(50), result(9, 12, 300.0), 9),
        ("trips-going", "running", 0.1, None, {}, None, 2),
    ]
    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project_id, tracker_key=f"TRIPS-{next(_KEYS)}", title="T", state="ready")
        session.add(item)
        await session.flush()
        for run_id, status, days_ago, hours, tokens, outcome, tool_rows in runs:
            started = now - timedelta(days=days_ago)
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project_id,
                    stage_role="implement",
                    agent_slug="fon-agent",
                    backend="claude-code",
                    attempt=1,
                    status=status,
                    started_at=started,
                    ended_at=None if hours is None else started + timedelta(hours=hours),
                    tokens=tokens,
                    cost_usd=0.4,
                    spend_collected=bool(tokens),
                    result=outcome,
                )
            )
            await session.flush()
            for _ in range(tool_rows):
                session.add(
                    CostLedger(
                        project_id=project_id,
                        work_item_id=item.id,
                        run_id=run_id,
                        ts=started,
                        kind="tool",
                        provider="mcp:jira",
                        model="jira_search",
                    )
                )


async def test_the_report_reads_the_routes_and_agrees_with_its_sql(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The tool walks the real routes (projects, work items, runs, access) as an operator would; on
    PostgreSQL its `--sql` gives the same numbers from the tables."""
    from datetime import timedelta

    from choregos_core import utcnow

    from .conftest import SUITE_ON_POSTGRES

    tool = _report_tool()
    await _seed_window(project["id"])
    found = await tool.collect(client, "varga", utcnow() - timedelta(days=30))
    assert (found.skipped_work_items, found.skipped_access) == (0, 0)
    (row,) = tool.aggregate(found.samples)
    assert (row.agent, row.backend, row.runs, row.success_rate) == ("fon-agent", "claude-code", 3, 0.6667)
    requests, calls = row.spreads["requests"], row.spreads["platform_tool_calls"]
    assert (requests.p50, requests.p90) == pytest.approx((5.0, 6.6)), "the run booked before S57-01 is out"
    assert (calls.p50, calls.p90) == pytest.approx((1.0, 5.0)), "6, 1 and 0 ledger rows"
    assert row.share_many_platform_calls == 0.3333
    assert row.spreads["agent_tool_calls"].p50 == 8.0

    if not SUITE_ON_POSTGRES:
        return  # `percentile_cont` and `->>` are PostgreSQL's: CI replays this on its PostgreSQL
    from choregos_api.db.session import session_scope
    from sqlalchemy import text

    async with session_scope(orgs="*") as session:
        (record,) = (await session.execute(text(tool.sql(30)))).mappings().all()
    expected = row.to_dict()
    assert set(record) == set(expected)
    for name, value in expected.items():
        served = record[name]
        if value is None or isinstance(value, str):
            assert served == value, name
        else:
            assert float(served) == pytest.approx(value, abs=1e-3), name
