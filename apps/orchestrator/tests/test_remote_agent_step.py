"""A step carried by a remote agent over A2A (ADR 0062), prepared and started by the orchestrator.

The orchestrator never calls the agent: it prepares the `RemoteCall`, reserves the declared cost,
refuses what the project cannot reach, mints the access token for the run's Secret only, and books
the declared cost. The token is the point: it must be in the executor's spec and nowhere that
outlives the run.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from choregos_adapters.a2a import parse_card
from choregos_adapters.fakes.a2a import FakeA2aAgent
from choregos_core import utcnow
from choregos_orchestrator.activities import bilan
from choregos_orchestrator.activities import stage as stage_activities
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

#: Built at run time: a literal that looks like a token would trip the secrets scan of the CI.
TOKEN = "-".join(["static", "5b2e8d4f" * 3])
HOST = "agent.remote.test"


def _runtime(**extra: Any) -> dict[str, Any]:
    fake = FakeA2aAgent()
    card = parse_card(fake.card_document())
    return {
        "kind": "a2a",
        "card_url": fake.card_url,
        "skill": "work-in-jira",
        "credential": "rovo",
        "timeout_minutes": 10,
        "declared_cost": {"per_task_usd": 0.05, "per_minute_usd": 0.01},
        "input": "text",
        "pinned_card": {
            "url": card.url,
            "protocol_version": card.protocol_version,
            "digest": card.digest,
            "skills": ["work-in-jira", "work-in-confluence"],
        },
        **extra,
    }


async def _decor(
    setup: Fixture,
    *,
    runtime: dict[str, Any] | None = None,
    allow: bool = True,
    credential: str | None = "connected",
) -> None:
    """The remote agent, the credential holding its token, and the project's egress policy."""
    from choregos_api.db.models import Agent, AgentVersion, PolicyDef, Project, RemoteCredential
    from choregos_api.db.session import session_scope
    from choregos_api.secret_store import get_secret_store
    from sqlalchemy import select

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug="rovo",
            kind="remote",
            display_name="Rovo",
            status="active",
            lifecycle="active",
        )
        session.add(agent)
        await session.flush()
        spec = {"instructions": "Answer about {{ ticket.title }}.", "runtime": runtime or _runtime()}
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=spec, checksum="sha256:r1")
        )
        if credential is not None:
            session.add(
                RemoteCredential(
                    org_id=project.org_id,
                    name="rovo",
                    type="bearer",
                    config={},
                    status=credential,
                    secret_ref="store:remote/rovo/token",
                )
            )
            await get_secret_store().put(session, project.org_id, "remote/rovo/token", TOKEN, "test")
        if allow:
            policy = (
                (await session.execute(select(PolicyDef).where(PolicyDef.project_id == setup.project_id)))
                .scalars()
                .first()
            )
            assert policy is not None
            doc = json.loads(json.dumps(policy.json_doc))
            network = doc.setdefault("sandbox", {}).setdefault("network", {})
            network["allow_domains"] = [*network.get("allow_domains", []), HOST]
            policy.json_doc = doc


def _plan(setup: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-ask",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "rovo",
        "attempt": 1,
        "agent": "rovo",
        **extra,
    }


async def _reservations() -> list[Any]:
    from choregos_api.db.models import GatewayKeyRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(GatewayKeyRow))).scalars())


async def test_a_remote_agents_step_carries_the_call_and_no_model(setup: Fixture) -> None:
    await _decor(setup)
    prepared = await stage_activities.prepare_stage(_plan(setup))

    stage_input = prepared["stage_input"]
    assert stage_input["agent"]["backend"] == "a2a"
    assert stage_input["model"]["litellm_model"] == "none", "the remote agent brings its own model"
    remote = stage_input["remote"]
    assert remote["endpoint"] == f"https://{HOST}/v1/rpc" and remote["skill"] == "work-in-jira"
    assert remote["card_digest"].startswith("sha256:") and remote["timeout_s"] == 600
    assert remote["declared_cost"] == {"per_task_usd": 0.05, "per_minute_usd": 0.01}
    assert remote["credential"] == "rovo" and remote["agent_slug"] == "rovo"
    assert stage_input["memory_scope"] == "none", "the project's memory is not sent to a third party"
    assert stage_input["tools"]["mcp"] == {} and stage_input["skills"] == []
    assert setup.adapters.gateway.keys == {}, "no gateway key exists for a remote agent"
    (reserved,) = await _reservations()
    assert reserved.budget_usd == pytest.approx(0.05 + 0.01 * 10), "what is reserved is the declared cost"


async def test_the_access_token_reaches_the_executor_spec_and_nothing_that_outlives_the_run(
    setup: Fixture,
) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    await _decor(setup)
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert TOKEN not in json.dumps(prepared)
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )

    spec = setup.adapters.executor.started[-1]
    assert spec.remote_token == TOKEN, "the run's Secret carries it"
    assert spec.gateway_key is None
    assert TOKEN not in spec.stage_input.model_dump_json(), "the StageInput the runner reads never has it"
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None and TOKEN not in json.dumps(run.stage_input) + json.dumps(run.tokens or {})
    assert setup.adapters.gateway.keys == {}


async def test_a_host_the_policy_does_not_allow_is_refused_before_any_reservation(setup: Fixture) -> None:
    await _decor(setup, allow=False)
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable
    assert HOST in str(raised.value) and "allow_domains" in str(raised.value)
    assert await _reservations() == []


@pytest.mark.parametrize("state", ["pending", "revoked"])
async def test_a_credential_that_cannot_be_used_stops_the_step(setup: Fixture, state: str) -> None:
    await _decor(setup, credential=state)
    with pytest.raises(ApplicationError, match=state) as raised:
        await stage_activities.prepare_stage(_plan(setup))
    assert raised.value.type == "AgentIndisponible" and raised.value.non_retryable


async def test_a_credential_that_vanished_stops_the_step(setup: Fixture) -> None:
    await _decor(setup, credential=None)
    with pytest.raises(ApplicationError, match="no longer exists"):
        await stage_activities.prepare_stage(_plan(setup))


async def test_an_actor_backend_that_contradicts_a_remote_agent_is_refused(setup: Fixture) -> None:
    await _decor(setup)
    with pytest.raises(ApplicationError) as raised:
        await stage_activities.prepare_stage(_plan(setup, backend="codex"))
    assert raised.value.type == "AgentIndisponible" and "remote" in str(raised.value)


async def test_a_declared_cost_that_does_not_fit_under_the_budget_refuses_the_stage(setup: Fixture) -> None:
    await _decor(setup, runtime=_runtime(declared_cost={"per_task_usd": 100000.0}))
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert prepared["refused"]["reason"] == "budget"
    assert await _reservations() == []


async def test_a_free_remote_agent_starts_with_nothing_declared(setup: Fixture) -> None:
    await _decor(setup, runtime=_runtime(declared_cost={}))
    prepared = await stage_activities.prepare_stage(_plan(setup))
    assert "refused" not in prepared and prepared["stage_input"]["remote"]["declared_cost"] == {
        "per_task_usd": None,
        "per_minute_usd": None,
    }


async def test_the_declared_cost_is_booked_as_external_apart_from_models(setup: Fixture) -> None:
    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _decor(setup)
    prepared = await stage_activities.prepare_stage(_plan(setup))
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        run.started_at = utcnow() - timedelta(minutes=2)
        run.ended_at = utcnow()
    await bilan.collect_spend({"project_id": setup.project_id, "run_id": prepared["run_id"]})

    async with session_scope() as session:
        (line,) = (await session.execute(select(CostLedger))).scalars().all()
        assert line.kind == "external" and line.provider == HOST and line.model == "a2a:rovo"
        assert line.cost_usd == pytest.approx(0.05 + 0.01 * 2, abs=0.002), (
            "per task, plus per minute of wall time"
        )
        assert line.tokens_in == 0 and line.tokens_out == 0
    assert all(r.revoked for r in await _reservations()), "the reservation is closed with the run"


async def test_nothing_declared_is_booked_as_unmeasured(setup: Fixture) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _decor(setup, runtime=_runtime(declared_cost={}))
    prepared = await stage_activities.prepare_stage(_plan(setup))
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    await bilan.collect_spend({"project_id": setup.project_id, "run_id": prepared["run_id"]})
    async with session_scope() as session:
        (line,) = (await session.execute(select(CostLedger))).scalars().all()
        assert line.kind == "external" and line.cost_usd == 0 and "unmeasured" in line.model


async def test_an_agent_that_is_not_remote_is_unchanged(setup: Fixture) -> None:
    """The remote branch must not touch an ordinary stage: a gateway key is still minted."""
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(org_id=project.org_id, slug="plain", kind="internal", display_name="P", status="active")
        session.add(agent)
        await session.flush()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=project.org_id,
                version=1,
                spec={"instructions": "x", "model": "profile:standard"},
                checksum="sha256:p1",
            )
        )
    prepared = await stage_activities.prepare_stage(_plan(setup, agent="plain"))
    assert prepared["stage_input"].get("remote") is None
    assert prepared["stage_input"]["agent"]["backend"] != "a2a"
    await stage_activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": prepared["stage_input"],
        }
    )
    assert setup.adapters.executor.started[-1].remote_token is None
    assert setup.adapters.executor.started[-1].gateway_key
