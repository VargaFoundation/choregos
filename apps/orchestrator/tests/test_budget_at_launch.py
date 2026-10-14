"""A work item stops at the budget it was given at launch (S27-06, ADR 0047)."""

from __future__ import annotations

import pytest

from .conftest import Fixture
from .test_budget_reservation import _plan, spend

pytestmark = pytest.mark.asyncio


async def _give(setup: Fixture, budget_usd: float) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.budget_usd = budget_usd


async def test_the_key_is_minted_within_the_given_budget(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _give(setup, 1.0)
    await spend(setup, 0.25)
    prepared = await prepare_stage(_plan(setup))
    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.75)


async def test_the_ticket_stops_at_the_given_amount(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _give(setup, 1.0)
    await spend(setup, 0.6)
    await spend(setup, 0.4, kind="tool")
    prepared = await prepare_stage(_plan(setup))
    assert prepared["refused"]["reason"] == "budget"
    assert not setup.adapters.gateway.keys
