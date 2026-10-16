"""Each run gets its effective network (ADR 0070): agent, project and organisation, plus the repo host."""

from __future__ import annotations

import json
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

PROJECT_HOSTS = ["github.com", "pypi.org", "files.pythonhosted.org"]


async def _network(
    setup: Fixture, *, project: dict[str, Any] | None = None, ceiling: dict[str, Any] | None = None
) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        policy = policy_model(row)
        for key, value in {"allow_domains": PROJECT_HOSTS, **(project or {})}.items():
            setattr(policy.sandbox.network, key, value)
        row.json_doc = policy.model_dump(mode="json", exclude_none=True)
        if ceiling is not None:
            owner = await session.get(Project, setup.project_id)
            assert owner is not None
            org = await session.get(Organization, owner.org_id)
            assert org is not None
            org.settings = {**(org.settings or {}), "policy_ceiling": {"ceiling": {"network": ceiling}}}


async def _agent(setup: Fixture, network: dict[str, Any] | None = None, slug: str = "pip") -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        agent = Agent(
            org_id=projet.org_id, slug=slug, kind="internal", display_name=slug, status="active", scope="org"
        )
        session.add(agent)
        await session.flush()
        spec: dict[str, Any] = {"instructions": "Install what the ticket needs.", "model": "profile:standard"}
        if network is not None:
            spec["network"] = network
        session.add(
            AgentVersion(agent_id=agent.id, org_id=projet.org_id, version=1, spec=spec, checksum="sha256:v1")
        )


def _plan(setup: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
        **extra,
    }


async def _permissions(setup: Fixture, **extra: Any) -> dict[str, Any]:
    from choregos_orchestrator.activities.stage import prepare_stage

    return dict((await prepare_stage(_plan(setup, **extra)))["stage_input"]["permissions"])


async def test_a_stage_without_an_agent_gets_the_projects_list_the_deny_list_and_the_repository_host(
    setup: Fixture,
) -> None:
    await _network(
        setup, project={"allow_domains": ["pypi.org"]}, ceiling={"deny_domains": ["tracker.example"]}
    )
    permissions = await _permissions(setup)
    assert permissions["allow_domains"] == ["pypi.org", "github.com"], "github.com: the repository's host"
    assert permissions["deny_domains"] == ["tracker.example"]
    assert permissions["egress"] == "listed"
    assert permissions["builtin_web_tools"] is True


async def test_an_agent_narrows_the_projects_list(setup: Fixture) -> None:
    await _network(setup)
    await _agent(setup, {"domains": ["pypi.org"]})
    permissions = await _permissions(setup, agent="pip")
    assert permissions["allow_domains"] == ["pypi.org", "github.com"]
    assert "files.pythonhosted.org" not in permissions["allow_domains"]


async def test_an_agent_without_internet_still_reaches_its_repository(setup: Fixture) -> None:
    await _network(setup)
    await _agent(setup, {"domains": []})
    assert (await _permissions(setup, agent="pip"))["allow_domains"] == ["github.com"]


async def test_a_ceiling_deny_on_the_repository_host_removes_it_too(setup: Fixture) -> None:
    await _network(setup, ceiling={"deny_domains": ["github.com"]})
    permissions = await _permissions(setup)
    assert "github.com" not in permissions["allow_domains"]
    assert permissions["deny_domains"] == ["github.com"]


async def test_built_in_web_tools_follow_the_strictest_scope(setup: Fixture) -> None:
    await _network(setup, ceiling={"builtin_web_tools": "deny"})
    assert (await _permissions(setup))["builtin_web_tools"] is False


async def test_an_agent_can_switch_the_built_in_web_tools_off(setup: Fixture) -> None:
    await _network(setup)
    await _agent(setup, {"builtin_web_tools": False})
    assert (await _permissions(setup, agent="pip"))["builtin_web_tools"] is False


async def test_an_agent_needing_a_host_the_project_does_not_allow_is_refused_before_any_key(
    setup: Fixture,
) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    await _network(setup)
    await _agent(setup, {"domains": ["pypi.org", "api.stripe.com"]})
    from choregos_orchestrator.activities.stage import prepare_stage

    with pytest.raises(ApplicationError, match=r"api\.stripe\.com") as refused:
        await prepare_stage(_plan(setup, agent="pip"))
    assert refused.value.type == "AgentIndisponible" and refused.value.non_retryable
    assert "billing-api" in str(refused.value) and "pypi.org`" not in str(refused.value)
    assert setup.adapters.gateway.keys == {}, "no gateway key was minted"
    async with session_scope() as session:
        assert await session.get(Run, f"{setup.work_item_id}-t-implement-1") is None


async def test_an_agent_needing_a_host_the_ceiling_denies_is_refused(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _network(setup, ceiling={"deny_domains": ["pypi.org"]})
    await _agent(setup, {"domains": ["pypi.org"]})
    with pytest.raises(ApplicationError, match="denied by the organisation") as refused:
        await prepare_stage(_plan(setup, agent="pip"))
    assert refused.value.non_retryable


async def test_a_trial_uses_the_drafts_network(setup: Fixture) -> None:
    await _network(setup)
    draft = {"instructions": "Try me.", "network": {"domains": ["pypi.org"], "builtin_web_tools": False}}
    permissions = await _permissions(setup, trial_spec=draft, trial_budget_usd=0.5)
    assert permissions["allow_domains"] == ["pypi.org", "github.com"]
    assert permissions["builtin_web_tools"] is False

    from choregos_orchestrator.activities.stage import prepare_stage

    outside = {"instructions": "Try me.", "network": {"domains": ["api.stripe.com"]}}
    with pytest.raises(ApplicationError, match=r"api\.stripe\.com") as refused:
        await prepare_stage(_plan(setup, trial_spec=outside, trial_budget_usd=0.5, attempt=2))
    assert refused.value.type == "AgentIndisponible"


async def test_a_job_step_gets_the_projects_network(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage
    from choregos_orchestrator.config import reset_settings_cache

    image = "registry.example/choregos-runner-quant@sha256:" + "a" * 64
    monkeypatch.setenv("CHOREGOS_JOB_IMAGES", json.dumps({"quant": image}))
    reset_settings_cache()
    await _network(
        setup, project={"allow_domains": ["pypi.org"]}, ceiling={"deny_domains": ["tracker.example"]}
    )
    plan = _plan(setup, role="backtest", actor="engine", max_minutes=20)
    plan["job"] = {"command": ["choregos-quant", "backtest"], "image": "quant"}
    permissions = (await prepare_stage(plan))["stage_input"]["permissions"]
    assert permissions["allow_domains"] == ["pypi.org", "github.com"]
    assert permissions["deny_domains"] == ["tracker.example"]
    assert permissions["egress"] == "listed"


async def test_the_deployments_instance_deny_list_is_merged(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_NETWORK_INSTANCE_DENY", "evil.example, spam.example")
    reset_settings_cache()
    await _network(setup)
    assert (await _permissions(setup))["deny_domains"] == ["evil.example", "spam.example"]
