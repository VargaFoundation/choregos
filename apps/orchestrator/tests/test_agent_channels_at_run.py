"""A stage records the revision its project is served: production, testing, a canary share (S40-02).

`prepare_stage` resolves the agent an actor names through the runtime's resolver (an activity: not
workflow code, so no replay history is involved) and records `run.agent_version`.
"""

from __future__ import annotations

from typing import Any

from .conftest import Fixture
from .kits.registry_agent import agent_stage_plan, install_agent


async def _channels(
    setup: Fixture,
    *,
    stable: int | None = 1,
    candidate: int | None = 2,
    percent: int = 0,
    project_channel: str = "stable",
) -> None:
    """Agent `coordinateur` with revisions 1 and 2, and its channels set as asked."""
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await install_agent(setup)
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "coordinateur"))).scalar_one()
        first = (
            await session.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
        ).scalar_one()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=agent.org_id,
                version=2,
                spec={**first.spec, "instructions": "Version deux de « {{ ticket.title }} »."},
                checksum="sha256:v2",
            )
        )
        agent.stable_version = stable
        agent.candidate_version = candidate
        agent.candidate_percent = percent
        project = await session.get(Project, setup.project_id)
        assert project is not None
        project.agent_channel = project_channel


async def _recorded(setup: Fixture, **plan: Any) -> tuple[int | None, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    prepared = await prepare_stage(agent_stage_plan(setup, agent="coordinateur", **plan))
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        return run.agent_version, prepared["stage_input"]["playbook"]["ref"]


async def test_a_project_that_follows_production_runs_production(setup: Fixture) -> None:
    await _channels(setup)
    version, ref = await _recorded(setup)
    assert version == 1 and ref.startswith("agent:coordinateur@1@")


async def test_a_project_that_follows_testing_runs_the_candidate(setup: Fixture) -> None:
    await _channels(setup, project_channel="candidate")
    version, ref = await _recorded(setup)
    assert version == 2 and ref.startswith("agent:coordinateur@2@")


async def test_a_work_item_inside_a_full_share_runs_the_candidate_on_every_attempt(setup: Fixture) -> None:
    await _channels(setup, percent=100)
    assert [(await _recorded(setup, attempt=n))[0] for n in (1, 2, 3)] == [2, 2, 2]


async def test_a_work_item_outside_the_share_runs_production(setup: Fixture) -> None:
    await _channels(setup, percent=0)
    assert (await _recorded(setup))[0] == 1


async def test_after_a_rollback_a_new_stage_records_the_rolled_back_revision(setup: Fixture) -> None:
    from choregos_api.db.models import Agent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _channels(setup, stable=2, candidate=None)
    assert (await _recorded(setup, attempt=1))[0] == 2
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "coordinateur"))).scalar_one()
        agent.stable_version = 1
    assert (await _recorded(setup, attempt=2))[0] == 1


async def test_an_agent_without_a_production_pointer_still_runs_its_latest_revision(setup: Fixture) -> None:
    """Compatibility: an agent written by a path that sets no pointer behaves as before channels."""
    await _channels(setup, stable=None, candidate=None)
    assert (await _recorded(setup))[0] == 2
