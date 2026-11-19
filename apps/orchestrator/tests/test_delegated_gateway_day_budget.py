"""On a gateway reached with a delegated key, the platform holds the project's day (S82-05, ADR 0205).

A delegated service account is no team admin: no team is created, no key joins one. The day budget
the gateway used to cut (S27-10) is then enforced by `prepare_stage` alone, from the cost ledger and
the open reservations, which is the first line of defence on every gateway.
"""

from __future__ import annotations

from typing import Any

import pytest
from tranibor_adapters.fakes import FakeGateway
from tranibor_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT

from .conftest import Fixture
from .test_budget_reservation import _plan, spend
from .test_llm_guard_on_the_run_key import _analyzer
from .test_project_day_team import _daily

pytestmark = pytest.mark.asyncio


def _delegate(setup: Fixture) -> FakeGateway:
    setup.adapters.gateway = FakeGateway(delegated=True)
    gateway: FakeGateway = setup.adapters.gateway
    return gateway


async def test_a_project_over_its_day_is_refused_without_a_gateway_team(setup: Fixture) -> None:
    gateway = _delegate(setup)
    await _daily(setup, 1.0)
    await spend(setup, 0.6, work_item=False)
    await spend(setup, 0.4, work_item=False, kind="tool")
    from tranibor_orchestrator.activities.stage import prepare_stage

    refused: dict[str, Any] = await prepare_stage(_plan(setup))
    assert refused["refused"]["reason"] == "budget" and "project_day" in refused["refused"]["detail"]
    assert not gateway.keys and not gateway.teams


async def test_a_run_key_is_minted_capped_by_what_is_left_of_the_day_and_joins_no_team(
    setup: Fixture,
) -> None:
    from tranibor_orchestrator.activities.stage import prepare_stage, start_run

    gateway = _delegate(setup)
    await _daily(setup, 1.0)
    await spend(setup, 0.7, work_item=False)
    prepared = await prepare_stage(_plan(setup))
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    [key] = gateway.keys.values()
    assert key.budget_usd == pytest.approx(0.3), "the day's remainder is the key's cap"
    assert key.models, "the models are always sent"
    assert not gateway.teams and not gateway.key_team


async def test_the_second_run_cannot_take_the_money_the_first_holds(setup: Fixture) -> None:
    """Without a team nothing else would stop two keys from spending the same remainder."""
    from tranibor_orchestrator.activities.stage import prepare_stage, start_run

    gateway = _delegate(setup)
    await _daily(setup, 1.0)
    first = await prepare_stage(_plan(setup))
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": first["stage_input"],
        }
    )
    assert len(gateway.keys) == 1
    assert first["stage_input"]["budget"]["usd"] == pytest.approx(1.0)
    second: dict[str, Any] = await prepare_stage({**_plan(setup), "attempt": 2})
    assert second.get("refused", {}).get("reason") == "budget", second


async def test_the_guard_mark_rides_on_the_delegated_key(setup: Fixture) -> None:
    from tranibor_orchestrator.activities.stage import prepare_stage, start_run

    gateway = _delegate(setup)
    await _analyzer(setup, True)
    prepared = await prepare_stage(_plan(setup))
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    [key] = gateway.keys.values()
    assert key.metadata[GUARD_METADATA_KEY] == GUARD_REDACT


async def test_provisioning_creates_no_team_and_says_who_holds_the_day(setup: Fixture) -> None:
    from tranibor_orchestrator.activities.base import db, project_bundle
    from tranibor_orchestrator.activities.provisioning import _gateway_create_team_and_budget

    gateway = _delegate(setup)
    await _daily(setup, 30.0)
    async with db() as session:
        bundle = await project_bundle(session, setup.project_id)
        detail = await _gateway_create_team_and_budget({}, bundle, None)
    assert not gateway.teams
    assert "held by the platform" in detail
