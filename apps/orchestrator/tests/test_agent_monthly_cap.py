"""An agent at 100 % of its monthly cap is suspended and its new runs are refused; Slack hears of the
50, 80 and 100 % alerts, once each (S27-09)."""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture
from .test_agent_du_registre import _agent, _plan


async def _with_cap(setup: Fixture, monthly: float) -> None:
    from choregos_api.db.models import AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _agent(setup)
    async with session_scope() as session:
        version = (await session.execute(select(AgentVersion))).scalars().one()
        spec = dict(version.spec)
        spec["budget"] = {"run_usd": 0.5, "monthly_usd": monthly}
        version.spec = spec


async def _spend_as_agent(setup: Fixture, usd: float, n: int) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.services.couts import record_cost

    async with session_scope() as session:
        run = Run(
            id=f"r-month-{n}",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            stage_role="implement",
            status="done",
            agent_slug="coordinateur",
            agent_version=1,
        )
        session.add(run)
        await session.flush()
        await record_cost(
            session,
            project_id=setup.project_id,
            work_item_id=setup.work_item_id,
            run_id=run.id,
            provider="p",
            model="m",
            backend=None,
            stage_role="implement",
            size=None,
            tokens_in=1,
            tokens_out=1,
            tokens_cached=0,
            cost_usd=usd,
            fx_rate=0.9,
        )


async def _tell(setup: Fixture) -> None:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import project_bundle
    from choregos_orchestrator.activities.bilan import _tell_slack_of_agent_alerts

    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_id)
        await _tell_slack_of_agent_alerts(session, bundle)


async def test_three_alerts_reach_slack_the_agent_is_suspended_and_its_runs_are_refused(
    setup: Fixture,
) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _with_cap(setup, 10.0)
    notifier: Any = setup.adapters.notify
    for n, amount in enumerate((5.5, 2.6, 2.0)):  # 5.5, 8.1, 10.1 of 10
        await _spend_as_agent(setup, amount, n)
        await _tell(setup)
    await _tell(setup)  # nothing new: nothing is sent again
    titles = [message.title for _, message in notifier.sent]
    assert len(titles) == 3, titles
    assert ["50 %" in titles[0], "80 %" in titles[1], "100 %" in titles[2]] == [True] * 3
    assert notifier.last().severity == "error"

    with pytest.raises(ApplicationError) as refused:
        await prepare_stage(_plan(setup, agent="coordinateur"))
    assert refused.value.non_retryable
    assert "suspended" in str(refused.value)


async def test_a_notifier_that_is_down_leaves_the_alert_to_the_next_try(setup: Fixture) -> None:
    await _with_cap(setup, 10.0)
    notifier: Any = setup.adapters.notify
    await _spend_as_agent(setup, 6.0, 0)
    notifier.panne = "slack is down"
    await _tell(setup)
    assert notifier.sent == []
    notifier.panne = None
    await _tell(setup)
    assert len(notifier.sent) == 1
