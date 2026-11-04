"""Round trips are measured on every run (S57-01, ADR 0122).

The gateway already counted a run's model calls (`Spend.requests`, one per spend log line); the
orchestrator dropped the number. `book_spend` now keeps it as `run.tokens["requests"]`, sums it on
the work item, and says "unknown" rather than zero when it cannot be told.
"""

from __future__ import annotations

from typing import Any

from .conftest import Fixture


async def _book(
    setup: Fixture,
    run_id: str,
    *,
    calls: int,
    tokens: dict[str, Any] | None = None,
    capabilities: frozenset[str] | None = None,
) -> dict[str, Any]:
    """A run whose key made `calls` model calls at the fake gateway, then `collect_spend`."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.outcome import collect_spend

    gateway = setup.adapters.gateway
    if capabilities is not None:
        gateway.capabilities = capabilities
    key = await gateway.mint_key({}, 5.0, 600, ["platform/standard"])
    for _ in range(calls):
        gateway.record_usage(key.key_id, tokens_in=1_000, tokens_out=100, model="platform/standard")
    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                model="platform/standard",
                status="running",
                gateway_key_id=key.key_id,
                started_at=utcnow(),
                tokens=dict(tokens or {}),
            )
        )
    return await collect_spend({"project_id": setup.project_id, "run_id": run_id})


async def _tokens(run_id: str) -> dict[str, Any]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return dict(run.tokens or {})


async def _item_totals(setup: Fixture) -> dict[str, Any]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        return dict(item.totals or {})


async def test_a_run_booked_from_the_gateway_keeps_its_model_calls(setup: Fixture) -> None:
    booked = await _book(setup, "run-seven", calls=7)
    assert (await _tokens("run-seven"))["requests"] == 7
    assert booked["requests"] == 7, "the activity's answer carries it too"


async def test_a_run_that_called_no_model_reads_zero_not_unknown(setup: Fixture) -> None:
    await _book(setup, "run-none", calls=0)
    assert (await _tokens("run-none"))["requests"] == 0


async def test_booking_twice_does_not_count_twice(setup: Fixture) -> None:
    from choregos_orchestrator.activities.outcome import collect_spend

    await _book(setup, "run-twice", calls=4)
    again = await collect_spend({"project_id": setup.project_id, "run_id": "run-twice"})
    assert again["requests"] == 4
    assert (await _item_totals(setup))["requests"] == 4


async def test_a_gateway_that_cannot_read_a_spend_back_leaves_the_calls_unknown(setup: Fixture) -> None:
    await _book(setup, "run-blind", calls=3, capabilities=frozenset({"list_models"}))
    tokens = await _tokens("run-blind")
    assert tokens["unmeasured"] is True
    assert "requests" not in tokens, "unknown is not zero round trips"
    assert "requests" not in await _item_totals(setup)


async def test_a_resumed_run_adds_the_calls_of_its_second_segment(setup: Fixture) -> None:
    first = {"tokens_in": 5_000, "tokens_out": 500, "cost_usd": 0.1, "requests": 3, "resumed": 1}
    await _book(setup, "run-resumed", calls=2, tokens={**first, "resumed_for": "answer-1"})
    assert (await _tokens("run-resumed"))["requests"] == 5


async def test_a_first_segment_booked_before_the_count_leaves_the_run_unknown(setup: Fixture) -> None:
    first = {"tokens_in": 5_000, "tokens_out": 500, "cost_usd": 0.1, "resumed": 1}
    await _book(setup, "run-older", calls=2, tokens={**first, "resumed_for": "answer-1"})
    assert "requests" not in await _tokens("run-older")


async def test_the_work_item_sums_the_calls_of_its_runs(setup: Fixture) -> None:
    await _book(setup, "run-a", calls=2)
    await _book(setup, "run-b", calls=3)
    assert (await _item_totals(setup))["requests"] == 5


async def test_a_work_item_with_a_run_booked_before_the_count_stays_unknown(setup: Fixture) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.totals = {"tokens_in": 9_000, "tokens_out": 900, "cost_usd": 0.2, "runs": 1}
    await _book(setup, "run-after", calls=2)
    totals = await _item_totals(setup)
    assert totals["runs"] == 2
    assert "requests" not in totals, "two of its calls are not all of them"
