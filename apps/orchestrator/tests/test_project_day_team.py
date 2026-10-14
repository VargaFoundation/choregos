"""The project's day budget is a gateway team, created at provisioning, that every key joins (S27-10).

Before, `daily_project_usd` was displayed and never enforced, and the provisioning step "create team
and budget" minted a key and revoked it: no team was ever created.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from .conftest import Fixture
from .test_budget_reservation import _plan, spend

pytestmark = pytest.mark.asyncio


async def _daily(setup: Fixture, usd: float | None) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(PolicyDef.project_id == setup.project_id, PolicyDef.is_active)
            )
        ).scalar_one()
        doc = json.loads(json.dumps(row.json_doc))
        doc.setdefault("budgets", {})["daily_project_usd"] = usd
        row.json_doc = doc


async def test_provisioning_creates_the_team_with_the_day_budget(setup: Fixture) -> None:
    from choregos_orchestrator.activities.base import db, project_bundle
    from choregos_orchestrator.activities.provisioning import (
        _gateway_create_team_and_budget,
        project_team_id,
    )

    await _daily(setup, 30.0)
    async with db() as session:
        bundle = await project_bundle(session, setup.project_id)
        detail = await _gateway_create_team_and_budget({}, bundle, None)
    team = project_team_id(setup.project_id)
    assert setup.adapters.gateway.teams[team] == {"budget_usd": 30.0, "duration": "1d"}
    assert team in detail
    assert not setup.adapters.gateway.keys, "no key is minted and thrown away any more"


async def test_a_run_key_joins_the_project_team_and_the_gateway_cuts_the_day(setup: Fixture) -> None:
    from choregos_adapters.fakes import BudgetExceededError
    from choregos_orchestrator.activities.provisioning import project_team_id
    from choregos_orchestrator.activities.stage import prepare_stage, start_run

    await _daily(setup, 1.0)
    prepared = await prepare_stage(_plan(setup))
    # The key is minted when the run starts, for its Secret (#523).
    await start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    gateway = setup.adapters.gateway
    [(key_id, key)] = gateway.keys.items()
    team = project_team_id(setup.project_id)
    assert gateway.key_team[key_id] == team and gateway.teams[team]["budget_usd"] == 1.0
    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(1.0)
    # Other runs of the project spent the day in parallel: the next call is refused by the TEAM.
    gateway.team_spend[team] = 0.99
    with pytest.raises(BudgetExceededError, match="team"):
        gateway.record_usage(key_id, tokens_in=100_000, tokens_out=0, model=key.models[0])


async def test_a_project_over_its_day_gets_its_next_key_refused(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _daily(setup, 1.0)
    await spend(setup, 0.6, work_item=False)
    await spend(setup, 0.4, work_item=False, kind="tool")
    refused: dict[str, Any] = await prepare_stage(_plan(setup))
    assert refused["refused"]["reason"] == "budget" and "project_day" in refused["refused"]["detail"]
    assert not setup.adapters.gateway.keys


async def test_no_daily_budget_no_team(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _daily(setup, None)  # the preset's day budget is lifted
    await prepare_stage(_plan(setup))
    assert not setup.adapters.gateway.teams
