"""Two stages cannot reserve the same remainder; the organisation month and the agent's open keys
enter the reservation (S27-11, ADR 0047 addendum).

Before, `prepare_stage` read what was left, then reserved it, with nothing between the two: two
stages preparing at once both read the same remainder and each got a key for all of it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from .conftest import Fixture
from .test_agent_du_registre import _plan as _agent_plan
from .test_agent_monthly_cap import _spend_as_agent, _with_cap
from .test_budget_reservation import _plan, leave_on_ticket, spend

pytestmark = pytest.mark.asyncio


async def _org_month_cap(setup: Fixture, usd: float) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.definitions import CEILING_KEY

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        org = await session.get(Organization, project.org_id)
        assert org is not None
        ceiling = {"ceiling": {"budgets": {"monthly_org_usd": usd}}}
        org.settings = {**(org.settings or {}), CEILING_KEY: ceiling}


async def _project_day_cap(setup: Fixture, usd: float) -> None:
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


async def _open_key(setup: Fixture, key_id: str, usd: float, *, project_id: str, agent: str | None) -> None:
    """A key still open (not revoked, not expired) for a run of `agent`, as a run in flight holds."""
    from choregos_api.db.models import GatewayKeyRow, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        run_id = f"run-{key_id}"
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=project_id,
                stage_role="implement",
                status="running",
                agent_slug=agent,
                agent_version=1 if agent else None,
            )
        )
        await session.flush()
        session.add(
            GatewayKeyRow(
                key_id=key_id,
                run_id=run_id,
                project_id=project_id,
                budget_usd=usd,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )


async def _other_project(setup: Fixture) -> str:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        mine = await session.get(Project, setup.project_id)
        assert mine is not None
        other = Project(org_id=mine.org_id, slug="other", name="Other")
        session.add(other)
        await session.flush()
        return str(other.id)


async def _reserved_keys(setup: Fixture) -> list[float]:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(GatewayKeyRow.budget_usd).where(GatewayKeyRow.revoked.is_(False)))
        ).scalars()
        return [float(usd) for usd in rows]


@pytest.mark.parametrize("scope", ["ticket", "project_day", "org_month", "agent_month"])
async def test_two_stages_at_once_cannot_reserve_the_same_remainder(setup: Fixture, scope: str) -> None:
    """A remainder that fits one stage: one key is reserved, the other stage is refused as below
    the floor — whichever scope the remainder is under."""
    from choregos_orchestrator.activities.stage import prepare_stage

    if scope == "agent_month":
        await _with_cap(setup, 10.0, run_usd=None)
        await _spend_as_agent(setup, 9.9, 0)
        first = _agent_plan(setup, agent="coordinateur", transition_id="t-a")
        second = _agent_plan(setup, agent="coordinateur", transition_id="t-b")
    else:
        if scope == "ticket":
            await leave_on_ticket(setup, 0.10)
        elif scope == "project_day":
            await _project_day_cap(setup, 1.0)
            await spend(setup, 0.9, work_item=False)
        else:
            await _org_month_cap(setup, 1.0)
            await spend(setup, 0.9, work_item=False)
        first = {**_plan(setup), "transition_id": "t-a"}
        second = {**_plan(setup), "transition_id": "t-b"}

    prepared = await asyncio.gather(prepare_stage(first), prepare_stage(second))

    started = [p for p in prepared if "refused" not in p]
    refused = [p for p in prepared if "refused" in p]
    assert len(started) == 1 and len(refused) == 1, prepared
    assert started[0]["stage_input"]["budget"]["usd"] == pytest.approx(0.10)
    assert refused[0]["refused"]["reason"] == "budget"
    assert f"bound by {scope}" in refused[0]["refused"]["detail"]
    assert await _reserved_keys(setup) == [pytest.approx(0.10)], "one reservation, not two"


async def test_the_organisation_month_caps_a_key(setup: Fixture) -> None:
    """The organisation's month is the spend of ALL its projects, not the project's, and of that
    organisation only."""
    from choregos_api.db.models import CostLedger, Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.stage import prepare_stage

    await _org_month_cap(setup, 1.0)
    other = await _other_project(setup)
    async with session_scope() as session:
        stranger = Organization(slug="elsewhere", name="Elsewhere")
        session.add(stranger)
        await session.flush()
        foreign = Project(org_id=stranger.id, slug="foreign", name="Foreign")
        session.add(foreign)
        await session.flush()
        for project_id, usd in ((other, 0.7), (foreign.id, 50.0)):
            session.add(CostLedger(project_id=project_id, kind="model", cost_usd=usd, ts=utcnow()))

    prepared = await prepare_stage(_plan(setup))

    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.3)


async def test_the_organisation_s_open_keys_reduce_its_month(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _org_month_cap(setup, 1.0)
    await _open_key(setup, "k-other", 0.6, project_id=await _other_project(setup), agent=None)

    prepared = await prepare_stage(_plan(setup))

    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.4)


async def test_the_agent_s_open_keys_reduce_its_monthly_remainder(setup: Fixture) -> None:
    """S27-09's gap: a run of the same agent still holding a key of USD 0.60 may spend it."""
    from choregos_orchestrator.activities.stage import prepare_stage

    await _with_cap(setup, 10.0, run_usd=None)
    await _spend_as_agent(setup, 9.0, 0)
    await _open_key(setup, "k-in-flight", 0.6, project_id=setup.project_id, agent="coordinateur")

    prepared = await prepare_stage(_agent_plan(setup, agent="coordinateur"))

    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(0.4)


async def test_another_agent_s_open_keys_do_not_reduce_it(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _with_cap(setup, 10.0, run_usd=None)
    await _spend_as_agent(setup, 9.0, 0)
    await _open_key(setup, "k-another", 0.6, project_id=setup.project_id, agent="someone-else")

    prepared = await prepare_stage(_agent_plan(setup, agent="coordinateur"))

    assert prepared["stage_input"]["budget"]["usd"] == pytest.approx(1.0)
